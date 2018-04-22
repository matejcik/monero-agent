#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Author: Dusan Klinec, ph4r05, 2018

import binascii

import monero_serialize as xmrser
from monero_serialize import xmrserialize, xmrtypes
from monero_glue import trezor, trezor_lite, monero, common, crypto


class TData(object):
    """
    Agent transaction-scoped data
    """
    def __init__(self):
        self.tsx_data = None  # type: monero.TsxData
        self.tx = xmrtypes.Transaction(version=2, vin=[], vout=[], extra=[])
        self.tx_in_hmacs = []
        self.tx_out_entr_hmacs = []
        self.tx_out_hmacs = []
        self.tx_out_rsigs = []
        self.tx_out_pk = []
        self.tx_out_ecdh = []
        self.source_permutation = []
        self.alphas = []
        self.pseudo_outs = []


class Agent(object):
    """
    Glue agent, running on host
    """
    def __init__(self, trezor):
        self.trezor = trezor  # type: trezor_lite.TrezorLite
        self.ct = None  # type: TData

    def is_simple(self, rv):
        """
        True if simpe
        :param rv:
        :return:
        """
        return rv.type in [xmrtypes.RctType.Simple, xmrtypes.RctType.SimpleBulletproof]

    def is_bulletproof(self, rv):
        """
        True if bulletproof
        :param rv:
        :return:
        """
        return rv.type in [xmrtypes.RctType.FullBulletproof, xmrtypes.RctType.SimpleBulletproof]

    async def sign_unsigned_tx(self, unsig):
        """
        Processes unsigned transaction set, returns serialized signed transactions.
        :param unsig:
        :return:
        """
        txes = []
        for tx in unsig.txes:
            await self.sign_transaction_data(tx)
            txes.append(await self.serialized_tx())

        return txes

    async def sign_tx(self, construction_data):
        """
        Transfers transaction, serializes response

        :param construction_data:
        :return:
        """
        if not isinstance(construction_data, list):
            construction_data = [construction_data]

        txes = []
        for tdata in construction_data:
            await self.sign_transaction_data(tdata)
            txes.append(await self.serialized_tx())

        return txes

    async def serialized_tx(self):
        """
        Returns the last signed transaction as blob
        :return:
        """
        return await self.serialize_tx(self.ct.tx)

    async def serialize_tx(self, tx):
        """
        Serializes transaction
        :param tx:
        :return:
        """
        writer = xmrserialize.MemoryReaderWriter()
        ar1 = xmrserialize.Archive(writer, True)
        await ar1.message(tx, msg_type=xmrtypes.Transaction)
        return bytes(writer.buffer)

    async def sign_transaction_data(self, tx):
        """
        Uses Trezor to sign the transaction
        :param tx:
        :type tx: xmrtypes.TxConstructionData
        :return:
        """
        self.ct = TData()

        payment_id = []
        extras = await monero.parse_extra_fields(tx.extra)
        extra_nonce = monero.find_tx_extra_field_by_type(extras, xmrtypes.TxExtraNonce)
        if extra_nonce and monero.has_encrypted_payment_id(extra_nonce.nonce):
            payment_id = monero.get_encrypted_payment_id_from_tx_extra_nonce(extra_nonce.nonce)

        # Init transaction
        tsx_data = trezor.TsxData()
        tsx_data.version = 1
        tsx_data.payment_id = payment_id
        tsx_data.unlock_time = tx.unlock_time
        tsx_data.outputs = tx.splitted_dsts
        tsx_data.change_dts = tx.change_dts
        tsx_data.num_inputs = len(tx.sources)
        tsx_data.mixin = len(tx.sources[0].outputs)
        tsx_data.fee = sum([x.amount for x in tx.sources]) - sum([x.amount for x in tx.splitted_dsts])
        tsx_data.account = tx.subaddr_account
        tsx_data.minor_indices = tx.subaddr_indices
        self.ct.tx.unlock_time = tx.unlock_time

        self.ct.tsx_data = tsx_data
        init_res = await self.trezor.init_transaction(tsx_data)
        in_memory = init_res[0]
        self.ct.tx_out_entr_hmacs = init_res[1]

        # Set transaction inputs
        for idx, src in enumerate(tx.sources):
            vini, vini_hmac, pseudo_out, alpha_enc = await self.trezor.set_tsx_input(src)
            self.ct.tx.vin.append(vini)
            self.ct.tx_in_hmacs.append(vini_hmac)
            self.ct.pseudo_outs.append(pseudo_out)
            self.ct.alphas.append(alpha_enc)

        # Sort key image
        self.ct.source_permutation = list(range(len(tx.sources)))
        self.ct.source_permutation.sort(key=lambda x: self.ct.tx.vin[x].k_image, reverse=True)

        def swapper(x, y):
            self.ct.tx.vin[x], self.ct.tx.vin[y] = self.ct.tx.vin[y], self.ct.tx.vin[x]
            self.ct.tx_in_hmacs[x], self.ct.tx_in_hmacs[y] = self.ct.tx_in_hmacs[y], self.ct.tx_in_hmacs[x]
            self.ct.pseudo_outs[x], self.ct.pseudo_outs[y] = self.ct.pseudo_outs[y], self.ct.pseudo_outs[x]
            self.ct.alphas[x], self.ct.alphas[y] = self.ct.alphas[y], self.ct.alphas[x]
            tx.sources[x], tx.sources[y] = tx.sources[y], tx.sources[x]

        common.apply_permutation(self.ct.source_permutation, swapper)

        if not in_memory:
            await self.trezor.tsx_inputs_permutation(self.ct.source_permutation)

        # Set vin_i back - tx prefix hashing
        # Done only if not in-memory.
        if not in_memory:
            for idx in range(len(self.ct.tx.vin)):
                await self.trezor.tsx_input_vini(tx.sources[idx], self.ct.tx.vin[idx], self.ct.tx_in_hmacs[idx],
                                                 self.ct.pseudo_outs[idx] if not in_memory else None)

        # Set transaction outputs
        for idx, dst in enumerate(tx.splitted_dsts):
            vouti, vouti_mac, rsig, out_pk, ecdh_info = await self.trezor.set_tsx_output1(dst, self.ct.tx_out_entr_hmacs[idx])
            self.ct.tx.vout.append(vouti)
            self.ct.tx_out_hmacs.append(vouti_mac)
            self.ct.tx_out_rsigs.append(rsig)
            self.ct.tx_out_pk.append(out_pk)
            self.ct.tx_out_ecdh.append(ecdh_info)

        tx_extra, tx_prefix_hash, rv = await self.trezor.all_out1_set()
        self.ct.tx.extra = list(bytearray(tx_extra))

        # Verify transaction prefix hash correctness, tx hash in one pass
        tx_prefix_hash_computed = await monero.get_transaction_prefix_hash(self.ct.tx)
        if tx_prefix_hash != tx_prefix_hash_computed:
            raise ValueError('Transaction prefix has does not match')

        # RctSig
        if self.is_simple(rv):
            if self.is_bulletproof(rv):
                rv.p.pseudoOuts = [x[0] for x in self.ct.pseudo_outs]
            else:
                rv.pseudoOuts = [x[0] for x in self.ct.pseudo_outs]

        # Range proof
        rv.p.rangeSigs = []
        rv.outPk = []
        rv.ecdhInfo = []
        for idx in range(len(self.ct.tx_out_rsigs)):
            rv.p.rangeSigs.append(self.ct.tx_out_rsigs[idx][0])
            rv.outPk.append(self.ct.tx_out_pk[idx])
            rv.ecdhInfo.append(self.ct.tx_out_ecdh[idx])

        # MLSAG message check
        mlsag_hash = await self.trezor.tsx_mlsag_done()
        mlsag_hash_computed = await monero.get_pre_mlsag_hash(rv)
        if mlsag_hash != mlsag_hash_computed:
            raise ValueError('Pre MLSAG hash has does not match')

        # Sign each input
        rv.p.MGs = []
        for idx, src in enumerate(tx.sources):
            mg, msc = await self.trezor.sign_input(src, self.ct.tx.vin[idx], self.ct.tx_in_hmacs[idx],
                                                   self.ct.pseudo_outs[idx],
                                                   self.ct.alphas[idx])
            rv.p.MGs.append(mg)

        self.ct.tx.signatures = []
        self.ct.tx.rct_signatures = rv
        return self.ct.tx



#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Author: Dusan Klinec, ph4r05, 2018

from monero_serialize import xmrtypes, xmrserialize
from . import common as common
from . import crypto
from . import b58
from . import b58_mnr
import binascii
import base64
import struct


class TsxData(xmrserialize.MessageType):
    """
    TsxData, initial input to the transaction processing.
    Serialization structure for easy hashing.
    """
    __slots__ = ['payment_id', 'unlock_time', 'outputs', 'change_dts']
    FIELDS = [
        ('payment_id', xmrserialize.BlobType),
        ('unlock_time', xmrserialize.UVarintType),
        ('outputs', xmrserialize.ContainerType, xmrtypes.TxDestinationEntry),
        ('change_dts', xmrtypes.TxDestinationEntry),
    ]

    def __init__(self, payment_id=None, outputs=None, change_dts=None, **kwargs):
        super().__init__(**kwargs)

        self.payment_id = payment_id
        self.change_dts = change_dts
        self.outputs = outputs if outputs else []  # type: list[xmrtypes.TxDestinationEntry]


def net_version():
    """
    Network version bytes
    :return:
    """
    return b'\x12'


def addr_to_hash(addr: xmrtypes.AccountPublicAddress):
    """
    Creates hashable address representation
    :param addr:
    :return:
    """
    return bytes(addr.m_spend_public_key + addr.m_view_public_key)


def encode_addr(version, spend_pub, view_pub):
    """
    Encodes public keys as versions
    :param version:
    :param spendP:
    :param viewP:
    :return:
    """
    buf = version + spend_pub + view_pub
    h = crypto.cn_fast_hash(buf)
    buf = binascii.hexlify(buf + h[0:4])
    return b58_mnr.b58encode(buf)


def classify_subaddresses(tx_dests, change_addr : xmrtypes.AccountPublicAddress):
    """
    Classify destination subaddresses
    void classify_addresses()
    :param tx_dests:
    :type tx_dests: list[xmrtypes.TxDestinationEntry]
    :param change_addr:
    :return:
    """
    num_stdaddresses = 0
    num_subaddresses = 0
    single_dest_subaddress = None
    addr_set = set()
    for tx in tx_dests:
        if change_addr and change_addr == tx.addr:
            continue
        addr_hashed = addr_to_hash(tx.addr)
        if addr_hashed in addr_set:
            continue
        addr_set.add(addr_hashed)
        if tx.is_subaddress:
            num_subaddresses+=1
            single_dest_subaddress = tx.addr
        else:
            num_stdaddresses+=1
    return num_stdaddresses, num_subaddresses, single_dest_subaddress


async def parse_extra_fields(extra_buff):
    """
    Parses extra buffer to the extra fields vector
    :param extra_buff:
    :return:
    """
    extras = []
    rw = xmrserialize.MemoryReaderWriter(extra_buff)
    ar2 = xmrserialize.Archive(rw, False)
    while len(rw.buffer) > 0:
        extras.append(await ar2.variant(elem_type=xmrtypes.TxExtraField))
    return extras


def find_tx_extra_field_by_type(extra_fields, msg):
    """
    Finds given message type in the extra array, or returns None if not found
    :param extra_fields:
    :param msg:
    :return:
    """
    for x in extra_fields:
        if isinstance(x, msg):
            return x


def has_encrypted_payment_id(extra_nonce):
    """
    Returns true if encrypted payment id is present
    :param extra_nonce:
    :return:
    """
    return len(extra_nonce) == 9 and extra_nonce[0] == 1


def get_encrypted_payment_id_from_tx_extra_nonce(extra_nonce):
    """
    Extracts encrypted payment id from extra
    :param extra_nonce:
    :return:
    """
    if 9 != len(extra_nonce):
        raise ValueError('Nonce size mismatch')
    if 0x1 != extra_nonce[0]:
        raise ValueError('Nonce payment type invalid')
    return extra_nonce[1:]


def set_payment_id_to_tx_extra_nonce(payment_id):
    """
    Sets payment ID to the extra
    :param payment_id:
    :return:
    """
    return b'\x00' + payment_id


def absolute_output_offsets_to_relative(off):
    """
    Relative offsets, prev + cur = next.
    Helps with varint encoding size.
    :param off:
    :return:
    """
    if len(off) == 0:
        return off
    res = sorted(off)
    for i in range(len(off)-1, 0, -1):
        res[i] -= res[i-1]
    return res


def get_destination_view_key_pub(destinations, change_addr=None):
    """
    Returns destination address public view key
    :param destinations:
    :type destinations: list[xmrtypes.TxDestinationEntry]
    :param change_addr:
    :return:
    """
    addr = xmrtypes.AccountPublicAddress(m_spend_public_key=crypto.NULL_KEY_ENC, m_view_public_key=crypto.NULL_KEY_ENC)
    count = 0
    for dest in destinations:
        if dest.amount == 0:
            continue
        if change_addr and dest.addr == change_addr:
            continue
        if dest.addr == addr:
            continue
        if count > 0:
            return [0]*32
        addr = dest.addr
        count += 1
    return addr.m_view_public_key


def encrypt_payment_id(payment_id, public_key, secret_key):
    """
    Encrypts payment_id hex.
    Used in the transaction extra. Only recipient is able to decrypt.
    :param payment_id:
    :param public_key:
    :param secret_key:
    :return:
    """
    derivation_p = crypto.generate_key_derivation(public_key, secret_key)
    derivation = crypto.encodepoint(derivation_p)
    derivation += b'\x8b'
    hash = crypto.cn_fast_hash(derivation)
    pm_copy = bytearray(payment_id)
    for i in range(8):
        pm_copy[i] ^= hash[i]
    return pm_copy


def set_encrypted_payment_id_to_tx_extra_nonce(payment_id):
    return b'\x01' + payment_id


async def remove_field_from_tx_extra(extra, mtype):
    """
    Removes extra field of fiven type from the buffer
    Reserializes with skipping the given mtype.
    :param extra:
    :param mtype:
    :return:
    """
    if len(extra) == 0:
        return []

    reader = xmrserialize.MemoryReaderWriter(extra)
    writer = xmrserialize.MemoryReaderWriter()
    ar_read = xmrserialize.Archive(reader, False)
    ar_write = xmrserialize.Archive(writer, True)
    while len(reader.buffer) > 0:
        c_extras = await ar_read.variant(elem_type=xmrtypes.TxExtraField)
        if not isinstance(c_extras, mtype):
            await ar_write.variant(c_extras, elem_type=xmrtypes.TxExtraField)

    return writer.buffer


def add_extra_nonce_to_tx_extra(extra, extra_nonce):
    """
    Appends nonce extra to the extra buffer
    :param extra:
    :param extra_nonce:
    :return:
    """
    if len(extra_nonce) > 255:
        raise ValueError('Nonce could be 255 bytes max')
    extra += b'\x02' + len(extra_nonce).to_bytes(1, byteorder='big') + extra_nonce
    return extra


def add_tx_pub_key_to_extra(tx_extra, pub_key):
    """
    Adds public key to the extra
    :param tx_extra:
    :param pub_key:
    :return:
    """
    tx_extra.append(1)  # TX_EXTRA_TAG_PUBKEY
    tx_extra.extend(crypto.encodepoint(pub_key))


async def add_additional_tx_pub_keys_to_extra(tx_extra, additional_pub_keys):
    """
    Adds all pubkeys to the extra
    :param tx_extra:
    :param additional_pub_keys:
    :return:
    """
    pubs_msg = xmrtypes.TxExtraAdditionalPubKeys(data=[crypto.encodepoint(x) for x in additional_pub_keys])

    rw = xmrserialize.MemoryReaderWriter()
    ar = xmrserialize.Archive(rw, True)
    await ar.message(pubs_msg)
    tx_extra.extend(rw.buffer)


def get_subaddress_secret_key(secret_key, index=None, major=None, minor=None):
    """
    Builds subaddress secret key from the subaddress index
    Hs(SubAddr || a || index_major || index_minor)

    TODO: handle endianity in the index
    C-code simply does: memcpy(data + sizeof(prefix) + sizeof(crypto::secret_key), &index, sizeof(subaddress_index));
    Where the index has the following form:

    struct subaddress_index {
        uint32_t major;
        uint32_t minor;
    }

    https://docs.python.org/3/library/struct.html#byte-order-size-and-alignment
    :param secret_key:
    :param index:
    :param major:
    :param minor:
    :return:
    """
    if index:
        major = index.major
        minor = index.minor
    prefix = b'SubAddr'
    buffer = bytearray(len(prefix) + 1 + 32 + 4 + 4)
    struct.pack_into('=7sb32sLL', buffer, 0, prefix, 0, crypto.encodeint(secret_key), major, minor)
    return crypto.hash_to_scalar(buffer)


def generate_key_derivation(pub_key, priv_key):
    """
    Generates derivation priv_key * pub_key.
    Simple ECDH.
    :param pub_key:
    :param priv_key:
    :return:
    """
    return crypto.generate_key_derivation(pub_key, priv_key)


def derive_subaddress_public_key(pub_key, derivation, output_index):
    """
    Derives subaddress public spend key address.
    :param pub_key:
    :param derivation:
    :param output_index:
    :return:
    """
    return crypto.derive_subaddress_public_key(pub_key, derivation, output_index)


def is_out_to_acc_precomp(subaddresses, out_key, derivation, additional_derivations, output_index):
    """
    Searches subaddresses for the computed subaddress_spendkey.
    If found, returns (major, minor), derivation.

    :param subaddresses:
    :param out_key:
    :param derivation:
    :param additional_derivations:
    :param output_index:
    :return:
    """
    subaddress_spendkey = crypto.encodepoint(derive_subaddress_public_key(out_key, derivation, output_index))
    if subaddress_spendkey in subaddresses:
        return subaddresses[subaddress_spendkey], derivation

    if additional_derivations and len(additional_derivations) > 0:
        if output_index >= len(additional_derivations):
            raise ValueError('Wrong number of additional derivations')

        subaddress_spendkey = derive_subaddress_public_key(out_key, additional_derivations[output_index], output_index)
        subaddress_spendkey = crypto.encodepoint(subaddress_spendkey)
        if subaddress_spendkey in subaddresses:
            return subaddresses[subaddress_spendkey], additional_derivations[output_index]

    return None


def generate_key_image_helper_precomp(ack, out_key, recv_derivation, real_output_index, received_index):
    """
    Generates UTXO spending key and key image.

    :param ack:
    :param out_key:
    :param recv_derivation:
    :param real_output_index:
    :param received_index:
    :return:
    """
    if ack.spend_key_private == 0:
        raise ValueError('Watch-only wallet not supported')

    # derive secret key with subaddress - step 1: original CN derivation
    scalar_step1 = crypto.derive_secret_key(recv_derivation, real_output_index, ack.spend_key_private)

    # step 2: add Hs(SubAddr || a || index_major || index_minor)
    subaddr_sk = None
    scalar_step2 = None
    if received_index == (0, 0):
        scalar_step2 = scalar_step1
    else:
        subaddr_sk = get_subaddress_secret_key(ack.view_key_private, received_index)
        scalar_step2 = crypto.sc_add(scalar_step1, subaddr_sk)

    # TODO: multisig here
    # ...

    pub_ver = crypto.scalarmult_base(scalar_step2)

    if not crypto.point_eq(pub_ver, out_key):
        raise ValueError('key image helper precomp: given output pubkey doesn\'t match the derived one')

    ki = crypto.generate_key_image(crypto.encodepoint(pub_ver), scalar_step2)
    return scalar_step2, ki


def generate_key_image_helper(creds, subaddresses, out_key, tx_public_key, additional_tx_public_keys, real_output_index):
    """
    Generates UTXO spending key and key image.
    Supports subaddresses.

    :param creds:
    :param subaddresses:
    :param out_key:
    :param tx_public_key:
    :param additional_tx_public_keys:
    :param real_output_index:
    :return:
    """
    recv_derivation = generate_key_derivation(tx_public_key, creds.view_key_private)

    additional_recv_derivations = []
    for add_pub_key in additional_tx_public_keys:
        additional_recv_derivations.append(generate_key_derivation(add_pub_key, creds.view_key_private))

    subaddr_recv_info = is_out_to_acc_precomp(subaddresses, out_key, recv_derivation, additional_recv_derivations, real_output_index)

    xi, ki = generate_key_image_helper_precomp(creds, out_key, subaddr_recv_info[1], real_output_index, subaddr_recv_info[0])
    return xi, ki, recv_derivation


async def get_pre_mlsag_hash(rv):
    """
    Generates final message for the Ring CT signature
    
    :param rv:
    :type rv: xmrtypes.RctSig
    :return:
    """
    kc_master = common.HashWrapper(common.get_keccak())
    kc_master.update(rv.message)

    if len(rv.mixRing) == 0:
        raise ValueError('Empty mixring')

    is_simple = rv.type in [xmrtypes.RctType.Simple, xmrtypes.RctType.SimpleBulletproof]
    inputs = len(rv.mixRing) if is_simple else len(rv.mixRing[0])
    outputs = len(rv.ecdhInfo)

    kwriter = common.get_keccak_writer()
    ar = xmrserialize.Archive(kwriter, True)
    await rv.serialize_rctsig_base(ar, inputs, outputs)
    c_hash = kwriter.get_digest()
    kc_master.update(c_hash)

    kc = common.get_keccak()
    if rv.type in [xmrtypes.RctType.FullBulletproof, xmrtypes.RctType.SimpleBulletproof]:
        for p in rv.p.bulletproofs:
            kc.update(p.A)
            kc.update(p.S)
            kc.update(p.T1)
            kc.update(p.T2)
            kc.update(p.taux)
            kc.update(p.mu)
            for i in range(len(p.L)):
                kc.update(p.L[i])
            for i in range(len(p.R)):
                kc.update(p.R[i])
            kc.update(p.a)
            kc.update(p.b)
            kc.update(p.t)

    else:
        for r in rv.p.rangeSigs:
            for i in range(64):
                kc.update(r.asig.s0[i])
            for i in range(64):
                kc.update(r.asig.s1[i])
            kc.update(r.asig.ee)
            for i in range(64):
                kc.update(r.Ci[i])

    c_hash = kc.digest()
    kc_master.update(c_hash)
    return kc_master.digest()


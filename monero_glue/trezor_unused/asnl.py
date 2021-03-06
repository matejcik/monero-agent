#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Author: https://github.com/monero-project/mininero
# Author: Dusan Klinec, ph4r05, 2018


import logging

from monero_glue.xmr import crypto

logger = logging.getLogger(__name__)


def gen_schnorr_non_linkable(x, P1, P2, index):
    """
    Generates simple Schnorr
    :param x:
    :param P1:
    :param P2:
    :param index:
    :return:
    """
    a = crypto.random_scalar()
    L1 = crypto.scalarmult_base(a)
    s2 = crypto.random_scalar()
    c2 = crypto.cn_fast_hash(crypto.encodepoint(L1))
    L2 = crypto.point_add(
        crypto.scalarmult_base(s2),
        crypto.scalarmult(P2 if index == 0 else P1, crypto.decodeint(c2)),
    )
    c1 = crypto.cn_fast_hash(crypto.encodepoint(L2))
    s1 = crypto.sc_mulsub(x, crypto.decodeint(c1), a)

    return (L1, s1, s2) if index == 0 else (L2, s2, s1)


def ver_schnorr_non_linkable(P1, P2, L1, s1, s2):
    """
    Verifies Schnorr signature generated by gen_schnorr_non_linkable
    :param P1:
    :param P2:
    :param L1:
    :param s1:
    :param s2:
    :return:
    """
    c2 = crypto.cn_fast_hash(crypto.encodepoint(L1))
    L2 = crypto.point_add(
        crypto.scalarmult_base(s2), crypto.scalarmult(P2, crypto.decodeint(c2))
    )
    c1 = crypto.cn_fast_hash(L2)
    L1p = crypto.point_add(
        crypto.scalarmult_base(s1), crypto.scalarmult(P1, crypto.decodeint(c1))
    )

    if L1 == L1p:
        return 0

    else:
        logger.warning("Didn't verify L1: %s, L1p: %s" % (L1, L1p))
        return -1


def gen_asnl(x, P1, P2, indices):
    """
    Aggregate Schnorr Non-Linkable
    x, P1, P2, are key vectors here, but actually you
    indices specifices which column of the given row of the key vector you sign.
    the key vector with the first or second key

    :param x:
    :param P1:
    :param P2:
    :param indices:
    :return:
    """
    n = len(x)
    logger.info("Generating Aggregate Schnorr Non-linkable Ring Signature")

    L1 = [None] * n
    s1 = [None] * n
    s2 = [None] * n
    s = crypto.sc_0()
    for j in range(0, n):
        L1[j], s1[j], s2[j] = gen_schnorr_non_linkable(x[j], P1[j], P2[j], indices[j])
        s = crypto.sc_add(s, s1[j])
    return L1, s2, s


def ver_asnl(P1, P2, L1, s2, s):
    """
    Aggregate Schnorr Non-Linkable
    :param P1:
    :param P2:
    :param L1:
    :param s2:
    :param s:
    :return:
    """
    logger.info("Verifying Aggregate Schnorr Non-linkable Ring Signature")

    n = len(P1)
    LHS = crypto.scalarmult_base(crypto.sc_0())
    RHS = crypto.scalarmult_base(s)
    for j in range(0, n):
        c2 = crypto.hash_to_scalar(crypto.encodepoint(L1[j]))
        L2 = crypto.point_add(
            crypto.scalarmult_base(s2[j]), crypto.scalarmult(P2[j], c2)
        )
        LHS = crypto.point_add(LHS, L1[j])
        c1 = crypto.hash_to_scalar(crypto.encodepoint(L2))
        RHS = crypto.point_add(RHS, crypto.scalarmult(P1[j], c1))

    if crypto.point_eq(LHS, RHS):
        return 1
    else:
        logger.warning(
            "Didn't verify L1: %s, L1p: %s"
            % (crypto.encodepoint(LHS), crypto.encodepoint(RHS))
        )
        return 0

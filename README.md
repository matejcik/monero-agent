# Monero Wallet Python implementation

[![Build Status](https://travis-ci.org/ph4r05/monero-agent.svg?branch=master)](https://travis-ci.org/ph4r05/monero-agent)

Pure-python Monero Wallet implementation in Python3.

Implements transaction signing protocol designed for Trezor hardware wallet as described in [monero-trezor-doc].

The main purpose of this repo is to provide host side (agent) for the transaction signing with the Trezor hardware wallet.
The repo also contains the initial implementation for the Trezor side. The Trezor protocol side underwent heavy refactoring
and is about to be merged to the [trezor-core] repository.

The repo provides integration tests for Trezor wallet transaction signing.

- PR adding Monero support to the Trezor hardware wallet (client side of the signing protocol): https://github.com/trezor/trezor-core/pull/293
- PR adding Trezor hardware support to official Monero codebase: https://github.com/monero-project/monero/pull/4241

## Work in progress

This work is still in progress.

The pure python EC crypto operations are not constant-time as it serves mainly as PoC at the moment.
The code supports also [trezor-crypto] crypto backend which is fast and constant-time.

Moreover, the code will probably be subject to a major refactoring and cleaning.

## Supported features

 - Full RingCT (one UTXO)
 - Simple RingCT (more than 1 UTXOs)
 - Sub-addresses
 - Key image sync
 - Bulletproofs (batch verification, signing, ready for v9 fork)
 - Ledger protocol implementation, HW wallet side

## Roadmap

 - Spend proof
 - Reserver proof
 - Multisig
 - Wallet implementation (funds receiving, UTXO mixing)
 - Ledger protocol implementation, host side

## Protocol

In order to support RingCT on hardware wallet with limited resources a subdivided protocol had to be implemented.
It is not feasible to process the signed transaction in one run on the hardware wallet with tens of UTXOs and multiple outputs.

The introduction to the topic is described here:

https://github.com/ph4r05/monero-trezor-doc

The documentation can be out of sync from the code. Take this source code as a primary reference.

In the current protocol it is assumed there may be multiple input UTXO (tens to hundreds). So it is optimized
to work incrementally, one UTXO at a time. This is reasonable to assume as your funds may be scattered over
many small transactions. On the other hand we assume the number of outputs is relatively small (small units) as
it usually is in the Monero transactions.

It is quite easy to extend protocol to work with large amounts of outputs but due to the message structure
which is later signed it will be needed to add two more roundrips with sending output related data one by one
to the Trezor for incremental hashing.

Outputs are pinned in the beginning of the protocol - number of outputs is fixed at this point in the Trezor
and HMAC with unique key (index dependent) is generated for each output. So in further roundtrips it is assured only
previously pinned outputs in the exact given order are processed. The same principle is used for each data produced by
the Trezor which are later used as inputs.

## Project structure

Agent <-> Trezor

Agent is an object supposed to run on the host PC where Trezor is the HW wallet implementation.
`agent.py` and `trezor.py` are mainly ports of the C++ code to the Python for PoC, experimentation and testing.
These versions are not optimized for usage in HW environment.

Optimized versions are `agent_lite.py` and `trezor_lite.py`.

## Serialize lib

The project depends on my `monero-serialize` library.
Data objects used in the Monero are defined there, it supports serialization / deserialization to binary format.
The serialized binary messages are hashed during the transaction signature.

https://github.com/ph4r05/monero-serialize

## Crypto

Monero uses Ed25519 elliptic curve. The current implementation is not optimized to avoid side-channel leaks (e.g., timing)
as it serves mainly as PoC.

The project uses Ed25519 implementation which
works in extended Edwards coordinates `(x, y, z, t)`.

The only code directly handling point representation is `crypto.py`. All other objects are using `crypto.py`
to do the EC computation. Point representation is opaque to the other modules.

The opaque point representation can be converted to bytearray representation suitable for transport
(compressed, y-coordinate + sign flag) using `crypto.encodepoint()` and `crypto.decodepoint()`.

Scalars are represented as integers (no encoding / decoding is needed). However, we are working in modular ring so
for scalar operations such as addition, division, comparison use the `crypto.sc_*` methods.

## Trezor-crypto

A new crypto backend was added, `trezor-crypto`.
I implemented missing cryptographic algorithms to the [trezor-crypto], branch `lib` (abbrev. TCRY).
Compiled shared library `libtrezor-crypto.so` can be used instead of the Python crypto backend.
TCRY implements constant-time curve operations, uses [libsodium] to generate random values.

Borromean Range proof was reimplemented in C for CPU and memory efficiency.

Travis tests with both crypto backends. In order to test with TCRY install all its dependencies. `libsodium` is the only one
dependency for the shared lib. For more info take a look at `travis-install-libtrezor-crypto.sh`.

Crypto dependency is selected based on the `EC_BACKEND` env var. `0` is for Python backend, `1` for TCRY.
Path to the TCRY is specified via `LIBTREZOR_CRYPTO_PATH` env var. If the TCRY is not found or could not be loaded
the code fallbacks to python backend. This behaviour can be changed by setting `EC_BACKEND_FORCE` env var to `1`.

TCRY is also 20 times faster (unit tests).

```bash
$> EC_BACKEND_FORCE=1 EC_BACKEND=0  ./venv/bin/python -m unittest monero_glue_test/test_*.py
...s................................................................
----------------------------------------------------------------------
Ran 68 tests in 416.823s

OK
```

TCRY backend:

```bash
$>  EC_BACKEND_FORCE=1 EC_BACKEND=1  ./venv/bin/python -m unittest monero_glue_test/test_*.py
....................................................................
----------------------------------------------------------------------
Ran 68 tests in 84.057s

OK
```

UPDATE: I created a python binding [py-trezor-crypto] which can be installed from pip. The pip builds [trezor-crypto]
library. Please refer to the readme of the [py-trezor-crypto] for installation details (dependencies).

To install python bindings with agent run:

```bash
pip install 'monero_agent[tcry]'
```

Libsodium, pkg-config, gcc, python-dev are required for the installation.

## More on using the repo

Please refer to the PoC.md for more usage examples.

### Memory considerations

Python uses arbitrary precision integers with a memory overhead.
The following command shows the amount of memory required for certain data types and sizes:

```python
>>> sys.getsizeof(0)
24
>>> sys.getsizeof(2**32-1)  # 4B num
32
>>> sys.getsizeof(2**64-1)  # 8B num
36
>>> sys.getsizeof(2**256-1)  # 32B num
60
>>> sys.getsizeof(b'\x00'*32)  # 32B hex
65
>>> sys.getsizeof(b'\x00'*64)  # 64B hex
97
```

Monero works in EC with 32 B numbers.
To store a 32 B number it takes 60 B in integer representation and 65 B in the byte string encoded
representation (some ed25519 libraries and mininero use this representation).
For scalars it is apparently more effective to store integers naturally, saving both memory and CPU cycles with recoding.

EC point arithmetics can use classic point coordinates `(x, y)` or extended Edwards point coordinates `(x,y,z,t)`.
It takes 64 and 80 B to store tuple of 2 and 4 elements respectively.
It thus take 184 B and 320 B to store an EC point in the natural form compared to the 65 B byte representation.

# Donations
Thanks for your support!

47BEukN83whUdvuXbaWmDDQLYNUpLsvFR2jioQtpP5vD8b3o74b9oFgQ3KFa3ibjbwBsaJEehogjiUCfGtugUGAuJAfbh1Z

# Related projects

- [monero-trezor-doc]
- [monero-serialize]
- [trezor-crypto]
- [py-trezor-crypto]
- [py-cryptonight]
- [trezor-core]
- [trezor-crypto]
- [trezor-common]


[trezor-core]: https://github.com/ph4r05/trezor-core
[trezor-crypto]: https://github.com/ph4r05/trezor-crypto
[trezor-common]: https://github.com/ph4r05/trezor-common
[libsodium]: https://github.com/jedisct1/libsodium
[py-trezor-crypto]: https://github.com/ph4r05/py-trezor-crypto
[py-cryptonight]: https://github.com/ph4r05/py-cryptonight
[monero-serialize]: https://github.com/ph4r05/monero-serialize
[monero-trezor-doc]: https://github.com/ph4r05/monero-trezor-doc

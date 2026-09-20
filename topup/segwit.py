from __future__ import annotations

import hashlib
import struct

from bech32 import bech32_decode, convertbits
from coincurve import PrivateKey

SIGHASH_ALL = 0x01
_B58_ALPHABET = b"123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def _hash256(data: bytes) -> bytes:
    return _sha256(_sha256(data))


def _hash160(data: bytes) -> bytes:
    try:
        ripe = hashlib.new("ripemd160", usedforsecurity=False)
    except (ValueError, TypeError):
        ripe = hashlib.new("ripemd160")
    ripe.update(_sha256(data))
    return ripe.digest()


def _varint(n: int) -> bytes:
    if n < 0xFD:
        return struct.pack("<B", n)
    if n <= 0xFFFF:
        return b"\xfd" + struct.pack("<H", n)
    if n <= 0xFFFFFFFF:
        return b"\xfe" + struct.pack("<I", n)
    return b"\xff" + struct.pack("<Q", n)


def _b58decode_check(address: str) -> bytes:
    num = 0
    for char in address.encode():
        num = num * 58 + _B58_ALPHABET.index(char)
    full = num.to_bytes((num.bit_length() + 7) // 8 or 1, "big")
    pad = len(address) - len(address.lstrip("1"))
    raw = b"\x00" * pad + full
    payload, checksum = raw[:-4], raw[-4:]
    if _hash256(payload)[:4] != checksum:
        raise ValueError(f"Invalid address checksum: {address}")
    return payload


def script_pubkey(address: str, hrp: str, p2pkh_version: int) -> bytes:
    lowered = address.lower()
    if lowered.startswith(hrp + "1"):
        header, data = bech32_decode(lowered)
        if header != hrp or data is None:
            raise ValueError(f"Invalid bech32 address: {address}")
        witver = data[0]
        converted = convertbits(data[1:], 5, 8, False)
        if converted is None:
            raise ValueError(f"Invalid bech32 address: {address}")
        prog = bytes(converted)
        if witver != 0 or len(prog) != 20:
            raise ValueError("Treasury address must be a native-segwit P2WPKH address.")
        return b"\x00\x14" + prog

    raw = _b58decode_check(address)
    if raw[0] != p2pkh_version or len(raw) != 21:
        raise ValueError(
            f"Unsupported {hrp} destination address. Use a {hrp}1q... or legacy P2PKH address."
        )
    return b"\x76\xa9\x14" + raw[1:] + b"\x88\xac"


def compressed_pubkey(priv_hex: str) -> bytes:
    return PrivateKey(bytes.fromhex(priv_hex)).public_key.format(compressed=True)


def p2wpkh_script_pubkey(priv_hex: str) -> bytes:
    return b"\x00\x14" + _hash160(compressed_pubkey(priv_hex))


def estimate_vsize(n_in: int, dest_script: bytes) -> int:
    out_size = 8 + len(_varint(len(dest_script))) + len(dest_script)
    return 11 + (68 * n_in) + out_size


def build_p2wpkh_tx(
    priv_hex: str,
    utxos: list[tuple[str, int, int]],
    dest_script: bytes,
    fee_sats: int,
) -> str:
    total = sum(value for _txid, _vout, value in utxos)
    send = total - fee_sats
    if send <= 0:
        raise ValueError("Fee is larger than the deposit.")

    pubkey = compressed_pubkey(priv_hex)
    pub_hash = _hash160(pubkey)
    script_code = b"\x19\x76\xa9\x14" + pub_hash + b"\x88\xac"
    sequence = 0xFFFFFFFF
    version = 2
    locktime = 0

    outpoints = [bytes.fromhex(txid)[::-1] + struct.pack("<I", vout) for txid, vout, _value in utxos]
    hash_prevouts = _hash256(b"".join(outpoints))
    hash_sequence = _hash256(b"".join(struct.pack("<I", sequence) for _ in utxos))
    raw_output = struct.pack("<Q", send) + _varint(len(dest_script)) + dest_script
    hash_outputs = _hash256(raw_output)

    key = PrivateKey(bytes.fromhex(priv_hex))
    witnesses: list[bytes] = []
    for i, (_txid, _vout, value) in enumerate(utxos):
        preimage = b"".join(
            [
                struct.pack("<I", version),
                hash_prevouts,
                hash_sequence,
                outpoints[i],
                script_code,
                struct.pack("<Q", value),
                struct.pack("<I", sequence),
                hash_outputs,
                struct.pack("<I", locktime),
                struct.pack("<I", SIGHASH_ALL),
            ]
        )
        signature = key.sign(
            preimage,
            hasher=lambda data: hashlib.sha256(hashlib.sha256(data).digest()).digest(),
        ) + bytes([SIGHASH_ALL])
        witnesses.append(_varint(2) + _varint(len(signature)) + signature + _varint(len(pubkey)) + pubkey)

    vin = b"".join(outpoint + b"\x00" + struct.pack("<I", sequence) for outpoint in outpoints)
    raw = b"".join(
        [
            struct.pack("<I", version),
            b"\x00\x01",
            _varint(len(utxos)),
            vin,
            _varint(1),
            raw_output,
            b"".join(witnesses),
            struct.pack("<I", locktime),
        ]
    )
    return raw.hex()

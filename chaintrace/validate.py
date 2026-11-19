"""Input validation. Seeds are interpolated into upstream API URLs, so they must be strict."""

from __future__ import annotations

import re

_TXID = re.compile(r"^[0-9a-fA-F]{64}$")
# Legacy P2PKH / P2SH (base58) and SegWit / Taproot (bech32/bech32m, lowercase) mainnet addresses.
_BASE58 = re.compile(r"^[13][a-km-zA-HJ-NP-Z1-9]{25,34}$")
_BECH32 = re.compile(r"^bc1[ac-hj-np-z02-9]{11,71}$")


def is_txid(value: str) -> bool:
    return bool(_TXID.match(value))


def is_btc_address(value: str) -> bool:
    return bool(_BASE58.match(value) or _BECH32.match(value))


def classify_seed(seed: str) -> str:
    """Return 'tx' or 'address', or raise ValueError."""
    seed = seed.strip()
    if is_txid(seed):
        return "tx"
    if is_btc_address(seed) or is_btc_address(seed.lower()):
        return "address"
    raise ValueError(
        "Seed must be a 64-character transaction ID or a Bitcoin mainnet address "
        "(1..., 3..., or bc1...)."
    )


def normalize_seed(seed: str) -> str:
    seed = seed.strip()
    kind = classify_seed(seed)
    if kind == "tx" or seed.lower().startswith("bc1"):
        return seed.lower()
    return seed

"""Synthetic blockchain fixtures so the engine can be tested offline and deterministically."""

from __future__ import annotations

import pytest

from chaintrace.esplora import NotFoundError
from chaintrace.models import Transaction

BTC = 100_000_000
_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def addr(name: str) -> str:
    """Deterministic, syntactically valid bech32-looking address."""
    n = sum(ord(c) * 31**i for i, c in enumerate(name)) % (32**12)
    body = ""
    for _ in range(12):
        body += _CHARSET[n % 32]
        n //= 32
    return "bc1q" + body.ljust(38, "q")


def txid(c: str) -> str:
    return (c * 64)[:64]


def make_tx(tid: str, inputs: list[tuple], outputs: list[tuple], height: int = 800_000) -> Transaction:
    """inputs: (prev_txid, vout, address, sats[, type]); outputs: (address, sats[, type])"""
    vin = []
    for inp in inputs:
        prev, vout, a, value, *rest = inp
        vin.append({"txid": prev, "vout": vout, "is_coinbase": False,
                    "prevout": {"scriptpubkey_address": a, "value": value,
                                "scriptpubkey_type": rest[0] if rest else "v0_p2wpkh"}})
    vout = [{"scriptpubkey_address": a, "value": v, "scriptpubkey_type": rest[0] if rest else "v0_p2wpkh"}
            for a, v, *rest in outputs]
    fee = sum(i[3] for i in inputs) - sum(o[1] for o in outputs)
    raw = {"txid": tid, "vin": vin, "vout": vout, "fee": fee,
           "status": {"confirmed": True, "block_height": height, "block_time": 1_700_000_000 + height}}
    return Transaction.from_esplora(raw)


class FakeClient:
    source = "fake://test-chain"

    def __init__(self, txs: list[Transaction]):
        self.txs = {t.txid: t for t in txs}
        self.calls = 0

    def get_tx(self, tid: str) -> Transaction:
        self.calls += 1
        if tid not in self.txs:
            raise NotFoundError(tid)
        return self.txs[tid]

    def get_outspends(self, tid: str) -> list[dict]:
        tx = self.get_tx(tid)
        spends = [{"spent": False} for _ in tx.outputs]
        for t in self.txs.values():
            for k, i in enumerate(t.inputs):
                if i.prev_txid == tid:
                    spends[i.prev_vout] = {"spent": True, "txid": t.txid, "vin": k}
        return spends

    def get_address_txs(self, address: str, max_txs: int = 50) -> list[Transaction]:
        return [t for t in self.txs.values()
                if address in t.input_addresses or address in t.output_addresses][:max_txs]


# --- Scenario -------------------------------------------------------------------
# TX_A (seed): A1 pays 1.0 BTC (round) to B1, 8.9999 change to A2
# TX_B: A2 peels 0.5 to C1, 8.4998 to A3      -> peel chain A -> B -> C
# TX_C: A3 peels 0.3 to D1, 8.1997 to A4
# TX_D: B1 + B2 (co-spent => same owner) pay EXCH
# TX_J: equal-output CoinJoin spending C1 with two strangers
TX_A, TX_B, TX_C, TX_D, TX_J, TX_X = (txid(c) for c in "abcdef")


@pytest.fixture
def chain() -> list[Transaction]:
    return [
        make_tx(TX_A, [(TX_X, 0, addr("A1"), 10 * BTC)],
                [(addr("B1"), 1 * BTC, "p2pkh"), (addr("A2"), 899_990_000)]),
        make_tx(TX_B, [(TX_A, 1, addr("A2"), 899_990_000)],
                [(addr("C1"), 50_000_000, "p2pkh"), (addr("A3"), 849_980_000)]),
        make_tx(TX_C, [(TX_B, 1, addr("A3"), 849_980_000)],
                [(addr("D1"), 30_000_000, "p2pkh"), (addr("A4"), 819_970_000)]),
        make_tx(TX_D, [(TX_A, 0, addr("B1"), 1 * BTC, "p2pkh"), ("9" * 64, 0, addr("B2"), 20_000_000, "p2pkh")],
                [(addr("EXCH"), 119_990_000)]),
        make_tx(TX_J, [(TX_B, 0, addr("C1"), 50_000_000, "p2pkh"),
                       ("8" * 64, 0, addr("S1"), 50_000_000), ("7" * 64, 0, addr("S2"), 50_000_000)],
                [(addr("J1"), 49_990_000), (addr("J2"), 49_990_000), (addr("J3"), 49_990_000)]),
    ]


@pytest.fixture
def client(chain) -> FakeClient:
    return FakeClient(chain)

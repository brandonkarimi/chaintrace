"""Classic UTXO forensic heuristics.

All results are *probabilistic investigative leads*, not proof. Each function
returns the reasons behind its conclusion so an analyst can check them.

References:
  - Meiklejohn et al., "A Fistful of Bitcoins" (2013): common-input-ownership, change.
  - Möser & Narayanan, "Resurrecting Address Clustering in Bitcoin" (2022).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from .models import Transaction

ROUND_UNIT = 100_000  # 0.001 BTC: payments are often round in BTC terms


# -- CoinJoin detection ------------------------------------------------------------
def is_coinjoin(tx: Transaction) -> bool:
    """Detect equal-output CoinJoins (Whirlpool, Wasabi, JoinMarket-style).

    Rule: at least 3 distinct input addresses and at least 3 outputs of the
    same value. A single-owner batch payout fails the distinct-inputs test.
    """
    if tx.is_coinbase or len(tx.inputs) < 3 or len(tx.outputs) < 3:
        return False
    counts = Counter(o.value for o in tx.outputs if o.value > 0)
    if not counts:
        return False
    _, n_equal = counts.most_common(1)[0]
    return n_equal >= 3 and len(tx.input_addresses) >= 3


# -- Common-input-ownership clustering --------------------------------------------
class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:  # path compression
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def cluster_addresses(txs: list[Transaction], coinjoins: set[str] | None = None) -> dict[str, list[str]]:
    """Group addresses spent together as inputs of the same tx (likely one owner).

    CoinJoins are skipped because their inputs belong to many different people.
    Returns {cluster_id: sorted member addresses}. Only clusters of 2+ addresses
    are returned, ordered by size (largest first) for stable IDs.
    """
    coinjoins = coinjoins if coinjoins is not None else {t.txid for t in txs if is_coinjoin(t)}
    uf = _UnionFind()
    for tx in txs:
        if tx.txid in coinjoins or tx.is_coinbase:
            continue
        addrs = sorted(tx.input_addresses)
        for a in addrs:
            uf.find(a)
        for a in addrs[1:]:
            uf.union(addrs[0], a)

    groups: dict[str, list[str]] = {}
    for addr in uf.parent:
        groups.setdefault(uf.find(addr), []).append(addr)
    multi = sorted((sorted(m) for m in groups.values() if len(m) > 1), key=lambda m: (-len(m), m[0]))
    return {f"C{i + 1}": members for i, members in enumerate(multi)}


# -- Change detection --------------------------------------------------------------
@dataclass
class ChangeGuess:
    index: int
    address: str | None
    confidence: float
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"index": self.index, "address": self.address, "confidence": round(self.confidence, 2), "reasons": self.reasons}


def _is_round(value: int) -> bool:
    return value > 0 and value % ROUND_UNIT == 0


def detect_change(tx: Transaction) -> ChangeGuess | None:
    """Guess which output of a 2-output tx is change returning to the sender."""
    if tx.is_coinbase or len(tx.outputs) != 2 or is_coinjoin(tx):
        return None
    outs = tx.outputs
    input_addrs = tx.input_addresses

    # Address reuse: an output paying back to an input address is change.
    reused = [o for o in outs if o.address and o.address in input_addrs]
    if len(reused) == 1:
        o = reused[0]
        return ChangeGuess(o.index, o.address, 0.95, ["output address is also an input address (address reuse)"])

    scores = [0.0, 0.0]
    reasons: list[list[str]] = [[], []]

    input_types = {i.script_type for i in tx.inputs if i.script_type}
    if len(input_types) == 1:
        (t,) = input_types
        matching = [k for k, o in enumerate(outs) if o.script_type == t]
        if len(matching) == 1:
            k = matching[0]
            scores[k] += 0.35
            reasons[k].append(f"only output with the same script type as the inputs ({t})")

    round_flags = [_is_round(o.value) for o in outs]
    if round_flags.count(True) == 1:
        k = round_flags.index(False)
        scores[k] += 0.3
        reasons[k].append("the other output is a round amount (likely the payment)")

    if len(tx.inputs) > 1:
        min_input = min(i.value for i in tx.inputs)
        below = [k for k, o in enumerate(outs) if o.value < min_input]
        if len(below) == 1:
            k = below[0]
            scores[k] += 0.25
            reasons[k].append("smaller than every input; a wallet would not add an unneeded input to pay it")

    best = 0 if scores[0] >= scores[1] else 1
    if scores[best] < 0.3 or scores[best] == scores[1 - best]:
        return None
    o = outs[best]
    return ChangeGuess(o.index, o.address, min(0.9, scores[best]), reasons[best])


# -- Peel chain detection ----------------------------------------------------------
def _peel_shape(tx: Transaction) -> bool:
    return not tx.is_coinbase and 1 <= len(tx.inputs) <= 2 and len(tx.outputs) == 2


def detect_peel_chains(txs: list[Transaction], min_length: int = 3) -> list[list[str]]:
    """Find peel chains: a large balance repeatedly 'peeled' by small payments.

    Each hop is a 1-2 input, 2-output tx whose larger output (at least 2x the
    smaller) is spent by the next hop of the same shape. Common in laundering
    and exchange cash-out.
    """
    by_id = {t.txid: t for t in txs}
    spender: dict[tuple[str, int], str] = {}
    for t in txs:
        for i in t.inputs:
            if i.prev_txid is not None and i.prev_vout is not None:
                spender[(i.prev_txid, i.prev_vout)] = t.txid

    nxt: dict[str, str] = {}
    for t in txs:
        if not _peel_shape(t):
            continue
        big, small = sorted(t.outputs, key=lambda o: o.value, reverse=True)
        if small.value <= 0 or big.value < 2 * small.value:
            continue
        s = spender.get((t.txid, big.index))
        if s and s in by_id and _peel_shape(by_id[s]):
            nxt[t.txid] = s

    has_pred = set(nxt.values())
    chains = []
    for start in nxt:
        if start in has_pred:
            continue
        chain, cur, seen = [start], start, {start}
        while cur in nxt and nxt[cur] not in seen:
            cur = nxt[cur]
            chain.append(cur)
            seen.add(cur)
        if len(chain) >= min_length:
            chains.append(chain)
    return sorted(chains, key=len, reverse=True)

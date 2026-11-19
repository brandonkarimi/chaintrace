"""Explainable rule-based address risk scoring.

Each address gets a 0-100 score and a list of reasons. This is a baseline; the
planned GNN model (trained on the Elliptic datasets) will plug in as another
signal next to these rules, so its output can be compared against them.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from .labels import LabelStore
from .models import Transaction

MAX_PAIRS_PER_TX = 5000  # skip exposure edges for enormous txs (input x output blow-up)


@dataclass
class RiskAssessment:
    score: int = 0
    reasons: list[str] = field(default_factory=list)

    @property
    def level(self) -> str:
        if self.score >= 80:
            return "severe"
        if self.score >= 50:
            return "high"
        if self.score >= 25:
            return "medium"
        return "low"

    def to_dict(self) -> dict:
        return {"score": self.score, "level": self.level, "reasons": self.reasons}


def _bfs(adj: dict[str, set[str]], start: str, max_depth: int) -> dict[str, int]:
    dist = {start: 0}
    q = deque([start])
    while q:
        cur = q.popleft()
        if dist[cur] >= max_depth:
            continue
        for n in adj.get(cur, ()):
            if n not in dist:
                dist[n] = dist[cur] + 1
                q.append(n)
    return dist


def score_addresses(
    txs: list[Transaction],
    labels: LabelStore,
    clusters: dict[str, list[str]],
    coinjoins: set[str],
    peel_txs: set[str],
) -> dict[str, RiskAssessment]:
    addresses: set[str] = set()
    downstream: dict[str, set[str]] = {}  # funds flowed a -> b
    upstream: dict[str, set[str]] = {}  # reverse
    for tx in txs:
        ins, outs = tx.input_addresses, tx.output_addresses
        addresses |= ins | outs
        if tx.txid in coinjoins or len(ins) * len(outs) > MAX_PAIRS_PER_TX:
            continue  # CoinJoins break the link; huge txs would dilute exposure meaninglessly
        for a in ins:
            for b in outs:
                if a != b:
                    downstream.setdefault(a, set()).add(b)
                    upstream.setdefault(b, set()).add(a)

    risk = {a: RiskAssessment() for a in addresses}
    extra: dict[str, int] = {a: 0 for a in addresses}

    def bump_base(addr: str, score: int, reason: str) -> None:
        r = risk[addr]
        if score > r.score:
            r.score = score
        r.reasons.append(reason)

    # 1. Direct labels
    flagged = []
    for a in addresses:
        label = labels.get(a)
        if label:
            bump_base(a, label.severity, f"labeled {label.category}: {label.name} (source: {label.source or 'n/a'})")
            if label.illicit:
                flagged.append((a, label))

    # 2. Cluster co-membership (common-input ownership => same entity)
    for cid, members in clusters.items():
        illicit = [(m, lb) for m in members if (lb := labels.get(m)) is not None and lb.illicit]
        for m in members:
            for src, label in illicit:
                if src != m and m in risk:
                    bump_base(m, int(label.severity * 0.9),
                              f"same cluster {cid} (common-input ownership) as {label.name} ({label.category})")

    # 3. Flow exposure to illicit sources (direction matters). An entity often controls
    #    many addresses, so exposure counts once per entity, at its closest distance.
    received: dict[tuple[str, str], tuple[int, str]] = {}  # (addr, entity) -> (distance, category)
    sent: dict[tuple[str, str], str] = {}
    for src, label in flagged:
        for addr, d in _bfs(downstream, src, 2).items():
            if d > 0 and addr in risk:
                key = (addr, label.name)
                if key not in received or d < received[key][0]:
                    received[key] = (d, label.category)
        for addr, d in _bfs(upstream, src, 1).items():
            if d == 1 and addr in risk:
                sent[(addr, label.name)] = label.category
    for (addr, name), (d, cat) in received.items():
        extra[addr] += 40 if d == 1 else 20
        risk[addr].reasons.append(f"received funds {d} hop{'s' if d > 1 else ''} downstream of {name} ({cat})")
    for (addr, name), cat in sent.items():
        extra[addr] += 25
        risk[addr].reasons.append(f"sent funds directly to {name} ({cat})")

    # 4. Behavioural signals
    peel_count: dict[str, int] = {}
    for tx in txs:
        if tx.txid in coinjoins:
            for a in tx.output_addresses:
                extra[a] += 20
                risk[a].reasons.append(f"received a CoinJoin output (tx {tx.txid[:12]}...)")
            for a in tx.input_addresses:
                extra[a] += 10
                risk[a].reasons.append(f"spent into a CoinJoin (tx {tx.txid[:12]}...)")
        if tx.txid in peel_txs:
            for a in tx.output_addresses:
                peel_count[a] = peel_count.get(a, 0) + 1

    for a, n in peel_count.items():
        extra[a] += 10
        risk[a].reasons.append(f"received outputs of {n} peel-chain transaction{'s' if n > 1 else ''}")

    for a, r in risk.items():
        r.score = max(0, min(100, r.score + extra[a]))
        # de-duplicate reasons, keeping order
        r.reasons = list(dict.fromkeys(r.reasons))
    return risk

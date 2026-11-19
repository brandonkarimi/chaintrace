"""Run every heuristic over a trace result and summarise the findings."""

from __future__ import annotations

from dataclasses import dataclass, field

from .heuristics import ChangeGuess, cluster_addresses, detect_change, detect_peel_chains, is_coinjoin
from .labels import LabelStore
from .models import sats_to_btc
from .risk import RiskAssessment, score_addresses
from .tracer import TraceResult


@dataclass
class Analysis:
    coinjoins: set[str] = field(default_factory=set)
    clusters: dict[str, list[str]] = field(default_factory=dict)
    address_cluster: dict[str, str] = field(default_factory=dict)
    change: dict[str, ChangeGuess] = field(default_factory=dict)
    peel_chains: list[list[str]] = field(default_factory=list)
    risk: dict[str, RiskAssessment] = field(default_factory=dict)
    findings: list[str] = field(default_factory=list)

    def top_risk(self, n: int = 15) -> list[dict]:
        ranked = sorted(self.risk.items(), key=lambda kv: kv[1].score, reverse=True)
        return [{"address": a, **r.to_dict()} for a, r in ranked[:n] if r.score > 0]


def analyze(result: TraceResult, labels: LabelStore) -> Analysis:
    txs = list(result.txs.values())
    a = Analysis()
    a.coinjoins = {t.txid for t in txs if is_coinjoin(t)}
    a.clusters = cluster_addresses(txs, a.coinjoins)
    a.address_cluster = {m: cid for cid, members in a.clusters.items() for m in members}
    for t in txs:
        guess = detect_change(t)
        if guess:
            a.change[t.txid] = guess
    a.peel_chains = detect_peel_chains(txs)
    peel_txs = {txid for chain in a.peel_chains for txid in chain}
    a.risk = score_addresses(txs, labels, a.clusters, a.coinjoins, peel_txs)
    a.findings = _findings(result, a, labels)
    return a


def _findings(result: TraceResult, a: Analysis, labels: LabelStore) -> list[str]:
    f: list[str] = []
    n_addr = len(a.risk)
    f.append(f"Traced {len(result.txs)} transactions involving {n_addr} addresses "
             f"({result.params.direction}, up to {result.params.max_hops} hops).")

    labeled = sorted({(addr, lb.name, lb.category) for addr in a.risk if (lb := labels.get(addr))},
                     key=lambda x: x[2])
    for addr, name, cat in labeled:
        f.append(f"Known entity reached: {name} [{cat}] at {addr}.")

    if a.clusters:
        biggest = a.clusters["C1"]
        f.append(f"{len(a.clusters)} multi-address clusters found via common-input ownership; "
                 f"the largest (C1) has {len(biggest)} addresses.")
    if a.coinjoins:
        f.append(f"{len(a.coinjoins)} CoinJoin transaction(s) detected; tracing through them is unreliable.")
    for chain in a.peel_chains[:3]:
        first = result.txs[chain[0]]
        f.append(f"Peel chain of {len(chain)} hops starting at {chain[0][:12]}... "
                 f"(initial value {sats_to_btc(first.total_out):.8f} BTC).")
    if a.change:
        f.append(f"Probable change output identified in {len(a.change)} transaction(s).")
    severe = [x for x in a.risk.values() if x.level in ("severe", "high")]
    if severe:
        f.append(f"{len(severe)} address(es) scored high or severe risk.")
    if result.stopped:
        f.append(f"{len(result.stopped)} transaction(s) were not expanded (CoinJoin or large batch).")
    return f

"""OFAC clustering study: measure common-input-ownership clustering against ground truth.

OFAC lists many Bitcoin addresses per sanctioned entity (e.g. 117 for Hydra Market).
That gives ground truth for clustering: if two sanctioned addresses are spent together,
the heuristic says they share an owner, and OFAC says whether they do.

For every sanctioned Bitcoin address the study fetches its history, clusters the
inputs of every transaction that spends from a sanctioned address, and reports:
  - precision: of merged sanctioned-address pairs, how many belong to the same entity
  - recall:    of same-entity pairs that both spent on chain, how many were merged
  - leads:     unlisted addresses placed in a cluster with a sanctioned address
  - behaviour: how often sanctioned addresses spend directly into a CoinJoin
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Callable

from .esplora import ChainClient, ChainDataError
from .heuristics import cluster_addresses, is_coinjoin
from .models import Transaction, sats_to_btc

_UID = re.compile(r"uid (\d+)")


@dataclass
class SanctionedSet:
    entities: dict[str, set[str]] = field(default_factory=dict)  # address -> SDN uids
    names: dict[str, str] = field(default_factory=dict)  # uid -> entity name

    @classmethod
    def from_file(cls, path: str | Path, chain: str = "XBT") -> "SanctionedSet":
        s = cls()
        for e in json.loads(Path(path).read_text(encoding="utf-8"))["entries"]:
            if e.get("chain") != chain:
                continue
            m = _UID.search(e.get("source", ""))
            uid = m.group(1) if m else e["name"]
            s.entities.setdefault(e["address"], set()).add(uid)
            s.names[uid] = e["name"]
        return s

    def same_entity(self, a: str, b: str) -> bool:
        return bool(self.entities[a] & self.entities[b])


def _pct(n: int, d: int) -> float | None:
    return round(100 * n / d, 1) if d else None


def run_study(
    client: ChainClient,
    sanctioned: SanctionedSet,
    max_txs: int = 100,
    service_cluster_size: int = 1000,
    progress: Callable[[int, int, str], None] | None = None,
) -> dict:
    stats: dict[str, dict] = {}
    txs: dict[str, Transaction] = {}
    truncated: list[str] = []
    errors: dict[str, str] = {}

    addresses = sorted(sanctioned.entities)
    for n, addr in enumerate(addresses, 1):
        if progress:
            progress(n, len(addresses), addr)
        try:
            info = client.get_address(addr)  # type: ignore[attr-defined]
            stats[addr] = info.get("chain_stats") or {}
            if stats[addr].get("tx_count", 0) == 0:
                continue
            history = client.get_address_txs(addr, max_txs=max_txs)
        except (ChainDataError, ValueError) as e:
            errors[addr] = str(e)
            continue
        if stats[addr]["tx_count"] > len(history):
            truncated.append(addr)
        for tx in history:
            txs[tx.txid] = tx

    listed = set(addresses)
    spends = [t for t in txs.values() if t.input_addresses & listed and not t.is_coinbase]
    coinjoins = {t.txid for t in spends if is_coinjoin(t)}
    clusters = cluster_addresses(spends, coinjoins)

    # Which sanctioned addresses actually spent (outside CoinJoins)? Only those can be clustered.
    spent = {a for t in spends if t.txid not in coinjoins for a in t.input_addresses & listed}
    cluster_of = {m: cid for cid, members in clusters.items() for m in members}

    # Pair-level precision: every pair of sanctioned addresses merged into one cluster.
    merged_pairs = same_pairs = 0
    mixed_clusters = []
    leads: set[str] = set()
    for cid, members in clusters.items():
        sanc = [m for m in members if m in listed]
        if not sanc:
            continue
        leads.update(m for m in members if m not in listed)
        for a, b in combinations(sanc, 2):
            merged_pairs += 1
            same_pairs += sanctioned.same_entity(a, b)
        uids = set().union(*(sanctioned.entities[a] for a in sanc))
        if len(sanc) > 1 and not any(all(u in sanctioned.entities[a] for a in sanc) for u in uids):
            mixed_clusters.append(sorted(sanctioned.names[u] for u in uids))

    # Pair-level recall: same-entity pairs where both addresses spent on chain.
    by_entity: dict[str, list[str]] = defaultdict(list)
    for a in spent:
        for u in sanctioned.entities[a]:
            by_entity[u].append(a)
    possible = recovered = 0
    for members in by_entity.values():
        for a, b in combinations(sorted(members), 2):
            possible += 1
            recovered += a in cluster_of and cluster_of.get(a) == cluster_of.get(b)

    sanc_clusters = [c for c in clusters.values() if set(c) & listed]
    # Clusters this large are custodial services (exchanges, OTC desks) pooling many users'
    # funds, not one owner; their members are reported separately, not as entity leads.
    service = [c for c in sanc_clusters if len(c) >= service_cluster_size]
    service_members = {m for c in service for m in c}
    entity_leads = leads - service_members
    active = [a for a in addresses if stats.get(a, {}).get("tx_count", 0) > 0]
    into_coinjoin = {a for t in spends if t.txid in coinjoins for a in t.input_addresses & listed}

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "data_source": getattr(client, "source", "unknown"),
        "params": {"max_txs_per_address": max_txs},
        "dataset": {
            "sanctioned_addresses": len(addresses),
            "sanctioned_entities": len(sanctioned.names),
            "active_addresses": len(active),
            "total_received_btc": round(sats_to_btc(sum(stats[a].get("funded_txo_sum", 0) for a in active)), 2),
            "transactions_analyzed": len(txs),
            "spending_transactions": len(spends),
            "histories_truncated": len(truncated),
            "fetch_errors": len(errors),
        },
        "clustering": {
            "addresses_that_spent": len(spent),
            "clusters_with_sanctioned_addresses": len(sanc_clusters),
            "largest_cluster": max((len(c) for c in sanc_clusters), default=0),
            "merged_sanctioned_pairs": merged_pairs,
            "same_entity_pairs": same_pairs,
            "pair_precision_pct": _pct(same_pairs, merged_pairs),
            "same_entity_pairs_possible": possible,
            "same_entity_pairs_recovered": recovered,
            "pair_recall_pct": _pct(recovered, possible),
            "mixed_entity_clusters": mixed_clusters,
        },
        "leads": {
            "unlisted_addresses_linked": len(leads),
            "unlisted_addresses_linked_excluding_services": len(entity_leads),
            "service_scale_clusters": len(service),
            "service_cluster_threshold": service_cluster_size,
            "entities_with_new_leads": len({
                u for cid, members in clusters.items() if set(members) - listed
                for m in members if m in listed for u in sanctioned.entities[m]
            }),
        },
        "behaviour": {
            "coinjoin_spends": len(coinjoins),
            "addresses_spending_into_coinjoin": len(into_coinjoin),
            "pct_of_spenders_using_coinjoin": _pct(len(into_coinjoin), len(spent | into_coinjoin)),
        },
        # Kept out of the public summary: lead addresses are unverified attributions.
        "_private": {"lead_addresses": sorted(leads), "errors": errors, "truncated": truncated},
    }

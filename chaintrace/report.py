"""Investigation reports with verifiable evidence integrity.

Each report embeds the raw upstream transaction data it was built from, a
SHA-256 hash of every transaction, and a hash over the whole evidence set.
`chaintrace verify report.json` recomputes them, so anyone can check that the
evidence was not altered after the report was generated.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from . import __version__
from .analysis import Analysis
from .labels import LabelStore
from .models import sats_to_btc
from .tracer import TraceResult

DISCLAIMER = (
    "Clustering, change detection and risk scores are probabilistic heuristics. "
    "They are investigative leads, not proof of ownership or identity, and must be "
    "corroborated (e.g. via exchange records obtained through legal process) before "
    "any attribution is made."
)


def canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def evidence_digest(tx_hashes: dict[str, str]) -> str:
    return sha256_hex("".join(f"{txid}:{h}\n" for txid, h in sorted(tx_hashes.items())))


def build_report(result: TraceResult, analysis: Analysis, labels: LabelStore, source: str,
                 case_name: str = "", analyst: str = "") -> dict:
    raw = {txid: tx.raw for txid, tx in result.txs.items()}
    tx_hashes = {txid: sha256_hex(canonical_json(r)) for txid, r in raw.items()}
    return {
        "report_type": "chaintrace.investigation",
        "tool": {"name": "ChainTrace", "version": __version__},
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "case": {"name": case_name, "analyst": analyst},
        "seed": {"value": result.seed, "type": result.seed_type},
        "parameters": result.params_dict(),
        "data_source": source,
        "summary": {
            "transactions": len(result.txs),
            "addresses": len(analysis.risk),
            "truncated": result.truncated,
            "findings": analysis.findings,
            "warnings": result.warnings,
        },
        "known_entities": [lb.to_dict() for a in sorted(analysis.risk) if (lb := labels.get(a))],
        "risk": analysis.top_risk(50),
        "clusters": analysis.clusters,
        "coinjoins": sorted(analysis.coinjoins),
        "peel_chains": analysis.peel_chains,
        "change_outputs": {txid: g.to_dict() for txid, g in analysis.change.items()},
        "not_expanded": result.stopped,
        "terminal_addresses": result.terminal_addresses,
        "transactions": [
            {"txid": txid, "hop": result.hops[txid], "block_height": tx.block_height,
             "block_time": tx.block_time, "btc_out": sats_to_btc(tx.total_out),
             "inputs": len(tx.inputs), "outputs": len(tx.outputs)}
            for txid, tx in sorted(result.txs.items(), key=lambda kv: (abs(result.hops[kv[0]]), kv[0]))
        ],
        "methodology": [
            "Breadth-first expansion from the seed via the Esplora API (outspends forward, prevouts backward).",
            "Expansion stops at labeled entities, CoinJoins, and transactions above the size threshold.",
            "Common-input-ownership clustering, excluding CoinJoins.",
            "Change detection: address reuse, script-type match, round-amount and optimal-change heuristics.",
            "Peel chain detection over 1-2 input / 2 output transactions.",
            "Rule-based risk: entity labels, cluster co-membership, directional flow exposure, CoinJoin and peel signals.",
        ],
        "disclaimer": DISCLAIMER,
        "evidence": {
            "hash_algorithm": "sha256 over canonical JSON (sorted keys, no whitespace)",
            "transaction_hashes": tx_hashes,
            "evidence_digest": evidence_digest(tx_hashes),
            "raw_transactions": raw,
        },
    }


def verify_report(report: dict) -> tuple[bool, list[str]]:
    """Recompute evidence hashes. Returns (ok, list of problems)."""
    problems: list[str] = []
    ev = report.get("evidence") or {}
    raw = ev.get("raw_transactions") or {}
    recorded = ev.get("transaction_hashes") or {}
    if set(raw) != set(recorded):
        problems.append("Set of raw transactions does not match the set of recorded hashes.")
    recomputed = {}
    for txid, r in raw.items():
        h = sha256_hex(canonical_json(r))
        recomputed[txid] = h
        if recorded.get(txid) != h:
            problems.append(f"Hash mismatch for transaction {txid}.")
        if r.get("txid") != txid:
            problems.append(f"Raw data under {txid} has a different txid field.")
    if evidence_digest(recorded) != ev.get("evidence_digest"):
        problems.append("Evidence digest does not match the recorded transaction hashes.")
    return (not problems, problems)

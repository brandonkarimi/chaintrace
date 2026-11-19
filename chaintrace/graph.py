"""Convert a trace + analysis into Cytoscape.js elements for the web UI.

The graph is bipartite (address -> tx -> address), the same convention
professional tracing tools use, because Bitcoin txs have many inputs and outputs.
"""

from __future__ import annotations

from .analysis import Analysis
from .labels import LabelStore
from .models import sats_to_btc
from .tracer import TraceResult


def to_cytoscape(result: TraceResult, analysis: Analysis, labels: LabelStore, display_limit: int = 10) -> list[dict]:
    nodes: dict[str, dict] = {}
    edges: dict[str, dict] = {}
    peel_txs = {t for chain in analysis.peel_chains for t in chain}

    def add_address(addr: str) -> None:
        if addr in nodes:
            return
        label = labels.get(addr)
        risk = analysis.risk.get(addr)
        nodes[addr] = {"data": {
            "id": addr,
            "kind": "address",
            "label": label.name if label else f"{addr[:6]}…{addr[-4:]}",
            "address": addr,
            "entity": label.to_dict() if label else None,
            "cluster": analysis.address_cluster.get(addr),
            "risk": risk.score if risk else 0,
            "risk_level": risk.level if risk else "low",
            "risk_reasons": risk.reasons if risk else [],
            "seed": addr == result.seed,
            "terminal": addr in result.terminal_addresses,
        }}

    def add_edge(eid: str, src: str, dst: str, value: int, **extra) -> None:
        if eid in edges:
            edges[eid]["data"]["value"] += value
            edges[eid]["data"]["btc"] = sats_to_btc(edges[eid]["data"]["value"])
        else:
            edges[eid] = {"data": {"id": eid, "source": src, "target": dst, "value": value,
                                   "btc": sats_to_btc(value), **extra}}

    for txid, tx in result.txs.items():
        change = analysis.change.get(txid)
        nodes[txid] = {"data": {
            "id": txid,
            "kind": "tx",
            "label": f"tx {txid[:8]}",
            "txid": txid,
            "hop": result.hops.get(txid, 0),
            "seed": txid == result.seed,
            "coinjoin": txid in analysis.coinjoins,
            "peel": txid in peel_txs,
            "stopped": result.stopped.get(txid),
            "n_in": len(tx.inputs),
            "n_out": len(tx.outputs),
            "btc_out": sats_to_btc(tx.total_out),
            "fee": tx.fee,
            "block_height": tx.block_height,
            "block_time": tx.block_time,
            "change": change.to_dict() if change else None,
        }}

        ins = sorted((i for i in tx.inputs if i.address), key=lambda i: i.value, reverse=True)
        shown_in = [i for i in ins if i.address == result.seed] + [i for i in ins if i.address != result.seed]
        shown_in, hidden_in = shown_in[:display_limit], shown_in[display_limit:]
        for i in shown_in:
            add_address(i.address)  # type: ignore[arg-type]
            add_edge(f"{i.address}>{txid}", i.address, txid, i.value, kind="input")  # type: ignore[arg-type]
        if hidden_in:
            _overflow(nodes, edges, txid, "in", hidden_in)

        outs = sorted((o for o in tx.outputs if o.address), key=lambda o: o.value, reverse=True)
        outs = [o for o in outs if o.address == result.seed] + [o for o in outs if o.address != result.seed]
        shown_out, hidden_out = outs[:display_limit], outs[display_limit:]
        for o in shown_out:
            add_address(o.address)  # type: ignore[arg-type]
            add_edge(f"{txid}>{o.address}", txid, o.address, o.value,  # type: ignore[arg-type]
                     kind="output", change=bool(change and change.index == o.index))
        if hidden_out:
            _overflow(nodes, edges, txid, "out", hidden_out)

    return list(nodes.values()) + list(edges.values())


def _overflow(nodes: dict, edges: dict, txid: str, side: str, hidden: list) -> None:
    nid = f"{txid}:more_{side}"
    total = sum(x.value for x in hidden)
    nodes[nid] = {"data": {"id": nid, "kind": "overflow",
                           "label": f"+{len(hidden)} more {'inputs' if side == 'in' else 'outputs'}",
                           "btc": sats_to_btc(total)}}
    src, dst = (nid, txid) if side == "in" else (txid, nid)
    edges[f"{src}>{dst}"] = {"data": {"id": f"{src}>{dst}", "source": src, "target": dst,
                                      "value": total, "btc": sats_to_btc(total), "kind": "overflow"}}

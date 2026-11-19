"""Command-line interface.

    chaintrace trace <txid|address> [--direction forward|backward|both] [--hops 3] [--out report.json]
    chaintrace serve [--host 127.0.0.1] [--port 8000]
    chaintrace update-sanctions
    chaintrace verify report.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .labels import DEFAULT_DATA_DIR


def _cmd_trace(args) -> int:
    from .analysis import analyze
    from .esplora import ChainDataError, EsploraClient
    from .labels import LabelStore
    from .models import SATS_PER_BTC
    from .report import build_report
    from .tracer import TraceParams, Tracer

    client = EsploraClient(base_url=args.api)
    labels = LabelStore.from_dir()
    params = TraceParams(direction=args.direction, max_hops=args.hops, max_txs=args.max_txs,
                         min_value=int(round(args.min_btc * SATS_PER_BTC)))
    try:
        result = Tracer(client, labels).trace(args.seed, params)
    except (ValueError, ChainDataError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    analysis = analyze(result, labels)

    print(f"\nChainTrace: {result.seed_type} {result.seed}\n")
    for line in analysis.findings:
        print(f"  * {line}")
    for w in result.warnings[:5]:
        print(f"  ! {w}")
    top = analysis.top_risk(10)
    if top:
        print("\n  Highest-risk addresses:")
        for r in top:
            print(f"    {r['score']:>3} {r['level']:<7} {r['address']}")
            for reason in r["reasons"][:2]:
                print(f"            - {reason}")
    if args.out:
        rep = build_report(result, analysis, labels, client.source, args.case or "", args.analyst or "")
        Path(args.out).write_text(json.dumps(rep, indent=2), encoding="utf-8")
        print(f"\n  Report written to {args.out} (evidence digest {rep['evidence']['evidence_digest'][:16]}...)")
    print()
    return 0


def _cmd_serve(args) -> int:
    import uvicorn

    from .api import create_app

    uvicorn.run(create_app(), host=args.host, port=args.port)
    return 0


def _cmd_update_sanctions(args) -> int:
    from .sanctions import update_sanctions

    out = Path(args.out) if args.out else DEFAULT_DATA_DIR / "labels" / "ofac_sdn.json"
    stats = update_sanctions(out)
    print(f"Saved {stats['total']} sanctioned addresses to {out}")
    for chain, n in stats["by_chain"].items():
        print(f"  {chain:<6} {n}")
    return 0


def _cmd_verify(args) -> int:
    from .report import verify_report

    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    ok, problems = verify_report(report)
    n = len((report.get("evidence") or {}).get("raw_transactions") or {})
    if ok:
        print(f"OK: all {n} transaction hashes and the evidence digest verify.")
        return 0
    print("FAILED: evidence integrity check did not pass:")
    for p in problems:
        print(f"  - {p}")
    return 2


def _cmd_study(args) -> int:
    from .esplora import EsploraClient
    from .study import SanctionedSet, run_study

    sanctioned = SanctionedSet.from_file(DEFAULT_DATA_DIR / "labels" / "ofac_sdn.json")
    client = EsploraClient(base_url=args.api)

    def progress(n: int, total: int, addr: str) -> None:
        print(f"\r  [{n}/{total}] {addr:<62}", end="", file=sys.stderr, flush=True)

    result = run_study(client, sanctioned, max_txs=args.max_txs, progress=progress)
    print(file=sys.stderr)
    private = result.pop("_private")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    private_path = out.with_name(out.stem + ".private.json")
    private_path.write_text(json.dumps(private, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k in ("dataset", "clustering", "leads", "behaviour")}, indent=2))
    print(f"\nSummary written to {out}; lead addresses written to {private_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="chaintrace", description="Bitcoin fund-flow tracing and forensics")
    sub = p.add_subparsers(dest="command", required=True)

    t = sub.add_parser("trace", help="trace funds from a txid or address")
    t.add_argument("seed")
    t.add_argument("--direction", choices=["forward", "backward", "both"], default="forward")
    t.add_argument("--hops", type=int, default=3)
    t.add_argument("--max-txs", type=int, default=150)
    t.add_argument("--min-btc", type=float, default=0.0, help="ignore flows smaller than this")
    t.add_argument("--out", help="write a JSON investigation report")
    t.add_argument("--case", help="case name for the report")
    t.add_argument("--analyst", help="analyst name for the report")
    t.add_argument("--api", help="Esplora base URL (default https://blockstream.info/api)")
    t.set_defaults(func=_cmd_trace)

    s = sub.add_parser("serve", help="run the web application")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.set_defaults(func=_cmd_serve)

    u = sub.add_parser("update-sanctions", help="download OFAC SDN crypto addresses")
    u.add_argument("--out")
    u.set_defaults(func=_cmd_update_sanctions)

    v = sub.add_parser("verify", help="verify a report's evidence hashes")
    v.add_argument("report")
    v.set_defaults(func=_cmd_verify)

    st = sub.add_parser("study", help="measure clustering accuracy against OFAC-sanctioned addresses")
    st.add_argument("--max-txs", type=int, default=100, help="history fetched per sanctioned address")
    st.add_argument("--out", default="results/ofac_study.json")
    st.add_argument("--api", help="Esplora base URL (default https://blockstream.info/api)")
    st.set_defaults(func=_cmd_study)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

# ChainTrace

**Open-source Bitcoin fund-flow tracing and forensic analysis.** Paste a transaction or address. ChainTrace builds a multi-hop money-flow graph, clusters addresses that belong to the same owner, detects change outputs, peel chains and CoinJoins, and scores every address for risk, including exposure to **OFAC-sanctioned wallets**. It then exports an investigation report whose evidence can be checked cryptographically.

![ChainTrace tracing funds forward from an OFAC-sanctioned address](docs/screenshot.png)

*Above: a forward trace from an address on the U.S. Treasury OFAC SDN list. Risk spreads to cluster members and downstream recipients, and a 5-hop peel chain is detected.*

## Why this exists

Investigators at agencies like the FBI, IRS-CI, the Secret Service and Treasury trace ransomware payments, sanctions evasion and fraud proceeds on public blockchains. Commercial tools (Chainalysis Reactor, TRM Labs) are closed and expensive. ChainTrace implements the core methods from published research in the open. Every conclusion comes with the reasons behind it, so an analyst can check it instead of trusting a black box.

## Features

| Capability | How |
|---|---|
| **Multi-hop tracing** | Breadth-first expansion forward (outputs to spending txs) and/or backward (inputs to funding txs), bounded by hops, a tx budget and a per-tx branch limit |
| **Smart stopping** | Stops at labeled entities (e.g. an exchange, where legal process would be sent), CoinJoins, and large batch transactions |
| **Address clustering** | Common-input-ownership heuristic with union-find, **excluding CoinJoins** (which would otherwise merge strangers) |
| **Change detection** | Address reuse, script-type matching, round-payment and optimal-change heuristics, each with a confidence and stated reasons |
| **Peel chain detection** | Finds chains of 1–2 input / 2 output txs where a large balance is repeatedly peeled, a common laundering and cash-out pattern |
| **CoinJoin detection** | Equal-output CoinJoins (Whirlpool, Wasabi, JoinMarket-style) |
| **Explainable risk scoring** | 0–100 per address from entity labels, cluster co-membership, *directional* flow exposure (counted once per entity), CoinJoin and peel signals |
| **Sanctions screening** | `chaintrace update-sanctions` imports every crypto address from the OFAC SDN list (1,000+ across BTC, ETH, TRX, USDT and more) |
| **Evidence integrity** | Reports embed the raw chain data plus per-tx SHA-256 hashes and an overall evidence digest; `chaintrace verify` detects any tampering |
| **Web UI + CLI + REST API** | Interactive Cytoscape.js graph, a scriptable CLI, and a FastAPI backend with OpenAPI docs at `/docs` |

## Quick start

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install --upgrade pip
pip install -e ".[dev]"

chaintrace update-sanctions          # download OFAC-sanctioned addresses
chaintrace serve                     # open http://127.0.0.1:8000
```

Or with Docker (not yet tested in this repo; the Docker daemon wasn't running when it was written):

```bash
docker compose up --build            # http://localhost:8000
```

## CLI

```bash
# Where did the money go? Write a court-style report
chaintrace trace <txid-or-address> --direction forward --hops 3 --out report.json --case "Case 001"

# Where did it come from?
chaintrace trace <address> --direction backward --hops 2

# Check that a report's evidence has not been altered
chaintrace verify report.json
```

## Architecture

```
web/ (Cytoscape.js + dagre graph UI)
        │  REST: POST /api/trace, POST /api/report
chaintrace/api.py (FastAPI)
 ├─ tracer.py      multi-hop BFS expansion with stop rules
 ├─ heuristics.py  CoinJoin, clustering, change, peel chains
 ├─ risk.py        explainable risk scoring
 ├─ analysis.py    runs the heuristics → findings
 ├─ graph.py       bipartite address↔tx graph for the UI
 ├─ report.py      evidence-hashed investigation reports
 ├─ sanctions.py   OFAC SDN importer
 └─ esplora.py     Blockstream/mempool.space API client + SQLite cache
                   (confirmed txs cached forever; spend status / history cached briefly)
```

Point `CHAINTRACE_ESPLORA_URL` at a self-hosted Esplora or mempool.space instance to avoid public rate limits. On a real case, it also avoids revealing your investigation targets to a third-party API.

## Testing

```bash
pytest
```

The tests run against a synthetic blockchain (`tests/conftest.py`), so they are offline and deterministic. They cover each heuristic, tracing limits and stop rules, risk propagation, report tamper detection, input validation and the API.

## Limitations (read before relying on results)

- Heuristics are **probabilistic**. Common-input ownership breaks with PayJoin and undetected CoinJoins; change detection is a best guess. Results are **leads, not proof**, and need corroboration (e.g. exchange KYC records obtained through legal process).
- Labels are only as good as their sources. Out of the box, ChainTrace knows only OFAC SDN addresses plus anything you add to `data/labels/custom.json`.
- Bitcoin only for now.

## Ethics

ChainTrace uses public blockchain data and public government sanctions lists only. It is meant for investigation, compliance, research and education. Do not use it to dox or harass private individuals. Attribution claims about real people require evidence beyond on-chain heuristics.

## Roadmap

- [ ] **GNN risk model**: GraphSAGE/GCN trained on the Elliptic and Elliptic2 labeled datasets, with GNNExplainer, compared against the rule-based baseline
- [ ] Ethereum and Tron (USDT) support, plus cross-chain bridge following
- [ ] Neo4j persistence for saved cases and analyst notes
- [ ] PDF report export
- [ ] AI case assistant: an LLM agent that drives the tracer and drafts reports in which every claim cites on-chain evidence

## Adding your own labels

Add entries to `data/labels/custom.json` (or any `*.json` in that folder):

```json
{"entries": [
  {"address": "bc1...", "name": "Example Exchange hot wallet", "category": "exchange", "source": "how you know this"}
]}
```

Categories: `sanctioned`, `terrorism`, `ransomware`, `darknet`, `theft`, `scam`, `mixer`, `gambling`, `exchange`, `service`, `other`.

## License

MIT

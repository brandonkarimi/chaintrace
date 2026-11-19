"""FastAPI web service. Run with: chaintrace serve   (or uvicorn --factory chaintrace.api:create_app)"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .analysis import analyze
from .esplora import ChainClient, ChainDataError, EsploraClient, NotFoundError
from .graph import to_cytoscape
from .labels import LabelStore
from .models import SATS_PER_BTC
from .report import build_report
from .tracer import TraceParams, Tracer

WEB_DIR = Path(__file__).resolve().parents[1] / "web"


class TraceRequest(BaseModel):
    seed: str = Field(..., min_length=26, max_length=100, description="txid or Bitcoin address")
    direction: Literal["forward", "backward", "both"] = "forward"
    max_hops: int = Field(3, ge=1, le=6)
    max_txs: int = Field(150, ge=1, le=500)
    min_value_btc: float = Field(0.0, ge=0, le=21_000_000)


class ReportRequest(TraceRequest):
    case_name: str = Field("", max_length=200)
    analyst: str = Field("", max_length=200)


def create_app(client: ChainClient | None = None, labels: LabelStore | None = None) -> FastAPI:
    client = client or EsploraClient()
    labels = labels if labels is not None else LabelStore.from_dir()
    tracer = Tracer(client, labels)
    app = FastAPI(title="ChainTrace", version=__version__,
                  description="Bitcoin fund-flow tracing and forensic analysis")

    def run(req: TraceRequest):
        params = TraceParams(direction=req.direction, max_hops=req.max_hops, max_txs=req.max_txs,
                             min_value=int(round(req.min_value_btc * SATS_PER_BTC)))
        try:
            result = tracer.trace(req.seed, params)
        except ValueError as e:
            raise HTTPException(400, str(e))
        except NotFoundError as e:
            raise HTTPException(404, f"Not found on chain: {e}")
        except ChainDataError as e:
            raise HTTPException(502, f"Blockchain data source error: {e}")
        return result, analyze(result, labels)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": __version__, "data_source": client.source, "labels": len(labels)}

    @app.post("/api/trace")
    def trace(req: TraceRequest):
        result, analysis = run(req)
        return {
            "seed": result.seed,
            "seed_type": result.seed_type,
            "elements": to_cytoscape(result, analysis, labels),
            "findings": analysis.findings,
            "top_risk": analysis.top_risk(),
            "clusters": analysis.clusters,
            "peel_chains": analysis.peel_chains,
            "stats": {"transactions": len(result.txs), "addresses": len(analysis.risk),
                      "clusters": len(analysis.clusters), "coinjoins": len(analysis.coinjoins)},
            "truncated": result.truncated,
            "warnings": result.warnings,
        }

    @app.post("/api/report")
    def report(req: ReportRequest):
        result, analysis = run(req)
        rep = build_report(result, analysis, labels, client.source, req.case_name, req.analyst)
        safe = re.sub(r"[^A-Za-z0-9]", "", result.seed)[:16]
        return JSONResponse(rep, headers={"Content-Disposition": f'attachment; filename="chaintrace-{safe}.json"'})

    if WEB_DIR.is_dir():
        app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app

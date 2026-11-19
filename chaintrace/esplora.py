"""Esplora API client (Blockstream / mempool.space) with an on-disk SQLite cache.

Confirmed transactions are immutable, so they are cached forever. Data that can
change (spend status, address history, unconfirmed txs) gets a short TTL.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable, Protocol

import httpx

from . import __version__
from .models import Transaction
from .validate import is_btc_address, is_txid

DEFAULT_ESPLORA_URL = "https://blockstream.info/api"
DEFAULT_CACHE_PATH = Path.home() / ".chaintrace" / "cache.sqlite"


class ChainDataError(Exception):
    """Upstream data could not be retrieved."""


class NotFoundError(ChainDataError):
    """The transaction or address does not exist upstream."""


class ChainClient(Protocol):
    """What the tracer needs from a data source. Lets tests use a fake client."""

    source: str

    def get_tx(self, txid: str) -> Transaction: ...
    def get_outspends(self, txid: str) -> list[dict]: ...
    def get_address_txs(self, address: str, max_txs: int = 50) -> list[Transaction]: ...


class Cache:
    def __init__(self, path: str | Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS cache ("
                " key TEXT PRIMARY KEY, value TEXT NOT NULL,"
                " fetched_at REAL NOT NULL, ttl REAL)"
            )
            self._conn.commit()

    def get(self, key: str) -> Any | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT value, fetched_at, ttl FROM cache WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            return None
        value, fetched_at, ttl = row
        if ttl is not None and time.time() - fetched_at > ttl:
            return None
        return json.loads(value)

    def put(self, key: str, value: Any, ttl: float | None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO cache (key, value, fetched_at, ttl) VALUES (?, ?, ?, ?)",
                (key, json.dumps(value), time.time(), ttl),
            )
            self._conn.commit()


def _tx_ttl(data: dict) -> float | None:
    return None if (data.get("status") or {}).get("confirmed") else 60.0


class EsploraClient:
    def __init__(
        self,
        base_url: str | None = None,
        cache_path: str | Path | None = None,
        timeout: float = 20.0,
        min_interval: float = 0.25,
        max_retries: int = 7,
    ):
        self.base_url = (base_url or os.environ.get("CHAINTRACE_ESPLORA_URL") or DEFAULT_ESPLORA_URL).rstrip("/")
        self.source = self.base_url
        self.cache = Cache(cache_path or os.environ.get("CHAINTRACE_CACHE") or DEFAULT_CACHE_PATH)
        self._http = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            headers={"User-Agent": f"chaintrace/{__version__}"},
        )
        self._min_interval = min_interval
        self._max_retries = max_retries
        self._rate_lock = threading.Lock()
        self._last_request = 0.0

    # -- low level -----------------------------------------------------------------
    def _throttle(self) -> None:
        with self._rate_lock:
            wait = self._min_interval - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()

    def _fetch(self, path: str) -> Any:
        last_error: Exception | None = None
        for attempt in range(self._max_retries):
            self._throttle()
            try:
                resp = self._http.get(path)
            except httpx.HTTPError as e:
                last_error = e
                time.sleep(0.5 * 2**attempt)
                continue
            if resp.status_code in (400, 404):
                raise NotFoundError(f"{path}: {resp.text.strip()[:200]}")
            if resp.status_code == 429:
                # Rate limited: wait as long as the server asks, or back off up to a minute.
                last_error = ChainDataError(f"{path}: HTTP 429")
                retry_after = resp.headers.get("Retry-After", "")
                time.sleep(float(retry_after) if retry_after.isdigit() else min(60.0, 2.0 * 2**attempt))
                continue
            if resp.status_code >= 500:
                last_error = ChainDataError(f"{path}: HTTP {resp.status_code}")
                time.sleep(0.5 * 2**attempt)
                continue
            resp.raise_for_status()
            return resp.json()
        raise ChainDataError(f"Upstream request failed after retries: {last_error}")

    def _get(self, path: str, ttl: float | None | Callable[[Any], float | None]) -> Any:
        cached = self.cache.get(path)
        if cached is not None:
            return cached
        data = self._fetch(path)
        self.cache.put(path, data, ttl(data) if callable(ttl) else ttl)
        return data

    # -- public API ----------------------------------------------------------------
    def get_tx(self, txid: str) -> Transaction:
        if not is_txid(txid):
            raise ValueError(f"Invalid txid: {txid!r}")
        return Transaction.from_esplora(self._get(f"/tx/{txid.lower()}", _tx_ttl))

    def get_outspends(self, txid: str) -> list[dict]:
        if not is_txid(txid):
            raise ValueError(f"Invalid txid: {txid!r}")
        return self._get(f"/tx/{txid.lower()}/outspends", 600.0)

    def get_address(self, address: str) -> dict:
        if not is_btc_address(address):
            raise ValueError(f"Invalid address: {address!r}")
        return self._get(f"/address/{address}", 600.0)

    def get_address_txs(self, address: str, max_txs: int = 50) -> list[Transaction]:
        """Most recent transactions for an address (mempool first, then confirmed, newest first)."""
        if not is_btc_address(address):
            raise ValueError(f"Invalid address: {address!r}")
        page = self._get(f"/address/{address}/txs", 300.0)
        raw: list[dict] = list(page)
        confirmed = [t for t in page if (t.get("status") or {}).get("confirmed")]
        # Esplora returns 25 confirmed txs per page; page through /txs/chain/:last_seen.
        while len(raw) < max_txs and len(confirmed) == 25:
            last = confirmed[-1]["txid"]
            page = self._get(f"/address/{address}/txs/chain/{last}", 300.0)
            confirmed = list(page)
            raw.extend(page)
        txs = []
        for d in raw[:max_txs]:
            self.cache.put(f"/tx/{d['txid']}", d, _tx_ttl(d))  # avoid refetching later
            txs.append(Transaction.from_esplora(d))
        return txs

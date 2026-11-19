"""Multi-hop fund-flow tracing.

Starting from a seed transaction or address, the tracer expands breadth-first:
  - forward:  follow outputs to the transactions that spend them ("where did it go?")
  - backward: follow inputs to the transactions that funded them ("where did it come from?")

Expansion is bounded by hop count, a transaction budget, and a per-tx branch
limit. It stops at labeled entities (e.g. an exchange: the point to send legal
process), large batch transactions, and CoinJoins, where tracing becomes unreliable.
"""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field

from .esplora import ChainClient, ChainDataError, NotFoundError
from .heuristics import is_coinjoin
from .labels import LabelStore
from .models import Transaction
from .validate import classify_seed, normalize_seed

DIRECTIONS = ("forward", "backward", "both")


@dataclass
class TraceParams:
    direction: str = "forward"
    max_hops: int = 3
    max_txs: int = 150
    max_branch: int = 12  # max outputs/inputs followed from any single tx
    min_value: int = 0  # satoshis; smaller outputs/inputs are not followed
    stop_at_labeled: bool = True
    large_tx_threshold: int = 50  # txs with more inputs or outputs are not expanded
    max_address_txs: int = 50  # history fetched for an address seed

    def validate(self) -> None:
        if self.direction not in DIRECTIONS:
            raise ValueError(f"direction must be one of {DIRECTIONS}")
        if not 0 <= self.max_hops <= 10:
            raise ValueError("max_hops must be between 0 and 10")
        if not 1 <= self.max_txs <= 2000:
            raise ValueError("max_txs must be between 1 and 2000")
        if self.max_branch < 1 or self.min_value < 0:
            raise ValueError("max_branch must be >= 1 and min_value >= 0")


@dataclass
class TraceResult:
    seed: str
    seed_type: str  # "tx" | "address"
    params: TraceParams
    txs: dict[str, Transaction] = field(default_factory=dict)
    hops: dict[str, int] = field(default_factory=dict)  # +n forward, -n backward, 0 seed
    stopped: dict[str, str] = field(default_factory=dict)  # txid -> why it was not expanded
    terminal_addresses: dict[str, str] = field(default_factory=dict)  # labeled addr -> label name
    branch_limited: set[str] = field(default_factory=set)  # txs where smaller branches were skipped
    truncated: bool = False
    warnings: list[str] = field(default_factory=list)

    def add(self, tx: Transaction, hop: int, direction: str) -> None:
        self.txs[tx.txid] = tx
        self.hops[tx.txid] = -hop if direction == "backward" else hop

    def params_dict(self) -> dict:
        return asdict(self.params)


class Tracer:
    def __init__(self, client: ChainClient, labels: LabelStore | None = None):
        self.client = client
        self.labels = labels or LabelStore()

    def trace(self, seed: str, params: TraceParams | None = None) -> TraceResult:
        params = params or TraceParams()
        params.validate()
        seed_type = classify_seed(seed)
        seed = normalize_seed(seed)
        result = TraceResult(seed=seed, seed_type=seed_type, params=params)
        queue: deque[tuple[str, int, str]] = deque()
        fwd = params.direction in ("forward", "both")
        back = params.direction in ("backward", "both")

        if seed_type == "tx":
            tx = self.client.get_tx(seed)  # NotFoundError propagates to the caller
            result.add(tx, 0, "seed")
            if fwd:
                queue.append((tx.txid, 0, "forward"))
            if back:
                queue.append((tx.txid, 0, "backward"))
        else:
            history = self.client.get_address_txs(seed, max_txs=params.max_address_txs)
            if not history:
                raise NotFoundError(f"No transactions found for address {seed}")
            if len(history) >= params.max_address_txs:
                result.warnings.append(
                    f"Address has at least {len(history)} transactions; only the most recent "
                    f"{params.max_address_txs} were used as starting points."
                )
            for tx in history:
                if len(result.txs) >= params.max_txs:
                    result.truncated = True
                    break
                result.add(tx, 0, "seed")
                if fwd and seed in tx.input_addresses:
                    queue.append((tx.txid, 0, "forward"))
                if back and seed in tx.output_addresses:
                    queue.append((tx.txid, 0, "backward"))

        while queue and not result.truncated:
            txid, hop, direction = queue.popleft()
            tx = result.txs[txid]
            if hop >= params.max_hops:
                continue
            if hop > 0:
                reason = self._stop_reason(tx, params)
                if reason:
                    result.stopped[txid] = reason
                    continue
            neighbors = (
                self._forward_neighbors(tx, params, result)
                if direction == "forward"
                else self._backward_neighbors(tx, params, result)
            )
            for ntxid in neighbors:
                if ntxid in result.txs:
                    continue
                if len(result.txs) >= params.max_txs:
                    result.truncated = True
                    break
                try:
                    ntx = self.client.get_tx(ntxid)
                except ChainDataError as e:
                    result.warnings.append(f"Could not fetch {ntxid}: {e}")
                    continue
                result.add(ntx, hop + 1, direction)
                queue.append((ntxid, hop + 1, direction))

        if result.branch_limited:
            result.warnings.append(
                f"{len(result.branch_limited)} transaction(s) had more than {params.max_branch} branches; "
                "only the largest values were followed."
            )
        if result.truncated:
            result.warnings.append(
                f"Transaction budget of {params.max_txs} reached; the graph is incomplete. "
                "Raise max_txs or lower max_hops to see more."
            )
        return result

    # ------------------------------------------------------------------------------
    @staticmethod
    def _stop_reason(tx: Transaction, params: TraceParams) -> str | None:
        if is_coinjoin(tx):
            return "CoinJoin: input-output links are deliberately obscured"
        if len(tx.inputs) > params.large_tx_threshold or len(tx.outputs) > params.large_tx_threshold:
            return (
                f"Large transaction ({len(tx.inputs)} in / {len(tx.outputs)} out): "
                "likely a service batch payout or consolidation"
            )
        return None

    def _labeled_stop(self, address: str | None, params: TraceParams, result: TraceResult) -> bool:
        if not params.stop_at_labeled:
            return False
        label = self.labels.get(address)
        if label is None:
            return False
        result.terminal_addresses[address] = f"{label.name} ({label.category})"  # type: ignore[index]
        return True

    def _forward_neighbors(self, tx: Transaction, params: TraceParams, result: TraceResult) -> list[str]:
        try:
            outspends = self.client.get_outspends(tx.txid)
        except ChainDataError as e:
            result.warnings.append(f"Could not fetch spend status for {tx.txid}: {e}")
            return []
        found: list[str] = []
        for o in sorted(tx.outputs, key=lambda o: o.value, reverse=True):
            if len(found) >= params.max_branch:
                result.branch_limited.add(tx.txid)
                break
            if o.value < params.min_value or o.index >= len(outspends):
                continue
            spend = outspends[o.index] or {}
            if not spend.get("spent") or not spend.get("txid"):
                continue
            if self._labeled_stop(o.address, params, result):
                continue
            if spend["txid"] not in found:
                found.append(spend["txid"])
        return found

    def _backward_neighbors(self, tx: Transaction, params: TraceParams, result: TraceResult) -> list[str]:
        found: list[str] = []
        for i in sorted(tx.inputs, key=lambda i: i.value, reverse=True):
            if len(found) >= params.max_branch:
                result.branch_limited.add(tx.txid)
                break
            if i.is_coinbase or i.prev_txid is None or i.value < params.min_value:
                continue
            if self._labeled_stop(i.address, params, result):
                continue
            if i.prev_txid not in found:
                found.append(i.prev_txid)
        return found

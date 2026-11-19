"""Chain-agnostic-ish data model for Bitcoin transactions (UTXO model)."""

from __future__ import annotations

from dataclasses import dataclass, field

SATS_PER_BTC = 100_000_000


def sats_to_btc(sats: int) -> float:
    return sats / SATS_PER_BTC


@dataclass(frozen=True)
class TxInput:
    prev_txid: str | None
    prev_vout: int | None
    address: str | None
    value: int  # satoshis
    script_type: str | None
    is_coinbase: bool = False


@dataclass(frozen=True)
class TxOutput:
    index: int
    address: str | None
    value: int  # satoshis
    script_type: str | None


@dataclass
class Transaction:
    txid: str
    inputs: list[TxInput]
    outputs: list[TxOutput]
    fee: int = 0
    block_height: int | None = None
    block_time: int | None = None
    raw: dict = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_esplora(cls, d: dict) -> "Transaction":
        """Build from an Esplora (Blockstream / mempool.space) /tx response."""
        inputs: list[TxInput] = []
        for vin in d.get("vin", []):
            coinbase = bool(vin.get("is_coinbase"))
            prev = vin.get("prevout") or {}
            inputs.append(
                TxInput(
                    prev_txid=None if coinbase else vin.get("txid"),
                    prev_vout=None if coinbase else vin.get("vout"),
                    address=prev.get("scriptpubkey_address"),
                    value=int(prev.get("value") or 0),
                    script_type=prev.get("scriptpubkey_type"),
                    is_coinbase=coinbase,
                )
            )
        outputs = [
            TxOutput(
                index=i,
                address=o.get("scriptpubkey_address"),
                value=int(o.get("value") or 0),
                script_type=o.get("scriptpubkey_type"),
            )
            for i, o in enumerate(d.get("vout", []))
        ]
        status = d.get("status") or {}
        return cls(
            txid=d["txid"],
            inputs=inputs,
            outputs=outputs,
            fee=int(d.get("fee") or 0),
            block_height=status.get("block_height"),
            block_time=status.get("block_time"),
            raw=d,
        )

    @property
    def is_coinbase(self) -> bool:
        return any(i.is_coinbase for i in self.inputs)

    @property
    def input_addresses(self) -> set[str]:
        return {i.address for i in self.inputs if i.address}

    @property
    def output_addresses(self) -> set[str]:
        return {o.address for o in self.outputs if o.address}

    @property
    def total_in(self) -> int:
        return sum(i.value for i in self.inputs)

    @property
    def total_out(self) -> int:
        return sum(o.value for o in self.outputs)

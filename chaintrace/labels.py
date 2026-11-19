"""Known-entity labels (sanctioned addresses, exchanges, mixers, ...).

Labels are loaded from JSON files in data/labels/. Each file is either a list of
entries or {"entries": [...]}, where an entry looks like:

    {"address": "bc1...", "name": "Example Exchange", "category": "exchange",
     "source": "where this attribution came from"}
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[1] / "data"

# Severity of each category, used both for ranking labels and as a base risk score.
CATEGORY_SEVERITY: dict[str, int] = {
    "sanctioned": 100,
    "terrorism": 100,
    "ransomware": 90,
    "darknet": 85,
    "theft": 85,
    "scam": 85,
    "mixer": 70,
    "gambling": 35,
    "exchange": 5,
    "service": 5,
    "other": 10,
}
ILLICIT_THRESHOLD = 70  # categories at or above this severity taint their neighbours


@dataclass(frozen=True)
class Label:
    address: str
    name: str
    category: str
    source: str = ""

    @property
    def severity(self) -> int:
        return CATEGORY_SEVERITY.get(self.category, CATEGORY_SEVERITY["other"])

    @property
    def illicit(self) -> bool:
        return self.severity >= ILLICIT_THRESHOLD

    def to_dict(self) -> dict:
        return {"address": self.address, "name": self.name, "category": self.category, "source": self.source}


def _key(address: str) -> str:
    # Hex (EVM) and bech32 addresses are case-insensitive; base58 is case-sensitive.
    a = address.strip()
    return a.lower() if a.lower().startswith(("0x", "bc1")) else a


class LabelStore:
    def __init__(self, labels: list[Label] | None = None):
        self._labels: dict[str, Label] = {}
        for label in labels or []:
            self.add(label)

    def add(self, label: Label) -> None:
        k = _key(label.address)
        existing = self._labels.get(k)
        if existing is None or label.severity > existing.severity:
            self._labels[k] = label

    def get(self, address: str | None) -> Label | None:
        if not address:
            return None
        return self._labels.get(_key(address))

    def __len__(self) -> int:
        return len(self._labels)

    @classmethod
    def from_dir(cls, data_dir: str | Path | None = None) -> "LabelStore":
        data_dir = Path(data_dir or os.environ.get("CHAINTRACE_DATA") or DEFAULT_DATA_DIR)
        store = cls()
        label_dir = data_dir / "labels"
        if not label_dir.is_dir():
            return store
        for path in sorted(label_dir.glob("*.json")):
            doc = json.loads(path.read_text(encoding="utf-8"))
            entries = doc.get("entries", []) if isinstance(doc, dict) else doc
            for e in entries:
                if not e.get("address"):
                    continue
                store.add(
                    Label(
                        address=e["address"],
                        name=e.get("name", "unknown"),
                        category=e.get("category", "other"),
                        source=e.get("source", path.name),
                    )
                )
        return store

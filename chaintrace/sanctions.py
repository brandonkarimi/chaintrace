"""Import cryptocurrency addresses from the U.S. Treasury OFAC SDN list.

OFAC publishes sanctioned digital currency addresses in the SDN XML file as
<id> entries with idType "Digital Currency Address - <TICKER>".
"""

from __future__ import annotations

import io
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import httpx

from . import __version__

SDN_URLS = [
    "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.XML",
    "https://www.treasury.gov/ofac/downloads/sdn.xml",
]
PREFIX = "Digital Currency Address - "


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child_text(elem: ET.Element, name: str) -> str:
    for c in elem:
        if _local(c.tag) == name:
            return (c.text or "").strip()
    return ""


def parse_sdn_xml(data: bytes) -> list[dict]:
    entries: list[dict] = []
    for _, elem in ET.iterparse(io.BytesIO(data), events=("end",)):
        if _local(elem.tag) != "sdnEntry":
            continue
        name = " ".join(p for p in (_child_text(elem, "firstName"), _child_text(elem, "lastName")) if p)
        uid = _child_text(elem, "uid")
        for idel in elem.iter():
            if _local(idel.tag) != "id":
                continue
            id_type = _child_text(idel, "idType")
            number = _child_text(idel, "idNumber")
            if id_type.startswith(PREFIX) and number:
                entries.append({
                    "address": number,
                    "chain": id_type[len(PREFIX):].strip(),
                    "name": name or "OFAC SDN entry",
                    "category": "sanctioned",
                    "source": f"OFAC SDN list (uid {uid})",
                })
        elem.clear()
    return entries


def fetch_sdn(timeout: float = 120.0) -> bytes:
    last: Exception | None = None
    with httpx.Client(timeout=timeout, follow_redirects=True,
                      headers={"User-Agent": f"chaintrace/{__version__}"}) as client:
        for url in SDN_URLS:
            try:
                r = client.get(url)
                r.raise_for_status()
                return r.content
            except httpx.HTTPError as e:
                last = e
    raise RuntimeError(f"Could not download the OFAC SDN list: {last}")


def update_sanctions(out_path: Path) -> dict:
    entries = parse_sdn_xml(fetch_sdn())
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "source": "U.S. Treasury OFAC Specially Designated Nationals list",
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "entries": entries,
    }
    out_path.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    chains: dict[str, int] = {}
    for e in entries:
        chains[e["chain"]] = chains.get(e["chain"], 0) + 1
    return {"total": len(entries), "by_chain": dict(sorted(chains.items(), key=lambda kv: -kv[1]))}

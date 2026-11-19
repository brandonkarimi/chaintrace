import copy

from fastapi.testclient import TestClient

from chaintrace.analysis import analyze
from chaintrace.api import create_app
from chaintrace.labels import Label, LabelStore
from chaintrace.report import build_report, verify_report
from chaintrace.sanctions import parse_sdn_xml
from chaintrace.tracer import Tracer
from chaintrace.validate import classify_seed

from .conftest import TX_A, addr


def _report(client):
    labels = LabelStore()
    r = Tracer(client, labels).trace(TX_A)
    return build_report(r, analyze(r, labels), labels, client.source, "Case 1", "Analyst")


def test_report_verifies_and_detects_tampering(client):
    rep = _report(client)
    ok, problems = verify_report(rep)
    assert ok, problems
    tampered = copy.deepcopy(rep)
    tampered["evidence"]["raw_transactions"][TX_A]["vout"][0]["value"] += 1
    ok, problems = verify_report(tampered)
    assert not ok and any(TX_A in p for p in problems)


def test_api_trace_and_errors(client):
    labels = LabelStore([Label(addr("EXCH"), "Example Exchange", "exchange")])
    api = TestClient(create_app(client, labels))
    assert api.get("/api/health").json()["status"] == "ok"

    resp = api.post("/api/trace", json={"seed": TX_A, "max_hops": 3})
    assert resp.status_code == 200
    body = resp.json()
    kinds = {e["data"].get("kind") for e in body["elements"]}
    assert {"tx", "address", "input", "output"} <= kinds
    seed_nodes = [e for e in body["elements"] if e["data"].get("seed")]
    assert seed_nodes and seed_nodes[0]["data"]["id"] == TX_A
    assert body["stats"]["transactions"] == 5

    assert api.post("/api/trace", json={"seed": "x" * 30}).status_code == 400
    assert api.post("/api/trace", json={"seed": "0" * 64}).status_code == 404
    assert api.post("/api/trace", json={"seed": TX_A, "max_hops": 99}).status_code == 422

    rep = api.post("/api/report", json={"seed": TX_A, "case_name": "Demo"})
    assert rep.status_code == 200 and "attachment" in rep.headers["content-disposition"]
    assert verify_report(rep.json())[0]


def test_seed_validation():
    assert classify_seed("a" * 64) == "tx"
    assert classify_seed("1BoatSLRHtKNngkdXEeobR76b53LETtpyT") == "address"
    assert classify_seed("bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq") == "address"
    for bad in ["", "../../etc/passwd", "0x" + "a" * 40, "a" * 63]:
        try:
            classify_seed(bad)
            raise AssertionError(f"accepted {bad!r}")
        except ValueError:
            pass


SDN_SAMPLE = b"""<?xml version="1.0"?>
<sdnList xmlns="https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/XML">
  <sdnEntry><uid>123</uid><lastName>EXAMPLE ENTITY</lastName><sdnType>Entity</sdnType>
    <idList>
      <id><uid>1</uid><idType>Digital Currency Address - XBT</idType><idNumber>bc1qexampleaddress</idNumber></id>
      <id><uid>2</uid><idType>Digital Currency Address - ETH</idType><idNumber>0xabc</idNumber></id>
      <id><uid>3</uid><idType>Registration Number</idType><idNumber>999</idNumber></id>
    </idList>
  </sdnEntry>
</sdnList>"""


def test_parse_sdn_xml():
    entries = parse_sdn_xml(SDN_SAMPLE)
    assert [(e["chain"], e["address"]) for e in entries] == [("XBT", "bc1qexampleaddress"), ("ETH", "0xabc")]
    assert entries[0]["category"] == "sanctioned" and "EXAMPLE ENTITY" in entries[0]["name"]

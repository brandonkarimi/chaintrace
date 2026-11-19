import pytest

from chaintrace.analysis import analyze
from chaintrace.esplora import NotFoundError
from chaintrace.labels import Label, LabelStore
from chaintrace.tracer import TraceParams, Tracer

from .conftest import TX_A, TX_B, TX_C, TX_D, TX_J, TX_X, addr


def test_forward_trace_follows_spends(client):
    r = Tracer(client).trace(TX_A, TraceParams(max_hops=3))
    assert set(r.txs) == {TX_A, TX_B, TX_C, TX_D, TX_J}
    assert r.hops[TX_A] == 0 and r.hops[TX_B] == 1 and r.hops[TX_C] == 2 and r.hops[TX_J] == 2
    assert not r.truncated


def test_hop_limit(client):
    r = Tracer(client).trace(TX_A, TraceParams(max_hops=1))
    assert set(r.txs) == {TX_A, TX_B, TX_D}


def test_backward_trace_and_missing_parent_is_a_warning(client):
    r = Tracer(client).trace(TX_C, TraceParams(direction="backward", max_hops=5))
    assert set(r.txs) == {TX_C, TX_B, TX_A}
    assert r.hops[TX_A] == -2
    assert any(TX_X in w for w in r.warnings)  # parent of TX_A is not on our fake chain


def test_stops_at_labeled_exchange(client):
    labels = LabelStore([Label(addr("C1"), "Example Exchange", "exchange")])
    r = Tracer(client, labels).trace(TX_A, TraceParams(max_hops=3))
    assert TX_J not in r.txs
    assert addr("C1") in r.terminal_addresses


def test_coinjoin_not_expanded(client):
    r = Tracer(client).trace(TX_B, TraceParams(max_hops=4))
    assert TX_J in r.txs
    assert "CoinJoin" in r.stopped[TX_J]


def test_budget_truncates(client):
    r = Tracer(client).trace(TX_A, TraceParams(max_txs=2))
    assert len(r.txs) == 2 and r.truncated
    assert any("budget" in w for w in r.warnings)


def test_address_seed(client):
    r = Tracer(client).trace(addr("A2"), TraceParams(direction="both", max_hops=1))
    assert {TX_A, TX_B} <= set(r.txs)
    assert r.seed_type == "address"


def test_invalid_and_unknown_seeds(client):
    with pytest.raises(ValueError):
        Tracer(client).trace("not-a-real-seed")
    with pytest.raises(NotFoundError):
        Tracer(client).trace("0" * 64)


def test_risk_propagates_from_sanctioned_source(client):
    labels = LabelStore([Label(addr("A1"), "Sanctioned Actor", "sanctioned", "test")])
    r = Tracer(client, labels).trace(TX_A, TraceParams(max_hops=3))
    a = analyze(r, labels)
    assert a.risk[addr("A1")].score == 100
    b1 = a.risk[addr("B1")]
    assert b1.score >= 40 and any("downstream of Sanctioned Actor" in x for x in b1.reasons)
    assert a.risk[addr("J1")].score >= 20  # CoinJoin output signal
    assert any("Sanctioned Actor" in f for f in a.findings)
    assert a.peel_chains == [[TX_A, TX_B, TX_C]]


def test_cluster_inherits_illicit_label(client):
    labels = LabelStore([Label(addr("B2"), "Ransomware Wallet", "ransomware", "test")])
    r = Tracer(client, labels).trace(TX_A, TraceParams(max_hops=3))
    a = analyze(r, labels)
    assert a.risk[addr("B1")].score >= 80
    assert any("same cluster" in x for x in a.risk[addr("B1")].reasons)


def test_exposure_counted_once_per_entity():
    from chaintrace.risk import score_addresses
    from .conftest import make_tx, txid

    # Two addresses of the same sanctioned entity pay one recipient in one tx.
    tx = make_tx(txid("e"), [(txid("1"), 0, addr("S_A"), 50_000), (txid("2"), 0, addr("S_B"), 50_000)],
                 [(addr("RCPT"), 90_000)])
    labels = LabelStore([Label(addr("S_A"), "Entity X", "sanctioned"), Label(addr("S_B"), "Entity X", "sanctioned")])
    risk = score_addresses([tx], labels, clusters={}, coinjoins=set(), peel_txs=set())
    assert risk[addr("RCPT")].score == 40

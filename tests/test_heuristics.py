from chaintrace.heuristics import cluster_addresses, detect_change, detect_peel_chains, is_coinjoin

from .conftest import BTC, TX_A, TX_B, TX_C, TX_D, TX_J, addr, make_tx, txid


def by_id(chain):
    return {t.txid: t for t in chain}


def test_coinjoin_detected(chain):
    txs = by_id(chain)
    assert is_coinjoin(txs[TX_J])
    assert not any(is_coinjoin(txs[t]) for t in (TX_A, TX_B, TX_C, TX_D))


def test_single_owner_batch_is_not_coinjoin():
    batch = make_tx(txid("1"), [(txid("2"), 0, addr("X"), BTC), (txid("2"), 1, addr("X"), BTC),
                                (txid("2"), 2, addr("X"), BTC)],
                    [(addr("P1"), 10_000), (addr("P2"), 10_000), (addr("P3"), 10_000)])
    assert not is_coinjoin(batch)


def test_common_input_clustering_skips_coinjoins(chain):
    clusters = cluster_addresses(chain)
    assert list(clusters.values()) == [sorted([addr("B1"), addr("B2")])]
    members = {a for m in clusters.values() for a in m}
    assert addr("S1") not in members  # CoinJoin co-inputs must not be merged


def test_change_detection_script_type_and_round_amount(chain):
    guess = detect_change(by_id(chain)[TX_A])
    assert guess is not None
    assert guess.address == addr("A2")
    assert guess.confidence >= 0.5
    assert any("script type" in r for r in guess.reasons)
    assert any("round" in r for r in guess.reasons)


def test_change_detection_address_reuse():
    tx = make_tx(txid("3"), [(txid("4"), 0, addr("R"), BTC)], [(addr("P"), 30_000_000), (addr("R"), 69_990_000)])
    guess = detect_change(tx)
    assert guess.address == addr("R") and guess.confidence == 0.95


def test_change_detection_abstains_when_ambiguous():
    tx = make_tx(txid("5"), [(txid("6"), 0, addr("Q"), BTC)], [(addr("P"), 40_000_123), (addr("Z"), 59_989_877)])
    assert detect_change(tx) is None


def test_peel_chain(chain):
    assert detect_peel_chains(chain) == [[TX_A, TX_B, TX_C]]
    assert detect_peel_chains(chain, min_length=4) == []

import json

from chaintrace.study import SanctionedSet, run_study

from .conftest import FakeClient, addr, make_tx, txid

BTC = 100_000_000


class StudyClient(FakeClient):
    def get_address(self, address: str) -> dict:
        txs = self.get_address_txs(address, max_txs=10_000)
        received = sum(o.value for t in txs for o in t.outputs if o.address == address)
        return {"chain_stats": {"tx_count": len(txs), "funded_txo_sum": received}}


def sanctioned(tmp_path, rows):
    entries = [{"address": a, "chain": "XBT", "name": name, "source": f"OFAC SDN list (uid {uid})"}
               for a, name, uid in rows]
    path = tmp_path / "sdn.json"
    path.write_text(json.dumps({"entries": entries}))
    return SanctionedSet.from_file(path)


def test_study_precision_recall_and_leads(tmp_path):
    h1, h2, h3, g1, x = addr("h1"), addr("h2"), addr("h3"), addr("g1"), addr("unlisted")
    s1, s2, s3, out = addr("s1"), addr("s2"), addr("s3"), addr("out")
    txs = [
        make_tx(txid("1"), [], [(h1, 2 * BTC), (h2, 2 * BTC), (h3, 2 * BTC), (g1, 2 * BTC)]),
        # h1 + h2 (same entity) co-spent with an unlisted address: correct merge + 1 lead
        make_tx(txid("2"), [(txid("1"), 0, h1, 2 * BTC), (txid("1"), 1, h2, 2 * BTC),
                            (txid("9"), 0, x, BTC)], [(out, 5 * BTC - 1000)]),
        # h3 spends alone; g1 (another entity) spends into a CoinJoin
        make_tx(txid("3"), [(txid("1"), 2, h3, 2 * BTC)], [(out, 2 * BTC - 1000)]),
        make_tx(txid("4"), [(txid("1"), 3, g1, 2 * BTC), (txid("8"), 0, s1, 2 * BTC),
                            (txid("8"), 1, s2, 2 * BTC), (txid("8"), 2, s3, 2 * BTC)],
                [(addr(f"cj{i}"), BTC) for i in range(4)] + [(out, 4 * BTC - 1000)]),
    ]
    s = sanctioned(tmp_path, [(h1, "HYDRA", 1), (h2, "HYDRA", 1), (h3, "HYDRA", 1), (g1, "GARANTEX", 2)])
    r = run_study(StudyClient(txs), s)

    assert r["dataset"]["active_addresses"] == 4
    assert r["dataset"]["total_received_btc"] == 8.0
    c = r["clustering"]
    assert c["merged_sanctioned_pairs"] == 1 and c["pair_precision_pct"] == 100.0
    # h1, h2, h3 all spent; only (h1, h2) was merged out of 3 possible pairs
    assert c["same_entity_pairs_possible"] == 3 and c["same_entity_pairs_recovered"] == 1
    assert c["mixed_entity_clusters"] == []
    assert r["leads"]["unlisted_addresses_linked"] == 1
    assert r["_private"]["lead_addresses"] == [x]
    assert r["behaviour"]["addresses_spending_into_coinjoin"] == 1


def test_study_flags_cross_entity_merge(tmp_path):
    a, b, out = addr("a"), addr("b"), addr("out")
    txs = [
        make_tx(txid("1"), [], [(a, BTC), (b, BTC)]),
        make_tx(txid("2"), [(txid("1"), 0, a, BTC), (txid("1"), 1, b, BTC)], [(out, 2 * BTC - 500)]),
    ]
    s = sanctioned(tmp_path, [(a, "ENTITY A", 1), (b, "ENTITY B", 2)])
    c = run_study(StudyClient(txs), s)["clustering"]
    assert c["pair_precision_pct"] == 0.0
    assert c["mixed_entity_clusters"] == [["ENTITY A", "ENTITY B"]]

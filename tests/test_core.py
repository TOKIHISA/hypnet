"""hypnet 単体のテスト。"""
import json
import shutil
import sys
from pathlib import Path

import pytest
import yaml

from hypnet import knowledge as K
from hypnet import ledger
from hypnet.cli import main
from hypnet.net import Net
from hypnet.schema import json_schema

EX = Path(__file__).resolve().parents[1] / "examples" / "reading_network"


@pytest.fixture
def net_dir(tmp_path, monkeypatch):
    d = tmp_path / "net"
    shutil.copytree(EX, d, ignore=shutil.ignore_patterns("inbox", "logs", "build", "hypotheses"))
    monkeypatch.chdir(d)
    monkeypatch.setenv("HYPNET_SIGNING_KEY", str(tmp_path / "key"))
    monkeypatch.setenv("HYPNET_ACTOR", "tester")
    return d


def test_independent_of_any_producer(net_dir):
    assert main(["validate"]) == 0
    assert not any(m == "hg" or m.startswith("hg.") for m in sys.modules)


def test_literature_disagreement_is_bounded_not_merged(net_dir):
    kn = K.derive(Net.load(net_dir))
    k = kn["kn.childcare_rate.increases.birth_rate"]
    assert k.state == "bounded" and len(k.holds_in) == 1 and len(k.fails_in) == 1
    assert kn["kn.road_centrality.increases.retail_density"].state == "local"


def test_explore_evidence_does_not_count_as_holding(net_dir):
    kn = K.derive(Net.load(net_dir))
    k = kn["kn.network_complexity.increases.retail_density"]
    assert k.state == "conjecture"       # my_city.H001 は探索の根拠しかない


def test_propagation_drafts_and_accept(net_dir):
    assert main(["propagate", "--collection", "my_city", "--draft"]) == 0
    drafts = list((net_dir / "inbox").glob("*.yaml"))
    assert len(drafts) == 1
    d = yaml.safe_load(drafts[0].read_text(encoding="utf-8"))
    assert d["origin"] == "literature" and d["parents"] == ["lit.H001"]
    assert main(["accept", "--all"]) == 0
    net = Net.load(net_dir)
    assert "my_city.H002" in net.nodes
    # 2回目は同じ下書きを作らない
    assert main(["propagate", "--collection", "my_city", "--draft"]) == 0
    assert not list((net_dir / "inbox").glob("*.yaml"))


def test_purpose_gate(net_dir):
    cols = yaml.safe_load((net_dir / "collections.yaml").read_text(encoding="utf-8"))
    for c in cols["nodes"]:
        c.pop("purpose", None)
    (net_dir / "collections.yaml").write_text(yaml.safe_dump(cols, allow_unicode=True), encoding="utf-8")
    net = Net.load(net_dir)
    assert K.draft_propagation(net, "my_city") == []     # 目的のないまとまりには流れ込まない


def test_premise_propagation_and_review(net_dir):
    hyps = yaml.safe_load((net_dir / "hypotheses.yaml").read_text(encoding="utf-8"))
    hyps["nodes"].append({"id": "my_city.H009", "kind": "hypothesis", "collection": "my_city",
                          "statement": "文献の不成立を前提にした派生", "origin": "claude", "parents": ["lit.H003"]})
    (net_dir / "hypotheses.yaml").write_text(yaml.safe_dump(hyps, allow_unicode=True), encoding="utf-8")
    net = Net.load(net_dir)
    assert [r["hyp"] for r in K.broken_premises(net)] == ["my_city.H009"]
    assert K.effective_priority(net, net.nodes["my_city.H009"]) == 0.0


def test_inference_and_why(net_dir):
    hyps = yaml.safe_load((net_dir / "hypotheses.yaml").read_text(encoding="utf-8"))
    hyps["nodes"].append({"id": "my_city.H010", "kind": "hypothesis", "collection": "my_city",
                          "statement": "店舗が多い通りほど歩行者の反応が大きい", "origin": "analyst", "status": "supported",
                          "claim": {"subject": "var.retail_density", "relation": "increases",
                                    "object": "var.pedestrian_response", "context": {"place": "jis:13"}},
                          "evidence": [{"by": "my_check", "kind": "confirm", "effect": "supports", "ref": "file://x"}]})
    (net_dir / "hypotheses.yaml").write_text(yaml.safe_dump(hyps, allow_unicode=True), encoding="utf-8")
    net = Net.load(net_dir)
    inf = K.inferences(net)
    assert any(r["subject"] == "var.road_centrality" and r["object"] == "var.pedestrian_response" for r in inf)
    text = K.why(net, "kn.childcare_rate.increases.birth_rate")
    assert "src.rindfuss2010" in text and "src.lappegard2008" in text


def test_ledger_detects_tampering(net_dir):
    cfg = yaml.safe_load((net_dir / "hypnet.yaml").read_text(encoding="utf-8"))
    cfg["rules"]["ledger"] = "error"
    (net_dir / "hypnet.yaml").write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    assert main(["ledger", "adopt", "--reason", "手書きのネットワークを取り込み"]) == 0
    assert main(["validate"]) == 0
    p = net_dir / "hypotheses.yaml"
    p.write_text(p.read_text(encoding="utf-8").replace("status: refuted", "status: supported"), encoding="utf-8")
    errs = [i for i in Net.load(net_dir).validate() if i.level == "error"]
    assert any("承認後に書き換えられた" in i.msg for i in errs)


def test_schema_and_in_memory_net():
    s = json_schema()
    assert {"Hypothesis", "Collection", "Evidence"} <= set(s["$defs"])
    net = Net.from_nodes([
        {"id": "var.a", "kind": "variable"}, {"id": "var.b", "kind": "variable"},
        {"id": "c", "kind": "collection"},
        {"id": "c.H001", "kind": "hypothesis", "collection": "c", "statement": "a→b", "origin": "analyst",
         "status": "supported", "claim": {"subject": "var.a", "relation": "increases", "object": "var.b"},
         "evidence": [{"by": "x", "kind": "confirm", "effect": "supports"}]},
    ])
    assert K.derive(net)["kn.a.increases.b"].state == "local"
    assert json.dumps(s)

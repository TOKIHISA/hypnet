"""hn コマンド: hypnet の YAML だけでネットワークを扱う（分析ハーネスなしでも使える）。"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import yaml

from . import knowledge as K
from . import ledger
from .net import Net, find_root
from .schema import AUTO_ORIGINS, KINDS, json_schema


def _net() -> Net:
    return Net.load(find_root())


def _dump(data: dict) -> str:
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=None, width=100)


def _ledger_on(net: Net) -> bool:
    return net.ledger_level != "off"


def _need_reason(reason: str) -> None:
    if not reason or len(reason.strip()) < 4:
        raise SystemExit("判断には理由（--reason）を言葉で書いてください")


def cmd_validate(a) -> int:
    net = _net()
    issues = net.validate()
    for i in issues:
        if i.level != "info" or a.verbose:
            print(i)
    errs = sum(i.level == "error" for i in issues)
    print(f"\nノード {len(net.nodes)} 件 / エラー {errs} / 警告 {sum(i.level == 'warn' for i in issues)}")
    return 1 if errs else 0


def cmd_knowledge(a) -> int:
    net = _net()
    if a.id:
        print(K.why(net, a.id))
        return 0
    kn = K.derive(net)
    order = ["bounded", "replicated", "local", "unsupported", "conjecture"]
    for k in sorted(kn.values(), key=lambda k: (order.index(k.state), k.id)):
        if k.state == "conjecture" and not a.all:
            continue
        print(f"{k.id:<48} {K.LABEL[k.state]}{' ✓' if k.endorsed else ''}")
        for ctx in k.holds_in:
            print(f"    ○ {ctx}")
        for ctx in k.fails_in:
            print(f"    × {ctx}")
    return 0


def cmd_why(a) -> int:
    print(K.why(_net(), a.id))
    return 0


def cmd_propagate(a) -> int:
    net = _net()
    kn = K.derive(net)
    state = net.root / "logs" / "knowledge_state.json"
    prev, cur = K.load_state(state), K.snapshot(net, kn)
    print(f"前回（{prev.get('at', 'なし')}）からの伝搬:")
    for e in K.events(prev, cur) or ["変化なし"]:
        print("  - " + e)
    for r in K.broken_premises(net):
        print(f"  前提崩れ: {r['hyp']} ← {r['broken_premise']}")
    for t in K.transfers(net, kn, a.collection):
        print(f"  転移: {t['knowledge']} → {t['collection']}  {'準備OK' if t['ready'] else '測れない変数: ' + ', '.join(t['missing'])}")
    for r in K.inferences(net, kn):
        print(f"  推論: {r['candidate']}  via {r['via']}  共通文脈: {r['shared_context']}")
    if a.draft:
        if not a.collection:
            print("--draft には --collection が要ります")
            return 1
        inbox = net.root / "inbox"
        inbox.mkdir(exist_ok=True)
        pending = {(yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("statement") for p in inbox.glob("*.yaml")}
        drafts = K.draft_propagation(net, a.collection, a.round, exclude=pending)
        for i, d in enumerate(drafts, 1):
            p = inbox / f"{date.today():%Y%m%d}-{a.collection}-{d['origin']}-{i}.yaml"
            n = i
            while p.exists():
                n += 1
                p = inbox / f"{date.today():%Y%m%d}-{a.collection}-{d['origin']}-{n}.yaml"
            p.write_text(_dump({k: v for k, v in d.items() if v not in (None, "", [])}), encoding="utf-8")
            print(f"  下書き: {p.relative_to(net.root)}")
    if not a.dry_run:
        K.save_state(state, cur)
    return 0


def cmd_accept(a) -> int:
    net = _net()
    inbox = net.root / "inbox"
    paths = sorted(inbox.glob("*.yaml")) if a.all else [Path(f).resolve() for f in a.files]
    failed = 0
    for p in paths:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        try:
            if data.get("kind") != "hypothesis":
                raise SystemExit(f"{p.name}: hn accept が扱うのは仮説の下書きだけです")
            col = net.nodes.get(data.get("collection"))
            if col is None:
                raise SystemExit(f"{p.name}: まとまり {data.get('collection')} がありません")
            moved = data.get("origin") in ("transfer", "inference") or (data.get("origin") == "literature" and col.role == "analysis")
            if moved and net.rule("purpose_gate", True) and col.purpose is None:
                raise SystemExit(f"{p.name}: {col.id} は目的（purpose）を宣言していません")
            if data.get("origin") in AUTO_ORIGINS:
                rnd = data.get("round")
                if rnd and sum(1 for h in net.hypotheses if h.round == rnd and h.origin in AUTO_ORIGINS) >= net.rule("budget_per_round", 5):
                    raise SystemExit(f"{p.name}: ラウンド {rnd} の予算を使い切っています")
                depth = K.auto_depth(net, data["origin"], data.get("parents", []))
                if depth > net.rule("max_auto_depth", 2):
                    raise SystemExit(f"{p.name}: 人間を介さない派生が {depth} 段になります")
            nid = data.get("id") or net.next_id(col.id)
            data = {"id": nid, **{k: v for k, v in data.items() if k != "id"}}
            node = KINDS["hypothesis"].model_validate(data)
            dest = net.root / "hypotheses" / f"{nid}.yaml"
            dest.parent.mkdir(exist_ok=True)
            dest.write_text(_dump(data), encoding="utf-8")
            if _ledger_on(net):
                ledger.record(net.root, "accept", dest, nid, a.reason or (data.get("note") or "")[:120])
            p.unlink()
            net.nodes[nid] = node
            print(f"✓ {nid} → {dest.relative_to(net.root)}")
        except SystemExit as e:
            failed += 1
            print(f"✗ {e}")
    return 1 if failed else 0


def _rewrite(net: Net, nid: str, mutate, action: str, reason: str) -> Path:
    path = net.paths.get(nid)
    if path is None:
        raise SystemExit(f"{nid} のファイルがありません")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    mutate(raw)
    KINDS[raw["kind"]].model_validate(raw)
    path.write_text(_dump(raw), encoding="utf-8")
    if _ledger_on(net):
        ledger.record(net.root, action, path, nid, reason)
    return path


def cmd_review(a) -> int:
    _need_reason(a.reason)
    net = _net()
    if a.premise not in net.ancestors(a.hypothesis):
        print(f"{a.premise} は {a.hypothesis} の祖先ではありません")
        return 1

    def m(raw):
        raw.setdefault("premise_reviewed", [])
        if a.premise not in raw["premise_reviewed"]:
            raw["premise_reviewed"].append(a.premise)
    _rewrite(net, a.hypothesis, m, "review", a.reason)
    print(f"{a.hypothesis}: 前提 {a.premise} を見直し済みにしました")
    return 0


def cmd_endorse(a) -> int:
    _need_reason(a.reason)
    net = _net()
    if a.knowledge not in K.derive(net):
        print(f"知見 {a.knowledge} がありません")
        return 1
    data = {"id": a.knowledge, "kind": "knowledge", "scope": a.scope, "endorsed": True,
            "endorsed_on": date.today().isoformat(), "note": a.reason}
    if a.knowledge in net.nodes:
        _rewrite(net, a.knowledge, lambda raw: raw.update({k: v for k, v in data.items() if k not in ("id", "kind")}),
                 "endorse", a.reason)
    else:
        path = net.root / "knowledge" / f"{a.knowledge.split('.', 1)[1]}.yaml"
        path.parent.mkdir(exist_ok=True)
        path.write_text(_dump(data), encoding="utf-8")
        if _ledger_on(net):
            ledger.record(net.root, "endorse", path, a.knowledge, a.reason)
    print(f"{a.knowledge} を承認しました")
    return 0


def cmd_export(a) -> int:
    from .export import build_graph, prov_jsonld, write_json, write_viz
    net = _net()
    out = net.root / "build"
    out.mkdir(exist_ok=True)
    g = build_graph(net)
    write_json(g, out / "graph.json")
    (out / "prov.jsonld").write_text(json.dumps(prov_jsonld(net), ensure_ascii=False, indent=1), encoding="utf-8")
    write_viz(g, out / "viz.html")
    print(f"build/ に graph.json, prov.jsonld, viz.html を書きました（ノード {g.number_of_nodes()}, エッジ {g.number_of_edges()}）")
    return 0


def cmd_schema(a) -> int:
    print(json.dumps(json_schema(), ensure_ascii=False, indent=1))
    return 0


def cmd_keygen(a) -> int:
    net = _net()
    fp, created = ledger.keygen(net.root, a.reason)
    print(f"署名鍵 {fp}（{'新規作成' if created else '既存'}）: {ledger.key_path()}")
    return 0


def cmd_ledger(a) -> int:
    net = _net()
    if a.action == "verify":
        issues = ledger.verify(net.root)
        for lv, w, m in issues:
            print(f"[{lv}] {w}: {m}")
        print(f"\n台帳 {len(ledger.read(net.root))} 件 / 信任された鍵 {', '.join(ledger.trusted_keys(net.root)) or 'なし'}")
        return 1 if any(lv == "error" for lv, _, _ in issues) else 0
    if a.action == "log":
        for e in ledger.history(net.root, a.targets[0] if a.targets else None):
            print(f"#{e['seq']} {e['at']} {e['actor']:<10} {e['action']:<8} {e.get('node') or e.get('path')}  "
                  f"{'✓署名' if e.get('sig') else '署名なし'}  {e.get('reason', '')}")
        return 0
    if a.action == "pending":
        for p in ledger.pending(net.root):
            print(p.relative_to(net.root))
        return 0
    paths = [net.root / t for t in a.targets] if a.targets else ledger.pending(net.root)
    ledger.adopt(net.root, paths, a.reason)
    print(f"{len(paths)} 件を今の状態で承認しました")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="hn", description="hypnet: 仮説と知見のネットワーク")
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("validate"); p.add_argument("-v", "--verbose", action="store_true"); p.set_defaults(fn=cmd_validate)
    p = sp.add_parser("knowledge"); p.add_argument("id", nargs="?"); p.add_argument("--all", action="store_true")
    p.set_defaults(fn=cmd_knowledge)
    p = sp.add_parser("why"); p.add_argument("id"); p.set_defaults(fn=cmd_why)
    p = sp.add_parser("propagate"); p.add_argument("--collection"); p.add_argument("--round")
    p.add_argument("--draft", action="store_true"); p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_propagate)
    p = sp.add_parser("accept"); p.add_argument("files", nargs="*"); p.add_argument("--all", action="store_true")
    p.add_argument("--reason", default=""); p.set_defaults(fn=cmd_accept)
    p = sp.add_parser("review"); p.add_argument("hypothesis"); p.add_argument("--premise", required=True)
    p.add_argument("--reason", required=True); p.set_defaults(fn=cmd_review)
    p = sp.add_parser("endorse"); p.add_argument("knowledge"); p.add_argument("--scope", required=True)
    p.add_argument("--reason", required=True); p.set_defaults(fn=cmd_endorse)
    sp.add_parser("export").set_defaults(fn=cmd_export)
    sp.add_parser("schema", help="交換フォーマットの JSON Schema を出力").set_defaults(fn=cmd_schema)
    p = sp.add_parser("keygen"); p.add_argument("--reason", default="署名鍵の導入"); p.set_defaults(fn=cmd_keygen)
    p = sp.add_parser("ledger"); p.add_argument("action", choices=["verify", "log", "pending", "adopt"])
    p.add_argument("targets", nargs="*"); p.add_argument("--reason", default=""); p.set_defaults(fn=cmd_ledger)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())

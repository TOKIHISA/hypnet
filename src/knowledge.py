"""知見の導出と伝搬。分析の手順には依存しない（仮説・根拠の種類・まとまりの宣言だけを見る）。

知見（knowledge）は書くものではなく、仮説の claim から導出されるもの:
  同じ（主語・関係・目的・条件）を持つ仮説を、まとまり（collection）をまたいで束ね、
  「どの文脈で成り立ち（holds_in）、どこで崩れ（fails_in）、どこで未検証か（open_in）」を集約する。

伝搬は4つ:
  1. 系譜を下る     … 祖先が棄却されたら、それを前提にした open な子に「前提崩れ」。支持されたら子の優先度を上げる
  2. 知見の状態     … 局所 → 再現 → 条件つき（成り立つ文脈と崩れる文脈がある）… の遷移
  3. 他のまとまりへの転移 … 成り立った知見を、その変数を測れる別のまとまりの仮説候補にする（origin: transfer）
  4. 合成による推論 … A→B と B→C の知見から A→C を候補にする（origin: inference）
伝搬が返すのは下書き（dict）だけ。承認と状態の確定は、受け取る側（人間・プロデューサー）がする。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from itertools import product
from pathlib import Path
from typing import Callable

from .net import Net
from .schema import AUTO_ORIGINS, GROUNDING, OPEN, Claim, Evidence, Hypothesis

COMPOSE = {
    ("increases", "increases"): "increases",
    ("increases", "decreases"): "decreases",
    ("decreases", "increases"): "decreases",
    ("decreases", "decreases"): "increases",
    ("causes", "causes"): "causes",
    ("precedes", "precedes"): "precedes",
}
LABEL = {
    "conjecture": "未検証",
    "local": "局所（1文脈で成立）",
    "replicated": "再現（複数文脈で成立）",
    "bounded": "条件つき（成り立つ文脈と崩れる文脈がある）",
    "unsupported": "不成立",
}
PHRASE = {
    "increases": "{s}が大きいほど{o}が大きい",
    "decreases": "{s}が大きいほど{o}が小さい",
    "moderates": "{s}が「{g}と{o}の関係」の強さを変える",
    "causes": "{s}が{o}を引き起こす",
    "precedes": "{s}の変化が{o}の変化に先行する",
    "associated": "{s}と{o}が関連する",
    "clusters_with": "{s}と{o}が同じ類型にまとまる",
    "located_near": "{s}と{o}が空間的に近接する",
}


def readable(net: Net, subject: str, relation: str, obj: str, given=()) -> str:
    g = "・".join(net.title(x) for x in given)
    return PHRASE.get(relation, "{s} " + relation + " {o}").format(s=net.title(subject), o=net.title(obj), g=g or "?")


# ---------------- 知見の導出 ----------------

def claim_key(c: Claim) -> tuple:
    return (c.subject, c.relation, c.object, tuple(sorted(c.given)))


def kid(key: tuple) -> str:
    s, r, o, g = key
    short = lambda v: v.split(".", 1)[1] if v.startswith("var.") else v
    base = f"kn.{short(s)}.{r}.{short(o)}"
    return base + ("" if not g else "." + "_".join(short(x) for x in g))


def context_of(h: Hypothesis) -> str:
    c = h.claim.context if h.claim else None
    parts = [p for p in ((c.place if c else ""), (c.population if c else "")) if p]
    return " / ".join(parts) if parts else f"collection:{h.collection}"


def grounding(h: Hypothesis) -> list[Evidence]:
    return [e for e in h.evidence if e.effect == "supports" and e.kind in GROUNDING]


def grounded(h: Hypothesis) -> bool:
    return bool(grounding(h))


@dataclass
class Knowledge:
    id: str
    key: tuple
    members: list[str] = field(default_factory=list)
    holds_in: dict[str, list[str]] = field(default_factory=dict)
    fails_in: dict[str, list[str]] = field(default_factory=dict)
    open_in: dict[str, list[str]] = field(default_factory=dict)
    collections: set[str] = field(default_factory=set)
    sources: set[str] = field(default_factory=set)
    opposed_by: list[str] = field(default_factory=list)
    note: object = None

    subject = property(lambda self: self.key[0])
    relation = property(lambda self: self.key[1])
    object = property(lambda self: self.key[2])
    given = property(lambda self: self.key[3])
    cases = property(lambda self: self.collections)   # 旧名

    @property
    def state(self) -> str:
        s, f = len(self.holds_in), len(self.fails_in)
        if s == 0 and f == 0:
            return "conjecture"
        if s and f:
            return "bounded"
        if f:
            return "unsupported"
        return "replicated" if s >= 2 else "local"

    @property
    def endorsed(self) -> bool:
        return bool(getattr(self.note, "endorsed", False))

    @property
    def text(self) -> str:
        g = f"（{', '.join(self.given)} のもとで）" if self.given else ""
        return f"{self.subject} {self.relation} {self.object}{g}"

    def as_row(self) -> dict:
        return {"knowledge": self.id, "state": LABEL[self.state] + (" ✓承認" if self.endorsed else ""),
                "holds_in": "; ".join(self.holds_in) or "–", "fails_in": "; ".join(self.fails_in) or "–",
                "members": len(self.members), "collections": len(self.collections), "literature": len(self.sources)}


def derive(net: Net) -> dict[str, Knowledge]:
    kn: dict[str, Knowledge] = {}
    for h in net.hypotheses:
        if not h.claim:
            continue
        key = claim_key(h.claim)
        k = kn.setdefault(kid(key), Knowledge(id=kid(key), key=key))
        k.members.append(h.id)
        k.collections.add(h.collection)
        k.sources |= {e.by for e in h.evidence if e.kind == "literature"}
        ctx = context_of(h)
        if h.status == "supported" and grounded(h):
            k.holds_in.setdefault(ctx, []).append(h.id)
        elif h.status == "refuted":
            k.fails_in.setdefault(ctx, []).append(h.id)
        elif h.status in OPEN or h.status == "supported":
            k.open_in.setdefault(ctx, []).append(h.id)
    opp = net.vocab.get("opposites") or {}
    for k in kn.values():
        o = opp.get(k.relation)
        if o and (other := kid((k.subject, o, k.object, k.given))) in kn:
            k.opposed_by.append(other)
    for n in net.of("knowledge"):
        if n.id in kn:
            kn[n.id].note = n
    return kn


# ---------------- 1. 系譜を下る伝搬 ----------------

def premise_state(net: Net, h: Hypothesis) -> tuple[list[str], list[str]]:
    """(崩れた前提, 支えている前提)。支持された祖先で遡りを止める。"""
    broken, support = [], []
    stack, seen = list(h.parents), set()
    while stack:
        a = stack.pop()
        if a in seen or a not in net.nodes:
            continue
        seen.add(a)
        anc = net.nodes[a]
        if anc.status in ("refuted", "abandoned", "superseded"):
            if a not in h.premise_reviewed:
                broken.append(a)
            continue
        if anc.status == "supported":
            support.append(a)
            continue
        stack.extend(anc.parents)
    return sorted(broken), sorted(support)


def effective_priority(net: Net, h: Hypothesis, kn: dict[str, Knowledge] | None = None) -> float:
    if h.priority is not None:
        base = h.priority
    elif h.origin in ("literature", "transfer"):
        base = 0.7
    elif h.origin in ("analyst", "stakeholder"):
        base = 0.6
    else:
        base = 0.4
    broken, support = premise_state(net, h)
    if broken:
        return 0.0
    p = base + 0.15 * len(support)
    if kn is not None and h.claim:
        k = kn.get(kid(claim_key(h.claim)))
        if k is not None and k.state == "bounded":
            p += 0.1
    return round(min(p, 1.0), 2)


def broken_premises(net: Net) -> list[dict]:
    rows = []
    for h in net.hypotheses:
        if h.status in OPEN:
            broken, _ = premise_state(net, h)
            if broken:
                rows.append({"hyp": h.id, "status": h.status, "broken_premise": ", ".join(broken),
                             "statement": h.statement[:36]})
    return rows


def auto_depth(net: Net, origin: str, parents: list[str]) -> int:
    """人間を介さない出自の仮説が上に何段続くか。確認データか文献で支持された祖先で数え直す。"""
    if origin not in AUTO_ORIGINS:
        return 0
    best = 0
    for p in parents:
        n = net.nodes.get(p)
        if n is None or n.kind != "hypothesis" or n.origin not in AUTO_ORIGINS:
            continue
        if n.status == "supported" and grounded(n):
            continue
        best = max(best, auto_depth(net, n.origin, n.parents))
    return best + 1


# ---------------- 3. 転移 ----------------

def operationalizable(net: Net, variables: list[str], collection: str) -> tuple[bool, list[str]]:
    c = net.nodes.get(collection)
    can = set(getattr(c, "measurable", []) or [])
    missing = [v for v in variables if v not in can]
    return not missing, missing


def transfers(net: Net, kn: dict[str, Knowledge] | None = None, collection: str | None = None) -> list[dict]:
    kn = kn if kn is not None else derive(net)
    targets = [c.id for c in net.of("collection") if c.role == "analysis" and (collection is None or c.id == collection)]
    weight = {"replicated": 0.9, "bounded": 0.85, "local": 0.7}
    rows = []
    for k in kn.values():
        if k.state not in weight:
            continue
        holders = [net.nodes[h] for hs in k.holds_in.values() for h in hs]
        from_lit = all(all(e.kind == "literature" for e in grounding(h)) for h in holders)
        src_cols = sorted({h.collection for h in holders if getattr(net.nodes.get(h.collection), "role", "") == "analysis"})
        for c in targets:
            if c in k.collections:
                continue
            ok, missing = operationalizable(net, [k.subject, k.object, *k.given], c)
            tgt = net.nodes[c].purpose
            rows.append({"knowledge": k.id, "case": c, "collection": c, "state": k.state, "priority": weight[k.state],
                         "ready": ok, "missing": missing, "origin": "literature" if from_lit else "transfer",
                         "parents": sorted(h.id for h in holders),
                         "purpose_declared": tgt is not None,
                         "from_purposes": {s: getattr(net.nodes[s].purpose, "ends", "（未宣言）") for s in src_cols},
                         "to_purpose": getattr(tgt, "ends", "（未宣言）"),
                         "to_must_not": list(getattr(tgt, "must_not", []) or [])})
    return sorted(rows, key=lambda r: (-r["ready"], -r["priority"], r["knowledge"], r["collection"]))


# ---------------- 4. 合成による推論 ----------------

def inferences(net: Net, kn: dict[str, Knowledge] | None = None) -> list[dict]:
    kn = kn if kn is not None else derive(net)
    usable = [k for k in kn.values() if k.state in ("local", "replicated") and not k.given]
    existing = {(k.subject, k.object) for k in kn.values()}
    rows, seen = [], set()
    for a, b in product(usable, usable):
        if a.id == b.id or a.object != b.subject or a.subject == b.object:
            continue
        rel = COMPOSE.get((a.relation, b.relation))
        if rel is None or (a.subject, b.object) in existing or (a.subject, rel, b.object) in seen:
            continue
        seen.add((a.subject, rel, b.object))
        shared = sorted(set(a.holds_in) & set(b.holds_in))
        rows.append({"candidate": f"{a.subject} {rel} {b.object}", "subject": a.subject, "relation": rel,
                     "object": b.object, "via": f"{a.id} ∘ {b.id}", "shared_context": "; ".join(shared) or "なし",
                     "priority": 0.5 if shared else 0.3,
                     "parents": sorted({h for hs in a.holds_in.values() for h in hs} | {h for hs in b.holds_in.values() for h in hs}),
                     "caveat": "" if shared else "前提が別々の文脈でしか成り立っていない"})
    return sorted(rows, key=lambda r: (-r["priority"], r["candidate"]))


def draft_propagation(net: Net, collection: str, round_id: str | None = None, limit: int | None = None,
                      exclude: set[str] | None = None) -> list[dict]:
    """転移と推論の仮説の下書きを、予算の範囲で返す（書き込みはしない）。"""
    kn = derive(net)
    budget = limit if limit is not None else net.rule("budget_per_round", 5)
    used = sum(1 for h in net.hypotheses if round_id and h.round == round_id and h.origin in AUTO_ORIGINS)
    exclude = set(exclude or ()) | {h.statement for h in net.hypotheses}
    col = net.nodes.get(collection)
    if col is None or col.kind != "collection":
        raise SystemExit(f"まとまり {collection} がありません")
    gate = net.rule("purpose_gate", True)
    ctx = (col.context.model_dump() if col.context else
           next((h.claim.context.model_dump() for h in net.hypotheses
                 if h.collection == collection and h.claim and h.claim.context.place), {}))
    out: list[dict] = []

    def room() -> bool:
        return used + len(out) < budget

    def base(stmt, origin, parents, claim, priority, note):
        return {"kind": "hypothesis", "collection": collection, "statement": stmt, "origin": origin,
                "status": "proposed", "born_from": collection, "parents": parents, "claim": claim,
                "priority": priority, "round": round_id, "note": note, "created": date.today().isoformat()}

    for t in transfers(net, kn, collection):
        if not (t["ready"] and room()) or (gate and not t["purpose_declared"]):
            continue
        k = kn[t["knowledge"]]
        stmt = f"（{'文献' if t['origin'] == 'literature' else '転移'}）{readable(net, k.subject, k.relation, k.object, k.given)}（{collection} でも成り立つか）"
        if stmt in exclude:
            continue
        out.append(base(stmt, t["origin"], t["parents"],
                        {"subject": k.subject, "relation": k.relation, "object": k.object, "given": list(k.given),
                         "context": ctx}, t["priority"],
                        f"知見 {k.id}（{LABEL[k.state]}）からの転移。データを見る前に立つので、取っておいたデータで直接検証してよい。"
                        f"移転元の目的: {t['from_purposes'] or '文献'} / 移転先の目的: {t['to_purpose']}"
                        + (f" / 使ってはならない用途: {', '.join(t['to_must_not'])}" if t["to_must_not"] else "")))
    for inf in inferences(net, kn):
        if not room() or (gate and col.purpose is None):
            break
        ok, _ = operationalizable(net, [inf["subject"], inf["object"]], collection)
        stmt = f"（推論）{readable(net, inf['subject'], inf['relation'], inf['object'])}"
        if not ok or stmt in exclude:
            continue
        out.append(base(stmt, "inference", inf["parents"],
                        {"subject": inf["subject"], "relation": inf["relation"], "object": inf["object"], "context": ctx},
                        inf["priority"], f"{inf['via']} の合成。相関の連鎖は合成できるとは限らない。{inf['caveat']}"))
    return out


# ---------------- イベント（前回からの変化） ----------------

def snapshot(net: Net, kn: dict[str, Knowledge] | None = None) -> dict:
    kn = kn if kn is not None else derive(net)
    return {
        "at": date.today().isoformat(),
        "knowledge": {k.id: {"state": k.state, "holds_in": sorted(k.holds_in), "fails_in": sorted(k.fails_in)}
                      for k in kn.values()},
        "broken": {r["hyp"]: r["broken_premise"] for r in broken_premises(net)},
    }


def events(prev: dict, cur: dict) -> list[str]:
    out = []
    pk, ck = prev.get("knowledge", {}), cur["knowledge"]
    for k, v in sorted(ck.items()):
        old = pk.get(k)
        if old is None:
            out.append(f"新しい知見 {k}: {LABEL[v['state']]}")
            continue
        if old["state"] != v["state"]:
            out.append(f"{k}: {LABEL[old['state']]} → {LABEL[v['state']]}")
        for ctx in sorted(set(v["holds_in"]) - set(old["holds_in"])):
            out.append(f"{k}: 成立する文脈が増えた（{ctx}）")
        for ctx in sorted(set(v["fails_in"]) - set(old["fails_in"])):
            out.append(f"{k}: 崩れる文脈が見つかった（{ctx}）")
    for h, b in sorted(cur["broken"].items()):
        if prev.get("broken", {}).get(h) != b:
            out.append(f"{h}: 前提 {b} が崩れた（見直しが必要）")
    return out


def load_state(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def save_state(path: Path, snap: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------- 来歴を遡って読む ----------------

Describe = Callable[[Hypothesis, Evidence], "str | None"]


def why(net: Net, target: str, describe: Describe | None = None, born: Callable[[str], "str | None"] | None = None) -> str:
    """知見・仮説がどう編まれたかを遡る。describe / born はプロデューサーが根拠や生まれを詳しく語るためのフック。"""
    kn = derive(net)
    lines: list[str] = []
    if target in kn:
        k = kn[target]
        lines.append(f"{k.id}  {readable(net, k.subject, k.relation, k.object, k.given)}")
        lines.append(f"  状態: {LABEL[k.state]}{'（人間が承認: ' + str(k.note.endorsed_on) + '）' if k.endorsed else ''}")
        if k.note is not None and getattr(k.note, "scope", ""):
            lines.append(f"  適用範囲（人間の記述）: {k.note.scope}")
        if k.opposed_by:
            lines.append(f"  逆向きの知見: {', '.join(k.opposed_by)}")
        for label, d in (("成り立つ", k.holds_in), ("崩れる", k.fails_in), ("未検証", k.open_in)):
            for ctx, hs in d.items():
                lines.append(f"  [{label}] {ctx}")
                for h in hs:
                    lines.extend("    " + x for x in _trace(net, h, 1, describe, born))
        return "\n".join(lines)
    n = net.nodes.get(target)
    if n is None or n.kind != "hypothesis":
        return f"{target} は知見でも仮説でもありません"
    return "\n".join(_trace(net, target, 3, describe, born))


def _trace(net: Net, hid: str, depth: int, describe, born) -> list[str]:
    h = net.nodes[hid]
    out = [f"{hid} [{h.status} / origin={h.origin}] {h.statement}"]
    if h.born_from:
        txt = born(h.born_from) if born else None
        if txt is None:
            b = net.nodes.get(h.born_from)
            txt = {"source": f"文献 {h.born_from} {getattr(b, 'title', '')[:40]}",
                   "collection": f"まとまり {h.born_from} に持ち込み"}.get(getattr(b, "kind", ""), h.born_from)
        out.append(f"  生まれ: {txt}")
    for e in h.evidence:
        txt = describe(h, e) if describe else None
        if txt is None:
            m = f" {e.metric}" if e.metric else ""
            ref = f" ref={e.ref}" if e.ref else ""
            label = f"文献 {e.by}" if e.kind == "literature" else f"{e.by}（{e.kind}）"
            txt = f"{label}{ref} → {e.effect}{m}"
        out.append(f"  根拠: {txt}")
    if h.parents and depth > 1:
        for p in h.parents:
            if p in net.nodes:
                out.extend("  ↑ " + x for x in _trace(net, p, depth - 1, describe, born))
    elif h.parents:
        out.append(f"  親: {', '.join(h.parents)}")
    return out

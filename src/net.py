"""ネットワークの読み込みと検査。ファイルからでも、プロデューサーが作ったノードの列からでも組める。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import yaml
from pydantic import ValidationError

from .schema import DEFAULT_VOCAB, KINDS, Hypothesis

MARKER = "hypnet.yaml"
SKIP_DIRS = {"inbox", "logs", "build", ".git", "__pycache__"}


@dataclass
class Issue:
    level: str
    where: str
    msg: str

    def __str__(self) -> str:
        return f"[{self.level}] {self.where}: {self.msg}"


def find_root(start: Path | None = None) -> Path:
    p = (start or Path.cwd()).resolve()
    for cand in [p, *p.parents]:
        if (cand / MARKER).exists():
            return cand
    raise SystemExit(f"{MARKER} が見つかりません（ネットワークのルートで実行してください）")


@dataclass
class Net:
    root: Path | None = None
    nodes: dict = field(default_factory=dict)
    paths: dict = field(default_factory=dict)
    config: dict = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)

    # ---------- 組み立て ----------
    @classmethod
    def load(cls, root: Path) -> "Net":
        cfg = yaml.safe_load((root / MARKER).read_text(encoding="utf-8")) or {}
        net = cls(root=root, config=cfg)
        for path in sorted(root.rglob("*.yaml")):
            rel = path.relative_to(root)
            if set(rel.parts[:-1]) & SKIP_DIRS or path.name == MARKER:
                continue
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
            except yaml.YAMLError as e:
                net.issues.append(Issue("error", str(rel), f"YAMLが読めません: {e}"))
                continue
            if data is None:
                continue
            items = data["nodes"] if isinstance(data, dict) and "nodes" in data else [data]
            for raw in items:
                net.add(raw, path, str(rel))
        return net

    @classmethod
    def from_nodes(cls, raws: Iterable[dict], config: dict | None = None, root: Path | None = None) -> "Net":
        net = cls(root=root, config=config or {})
        for raw in raws:
            net.add(raw, None, raw.get("id", "?"))
        return net

    def add(self, raw: dict, path: Path | None, where: str) -> None:
        kind = raw.get("kind") if isinstance(raw, dict) else None
        if kind not in KINDS:
            self.issues.append(Issue("error", where, f"kind が不明です: {kind!r}"))
            return
        try:
            node = KINDS[kind].model_validate(raw)
        except ValidationError as e:
            msgs = "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors())
            self.issues.append(Issue("error", f"{where} ({raw.get('id')})", msgs))
            return
        if node.id in self.nodes:
            self.issues.append(Issue("error", where, f"ID重複: {node.id}"))
            return
        self.nodes[node.id] = node
        if path is not None:
            self.paths[node.id] = path

    # ---------- 参照 ----------
    def of(self, kind: str) -> list:
        return [n for n in self.nodes.values() if n.kind == kind]

    @property
    def hypotheses(self) -> list[Hypothesis]:
        return self.of("hypothesis")

    @property
    def vocab(self) -> dict:
        return {**DEFAULT_VOCAB, **(self.config.get("vocab") or {})}

    def rule(self, key: str, default):
        return (self.config.get("rules") or {}).get(key, default)

    @property
    def ledger_level(self) -> str:
        v = self.rule("ledger", "off")
        return "off" if v in (False, None, "off") else ("error" if v is True else str(v))

    def title(self, nid: str) -> str:
        return getattr(self.nodes.get(nid), "title", "") or nid

    def ancestors(self, hid: str) -> list[str]:
        out, stack = [], list(getattr(self.nodes.get(hid), "parents", []))
        while stack:
            p = stack.pop()
            if p in out or p not in self.nodes:
                continue
            out.append(p)
            stack.extend(getattr(self.nodes[p], "parents", []))
        return out

    def next_id(self, collection: str) -> str:
        prefix = f"{collection}.H"
        nums = [int(i[len(prefix):]) for i in self.nodes if i.startswith(prefix) and i[len(prefix):].isdigit()]
        return f"{prefix}{(max(nums) if nums else 0) + 1:03d}"

    # ---------- 検査 ----------
    def validate(self) -> list[Issue]:
        out = list(self.issues)
        rels = set(self.vocab.get("claim_relations") or [])

        def need(where: str, ref: str, kinds: tuple[str, ...]) -> None:
            n = self.nodes.get(ref)
            if n is None:
                out.append(Issue("error", where, f"存在しない参照: {ref}"))
            elif n.kind not in kinds:
                out.append(Issue("error", where, f"{ref} は {n.kind}（期待: {'/'.join(kinds)}）"))

        for n in self.nodes.values():
            if n.kind == "collection":
                for v in n.measurable:
                    need(n.id, v, ("variable",))
            if n.kind != "hypothesis":
                continue
            need(n.id, n.collection, ("collection",))
            for p in n.parents:
                need(n.id, p, ("hypothesis",))
            if n.claim:
                for v in [n.claim.subject, n.claim.object, *n.claim.given]:
                    need(n.id, v, ("variable",))
                if rels and n.claim.relation not in rels:
                    out.append(Issue("error", n.id, f"未定義の関係: {n.claim.relation}"))
            for e in n.evidence:
                if e.kind == "literature":
                    need(n.id, e.by, ("source",))
            effects = {(e.effect, e.kind) for e in n.evidence}
            if n.status == "supported" and not any(eff == "supports" for eff, _ in effects):
                out.append(Issue("warn", n.id, "supported なのに支持する evidence がない"))
            if n.status == "supported" and not any(eff == "supports" and k in ("confirm", "literature") for eff, k in effects):
                out.append(Issue("info", n.id, "支持が confirm / literature の根拠に基づいていない（知見の『成り立つ』に数えない）"))
            if n.status == "refuted" and not any(eff == "refutes" for eff, _ in effects):
                out.append(Issue("warn", n.id, "refuted なのに棄却する evidence がない"))
            anc = set(self.ancestors(n.id))
            for r in n.premise_reviewed:
                if r not in anc:
                    out.append(Issue("error", n.id, f"premise_reviewed の {r} は祖先ではない"))
        out.extend(self._cycles())
        from .knowledge import broken_premises, derive
        kn = derive(self)
        for k in self.of("knowledge"):
            if k.id not in kn:
                out.append(Issue("warn", k.id, "対応する主張（claim）を持つ仮説がない知見の注記"))
        for r in broken_premises(self):
            out.append(Issue("warn", r["hyp"], f"前提 {r['broken_premise']} が棄却された（見直すか premise_reviewed に入れる）"))
        if self.root is not None and self.ledger_level != "off":
            from . import ledger
            out.extend(Issue(lv, w, m) for lv, w, m in ledger.verify(self.root, level=self.ledger_level))
        return out

    def _cycles(self) -> list[Issue]:
        import networkx as nx
        g = nx.DiGraph()
        for h in self.hypotheses:
            for p in h.parents:
                g.add_edge(h.id, p)
        return [Issue("error", " → ".join(c), "parents が循環しています") for c in nx.simple_cycles(g)]

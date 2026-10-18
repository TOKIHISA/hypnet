"""hypnet の交換フォーマット。

知識ネットワークを組むのに必要なのは、次の5種類のノードだけ（どれも YAML の1文書）:

  variable    … 主張に出てくる変数（外部ID same_as で、他のネットワークと結合する足場）
  source      … 根拠の出どころのうち、文献など外部にあるもの
  collection  … 仮説のまとまり（案件・研究・読書ノートなど）。目的と、測れる変数（measurable）を宣言する
  hypothesis  … 主張（claim）と、その系譜（parents）・状態（status）・根拠（evidence）
  knowledge   … 導出された知見への、人間の注記（適用範囲・承認）。知見そのものは書かない

分析の手順（データの分割、検定計画、notebook など）はこのフォーマットに含めない。
それは各自の「プロデューサー」（分析ハーネスや手作業）の仕事で、hypnet が受け取るのは結果だけ:
  - 根拠の種類（evidence.kind）: confirm（取っておいたデータでの検証）/ literature / explore / other
  - 根拠の参照（evidence.ref）: プロデューサー側の記録への参照（例: "hg:munis.S005"）
支持が confirm か literature の根拠に基づくときだけ、その仮説は知見の「成り立つ文脈」に数えられる。
"""
from __future__ import annotations

from datetime import date
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

Status = Literal["proposed", "testing", "supported", "refuted", "abandoned", "superseded"]
Origin = Literal["analyst", "claude", "stakeholder", "literature", "transfer", "inference", "other"]
Effect = Literal["supports", "refutes", "inconclusive"]
EvidenceKind = Literal["confirm", "literature", "explore", "other"]

OPEN = {"proposed", "testing"}
NEGATIVE = {"refuted", "abandoned"}
CLOSED = {"supported"} | NEGATIVE | {"superseded"}
AUTO_ORIGINS = {"claude", "transfer", "inference"}   # 人間を介さずに生まれうる出自
GROUNDING = {"confirm", "literature"}                # 知見の「成り立つ」に数える根拠の種類


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Variable(_Base):
    id: str
    kind: Literal["variable"]
    title: str = ""
    definition: str = ""
    unit: str = ""
    same_as: list[str] = Field(default_factory=list)
    note: str = ""


class Source(_Base):
    id: str
    kind: Literal["source"]
    title: str = ""
    authors: list[str] = Field(default_factory=list)
    year: Optional[int] = None
    venue: str = ""
    doi: str = ""
    url: str = ""
    note: str = ""


class Purpose(_Base):
    ends: str                                               # 何のためか
    beneficiaries: list[str] = Field(default_factory=list)  # 誰のためか
    must_not: list[str] = Field(default_factory=list)       # 使ってはならない用途


class PurposeRevision(_Base):
    on: date
    previous: Purpose
    reason: str
    born_from: Optional[str] = None
    by: str = ""


class Context(_Base):
    place: str = ""        # wikidata:Q17 / jis:13 / h3:... など
    period: str = ""
    population: str = ""


class Collection(_Base):
    id: str
    kind: Literal["collection"]
    role: Literal["analysis", "literature", "reading"] = "analysis"   # 転移の受け手になるのは analysis だけ
    title: str = ""
    question: str = ""
    purpose: Optional[Purpose] = None
    purpose_revisions: list[PurposeRevision] = Field(default_factory=list)
    measurable: list[str] = Field(default_factory=list)   # このまとまりで測れる変数（転移の可否を決める）
    context: Optional[Context] = None                      # 転移してきた仮説に付ける既定の文脈
    note: str = ""


class Claim(_Base):
    subject: str
    relation: str
    object: str
    given: list[str] = Field(default_factory=list)
    context: Context = Field(default_factory=Context)


class Evidence(_Base):
    by: str                     # 根拠を出したもの（source の ID、またはプロデューサー側のステップなど）
    kind: EvidenceKind
    effect: Effect
    metric: dict[str, Union[float, int, str]] = Field(default_factory=dict)
    note: str = ""
    ref: str = ""               # プロデューサー側の記録への参照


class Hypothesis(_Base):
    id: str
    kind: Literal["hypothesis"]
    collection: str
    statement: str
    origin: Origin
    status: Status = "proposed"
    born_from: Optional[str] = None      # 生まれた場所（source ID、collection ID、プロデューサー側の参照）
    parents: list[str] = Field(default_factory=list)
    claim: Optional[Claim] = None
    evidence: list[Evidence] = Field(default_factory=list)
    premise_reviewed: list[str] = Field(default_factory=list)
    priority: Optional[float] = None
    round: Optional[str] = None
    created: Optional[date] = None
    updated: Optional[date] = None
    note: str = ""
    ref: str = ""


class KnowledgeNote(_Base):
    id: str                     # kn.<主語>.<関係>.<目的>[.<条件>]
    kind: Literal["knowledge"]
    scope: str = ""
    endorsed: bool = False
    endorsed_on: Optional[date] = None
    note: str = ""


KINDS: dict[str, type[_Base]] = {
    "variable": Variable, "source": Source, "collection": Collection,
    "hypothesis": Hypothesis, "knowledge": KnowledgeNote,
}

DEFAULT_VOCAB: dict[str, Any] = {
    "claim_relations": ["increases", "decreases", "associated", "moderates", "causes",
                        "clusters_with", "precedes", "located_near"],
    "opposites": {"increases": "decreases", "decreases": "increases"},
}


def json_schema() -> dict:
    """全ノード種の JSON Schema（他の言語・ツールでプロデューサーを書くとき用）。"""
    defs: dict = {}
    for m in KINDS.values():
        sch = m.model_json_schema(ref_template="#/$defs/{model}")
        defs.update(sch.pop("$defs", {}))
        defs[m.__name__] = sch
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "hypnet node",
        "oneOf": [{"$ref": f"#/$defs/{m.__name__}"} for m in KINDS.values()],
        "$defs": defs,
    }

# hypnet 交換フォーマット 0.1

知識ネットワークを組むのに必要な YAML の仕様。分析の道具に依存しない。
どんな分析の仕組み（自作のハーネス、notebook、表計算、手作業）でも、このフォーマットを書き出せば
hypnet の知見の導出・伝搬・来歴・承認台帳が使える。JSON Schema は `schema/hypnet.schema.json`（`hn schema`）。

## 置き方

```
<ネットワーク>/
  hypnet.yaml        ルートの目印と規則（必須）
  *.yaml             ノード。1ファイル1ノードでも、{nodes: [...]} にまとめてもよい。ディレクトリ構成は自由
  inbox/             下書き（hn propagate --draft が書く。hn accept で正式になる）
  logs/              承認台帳と伝搬の状態（hn が書く）
```

`hypnet.yaml`:

```yaml
name: my-network
vocab:                         # 省略可。省略時は下の既定
  claim_relations: [increases, decreases, associated, moderates, causes, clusters_with, precedes, located_near]
  opposites: {increases: decreases, decreases: increases}
rules:
  budget_per_round: 5          # 1ラウンドで承認できる、人間を介さない新規仮説（claude / transfer / inference）の上限
  max_auto_depth: 2            # 人間を介さない派生が続いてよい段数
  purpose_gate: true           # 目的を宣言していないまとまりには、転移・推論・文献の仮説を流れ込ませない
  ledger: off                  # 承認台帳: off / warn / error
```

## ノードは5種類

### variable — 変数

```yaml
id: var.retail_density        # 必須
kind: variable                # 必須
title: 小売事業所密度
definition: 可住地面積あたりの小売事業所数
unit: 件/km2
same_as: ["custom:retail_establishment_density"]   # 外部ID。他のネットワークと結合する足場
```

### source — 文献など、外部にある根拠の出どころ

```yaml
id: src.porta2009
kind: source
title: Street centrality and densities of retail and services in Bologna, Italy
authors: [Porta, S., Strano, E.]
year: 2009
venue: Environment and Planning B, 36(3), 450–465
doi: 10.1068/b34098
url: https://arxiv.org/abs/physics/0701111
```

### collection — 仮説のまとまり（案件・研究・読書ノート）

```yaml
id: my_city
kind: collection
role: analysis                # analysis / literature / reading。転移の受け手になるのは analysis だけ
title: 自分の街の歩行環境
purpose:                      # 目的。宣言しないと、他の文脈の知見は流れ込まない（purpose_gate）
  ends: 何のための分析か
  beneficiaries: [誰のためか]
  must_not: [使ってはならない用途]
purpose_revisions:            # 目的の改訂履歴（前の目的・理由・改訂を促したもの）
  - {on: 2026-09-12, previous: {ends: ...}, reason: ..., born_from: my_city.H003, by: ...}
measurable: [var.road_centrality, var.retail_density]   # このまとまりで測れる変数。知見が転移できるかを決める
context: {place: "jis:13", population: 区内の街路}      # 転移してきた仮説に付ける既定の文脈
```

### hypothesis — 主張と、その系譜・状態・根拠

```yaml
id: my_city.H001              # 必須
kind: hypothesis              # 必須
collection: my_city           # 必須
statement: 区内でも、媒介中心性の高い通りほど店舗が多い   # 必須
origin: analyst               # 必須: analyst / claude / stakeholder / literature / transfer / inference / other
status: testing               # proposed / testing / supported / refuted / abandoned / superseded
born_from: src.porta2009      # 生まれた場所（source・collection の ID、またはプロデューサー側の参照）
parents: [lit.H001]           # 派生元の仮説（系譜）
claim:                        # 主張（知見に束ねるのに必要）
  subject: var.road_centrality
  relation: increases
  object: var.retail_density
  given: []                   # moderates などの条件
  context: {place: "jis:13", period: 2026, population: 区内の街路}
evidence:
  - by: my_check_2026-10      # 根拠を出したもの（source の ID、またはプロデューサー側のステップ）
    kind: confirm             # confirm / literature / explore / other
    effect: supports          # supports / refutes / inconclusive
    metric: {p: 0.01, n: 57}
    ref: "hg:munis.S007"      # プロデューサー側の記録への参照（notebook、ステップ、URL など）
premise_reviewed: []          # 祖先が棄却されたあと、人間が見直して「それでも立つ」とした祖先
priority: 0.7
round: R001
created: 2026-10-18
note: ""
```

**`evidence.kind` の約束**: 知見の「成り立つ文脈」に数えるのは、支持の根拠が `confirm`（仮説を作るのに使っていないデータでの検証）か
`literature`（文献）のときだけ。`explore` の支持は、仮説が生まれたデータでの支持なので数えない。
この区別をどう守るか（データの分割、検定計画の固定など）はプロデューサーの責任で、hypnet は書かれた種類を信じる。
だから `ref` で、プロデューサー側の記録まで辿れるようにしておく。

### knowledge — 導出された知見への、人間の注記

```yaml
id: kn.station_distance.decreases.retail_density   # kn.<主語>.<関係>.<目的>[.<条件>]（var. は省く）
kind: knowledge
scope: 鉄道網が一定以上ある地域。島しょ部では未確認   # 適用範囲（人間の言葉）
endorsed: true
endorsed_on: 2026-09-25
note: 承認の理由
```

知見そのもの（状態・成り立つ文脈・崩れる文脈）は書かない。hypnet が仮説から毎回導出する。

## 導出されるもの

| | 中身 |
|---|---|
| 知見 | claim の（主語・関係・目的・条件）が同じ仮説の束。状態は 未検証 / 局所 / 再現 / 条件つき / 不成立 |
| 前提崩れ | 祖先が refuted / abandoned / superseded になった open な仮説 |
| 転移 | 成り立った知見のうち、変数を測れる別のまとまりに、まだ仮説がないもの |
| 推論 | A→B と B→C（単調な関係）の知見から A→C の候補 |
| 来歴 | 知見 → 仮説 → 根拠（kind・by・ref）→ 祖先、と遡れる |

## プロデューサーを書くには

1. 自分の分析の記録から、上の5種類のノードを作る（YAML に書き出すか、Python なら `Net.from_nodes(dicts, config)`）
2. 根拠の種類 `kind` を正直に付ける（取っておいたデータでの検証だけが `confirm`）
3. `ref` に自分の記録への参照を入れる
4. `hypnet.knowledge.draft_propagation()` が返す下書き（dict）を、自分の承認の流れに取り込む

参照実装として、分析ハーネス hypograph の `hg/netbridge.py` がある。

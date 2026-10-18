# hypnet

仮説と知見のネットワーク。YAML の交換フォーマットと、それを読んで

- 同じ主張を、まとまり（案件・研究・文献）をまたいで**知見**として束ねる（局所 / 再現 / 条件つき / 不成立）
- **伝搬**させる（祖先の棄却による前提崩れ、別のまとまりへの転移、関係の合成による推論）
- 知見がどう編まれたかを**来歴**として遡る（`hn why`）
- 人間の判断を**承認台帳**に残す（ハッシュ連鎖と Ed25519 署名）

ための小さなライブラリと CLI（`hn`）。分析の手順には依存しない。
データの分割や検定計画、notebook の管理は「プロデューサー」の仕事で、hypnet が受け取るのは結果だけ。
フォーマットの仕様は [FORMAT.md](FORMAT.md)、JSON Schema は [schema/hypnet.schema.json](schema/hypnet.schema.json)。

## 使い方

```bash
pip install -e .              # 署名を使うなら pip install -e ".[sign]"
cd examples/reading_network   # 手書きの例（文献4本と、自分の街の分析のまとまり）
hn validate
hn knowledge                  # 知見の一覧（○ 成り立つ文脈 / × 崩れる文脈）
hn why kn.childcare_rate.increases.birth_rate
hn propagate --collection my_city --draft   # 転移・推論の下書きを inbox に
hn accept --all --reason "…"
hn export                     # build/ に graph.json・prov.jsonld・viz.html
hn schema                     # JSON Schema
```

承認台帳を使うときは `hypnet.yaml` の `rules.ledger` を `error` にし、`hn keygen` で署名鍵を作る
（秘密鍵は `~/.config/hypnet/` に置かれる）。手で書いたファイルは `hn ledger adopt --reason "…"` で取り込む。

## 例: 手書きの読書ネットワーク

`examples/reading_network/` は、分析の道具を一切使わずに YAML だけで組んだネットワーク。
同じノルウェーを対象にした2つの研究（Rindfuss et al. 2010 と Lappegård 2008）で、保育と出生の関係の結論が分かれており、
hypnet はこれを一つにまとめず「条件つき」の知見として扱う。自分の分析のまとまり（`my_city`）が道路網の中心性を測れると
宣言しているので、Porta et al. 2009 の主張が転移の下書きとして流れ込む。

文献の主張の要約は要旨に基づく簡略化。引用するときは原典を確認すること。

## プロデューサー

- 手作業: YAML を書く（この README の例）
- hypograph: 分析ハーネス（案件・ステップ・探索／確認の分割・検定計画の固定・確認データの封印）。`hg export-net` で hypnet の YAML を書き出し、内部では `hg/netbridge.py` で hypnet を直接使う
- 自作: [FORMAT.md](FORMAT.md) の「プロデューサーを書くには」

## ライセンス

MIT

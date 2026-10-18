"""ネットワークの書き出し: node-link JSON、PROV-O JSON-LD、可視化 HTML。"""
from __future__ import annotations

import json
from pathlib import Path

import networkx as nx

from .knowledge import LABEL, derive
from .net import Net

NS = "https://example.org/hypnet#"   # 公開時は自分の名前空間に置き換える


def build_graph(net: Net) -> nx.MultiDiGraph:
    g = nx.MultiDiGraph()
    for n in net.nodes.values():
        attrs = {"kind": n.kind, "title": getattr(n, "title", "") or getattr(n, "statement", "") or n.id}
        for key in ("collection", "status", "origin", "role"):
            if hasattr(n, key):
                attrs[key] = getattr(n, key)
        g.add_node(n.id, **attrs)

    def edge(u, v, rel):
        if u in g and v in g:
            g.add_edge(u, v, key=rel, rel=rel)

    for h in net.hypotheses:
        edge(h.id, h.collection, "part_of")
        if h.born_from:
            edge(h.id, h.born_from, "born_from")
        for p in h.parents:
            edge(h.id, p, "derived_from")
        for e in h.evidence:
            edge(e.by, h.id, e.effect)
        if h.claim:
            edge(h.id, h.claim.subject, "claim_subject")
            edge(h.id, h.claim.object, "claim_object")
            for v in h.claim.given:
                edge(h.id, v, "claim_given")
    for k in derive(net).values():
        g.add_node(k.id, kind="knowledge", title=k.text, state=k.state)
        for h in k.members:
            edge(h, k.id, "member_of")
        edge(k.id, k.subject, "about_subject")
        edge(k.id, k.object, "about_object")
        for o in k.opposed_by:
            edge(k.id, o, "opposes")
    return g


def prov_jsonld(net: Net, base: str = NS) -> dict:
    graph = []
    for n in net.nodes.values():
        item: dict = {"@id": f"hn:{n.id}", "rdfs:label": getattr(n, "title", "") or getattr(n, "statement", n.id)}
        if n.kind == "hypothesis":
            item.update({"@type": ["prov:Entity", "hn:Hypothesis"], "hn:status": n.status, "hn:origin": n.origin,
                         "dcterms:isPartOf": {"@id": f"hn:{n.collection}"},
                         "prov:wasDerivedFrom": [{"@id": f"hn:{p}"} for p in n.parents]})
            if n.born_from:
                item["hn:bornFrom"] = {"@id": f"hn:{n.born_from}"}
            if n.claim:
                c = n.claim
                item["hn:claim"] = {"hn:subject": {"@id": f"hn:{c.subject}"}, "hn:relation": f"hn:{c.relation}",
                                    "hn:object": {"@id": f"hn:{c.object}"},
                                    "hn:given": [{"@id": f"hn:{v}"} for v in c.given],
                                    "hn:place": c.context.place, "hn:period": c.context.period,
                                    "hn:population": c.context.population}
            item["hn:evidence"] = [{"hn:by": {"@id": f"hn:{e.by}"}, "hn:kind": e.kind, "hn:effect": e.effect,
                                    **({"hn:metric": e.metric} if e.metric else {}),
                                    **({"hn:ref": e.ref} if e.ref else {})} for e in n.evidence]
        elif n.kind == "variable":
            item.update({"@type": "hn:Variable", "owl:sameAs": [{"@id": s} for s in n.same_as]})
        elif n.kind == "source":
            item.update({"@type": ["prov:Entity", "dcterms:BibliographicResource"], "dcterms:creator": n.authors,
                         "dcterms:date": n.year, "dcterms:identifier": n.doi or n.url})
        elif n.kind == "collection":
            item.update({"@type": "hn:Collection", "hn:role": n.role,
                         **({"hn:purpose": n.purpose.ends} if n.purpose else {})})
        else:
            item["@type"] = "hn:KnowledgeNote"
        graph.append(item)
    for k in derive(net).values():
        graph.append({"@id": f"hn:{k.id}", "@type": "hn:Knowledge", "rdfs:label": k.text, "hn:state": k.state,
                      "hn:stateLabel": LABEL[k.state], "hn:subject": {"@id": f"hn:{k.subject}"},
                      "hn:relation": f"hn:{k.relation}", "hn:object": {"@id": f"hn:{k.object}"},
                      "hn:given": [{"@id": f"hn:{v}"} for v in k.given],
                      "hn:holdsIn": sorted(k.holds_in), "hn:failsIn": sorted(k.fails_in),
                      "prov:wasDerivedFrom": [{"@id": f"hn:{h}"} for h in k.members], "hn:endorsed": k.endorsed})
    return {"@context": {"prov": "http://www.w3.org/ns/prov#", "dcterms": "http://purl.org/dc/terms/",
                         "owl": "http://www.w3.org/2002/07/owl#", "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
                         "hn": base, "wikidata": "http://www.wikidata.org/entity/"},
            "@graph": graph}


def write_json(g: nx.MultiDiGraph, path: Path) -> None:
    path.write_text(json.dumps(nx.node_link_data(g, edges="edges"), ensure_ascii=False, indent=1, default=str),
                    encoding="utf-8")


VIZ_TEMPLATE = """<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Hypothesis Graph</title>
<style>
:root{--bg:#fbfaf7;--fg:#1d1d1b;--muted:#6b6a64;--line:#d9d6cc;--panel:#ffffff}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#161614;--fg:#ecebe6;--muted:#9a988f;--line:#3a3934;--panel:#1f1f1c}}
:root[data-theme=dark]{--bg:#161614;--fg:#ecebe6;--muted:#9a988f;--line:#3a3934;--panel:#1f1f1c}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,"Hiragino Sans",sans-serif}
header{padding:12px 16px;border-bottom:1px solid var(--line);display:flex;gap:12px;flex-wrap:wrap;align-items:baseline}
h1{font-size:16px;margin:0}.legend{display:flex;gap:10px;flex-wrap:wrap;color:var(--muted);font-size:12px}
.dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:4px;vertical-align:-1px}
#cy{height:calc(100vh - 160px);min-height:360px}
#info{border-top:1px solid var(--line);background:var(--panel);padding:10px 16px;min-height:96px;font-size:13px;white-space:pre-wrap}
select{background:var(--panel);color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:2px 6px}
</style></head><body>
<header><h1>仮説の系譜グラフ</h1>
<label>まとまり <select id="case"><option value="">すべて</option></select></label>
<div class="legend" id="legend"></div></header>
<div id="cy"></div><div id="info">ノードをタップすると詳細</div>
<script src="https://cdn.jsdelivr.net/npm/cytoscape@3/dist/cytoscape.min.js"></script>
<script>
const DATA = __DATA__;
const STATUS = {draft:"#b9b6ab",proposed:"#8fa8c8",testing:"#e0b04a",supported:"#3f9c6b",refuted:"#c8553d",abandoned:"#8c6d62",superseded:"#9b8fb5"};
const KIND = {case:"#555",collection:"#555",step:"#4b6584",dataset:"#7a8b6f",variable:"#a0789a",pattern:"#b58a3c",source:"#2f7f86",protocol:"#c46a2b",knowledge:"#1f6f50"};
const leg=document.getElementById('legend');
for(const [k,c] of Object.entries(STATUS)) leg.insertAdjacentHTML('beforeend',`<span><span class="dot" style="background:${c}"></span>${k}</span>`);
const els=[];
for(const n of DATA.nodes){els.push({data:{id:n.id,label:n.kind==='hypothesis'?n.id.split('.').pop():(n.kind==='knowledge'?n.id.slice(3):n.id),full:n,kind:n.kind,case:n.case||n.collection||'',
  color:n.kind==='hypothesis'?(STATUS[n.status]||'#999'):(KIND[n.kind]||'#999')}});}
DATA.edges.forEach((e,i)=>els.push({data:{id:'e'+i,source:e.source,target:e.target,rel:e.rel}}));
const cases=[...new Set(DATA.nodes.filter(n=>n.kind==='case'||n.kind==='collection').map(n=>n.id))];
const sel=document.getElementById('case');cases.forEach(c=>sel.insertAdjacentHTML('beforeend',`<option>${c}</option>`));
const fg=getComputedStyle(document.documentElement).getPropertyValue('--fg');
const cy=cytoscape({container:document.getElementById('cy'),elements:els,wheelSensitivity:0.3,
 style:[{selector:'node',style:{'background-color':'data(color)','label':'data(label)','font-size':9,'color':fg,'text-valign':'bottom','width':14,'height':14}},
  {selector:'node[kind="step"]',style:{shape:'round-rectangle',width:22}},
  {selector:'node[kind="hypothesis"]',style:{width:18,height:18}},
  {selector:'node[kind="source"]',style:{shape:'tag',width:16,height:16}},
  {selector:'node[kind="protocol"]',style:{shape:'vee',width:16,height:16}},
  {selector:'node[kind="knowledge"]',style:{shape:'star',width:22,height:22,'font-size':8}},
  {selector:'edge[rel="member_of"]',style:{'line-color':'#1f6f50','target-arrow-color':'#1f6f50',opacity:.4}},
  {selector:'node[kind="pattern"],node[kind="variable"],node[kind="dataset"]',style:{shape:'diamond',width:11,height:11,'font-size':8}},
  {selector:'node[kind="case"],node[kind="collection"]',style:{shape:'hexagon',width:24,height:24}},
  {selector:'edge',style:{width:1,'line-color':'#aaa','target-arrow-shape':'triangle','target-arrow-color':'#aaa','arrow-scale':0.6,'curve-style':'bezier',opacity:.6}},
  {selector:'edge[rel="supports"]',style:{'line-color':STATUS.supported,'target-arrow-color':STATUS.supported,width:2}},
  {selector:'edge[rel="refutes"]',style:{'line-color':STATUS.refuted,'target-arrow-color':STATUS.refuted,width:2}},
  {selector:'edge[rel="derived_from"]',style:{'line-style':'dashed',width:1.5}},
  {selector:'edge[rel="replicates"],edge[rel="fails_in"],edge[rel="contradicts"]',style:{'line-color':'#a0789a','target-arrow-color':'#a0789a',width:2.5}},
  {selector:'.dim',style:{opacity:.08}}],
 layout:{name:'cose',animate:false,nodeRepulsion:9000,idealEdgeLength:60}});
cy.on('tap','node',ev=>{const n=ev.target.data('full');const lines=[n.id+'  ('+n.kind+')'];
 for(const k of ['title','status','state','origin','stage','case','collection']) if(n[k]) lines.push(k+': '+n[k]);
 lines.push('');cy.edges().filter(e=>e.data('source')===n.id).forEach(e=>lines.push('→ '+e.data('rel')+' '+e.data('target')));
 cy.edges().filter(e=>e.data('target')===n.id).forEach(e=>lines.push('← '+e.data('rel')+' '+e.data('source')));
 document.getElementById('info').textContent=lines.join('\\n');});
sel.onchange=()=>{const c=sel.value;cy.elements().removeClass('dim');if(!c)return;
 const keep=cy.nodes().filter(n=>n.data('case')===c||n.id()===c);const nb=keep.closedNeighborhood();
 cy.elements().not(nb).addClass('dim');};
</script></body></html>"""


def write_viz(g: nx.MultiDiGraph, path: Path) -> None:
    nodes = [{"id": n, **{k: v for k, v in d.items()}} for n, d in g.nodes(data=True)]
    edges = [{"source": u, "target": v, "rel": d.get("rel", k)} for u, v, k, d in g.edges(keys=True, data=True)]
    payload = json.dumps({"nodes": nodes, "edges": edges}, ensure_ascii=False, default=str)
    path.write_text(VIZ_TEMPLATE.replace("__DATA__", payload.replace("</", "<\\/")), encoding="utf-8")

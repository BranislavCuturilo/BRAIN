#!/usr/bin/env python3
"""One page holding every level, with search, filters and UML-shaped blocks.

**Why the layout moved to the browser.** The first renderer computed x/y in
Python and baked them into the SVG, which is fine for one static picture and
useless the moment a filter hides half the nodes -- the survivors keep the
positions they had when their neighbours existed, and the diagram becomes a
field of holes. Filtering and layout are the same operation, so both run in the
page.

**This is still not a written page.** `render.py` is code; the JSON comes from
the AST. Run it a thousand times and the file is identical. What changed is
where the arithmetic happens, not who does it.

**Blocks, not dots.** A dot labelled `Location` says nothing. A box whose body
reads `tenant: FK · parent: FK · is_active: Bool` says what the thing IS, and
that density is the entire reason to draw boxes at all -- the same reason a UML
class has three compartments rather than a name.

No CDN, no font fetch, no analytics, no network of any kind. One file.
"""
from __future__ import annotations

import json


KIND_COLOR = {
    "service": "#3b82f6", "store": "#22c55e", "entry": "#f59e0b",
    "cron": "#a855f7", "external": "#ef4444", "model": "#22c55e",
}


def render(data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    p = data.get("project", {})
    return _TEMPLATE.replace("__DATA__", payload) \
                    .replace("__NAME__", _esc(p.get("name", "map"))) \
                    .replace("__DATE__", _esc(p.get("date", "")))


def _esc(s: str) -> str:
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


_TEMPLATE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Map — __NAME__</title>
<style>
:root{color-scheme:dark;
 --bg:#0d1117; --panel:#141922; --line:#232b39; --ink:#e6edf3; --dim:#7d8590;
 --accent:#58a6ff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
 font:12px/1.45 ui-sans-serif,system-ui,"Segoe UI",sans-serif;overflow:hidden}
#app{display:flex;height:100vh}

/* ---------- sidebar ---------- */
aside{width:260px;flex:0 0 260px;background:var(--panel);
 border-right:1px solid var(--line);display:flex;flex-direction:column}
aside h1{margin:0;padding:12px 14px 4px;font-size:13px;font-weight:600}
aside .sub{padding:0 14px 10px;color:var(--dim);font-size:11px}
aside section{padding:10px 14px;border-top:1px solid var(--line)}
aside label.h{display:block;font-size:10px;letter-spacing:.08em;
 text-transform:uppercase;color:var(--dim);margin-bottom:6px}
input[type=search],select{width:100%;padding:6px 8px;background:#0d1117;
 color:var(--ink);border:1px solid var(--line);border-radius:6px;font:inherit}
input[type=search]:focus,select:focus{outline:0;border-color:var(--accent)}
.chips{display:flex;flex-wrap:wrap;gap:5px}
.chip{display:flex;align-items:center;gap:5px;padding:3px 7px;border-radius:11px;
 border:1px solid var(--line);cursor:pointer;user-select:none;font-size:11px}
.chip.off{opacity:.35}
.chip i{width:8px;height:8px;border-radius:50%;display:inline-block}
.row{display:flex;justify-content:space-between;align-items:center;gap:8px;
 margin-top:7px;color:var(--dim);font-size:11px}
input[type=range]{width:110px}
#hits{flex:1;overflow:auto;border-top:1px solid var(--line)}
#hits div{padding:5px 14px;cursor:pointer;border-bottom:1px solid #1a2029;
 white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#hits div:hover{background:#1a2029}
#hits .k{color:var(--dim);font-size:10px}

/* ---------- canvas ---------- */
main{flex:1;position:relative;overflow:auto}
#stage{position:relative;transform-origin:0 0}
svg{position:absolute;inset:0;pointer-events:none;overflow:visible}
path.edge{fill:none;stroke:#2c3543;stroke-width:1.1}
path.edge.on{stroke:var(--accent);stroke-width:2}
path.edge.mute{opacity:.06}
line.band{stroke:#1e2632;stroke-width:1}
text.bandlbl{fill:#4d5666;font-size:9.5px;letter-spacing:.14em;
 text-transform:uppercase}

.node{position:absolute;width:196px;background:#161b22;border:1px solid #2b3442;
 border-radius:7px;overflow:hidden;cursor:pointer;transition:opacity .12s}
.node.mute{opacity:.13}
.node.on{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent)}
.node .hd{display:flex;align-items:center;gap:6px;padding:5px 8px;
 border-bottom:1px solid #232b39;background:#1b2230}
.node .hd b{font-weight:600;font-size:12px;white-space:nowrap;overflow:hidden;
 text-overflow:ellipsis;flex:1}
.node .hd .deg{color:var(--dim);font-size:10px}
.node .hd i{width:7px;height:7px;border-radius:50%;flex:0 0 auto}
.node .sub{padding:3px 8px;color:var(--dim);font-size:10px;
 white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.node ul{margin:0;padding:2px 0 4px;list-style:none;border-top:1px solid #1f2733}
.node li{padding:1px 8px;font-size:10.5px;color:#b6c2cf;
 white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.node li mark{background:#3b2f00;color:#ffd479;padding:0 1px}
.node .more{padding:1px 8px;color:var(--dim);font-size:10px}

#detail{position:absolute;right:14px;bottom:14px;width:320px;max-height:44vh;
 overflow:auto;background:var(--panel);border:1px solid var(--line);
 border-radius:8px;padding:11px 13px;display:none}
#detail h3{margin:0 0 4px;font-size:13px}
#detail .r{color:var(--dim);font-size:11px;margin-bottom:7px;word-break:break-all}
#detail ul{margin:6px 0 0;padding-left:16px}
#detail li{font-size:11px}
#detail .x{position:absolute;top:8px;right:10px;color:var(--dim);cursor:pointer}
#empty{position:absolute;inset:0;display:grid;place-items:center;color:var(--dim)}
kbd{background:#1b2230;border:1px solid var(--line);border-radius:4px;
 padding:1px 5px;font:inherit;font-size:10px}
</style></head><body><div id="app">

<aside>
  <h1>__NAME__</h1>
  <div class="sub">__DATE__ · generated locally · nothing uploaded</div>

  <section>
    <label class="h" for="lvl">Level</label>
    <input type="search" id="lvlq" placeholder="filter levels — app name…"
           autocomplete="off" style="margin-bottom:6px">
    <select id="lvl" size="1"></select>
    <div class="row"><span id="lvlcount"></span></div>
    <div class="row"><span id="lvlinfo"></span></div>
  </section>

  <section>
    <label class="h" for="q">Search — name, member, path</label>
    <input type="search" id="q" placeholder="tenant, postal_code, views.py…"
           autocomplete="off">
    <div class="row"><span id="qinfo">every level is searched</span></div>
  </section>

  <section>
    <label class="h">Kind</label>
    <div class="chips" id="kinds"></div>
    <div class="row"><label for="deg">min. connections</label>
      <input type="range" id="deg" min="0" max="10" value="0"><b id="degv">0</b></div>
    <div class="row"><label for="iso">hide unconnected</label>
      <input type="checkbox" id="iso"></div>
    <div class="row"><label for="mem">show members</label>
      <input type="checkbox" id="mem" checked></div>
  </section>

  <div id="hits"></div>
</aside>

<main id="main">
  <div id="stage"><svg id="wires"></svg></div>
  <div id="empty" hidden>nothing matches</div>
  <div id="detail"><span class="x" onclick="hideDetail()">✕</span>
    <h3 id="dt"></h3><div class="r" id="dr"></div><div id="dd"></div></div>
</main></div>

<script>
const DATA = __DATA__;
const COLOR = {service:"#3b82f6",store:"#22c55e",entry:"#f59e0b",
               cron:"#a855f7",external:"#ef4444"};
const $ = s => document.querySelector(s);
const stage = $("#stage"), wires = $("#wires");
let level = DATA.levels[0], kindsOff = new Set(), positions = new Map();

/* ---------- level picker: grouped, because there are dozens ---------- */
function fillLevels(filter){
  const sel=$("#lvl"); sel.innerHTML="";
  const f=(filter||"").trim().toLowerCase();
  const groups=new Map();
  DATA.levels.forEach((L,i)=>{
    if(f && !(`${L.title} ${L.app||""}`.toLowerCase().includes(f))) return;
    const g = L.app || "Overview";
    if(!groups.has(g)) groups.set(g,[]);
    groups.get(g).push([i,L]);
  });
  // Overview first, then apps alphabetically -- an app is found by name, and
  // the two whole-project levels are what you open first.
  const keys=[...groups.keys()].sort((a,b)=>
    a==="Overview"?-1:b==="Overview"?1:a.localeCompare(b));
  keys.forEach(g=>{
    const og=document.createElement("optgroup"); og.label=g;
    groups.get(g).forEach(([i,L])=>{
      const o=document.createElement("option"); o.value=i;
      o.textContent=`${L.short||L.title}  (${L.graph.nodes.length})`;
      if(L===level) o.selected=true;
      og.append(o);
    });
    sel.append(og);
  });
  $("#lvlcount").textContent =
    `${sel.querySelectorAll("option").length} of ${DATA.levels.length} levels`;
}
$("#lvl").onchange = e => { level = DATA.levels[+e.target.value]; build(); };
$("#lvlq").addEventListener("input", e => fillLevels(e.target.value));

/* ---------- kind chips ---------- */
function chips(){
  const box=$("#kinds"); box.innerHTML="";
  const seen=[...new Set(level.graph.nodes.map(n=>n.kind))];
  seen.forEach(k=>{
    const c=document.createElement("span");
    c.className="chip"+(kindsOff.has(k)?" off":"");
    c.innerHTML=`<i style="background:${COLOR[k]||"#888"}"></i>${k}`;
    c.onclick=()=>{kindsOff.has(k)?kindsOff.delete(k):kindsOff.add(k); chips(); draw();};
    box.append(c);
  });
}

/* ---------- filtering ---------- */
function matches(n,q){
  if(!q) return true;
  const hay=[n.label,n.sub,n.sourceRef,n.detail,...(n.members||[])]
    .filter(Boolean).join(" ").toLowerCase();
  return q.split(/\s+/).filter(Boolean).every(w=>hay.includes(w));
}
function degrees(){
  const d=new Map();
  level.graph.edges.forEach(e=>{
    d.set(e.to,(d.get(e.to)||0)+1); d.set(e.from,(d.get(e.from)||0)+0.0001);
  });
  return d;
}
function visible(){
  const q=$("#q").value.trim().toLowerCase();
  const min=+$("#deg").value, iso=$("#iso").checked, deg=degrees();
  return level.graph.nodes.filter(n=>{
    if(kindsOff.has(n.kind)) return false;
    if(Math.floor(deg.get(n.id)||0) < min) return false;
    if(iso && !deg.has(n.id)) return false;
    return matches(n,q);
  });
}

/* ---------- layout: bands by kind, WRAPPED to the height of the screen ----
 * The first version stacked every node of a kind in one column, so forty
 * services became a column forty boxes tall and the map was a scroll rather
 * than a diagram. A band now wraps into as many sub-columns as it needs to
 * fit the viewport, which trades vertical scrolling for horizontal -- the
 * right trade, because a diagram is read across and a list is read down.
 * ------------------------------------------------------------------------ */
const ORDER=["entry","cron","service","store","external"];
const COLW=226, GAP=14, PADT=30;

function nodeHeight(n,showMem){
  const m = showMem ? Math.min((n.members||[]).length,8) : 0;
  return 26 + (n.sub?15:0) + m*14 + (m?6:0);
}

function layout(nodes){
  const deg=degrees(), showMem=$("#mem").checked;
  const budget = Math.max(420, window.innerHeight - 90);

  const bands=new Map();
  nodes.forEach(n=>{
    const k = ORDER.indexOf(n.kind)<0 ? 2 : ORDER.indexOf(n.kind);
    if(!bands.has(k)) bands.set(k,[]);
    bands.get(k).push(n);
  });

  positions=new Map();
  let x=24, maxY=0;

  [...bands.keys()].sort((a,b)=>a-b).forEach(k=>{
    const bandX0=x;
    const list=bands.get(k).sort((a,b)=>
      (Math.floor(deg.get(b.id)||0)-Math.floor(deg.get(a.id)||0))
      || a.label.localeCompare(b.label));

    // Pre-measure so the band is split into columns of roughly equal height
    // rather than one full column and one stub.
    const heights=list.map(n=>nodeHeight(n,showMem)+GAP);
    const total=heights.reduce((s,h)=>s+h,0);
    const cols=Math.max(1,Math.ceil(total/budget));
    const target=total/cols;

    let y=PADT, used=0, col=0;
    list.forEach((n,i)=>{
      if(used>0 && used+heights[i]>target && col<cols-1){
        col++; x+=COLW; y=PADT; used=0;
      }
      const h=nodeHeight(n,showMem);
      positions.set(n.id,{x,y,h});
      y+=h+GAP; used+=heights[i];
      maxY=Math.max(maxY,y);
    });
    // Record the band so it can be drawn. Wrapping created the need: once
    // `service` runs across three columns there is nothing to say where it
    // ends and `store` begins, and the reader loses the one thing the column
    // order was carrying.
    bands_drawn.push({kind:ORDER[k]||"other", x0:bandX0, x1:x+COLW, cols:cols});
    x+=COLW+26;                       // a wider gutter BETWEEN kinds
  });

  stage.style.width=(x+40)+"px"; stage.style.height=(maxY+40)+"px";
  wires.setAttribute("width",x+40); wires.setAttribute("height",maxY+40);
}
let bands_drawn=[];

/* ---------- drawing ---------- */
function hl(txt,q){
  if(!q) return esc(txt);
  let out=esc(txt);
  q.split(/\s+/).filter(Boolean).forEach(w=>{
    out=out.replace(new RegExp("("+w.replace(/[.*+?^${}()|[\]\\]/g,"\\$&")+")","ig"),
                    "<mark>$1</mark>");
  });
  return out;
}
const esc=s=>String(s??"").replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));

function draw(){
  bands_drawn=[];
  const nodes=visible(), q=$("#q").value.trim().toLowerCase();
  $("#empty").hidden = nodes.length>0;
  layout(nodes);
  const keep=new Set(nodes.map(n=>n.id)), deg=degrees(), showMem=$("#mem").checked;

  stage.querySelectorAll(".node").forEach(e=>e.remove());
  nodes.forEach(n=>{
    const p=positions.get(n.id), d=document.createElement("div");
    d.className="node"; d.dataset.id=n.id;
    d.style.left=p.x+"px"; d.style.top=p.y+"px";
    const dg=Math.floor(deg.get(n.id)||0);
    const mem=showMem?(n.members||[]).slice(0,8):[];
    d.innerHTML=
      `<div class="hd"><i style="background:${COLOR[n.kind]||"#888"}"></i>`+
      `<b>${hl(n.label,q)}</b>${dg?`<span class="deg">←${dg}</span>`:""}</div>`+
      (n.sub?`<div class="sub">${hl(n.sub,q)}</div>`:"")+
      (mem.length?`<ul>${mem.map(m=>`<li>${hl(m,q)}</li>`).join("")}</ul>`:"")+
      ((n.members||[]).length>8&&showMem
        ?`<div class="more">+${n.members.length-8} more</div>`:"");
    d.onclick=ev=>{ev.stopPropagation(); select(n);};
    d.onmouseenter=()=>trace(n.id);
    d.onmouseleave=()=>trace(null);
    stage.append(d);
  });

  wires.innerHTML="";
  bands_drawn.forEach((b,i)=>{
    if(i>0){
      const ln=document.createElementNS("http://www.w3.org/2000/svg","line");
      ln.setAttribute("x1",b.x0-13); ln.setAttribute("x2",b.x0-13);
      ln.setAttribute("y1",8); ln.setAttribute("y2",stage.offsetHeight-8);
      ln.setAttribute("class","band"); wires.append(ln);
    }
    const tx=document.createElementNS("http://www.w3.org/2000/svg","text");
    tx.setAttribute("x",b.x0+2); tx.setAttribute("y",14);
    tx.setAttribute("class","bandlbl");
    tx.textContent=b.kind+(b.cols>1?"  ·  "+b.cols+" cols":"");
    wires.append(tx);
  });
  level.graph.edges.filter(e=>keep.has(e.from)&&keep.has(e.to)).forEach(e=>{
    const a=positions.get(e.from), b=positions.get(e.to);
    const x1=a.x+196, y1=a.y+16, x2=b.x, y2=b.y+16, mx=(x1+x2)/2;
    const path=document.createElementNS("http://www.w3.org/2000/svg","path");
    path.setAttribute("d",`M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`);
    path.setAttribute("class","edge");
    path.dataset.a=e.from; path.dataset.b=e.to;
    if(e.label){const ti=document.createElementNS("http://www.w3.org/2000/svg","title");
      ti.textContent=e.label; path.append(ti);}
    wires.append(path);
  });

  $("#lvlinfo").textContent =
    `${nodes.length} of ${level.graph.nodes.length} shown`+
    (level.dropped?` · ${level.dropped} beyond the cap`:"")+
    (level.caveat?" · approximate":"");
  hits(q);
}

/* ---------- trace: what does this touch ---------- */
function trace(id){
  const near=new Set(id?[id]:[]);
  wires.querySelectorAll(".edge").forEach(p=>{
    const hit=id&&(p.dataset.a===id||p.dataset.b===id);
    p.classList.toggle("on",!!hit);
    p.classList.toggle("mute",!!id&&!hit);
    if(hit){near.add(p.dataset.a);near.add(p.dataset.b);}
  });
  stage.querySelectorAll(".node").forEach(n=>
    n.classList.toggle("mute",!!id&&!near.has(n.dataset.id)));
}

/* ---------- detail ---------- */
function select(n){
  $("#dt").textContent=n.label;
  $("#dr").textContent=[n.kind,n.sub,n.sourceRef].filter(Boolean).join("  ·  ");
  const ins=level.graph.edges.filter(e=>e.to===n.id).map(e=>e.from);
  const outs=level.graph.edges.filter(e=>e.from===n.id).map(e=>e.to);
  $("#dd").innerHTML=
    (n.detail?`<div>${esc(n.detail)}</div>`:"")+
    ((n.members||[]).length?`<ul>${n.members.map(m=>`<li>${esc(m)}</li>`).join("")}</ul>`:"")+
    (ins.length?`<div class="r" style="margin-top:8px">used by ${ins.length}: ${esc(ins.slice(0,10).join(", "))}</div>`:"")+
    (outs.length?`<div class="r">uses ${outs.length}: ${esc(outs.slice(0,10).join(", "))}</div>`:"");
  $("#detail").style.display="block";
  trace(n.id);
}
function hideDetail(){$("#detail").style.display="none";trace(null);}

/* ---------- search across EVERY level ---------- */
function hits(q){
  const box=$("#hits"); box.innerHTML="";
  if(!q){ $("#qinfo").textContent="every level is searched"; return; }
  let n=0;
  DATA.levels.forEach((L,i)=>{
    L.graph.nodes.filter(x=>matches(x,q)).slice(0,40).forEach(x=>{
      if(n++>120) return;
      const d=document.createElement("div");
      d.innerHTML=`${esc(x.label)} <span class="k">— ${esc(L.title)}</span>`;
      d.onclick=()=>{ $("#lvl").value=i; level=L; chips(); draw();
                      const el=stage.querySelector(`[data-id="${CSS.escape(x.id)}"]`);
                      if(el){el.scrollIntoView({block:"center",inline:"center"});
                             select(x);} };
      box.append(d);
    });
  });
  $("#qinfo").textContent=`${n} match${n===1?"":"es"} across ${DATA.levels.length} levels`;
}

/* ---------- wiring ---------- */
["#q","#deg","#iso","#mem"].forEach(s=>{
  $(s).addEventListener("input",()=>{ $("#degv").textContent=$("#deg").value; draw(); });
});
$("#main").onclick=hideDetail;
let rt; addEventListener("resize",()=>{clearTimeout(rt);rt=setTimeout(draw,150);});
document.addEventListener("keydown",e=>{
  if(e.key==="/"&&document.activeElement!==$("#q")){e.preventDefault();$("#q").focus();}
  if(e.key==="Escape"){$("#q").value="";hideDetail();draw();}
});
function build(){ fillLevels($("#lvlq").value); chips(); draw(); }
build();
</script></body></html>"""

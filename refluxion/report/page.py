"""Render a :class:`Report` as one self-contained, offline HTML page."""

from __future__ import annotations

import html
import json

import numpy as np

from .blocks import block_extras

CSS = r"""
:root{
  color-scheme: light;
  --bg:#f6f5f2; --surface:#fcfcfb; --surface-2:#f0efeb; --wash:#e9e8e3;
  --ink:#141413; --ink-2:#4f4e4a; --muted:#86847e; --line:#e1e0d9; --axis:#c3c2b7;
  --accent:#1f4fa0; --accent-soft:#e3ebf8; --accent-ink:#ffffff;
  --good:#0ca30c; --good-ink:#0a6b0a; --good-soft:#e5f4e5;
  --warn:#c98500; --warn-ink:#8a5a00; --warn-soft:#fbf1dc;
  --bad:#d03b3b; --bad-ink:#a02525; --bad-soft:#f9e3e3;
  --c0:#2a78d6; --c1:#eb6834; --c2:#1baf7a; --c3:#eda100; --c4:#e87ba4; --c5:#008300; --c6:#4a3aa7; --c7:#e34948;
  --shadow:0 1px 2px rgba(20,20,19,.05),0 2px 12px rgba(20,20,19,.04);
  --mono:ui-monospace,"Cascadia Code","Cascadia Mono",Consolas,"SF Mono",Menlo,monospace;
  --sans:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){
  color-scheme: dark;
  --bg:#0f0f0e; --surface:#1a1a19; --surface-2:#222221; --wash:#2a2a28;
  --ink:#f4f3ef; --ink-2:#c3c2b7; --muted:#8f8d86; --line:#2c2c2a; --axis:#3d3d3a;
  --accent:#86b6ef; --accent-soft:#1d2a3d; --accent-ink:#0b0b0b;
  --good:#2fbf2f; --good-ink:#7fd67f; --good-soft:#15291a; --warn:#e3a21a; --warn-ink:#f0c46a; --warn-soft:#2d2412;
  --bad:#e66767; --bad-ink:#f09a9a; --bad-soft:#321a1a;
  --c0:#3987e5; --c1:#d95926; --c2:#199e70; --c3:#c98500; --c4:#d55181; --c5:#008300; --c6:#9085e9; --c7:#e66767;
  --shadow:none;
}}
:root[data-theme="dark"]{
  color-scheme: dark;
  --bg:#0f0f0e; --surface:#1a1a19; --surface-2:#222221; --wash:#2a2a28;
  --ink:#f4f3ef; --ink-2:#c3c2b7; --muted:#8f8d86; --line:#2c2c2a; --axis:#3d3d3a;
  --accent:#86b6ef; --accent-soft:#1d2a3d; --accent-ink:#0b0b0b;
  --good:#2fbf2f; --good-ink:#7fd67f; --good-soft:#15291a; --warn:#e3a21a; --warn-ink:#f0c46a; --warn-soft:#2d2412;
  --bad:#e66767; --bad-ink:#f09a9a; --bad-soft:#321a1a;
  --c0:#3987e5; --c1:#d95926; --c2:#199e70; --c3:#c98500; --c4:#d55181; --c5:#008300; --c6:#9085e9; --c7:#e66767;
  --shadow:none;
}
*{box-sizing:border-box}
[hidden]{display:none!important}
html{scroll-behavior:smooth;scroll-padding-top:76px}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 var(--sans);-webkit-font-smoothing:antialiased}
a{color:var(--accent)}
code{font:0.9em var(--mono);background:var(--surface-2);padding:1px 5px;border-radius:5px}
pre{font:12.5px/1.5 var(--mono);background:var(--surface-2);padding:14px 16px;border-radius:10px;overflow:auto;margin:10px 0}
.top{position:sticky;top:0;z-index:20;display:flex;align-items:center;gap:14px;padding:10px 20px;
  background:color-mix(in srgb,var(--bg) 86%,transparent);backdrop-filter:blur(10px);border-bottom:1px solid var(--line)}
.brand{display:flex;align-items:center;gap:9px;font-weight:650;letter-spacing:-.01em;white-space:nowrap}
.logo{width:22px;height:22px}
.brand small{font-weight:500;color:var(--muted)}
.top .grow{flex:1}
.ctl{display:flex;align-items:center;gap:6px;font-size:13px;color:var(--ink-2)}
select,button{font:inherit;font-size:13px;color:var(--ink);background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:5px 9px;cursor:pointer}
button:hover,select:hover{border-color:var(--axis)}
button:focus-visible,select:focus-visible,.bk:focus-visible,.lgi:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.layout{display:grid;grid-template-columns:220px minmax(0,1fr);gap:28px;max-width:1320px;margin:0 auto;padding:24px 20px 60px}
nav.toc{position:sticky;top:76px;align-self:start;max-height:calc(100vh - 96px);overflow:auto;font-size:13.5px}
nav.toc a{display:block;padding:5px 10px;border-radius:7px;color:var(--ink-2);text-decoration:none;border-left:2px solid transparent}
nav.toc a:hover{background:var(--surface-2);color:var(--ink)}
nav.toc a.on{color:var(--accent);border-left-color:var(--accent);background:var(--accent-soft)}
nav.toc .lbl{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);padding:12px 10px 4px}
main{min-width:0}
header.hero{margin:4px 0 22px}
header.hero h1{font-size:30px;line-height:1.15;letter-spacing:-.02em;margin:0 0 6px;text-wrap:balance}
header.hero .sub{color:var(--ink-2);font-size:16px;margin:0 0 12px;max-width:75ch}
.chips{display:flex;flex-wrap:wrap;gap:8px}
.chip{font-size:12.5px;padding:3px 10px;border-radius:999px;background:var(--surface);border:1px solid var(--line);color:var(--ink-2)}
.chip b{color:var(--ink);font-weight:600}
.chip.good{background:var(--good-soft);border-color:transparent;color:var(--good-ink)}
.chip.bad{background:var(--bad-soft);border-color:transparent;color:var(--bad-ink)}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:12px;margin:0 0 22px}
.kpi{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:14px 16px;box-shadow:var(--shadow);position:relative}
.kpi .l{font-size:12.5px;color:var(--ink-2)}
.kpi .v{font-size:25px;font-weight:650;letter-spacing:-.02em;margin-top:3px}
.kpi .v small{font-size:13px;font-weight:500;color:var(--ink-2);margin-left:3px}
.kpi .t{font-size:12.5px;margin-top:5px;display:flex;align-items:center;gap:6px;color:var(--ink-2)}
.kpi.good{border-left:4px solid var(--good)} .kpi.bad{border-left:4px solid var(--bad)}
.status{display:inline-flex;align-items:center;gap:5px;font-weight:600;font-size:12px;padding:1px 8px;border-radius:999px}
.status.good{background:var(--good-soft);color:var(--good-ink)} .status.bad{background:var(--bad-soft);color:var(--bad-ink)}
.status.warn{background:var(--warn-soft);color:var(--warn-ink)} .status.neutral{background:var(--surface-2);color:var(--ink-2)}
section.card{background:var(--surface);border:1px solid var(--line);border-radius:16px;padding:20px 22px;margin:0 0 18px;box-shadow:var(--shadow)}
section.card>h2{font-size:19px;letter-spacing:-.01em;margin:0 0 12px;display:flex;align-items:center;gap:10px}
section.card>h2 .n{font:600 12px var(--mono);color:var(--muted);background:var(--surface-2);padding:2px 7px;border-radius:6px}
section.card p{max-width:78ch}
h3,h4,h5{margin:18px 0 8px;letter-spacing:-.005em}
.tw{overflow-x:auto;margin:10px 0 6px;border:1px solid var(--line);border-radius:10px}
table.data{border-collapse:collapse;width:100%;font-size:13.5px;font-variant-numeric:tabular-nums}
table.data th{background:var(--surface-2);text-align:right;font-weight:600;padding:7px 10px;border-bottom:1px solid var(--line);white-space:nowrap;position:sticky;top:0}
table.data td{padding:6px 10px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
table.data th:first-child,table.data td:first-child{text-align:left;position:sticky;left:0;background:var(--surface);z-index:1}
table.data th:first-child{background:var(--surface-2);z-index:2}
table.data tr:last-child td{border-bottom:none}
table.data tr:hover td{background:var(--surface-2)}
table.data td.txt{text-align:left;white-space:normal}
table.data tr.sub td{font-weight:600;color:var(--ink-2);background:var(--surface-2);text-align:left}
table.data .u{color:var(--muted);font-weight:500;margin-left:4px}
table.data td .u{margin-left:0}
table.data col.hl, table.data td.hl{background:var(--accent-soft)}
.tbar{display:flex;justify-content:space-between;align-items:center;gap:10px;margin-top:14px}
.tbar .cap{font-size:12.5px;color:var(--muted)}
.tbar button{font-size:12px;padding:3px 8px}
details{margin:10px 0}
details>summary{cursor:pointer;color:var(--ink-2);font-size:13.5px;font-weight:600;padding:6px 0;list-style:none}
details>summary::before{content:"\25B8";display:inline-block;margin-right:7px;transition:transform .15s}
details[open]>summary::before{transform:rotate(90deg)}
figure.fig{margin:14px 0 6px}
figure.fig figcaption{font-size:12.5px;color:var(--muted);margin-top:4px}
figure.fig figcaption b{color:var(--ink-2)}
.figs{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:4px 22px}
svg.chart{width:100%;height:auto;max-width:720px;display:block;overflow:visible}
svg.chart .grid{stroke:var(--line);stroke-width:1}
svg.chart .axis{stroke:var(--axis);stroke-width:1}
svg.chart .tick,svg.chart .lab{fill:var(--ink-2);font:12px var(--sans)}
svg.chart .lg{fill:var(--ink-2);font:12px var(--sans)}
svg.chart .ln{fill:none;stroke-width:2;stroke-linejoin:round;stroke-linecap:round}
svg.chart .mk{stroke:var(--surface);stroke-width:2}
svg.chart .ref{stroke:var(--muted);stroke-width:1;stroke-dasharray:4 3}
svg.chart .lgi{cursor:pointer} svg.chart .lgi.off{opacity:.35}
svg.chart .series.off{display:none}
svg.chart .xh{stroke:var(--muted);stroke-width:1}
svg.chart .hot{stroke:var(--surface);stroke-width:2}
.c0{stroke:var(--c0)}.c1{stroke:var(--c1)}.c2{stroke:var(--c2)}.c3{stroke:var(--c3)}.c4{stroke:var(--c4)}.c5{stroke:var(--c5)}.c6{stroke:var(--c6)}.c7{stroke:var(--c7)}
.f0{fill:var(--c0)}.f1{fill:var(--c1)}.f2{fill:var(--c2)}.f3{fill:var(--c3)}.f4{fill:var(--c4)}.f5{fill:var(--c5)}.f6{fill:var(--c6)}.f7{fill:var(--c7)}
.pfdwrap{overflow-x:auto;border:1px solid var(--line);border-radius:12px;background:
  radial-gradient(circle,var(--line) 1px,transparent 1.2px) 0 0/22px 22px, var(--surface);padding:6px}
svg.pfd{display:block;min-width:560px}
svg.pfd .blk{fill:var(--surface-2);stroke:var(--ink-2);stroke-width:1.5;transition:stroke .15s}
svg.pfd .blk-in{fill:none;stroke:var(--muted);stroke-width:1.2}
svg.pfd .bk{cursor:pointer}
svg.pfd .bk:hover .blk,svg.pfd .bk.sel .blk{stroke:var(--accent);stroke-width:2.4}
svg.pfd .bname{fill:var(--ink);font:600 12.5px var(--sans)}
svg.pfd .btype{fill:var(--muted);font:11px var(--sans)}
svg.pfd .st{fill:none;stroke:var(--ink-2);stroke-width:1.6}
svg.pfd .st-tear{stroke-dasharray:6 4}
svg.pfd .hit{fill:none;stroke:transparent;stroke-width:14}
svg.pfd .stg{cursor:pointer}
svg.pfd .stg:hover .st,svg.pfd .stg.sel .st{stroke:var(--accent);stroke-width:2.6}
svg.pfd #arr path{fill:var(--ink-2)}
svg.pfd .pill rect{fill:var(--surface);stroke:var(--axis)}
svg.pfd .pill text{fill:var(--ink);font:11px var(--mono)}
svg.pfd .pill{cursor:pointer}
.legend-note{font-size:12.5px;color:var(--muted);margin:6px 2px 0}
.callout{display:flex;gap:10px;align-items:flex-start;padding:10px 14px;border-radius:10px;margin:12px 0;font-size:14px;background:var(--surface-2)}
.callout .ic{flex:none;width:20px;height:20px;border-radius:50%;display:grid;place-items:center;font:700 12px var(--sans);color:#fff;background:var(--accent);margin-top:1px}
.callout.good{background:var(--good-soft)} .callout.good .ic{background:var(--good)}
.callout.warn{background:var(--warn-soft)} .callout.warn .ic{background:var(--warn)}
.callout.bad{background:var(--bad-soft)} .callout.bad .ic{background:var(--bad)}
.calc{background:var(--surface-2);border-radius:12px;padding:12px 18px;font-size:14px}
.calc pre{background:var(--surface)}
.blockcard{border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin:12px 0;transition:box-shadow .3s,border-color .3s}
.blockcard.flash{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}
.blockcard h3{margin:0 0 4px;display:flex;align-items:center;gap:10px;font-size:16px}
.blockcard .spec{font:12px var(--mono);color:var(--muted);margin:0 0 6px;word-break:break-word}
.kv{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:2px 24px;font-size:13.5px}
.kv div{display:flex;justify-content:space-between;gap:12px;padding:5px 0;border-bottom:1px dashed var(--line)}
.kv div span:first-child{color:var(--ink-2)}
.kv div span:last-child{font-variant-numeric:tabular-nums;font-weight:550;text-align:right}
#tip{position:fixed;z-index:50;pointer-events:none;background:var(--surface);color:var(--ink);border:1px solid var(--line);
  box-shadow:0 8px 24px rgba(0,0,0,.14);border-radius:10px;padding:8px 11px;font-size:12.5px;min-width:130px;display:none}
#tip .h{font-weight:650;margin-bottom:4px}
#tip .r{display:flex;align-items:center;gap:7px;justify-content:space-between}
#tip .r .k{width:12px;height:2px;border-radius:1px;display:inline-block}
#tip .r .v{font-weight:650;font-variant-numeric:tabular-nums}
footer{color:var(--muted);font-size:12.5px;text-align:center;padding:20px}
@media (max-width:900px){.layout{grid-template-columns:1fr}nav.toc{display:none}.figs{grid-template-columns:1fr}}
@media (prefers-reduced-motion:reduce){html{scroll-behavior:auto}*{transition:none!important}}
@media print{.top,nav.toc,.tbar button{display:none}.layout{display:block}section.card{break-inside:avoid;box-shadow:none}}
"""

JS = r"""
(function(){
const U = JSON.parse(document.getElementById('units-data').textContent);
const root = document.documentElement;
const tip = document.getElementById('tip');
let unitSet = document.body.dataset.units;
function toUnit(si, q, set){
  const unit = (U.sets[set]||{})[q];
  if(!unit || q==='fraction') return [si, ''];
  if(q==='temperature'){
    if(unit==='K') return [si,unit]; if(unit==='C') return [si-273.15,unit];
    if(unit==='F') return [(si-273.15)*9/5+32,unit]; if(unit==='R') return [si*9/5,unit];
  }
  const f = (U.factors[q]||{})[unit]; return f ? [si/f, unit] : [si, ''];
}
function fmt(v){
  if(v===null||v===undefined||!isFinite(v)) return '-';
  if(v===0) return '0';
  const a=Math.abs(v);
  if(a>=1e9||a<1e-4) return v.toExponential(4);
  if(a>=1e5) return Math.round(v).toLocaleString('en-US');
  const dec=Math.max(0,5-Math.floor(Math.log10(a)));
  let s=v.toLocaleString('en-US',{minimumFractionDigits:0,maximumFractionDigits:dec});
  return s;
}
function applyUnits(set){
  unitSet=set;
  document.querySelectorAll('.q').forEach(el=>{const r=toUnit(parseFloat(el.dataset.si), el.dataset.q, set); el.textContent=fmt(r[0]);});
  document.querySelectorAll('.u').forEach(el=>{const unit=(U.sets[set]||{})[el.dataset.q]; el.textContent=unit||'';});
  const n=document.getElementById('unit-note'); if(n) n.hidden = (set===document.body.dataset.units);
  try{localStorage.setItem('refluxion-units',set)}catch(e){}
}
const sel=document.getElementById('unit-select');
if(sel){ sel.addEventListener('change',()=>applyUnits(sel.value)); }
// theme
const tbtn=document.getElementById('theme-btn');
function setTheme(t){ if(t){root.dataset.theme=t;} else {delete root.dataset.theme;} try{localStorage.setItem('refluxion-theme',t||'')}catch(e){} }
try{const t=localStorage.getItem('refluxion-theme'); if(t) root.dataset.theme=t;}catch(e){}
if(tbtn) tbtn.addEventListener('click',()=>{
  const dark = root.dataset.theme ? root.dataset.theme==='dark' : matchMedia('(prefers-color-scheme: dark)').matches;
  setTheme(dark?'light':'dark');
});
// tooltip helpers
function showTip(html,x,y){tip.innerHTML=html;tip.style.display='block';
  const w=tip.offsetWidth,h=tip.offsetHeight; let L=x+14,T=y+14;
  if(L+w>innerWidth-8) L=x-w-14; if(T+h>innerHeight-8) T=y-h-14; tip.style.left=L+'px'; tip.style.top=T+'px';}
function hideTip(){tip.style.display='none';}
function esc(s){return String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
// charts: crosshair + tooltip + legend toggles
document.querySelectorAll('svg.chart[data-chart]').forEach(svg=>{
  const d=JSON.parse(svg.dataset.chart); const NS='http://www.w3.org/2000/svg';
  const vb=svg.viewBox.baseVal;
  const xh=document.createElementNS(NS,'line'); xh.setAttribute('class','xh'); xh.setAttribute('y1',d.py[0]); xh.setAttribute('y2',d.py[1]); xh.style.display='none';
  svg.appendChild(xh);
  const dots=d.series.map((s,i)=>{const c=document.createElementNS(NS,'circle'); c.setAttribute('r',5); c.setAttribute('class','f'+i+' hot'); c.style.display='none'; svg.appendChild(c); return c;});
  const hidden=new Set();
  const fx=v=>d.px[0]+(d.px[1]-d.px[0])*(v-d.x[0])/(d.x[1]-d.x[0]);
  const fy=v=>{ if(d.logy){const lo=Math.log10(d.y[0]),hi=Math.log10(d.y[1]); return d.py[0]+(d.py[1]-d.py[0])*(1-(Math.log10(Math.max(v,1e-300))-lo)/(hi-lo));}
    return d.py[0]+(d.py[1]-d.py[0])*(1-(v-d.y[0])/(d.y[1]-d.y[0]));};
  const rect=document.createElementNS(NS,'rect'); rect.setAttribute('x',d.px[0]); rect.setAttribute('y',d.py[0]);
  rect.setAttribute('width',d.px[1]-d.px[0]); rect.setAttribute('height',d.py[1]-d.py[0]); rect.setAttribute('fill','transparent');
  svg.appendChild(rect);
  function move(ev){
    const pt=svg.createSVGPoint(); pt.x=ev.clientX; pt.y=ev.clientY; const p=pt.matrixTransform(svg.getScreenCTM().inverse());
    const xv=d.x[0]+(p.x-d.px[0])/(d.px[1]-d.px[0])*(d.x[1]-d.x[0]);
    // nearest x across visible series
    let best=null,bd=Infinity;
    d.series.forEach((s,i)=>{ if(hidden.has(i)) return; s.x.forEach((x,k)=>{ if(x===null||s.y[k]===null) return; const dd=Math.abs(x-xv); if(dd<bd){bd=dd;best=x;} }); });
    if(best===null){hideTip();return;}
    xh.setAttribute('x1',fx(best)); xh.setAttribute('x2',fx(best)); xh.style.display='';
    let rows='';
    d.series.forEach((s,i)=>{ let kk=-1,dd=Infinity; s.x.forEach((x,k)=>{ if(x===null||s.y[k]===null) return; const e=Math.abs(x-best); if(e<dd){dd=e;kk=k;} });
      if(kk<0||hidden.has(i)||dd>Math.abs(d.x[1]-d.x[0])*0.02+1e-12){dots[i].style.display='none';return;}
      dots[i].setAttribute('cx',fx(s.x[kk])); dots[i].setAttribute('cy',fy(s.y[kk])); dots[i].style.display='';
      rows+=`<div class="r"><span><span class="k" style="background:var(--c${i})"></span> ${esc(s.name)}</span><span class="v">${fmt(s.y[kk])}</span></div>`; });
    showTip(`<div class="h">${esc(d.xl||'x')} = ${fmt(best)}</div>${rows}`, ev.clientX, ev.clientY);
  }
  rect.addEventListener('pointermove',move);
  rect.addEventListener('pointerleave',()=>{hideTip(); xh.style.display='none'; dots.forEach(c=>c.style.display='none');});
  svg.querySelectorAll('.lgi').forEach(g=>{ const i=+g.dataset.i; const toggle=()=>{ if(hidden.has(i)){hidden.delete(i);g.classList.remove('off');} else {hidden.add(i);g.classList.add('off');}
      svg.querySelectorAll('.series[data-i="'+i+'"]').forEach(s=>s.classList.toggle('off',hidden.has(i))); };
    g.addEventListener('click',toggle); g.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();toggle();}}); });
});
// flowsheet
document.querySelectorAll('svg.pfd').forEach(svg=>{
  svg.querySelectorAll('.stg').forEach(g=>{
    g.addEventListener('pointermove',ev=>{
      let info={}; try{info=JSON.parse(g.dataset.info||'{}')}catch(e){}
      const row=(k,o)=>{ if(!o) return ''; const r=toUnit(o.si,o.quantity,unitSet); return `<div class="r"><span>${k}</span><span class="v">${fmt(r[0])} ${esc(r[1])}</span></div>`;};
      let h=`<div class="h">${esc(g.dataset.stream)}</div>`+row('T',info.T)+row('P',info.P)+row('Flow',info.F);
      if(info.vf!==undefined) h+=`<div class="r"><span>Vapor fraction</span><span class="v">${fmt(info.vf)}</span></div>`;
      if(info.phase) h+=`<div class="r"><span>Phase</span><span class="v">${esc(info.phase)}</span></div>`;
      showTip(h,ev.clientX,ev.clientY);
    });
    g.addEventListener('pointerleave',hideTip);
    g.addEventListener('click',()=>{ const th=document.querySelector('[data-streamcol="'+CSS.escape(g.dataset.stream)+'"]');
      document.querySelectorAll('td.hl,th.hl').forEach(e=>e.classList.remove('hl'));
      if(th){ const idx=[...th.parentNode.children].indexOf(th); th.closest('table').querySelectorAll('tr').forEach(tr=>{const c=tr.children[idx]; if(c) c.classList.add('hl');});
        th.scrollIntoView({behavior:'smooth',block:'center',inline:'center'}); } });
  });
  svg.querySelectorAll('.bk').forEach(g=>{ const go=()=>{ const el=document.getElementById('block-'+g.dataset.block); if(!el) return;
      el.scrollIntoView({behavior:'smooth',block:'start'}); el.classList.add('flash'); setTimeout(()=>el.classList.remove('flash'),1600); };
    g.addEventListener('click',go); g.addEventListener('keydown',e=>{if(e.key==='Enter'){go();}}); });
});
// copy table as CSV
document.querySelectorAll('button[data-copy]').forEach(b=>b.addEventListener('click',async()=>{
  const t=document.getElementById(b.dataset.copy); if(!t) return;
  const csv=[...t.querySelectorAll('tr')].map(tr=>[...tr.children].map(c=>'"'+c.innerText.replace(/"/g,'""').trim()+'"').join(',')).join('\n');
  try{ await navigator.clipboard.writeText(csv); b.textContent='Copied'; }
  catch(e){ const r=document.createRange(); r.selectNode(t); getSelection().removeAllRanges(); getSelection().addRange(r); b.textContent='Selected - press Ctrl+C'; }
  setTimeout(()=>b.textContent='Copy CSV',1800);
}));
// table of contents highlight
const links=[...document.querySelectorAll('nav.toc a[href^="#"]')];
const obs=new IntersectionObserver(es=>{es.forEach(e=>{ if(e.isIntersecting){ links.forEach(l=>l.classList.toggle('on', l.getAttribute('href')==='#'+e.target.id)); } });},{rootMargin:'-80px 0px -70% 0px'});
document.querySelectorAll('section.card[id]').forEach(s=>obs.observe(s));
try{const u=localStorage.getItem('refluxion-units'); if(u && sel && [...sel.options].some(o=>o.value===u) && u!==unitSet){sel.value=u; applyUnits(u);} }catch(e){}
})();
"""

LOGO = ('<svg class="logo" viewBox="0 0 24 24" aria-hidden="true"><rect x="7" y="2" width="10" height="20" rx="5" fill="none" '
        'stroke="currentColor" stroke-width="1.8"/><path d="M9.5 7h5M9.5 11h5M9.5 15h5" stroke="currentColor" stroke-width="1.4"/>'
        '<path d="M17 5h3.5v5" fill="none" stroke="var(--accent)" stroke-width="1.8" stroke-linecap="round"/></svg>')


def _e(s):
    return html.escape(str(s), quote=True)


def render_cell(c, units, idx_col=0):
    from . import Pill, Q, fmt

    if isinstance(c, Q):
        if c.si is None or (isinstance(c.si, float) and not np.isfinite(c.si)):
            return "<td>-</td>"
        v = units.from_si(c.si, c.quantity) if c.quantity != "fraction" else c.si
        return f'<td><span class="q" data-q="{_e(c.quantity)}" data-si="{c.si!r}">{_e(fmt(v))}</span></td>'
    if isinstance(c, Pill):
        return f'<td><span class="status {_e(c.status)}">{_e(c.text)}</span></td>'
    if isinstance(c, tuple) and len(c) == 2 and c[0] == "unit":
        return f'<td class="txt"><span class="u" data-q="{_e(c[1])}">{_e(units.unit(c[1]))}</span></td>'
    if isinstance(c, tuple) and len(c) == 2:
        label, q = c
        u = f'<span class="u" data-q="{_e(q)}">{_e(units.unit(q))}</span>' if q else ""
        return f'<td class="txt">{_e(label)} {u}</td>'
    if isinstance(c, (int, float, np.floating, np.integer)) and not isinstance(c, bool):
        return f"<td>{_e(fmt(float(c)) if not isinstance(c, (int, np.integer)) else str(c))}</td>"
    return f'<td class="txt">{_e(c)}</td>' if idx_col == 0 or (isinstance(c, str) and len(c) > 18) else f"<td>{_e(c)}</td>"


def render_table(t: dict, units, tid: str) -> str:
    head = []
    for col in t["columns"]:
        if isinstance(col, tuple):
            label, q = col
            u = f'<span class="u" data-q="{_e(q)}">{_e(units.unit(q))}</span>' if q else ""
            extra = f' data-streamcol="{_e(label)}"' if t.get("stream_table") else ""
            head.append(f"<th{extra}>{_e(label)}{u}</th>")
        else:
            head.append(f"<th>{_e(col)}</th>")
    body = []
    ncol = len(t["columns"])
    for row in t["rows"]:
        if row and isinstance(row[0], tuple) and len(row[0]) == 3 and row[0][0] == "__head__":
            _, label, q = row[0]
            u = f' <span class="u" data-q="{_e(q)}">{_e(units.unit(q))}</span>' if q else ""
            body.append(f'<tr class="sub"><td colspan="{ncol}">{_e(label)}{u}</td></tr>')
            continue
        body.append("<tr>" + "".join(render_cell(c, units, i) for i, c in enumerate(row)) + "</tr>")
    table = f'<div class="tw"><table class="data" id="{_e(tid)}"><thead><tr>{"".join(head)}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'
    bar = (f'<div class="tbar"><span class="cap">{_e(t.get("caption", ""))}</span>'
           f'<button type="button" data-copy="{_e(tid)}">Copy CSV</button></div>')
    if t.get("collapsible"):
        return f'<details><summary>{_e(t.get("title", "Table"))} ({len(t["rows"])} rows)</summary>{bar}{table}</details>'
    return bar + table


def render_item(rep, item, sid, k):
    from . import md_to_html

    kind = item[0]
    units = rep.units
    if kind == "md":
        return md_to_html(item[1])
    if kind == "calc":
        return f'<div class="calc">{md_to_html(item[1])}</div>'
    if kind == "callout":
        _, level, text = item
        icon = {"good": "&#10003;", "bad": "!", "warn": "!", "info": "i"}.get(level, "i")
        body = md_to_html(text)
        if body.startswith("<p>") and body.endswith("</p>") and body.count("<p>") == 1:
            body = body[3:-4]
        return f'<div class="callout {_e(level)}"><span class="ic">{icon}</span><div>{body}</div></div>'
    if kind == "table":
        return render_table(item[1], units, f"{sid}-t{k}")
    if kind == "svg":
        _, svg, _sa, name, title, caption = item
        cap = f"<b>{_e(title)}</b>" + (f" &middot; {_e(caption)}" if caption else "")
        return f'<figure class="fig">{svg}<figcaption>{cap}</figcaption></figure>'
    if kind == "pfd":
        return (f'<div class="pfdwrap">{item[1]}</div><p class="legend-note">Hover a stream for its conditions, click a stream to '
                'highlight it in the stream table, click a block to jump to its results. Dashed lines are tear streams.</p>')
    if kind == "block":
        _, b, rows = item
        status = {"ok": "good", "warning": "warn", "error": "bad"}.get(b.status, "neutral")
        kv = []
        for label, val, unit in rows:
            from . import Q, fmt
            if isinstance(val, Q):
                v = units.from_si(val.si, val.quantity)
                vs = f'<span class="q" data-q="{_e(val.quantity)}" data-si="{val.si!r}">{_e(fmt(v))}</span>'
                us = f' <span class="u" data-q="{_e(val.quantity)}">{_e(units.unit(val.quantity))}</span>'
            else:
                vs, us = _e(fmt(val)), ""
            kv.append(f"<div><span>{_e(label)}</span><span>{vs}{us}</span></div>")
        extras = block_extras(rep, b)
        ex_html = []
        figs = []
        for j, it in enumerate(extras):
            h = render_item(rep, it, f"{sid}-{b.name}", j)
            (figs if it[0] == "svg" else ex_html).append(h)
        msgs = "".join(f'<div class="callout warn"><span class="ic">!</span><div>{_e(m)}</div></div>' for m in b.messages)
        return (f'<div class="blockcard" id="block-{_e(b.name)}"><h3>{_e(b.name)} <span class="status neutral">{_e(b.type_name)}</span>'
                f'<span class="status {status}">{_e(b.status)}</span></h3><p class="spec">{_e(b.describe_spec())}</p>{msgs}'
                f'<div class="kv">{"".join(kv)}</div>{"".join(ex_html)}'
                + (f'<div class="figs">{"".join(figs)}</div>' if figs else "") + "</div>")
    if kind == "log":
        return f"<details><summary>Show the run log ({item[1].count(chr(10)) + 1} lines)</summary><pre>{_e(item[1])}</pre></details>"
    return ""


def render_html(rep) -> str:
    from .. import __version__
    from . import fmt, unit_tables_json

    fs = rep.fs
    units = rep.units
    sections = rep.sections()
    chips = []
    if fs is not None:
        ok = fs.status == "converged"
        chips.append(f'<span class="chip {"good" if ok else "bad"}">{_e("Converged" if ok else fs.status.capitalize())}</span>')
        chips.append(f'<span class="chip">Property method <b>{_e(fs.pm.name)}</b></span>')
        chips.append(f'<span class="chip"><b>{len(fs.comps)}</b> components &middot; <b>{len(fs.blocks)}</b> blocks &middot; '
                     f'<b>{len(fs.streams)}</b> streams</span>')
        chips.append(f'<span class="chip">Solved in <b>{fs.run_time:.2f} s</b></span>')
    chips.append(f'<span class="chip">Generated {_e(rep.created.strftime("%b %d, %Y %H:%M"))}</span>')
    kpis = []
    for h in rep.highlights:
        v = h["value"]
        if h["quantity"] and v is not None:
            vv = units.from_si(v, h["quantity"])
            vs = f'<span class="q" data-q="{_e(h["quantity"])}" data-si="{v!r}">{_e(fmt(vv))}</span>'
            us = f'<small><span class="u" data-q="{_e(h["quantity"])}">{_e(units.unit(h["quantity"]))}</span></small>'
        else:
            vs, us = _e(fmt(v)), (f"<small>{_e(h['unit'])}</small>" if h["unit"] else "")
        tgt = ""
        if h["kind"] and h["target"] is not None:
            word = {"good": "&#10003; meets", "bad": "&#10007; misses"}.get(h["status"], "")
            tgt = (f'<div class="t"><span class="status {h["status"]}">{word}</span> target {_e(h["kind"])} {_e(fmt(h["target"]))} '
                   f'{_e(h["unit"])}</div>')
        elif h["note"]:
            tgt = f'<div class="t">{_e(h["note"])}</div>'
        kpis.append(f'<div class="kpi {h["status"] if h["status"] != "neutral" else ""}"><div class="l">{_e(h["label"])}</div>'
                    f'<div class="v">{vs}{us}</div>{tgt}</div>')
    toc = ['<div class="lbl">On this page</div>']
    body = []
    for n, s in enumerate(sections, 1):
        toc.append(f'<a href="#{_e(s.id)}">{_e(s.title)}</a>')
        items = "".join(render_item(rep, it, s.id, k) for k, it in enumerate(s.items))
        body.append(f'<section class="card" id="{_e(s.id)}"><h2><span class="n">{n:02d}</span>{_e(s.title)}</h2>{items}</section>')
    options = "".join(f'<option value="{k}"{" selected" if k == units.base else ""}>{k}</option>'
                      for k in ("ENG", "SI", "MET", "METCBAR", "SI-KW"))
    units_json = unit_tables_json()
    if units.overrides:
        data = json.loads(units_json)
        data["sets"][units.base] = {**data["sets"][units.base], **units.overrides}
        units_json = json.dumps(data)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(rep.title)}</title>
<meta name="generator" content="Refluxion {_e(__version__)}">
<style>{CSS}</style>
</head>
<body data-units="{_e(units.base)}">
<div class="top">
  <div class="brand">{LOGO} Refluxion <small>report</small></div>
  <div class="grow"></div>
  <label class="ctl" for="unit-select">Units <select id="unit-select" aria-label="Unit set">{options}</select></label>
  <span class="ctl" id="unit-note" hidden>charts stay in {_e(units.base)}</span>
  <button type="button" id="theme-btn" aria-label="Toggle light or dark theme">&#9680; Theme</button>
</div>
<div class="layout">
<nav class="toc" aria-label="Sections">{"".join(toc)}</nav>
<main>
<header class="hero">
  <h1>{_e(rep.title)}</h1>
  {f'<p class="sub">{_e(rep.subtitle)}</p>' if rep.subtitle else ''}
  <div class="chips">{"".join(chips)}</div>
</header>
{f'<div class="cards">{"".join(kpis)}</div>' if kpis else ''}
{"".join(body)}
<footer>Made with Refluxion {_e(__version__)} &middot; an open-source learning simulator. Results are close to, not identical to, Aspen Plus.</footer>
</main>
</div>
<div id="tip" role="status"></div>
<script type="application/json" id="units-data">{units_json}</script>
<script>{JS}</script>
</body>
</html>"""

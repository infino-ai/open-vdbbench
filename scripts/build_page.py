#!/usr/bin/env python3
"""Render leaderboard/index.html from data/leaderboard_data.json.

The page is generated, not hand-maintained: rerun this after any new result so
the board and the underlying data can never disagree.

It stands alone at infino.ai/open-vdbbench. Every engine is drawn the same
way; the accent marks only controls and the recall bar.
"""
import json
import os
import sys

HTML = r'''<title>Open Vector Database Benchmark</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Geist:wght@400..700&family=Geist+Mono:wght@400..700&display=swap">
<style>
/* One dark palette, painted explicitly: the Infino tokens. */
:root {
  color-scheme: dark;
  --bg: #0B0C10; --surface: #111319; --surface-2: #181A22; --surface-3: #1F222C;
  --ink: #EDEBE4; --ink-2: #CCC9C0; --muted: #9C9DA6; --faint: #6C6E78;
  --line: #22242E; --line-2: #1A1C24;
  --accent: #D6400F; --accent-700: #FF7A45;
  --tint-2: rgba(214, 64, 15, 0.12);
  --bar: #B7B4AB; --bar-2: #585862;
  --font-sans: "Geist", system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  --font-mono: "Geist Mono", ui-monospace, "SF Mono", "Cascadia Code", "JetBrains Mono", Menlo, Consolas, monospace;
  --r-xs: 3px; --r-sm: 5px; --r: 8px;
  --ease: cubic-bezier(.2, .7, .2, 1);
  --gut: 20px;
}
@media (min-width: 720px) { :root { --gut: 32px; } }
*, *::before, *::after { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
html, body { overflow-x: clip; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font-family: var(--font-sans); font-size: 1rem; line-height: 1.5;
  -webkit-font-smoothing: antialiased;
}
h1, h2 { margin: 0; font-weight: 600; line-height: 1.1; text-wrap: balance; }
a { color: inherit; text-decoration: none; }
button { font: inherit; color: inherit; background: none; border: 0; padding: 0; cursor: pointer; }
img, svg { display: block; max-width: 100%; }
:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 2px; }
::selection { background: var(--accent); color: #000; }
* { scrollbar-width: thin; scrollbar-color: #2A2C36 transparent; }
[hidden] { display: none !important; }
.wrap { max-width: 1240px; margin-inline: auto; padding-inline: var(--gut); }
.mono { font-family: var(--font-mono); }

.title { padding-block: 56px 24px; }
.thead { display: flex; flex-wrap: wrap; align-items: flex-end; justify-content: space-between; gap: 18px 24px; }
.acts { display: flex; flex-wrap: wrap; gap: 10px; }
.btn {
  display: inline-flex; align-items: center; gap: 8px; min-height: 40px;
  font-family: var(--font-mono); font-size: 14px; font-weight: 600; line-height: 1;
  padding: 10px 15px; border-radius: 4px; border: 1px solid transparent; white-space: nowrap;
  transition: background .15s var(--ease), border-color .15s var(--ease), color .15s var(--ease);
}
.btn-primary { background: var(--accent); color: #fff; }
.btn-primary:hover { background: #EE5A24; }
.btn-secondary { background: var(--surface); border-color: var(--line); color: var(--ink); }
.btn-secondary:hover { border-color: var(--muted); }
h1 { font-size: clamp(1.9rem, 1.1rem + 2.4vw, 2.75rem); letter-spacing: -0.035em; }
.by { margin-top: 8px; font-family: var(--font-mono); font-size: 14px; color: var(--muted); }
.by a { color: var(--accent-700); }
.by a:hover { text-decoration: underline; text-underline-offset: 3px; }
.spec { display: flex; flex-wrap: wrap; gap: 12px 36px; margin: 20px 0 0; font-family: var(--font-mono); }
.spec dt { font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.09em; color: var(--faint); }
.spec dd { margin: 3px 0 0; font-size: 13.5px; color: var(--ink-2); }

/* filters: sticky on wide screens so they stay in reach of both panels */
.ctl { z-index: 20; background: rgba(11, 12, 16, 0.9); -webkit-backdrop-filter: blur(10px); backdrop-filter: blur(10px); border-block: 1px solid var(--line); }
@media (min-width: 900px) { .ctl { position: sticky; top: env(safe-area-inset-top, 0px); } }
.ctl .wrap { display: flex; flex-wrap: wrap; align-items: center; gap: 12px 28px; padding-block: 12px; }
.fg { display: flex; align-items: center; gap: 10px; }
.fl { font-family: var(--font-mono); font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.09em; color: var(--faint); white-space: nowrap; }
.seg { display: inline-flex; gap: 2px; padding: 2px; background: var(--surface); border: 1px solid var(--line); border-radius: var(--r-sm); }
.seg button {
  font-family: var(--font-mono); font-size: 13px; color: var(--muted);
  padding: 5px 11px; border-radius: var(--r-xs); white-space: nowrap;
  transition: color .15s var(--ease), background .15s var(--ease);
}
.seg button:hover { color: var(--ink); }
.seg button[aria-pressed="true"] { color: var(--accent-700); background: var(--tint-2); }

.blk { padding-block: 22px 0; }
.panel { background: var(--surface); border: 1px solid var(--line); border-radius: var(--r); overflow: hidden; }
.phead { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px 16px; padding: 18px 22px 14px; }
.phead h2 { font-size: 1.125rem; letter-spacing: -0.015em; }

/* results: p99 left of the axis, QPS right of it */
.rs { font-family: var(--font-mono); }
.rr, .rh {
  display: grid;
  grid-template-columns: 26px minmax(170px, 1.25fr) minmax(0, 3fr) 62px 88px 16px;
  grid-template-areas: "rk nm br rc md cv";
  column-gap: 16px; align-items: center; width: 100%;
  padding: 9px 22px; text-align: left;
}
.rh { padding-block: 10px; border-block: 1px solid var(--line); background: var(--surface-2); font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--faint); }
.row { border-bottom: 1px solid var(--line-2); }
.row:last-child { border-bottom: 0; }
.rr { transition: background .12s var(--ease); }
.rr:hover { background: var(--surface-2); }
.row.open > .rr { background: var(--surface-2); }
.rk { grid-area: rk; font-size: 12.5px; color: var(--faint); font-variant-numeric: tabular-nums; }
.nm { grid-area: nm; min-width: 0; }
.nm b { display: block; font-family: var(--font-sans); font-size: 15px; font-weight: 600; color: var(--ink); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.nm small { display: block; margin-top: 1px; font-size: 11.5px; color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.bars { grid-area: br; display: grid; grid-template-columns: minmax(0, 2fr) minmax(0, 3fr); align-items: center; min-width: 0; align-self: stretch; }
.lt, .qp { display: flex; align-items: center; gap: 8px; min-width: 0; }
.lt { justify-content: flex-end; border-right: 1px solid var(--line); align-self: stretch; }
.rh .lt { padding-right: 10px; }
.rh .qp { padding-left: 10px; }
.bar { display: block; height: 12px; width: calc(var(--w) * .8); min-width: 2px; flex: 0 1 auto; background: var(--bar); }
.lt .bar { background: var(--bar-2); border-radius: 2px 0 0 2px; }
.qp .bar { border-radius: 0 2px 2px 0; }
.v { flex: none; font-size: 13px; color: var(--ink-2); font-variant-numeric: tabular-nums; }
.rc { grid-area: rc; font-size: 13px; color: var(--ink-2); text-align: right; font-variant-numeric: tabular-nums; }
.md { grid-area: md; font-size: 12.5px; color: var(--muted); text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.cv { grid-area: cv; color: var(--faint); transition: transform .15s var(--ease); }
.row.open .cv { transform: rotate(180deg); color: var(--ink-2); }

.sort { font: inherit; color: inherit; letter-spacing: inherit; text-transform: inherit; white-space: nowrap; text-align: left; }
.sort:hover { color: var(--ink-2); }
.sort.on { color: var(--accent-700); }
.sort.on::after { content: " \2193"; }
.sort.on.asc::after { content: " \2191"; }
.rh .rc, .rh .md { text-align: right; }

.det { padding: 4px 22px 18px calc(22px + 26px + 16px); }
.det { background: var(--surface-2); }
.det dl { display: grid; grid-template-columns: repeat(auto-fill, minmax(190px, 1fr)); gap: 14px 28px; margin: 0; }
.det dt { font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--faint); }
.det dd { margin: 3px 0 0; font-size: 13px; color: var(--ink-2); overflow-wrap: anywhere; }
.det .wide { grid-column: 1 / -1; }
.det a { color: var(--ink-2); border-bottom: 1px solid var(--line); }
.det a:hover { color: var(--accent-700); border-color: var(--accent); }
.det .sm { display: none; }

.grp { padding: 16px 22px 18px; border-top: 1px solid var(--line); }
.grp .fl { display: block; margin-bottom: 10px; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chips span { font-family: var(--font-mono); font-size: 12.5px; color: var(--muted); border: 1px solid var(--line); border-radius: var(--r-xs); padding: 3px 9px; }
.chips span i { font-style: normal; color: var(--faint); margin-left: 6px; }
.none { padding: 28px 22px; font-family: var(--font-mono); font-size: 13px; color: var(--muted); border-top: 1px solid var(--line); }

@media (max-width: 760px) {
  .rr, .rh {
    grid-template-columns: 22px minmax(0, 1fr) 58px 16px;
    grid-template-areas: "rk nm rc cv" ". br br .";
    row-gap: 8px; column-gap: 10px; padding-inline: 16px;
  }
  .md { display: none; }
  .bars { grid-template-columns: minmax(0, 1fr) minmax(0, 1.35fr); }
  .det { padding: 4px 16px 16px 48px; }
  .det .sm { display: block; }
  .phead, .grp, .none { padding-inline: 16px; }
}

/* recall sweep */
.chart { overflow-x: auto; -webkit-overflow-scrolling: touch; padding: 0 12px 4px; }
.plot { position: relative; min-width: 640px; }
.plot svg { width: 100%; height: auto; }
.ax { fill: var(--faint); font-family: var(--font-mono); font-size: 12px; }
.ax.acc { fill: var(--accent-700); }
.at { fill: var(--muted); font-family: var(--font-mono); font-size: 12px; }
.gl { stroke: var(--line); stroke-width: 1; }
.gl2 { stroke: var(--line-2); stroke-width: 1; }
.thr { stroke: var(--accent-700); stroke-width: 1; stroke-dasharray: 3 4; opacity: .75; }
/* one colour per engine, assigned alphabetically, so the same engine keeps its
   colour in both datasets and no row is singled out */
.ln { fill: none; stroke-width: 1.75; stroke-opacity: .9; stroke-linejoin: round; }
.ln.f { stroke-width: 2.75; stroke-opacity: 1; }
.rp { fill: var(--surface); stroke-width: 2; }
.ld { fill: none; stroke-width: 1; stroke-opacity: .4; }
.lb { font-family: var(--font-mono); font-size: 12px; dominant-baseline: middle; cursor: default; }
.lb.f { font-weight: 700; }
.dim { opacity: .13; }
.sw { display: inline-block; width: 9px; height: 9px; border-radius: 2px; margin-right: 7px; }
.tip {
  position: absolute; pointer-events: none; z-index: 2;
  transform: translate(-50%, calc(-100% - 14px));
  background: var(--surface-3); border: 1px solid var(--line); border-radius: var(--r-sm);
  padding: 8px 11px; font-family: var(--font-mono); font-size: 12px; color: var(--ink-2);
  white-space: nowrap; box-shadow: 0 12px 28px -18px rgba(0, 0, 0, .7);
}
.tip b { display: block; color: var(--ink); font-weight: 600; margin-bottom: 3px; }
.tip span { display: block; font-variant-numeric: tabular-nums; }
.legend { display: flex; flex-wrap: wrap; gap: 6px; padding: 12px 22px 18px; border-top: 1px solid var(--line); }
.chip {
  font-family: var(--font-mono); font-size: 12.5px; color: var(--muted);
  border: 1px solid var(--line); border-radius: var(--r-xs); padding: 3px 9px;
  transition: color .15s var(--ease), border-color .15s var(--ease);
}
.chip:hover { color: var(--ink-2); }
.chip[aria-pressed="true"] { color: var(--ink); border-color: var(--c, var(--ink-2)); }
@media (max-width: 760px) { .legend { padding-inline: 16px; } }

.foot { margin-top: 56px; border-top: 1px solid var(--line); }
.foot .wrap { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 10px 24px; padding-block: 20px 28px; font-family: var(--font-mono); font-size: 13px; color: var(--muted); }
.foot nav { display: flex; flex-wrap: wrap; gap: 6px 20px; }
.foot a:hover { color: var(--accent-700); }
@media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
</style>

<main>
  <div class="wrap title">
    <div class="thead">
      <div>
        <h1>Open Vector Database Benchmark</h1>
        <div class="by">published by <a href="https://infino.ai">Infino</a></div>
      </div>
      <div class="acts">
        <a class="btn btn-secondary" href="__REPO__">
          <svg width="15" height="15" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><path d="M8 0C3.58 0 0 3.58 0 8a8 8 0 0 0 5.47 7.59c.4.07.55-.17.55-.38v-1.32c-2.23.49-2.7-1.08-2.7-1.08-.36-.92-.89-1.17-.89-1.17-.73-.5.05-.49.05-.49.8.06 1.23.83 1.23.83.71 1.22 1.87.87 2.33.66.07-.52.28-.87.5-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.22 2.2.82a7.6 7.6 0 0 1 4 0c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.28.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48v2.2c0 .21.15.46.55.38A8 8 0 0 0 16 8c0-4.42-3.58-8-8-8Z"/></svg>
          repo
        </a>
        <a class="btn btn-primary" href="__REPO__#submit-a-result">submit a result</a>
      </div>
    </div>
    <dl class="spec" id="spec"></dl>
  </div>

  <div class="ctl" id="ctl">
    <div class="wrap">
      <div class="fg"><span class="fl" id="l-case">dataset</span><div class="seg" id="f-case" role="group" aria-labelledby="l-case"></div></div>
      <div class="fg"><span class="fl" id="l-idx">index</span><div class="seg" id="f-idx" role="group" aria-labelledby="l-idx"></div></div>
      <div class="fg"><span class="fl" id="l-rec">recall &#x2265;</span><div class="seg" id="f-rec" role="group" aria-labelledby="l-rec"></div></div>
      <div class="fg"><span class="fl" id="l-ord">order</span><div class="seg" id="f-ord" role="group" aria-labelledby="l-ord"></div></div>
    </div>
  </div>

  <section class="blk" id="results">
    <div class="wrap">
      <div class="panel">
        <div class="phead"><h2>Results</h2></div>
        <div class="rs" id="rs"></div>
      </div>
    </div>
  </section>

  <section class="blk" id="sweep">
    <div class="wrap">
      <div class="panel">
        <div class="phead">
          <h2>Recall sweep</h2>
          <div class="seg" id="metric" role="group" aria-label="Plot values">
            <button data-m="qps" aria-pressed="true">QPS</button>
            <button data-m="p99" aria-pressed="false">p99 ms</button>
          </div>
        </div>
        <div class="chart">
          <div class="plot">
            <svg id="chart" viewBox="0 0 960 430" role="img" aria-label="Recall against throughput for each engine across its search parameter sweep, with the recall bar marked"></svg>
            <div class="tip" id="tip" hidden></div>
          </div>
        </div>
        <div class="legend" id="legend" role="group" aria-label="Highlight engines"></div>
      </div>
    </div>
  </section>
</main>

<footer class="foot">
  <div class="wrap">
    <nav aria-label="Benchmark">
      <a href="__REPO__/blob/main/METHODOLOGY.md">methodology</a>
      <a href="__REPO__/blob/main/AUDIT.md">audit</a>
      <a href="__REPO__/tree/main/results">raw results</a>
      <a href="__REPO__/issues/new">report an issue</a>
      <a href="https://infino.ai">infino.ai &#x2197;</a>
    </nav>
    <span>&#xA9; 2026 Infino AI Inc.</span>
  </div>
</footer>

<script>
const DATA = __DATA__, ENGINES = __ENGINES__, DEFAULTS = __DEFAULTS__, REPO = "__REPO__";
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
const CASES = Object.values(DATA.cases).sort((a, b) => a.rows - b.rows);
const INERT = "search parameter had no effect";
const BARS = [DATA.min_recall, 0.95, 0.99];
const INDEX = [["all", "all"], ["quantized", "quantized"], ["float32", "float32"]];
const ORDER = [["qps", "QPS"], ["p99", "p99"], ["recall", "recall"], ["measured", "measured"]];
const DIR = {engine: 1, p99: 1, qps: -1, recall: -1, measured: -1};
const KEY = {
  engine: r => name(r.e.engine_id).toLowerCase(),
  p99: r => r.p.p99_ms ?? Infinity,
  qps: r => r.p.qps,
  recall: r => r.p.recall,
  measured: r => r.p.measured || "",
};
const CHEVRON = '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M4 6l4 4 4-4"/></svg>';

const label = c => c.rows >= 1e6 ? `${c.rows / 1e6}M` : String(c.rows);
const name = id => (ENGINES[id]?.display || id).replace(/ \(self-hosted\)$/, "");
// Ten hues, then a lighter step of nine of them, handed out in alphabetical
// order of engine name across both datasets.
const PALETTE = ["#4C9BE8", "#E8B03E", "#4DBF6E", "#E5534B", "#A57BEA", "#2EC4C4", "#E86FB6", "#BFD14A", "#C08A5A", "#A0A4AE",
                 "#A9CCF5", "#F3D58E", "#A6E3B4", "#F4A3A0", "#D2BCF7", "#9EE6E6", "#F5B7DB", "#E3EDA0", "#E2C3A3"];
const COLOR = Object.fromEntries([...new Set(CASES.flatMap(c => c.entries.map(e => e.engine_id)))]
  .sort((a, b) => name(a).localeCompare(name(b))).map((id, i) => [id, PALETTE[i % PALETTE.length]]));
const f1 = n => n == null ? "" : n.toLocaleString("en-US", {minimumFractionDigits: 1, maximumFractionDigits: 1});
const f4 = n => n == null ? "" : n.toFixed(4);
const pct = (v, max) => `${max ? Math.max(v / max * 100, 0).toFixed(2) : 0}%`;
const short = s => String(s || "").split(" (")[0];
const params = p => Object.entries(p || {}).map(([k, v]) => `${k} ${v}`).join(", ") || "not recorded";

const S = {case: CASES[0], idx: "all", bar: DATA.min_recall, key: "qps", dir: -1, metric: "qps"};
const open = new Set(), focus = new Set();
let hover = null, PTS = [], LABS = [];

// The best point an engine measured at or above the recall bar.
function pick(e) {
  if ((e.flags || []).includes(INERT)) return null;
  const ok = (e.curve || []).filter(p => p.recall >= S.bar - 1e-12);
  return ok.length ? ok.reduce((a, b) => b.qps > a.qps ? b : a) : null;
}
function inIndex(id) {
  const q = ENGINES[id]?.quantized;
  return S.idx === "all" || (S.idx === "quantized" ? q === true : q === false);
}
function view() {
  const es = S.case.entries.filter(e => inIndex(e.engine_id));
  const rows = es.map(e => ({e, p: pick(e)}));
  const ranked = rows.filter(r => r.p).sort((a, b) => b.p.qps - a.p.qps);
  ranked.forEach((r, i) => r.rank = i + 1);
  const shown = ranked.slice().sort((a, b) => {
    const x = KEY[S.key](a), y = KEY[S.key](b);
    return (x < y ? -1 : x > y ? 1 : 0) * S.dir || b.p.qps - a.p.qps;
  });
  const below = rows.filter(r => !r.p).sort((a, b) => maxRecall(b.e) - maxRecall(a.e));
  const here = new Set(S.case.entries.map(e => e.engine_id));
  const missing = [...new Set(CASES.flatMap(c => c.entries.map(e => e.engine_id)))]
    .filter(id => !here.has(id) && inIndex(id));
  return {es, shown, below, missing};
}
const maxRecall = e => Math.max(...(e.curve || []).map(p => p.recall), 0);

function seg(el, items, cur) {
  el.innerHTML = items.map(([v, t]) => `<button data-v="${esc(v)}" aria-pressed="${String(v) === String(cur)}">${esc(t)}</button>`).join("");
}
function controls() {
  seg($("f-case"), CASES.map(c => [c.case_id, `${label(c)} vectors`]), S.case.case_id);
  seg($("f-idx"), INDEX, S.idx);
  seg($("f-rec"), BARS.map(b => [b, b.toFixed(2)]), S.bar);
  seg($("f-ord"), ORDER, S.key);
  const conc = String(DEFAULTS.num_concurrency).split(",").join(" ");
  const items = [
    ["dataset", `${S.case.dataset} ${label(S.case)} · ${S.case.dim} dim · cosine · k ${DEFAULTS.k}`],
    ["machine", `Azure ${DATA.baseline_vm} · ${DATA.reference_machine.vcpu} vCPU · ${DATA.reference_machine.ram_gb} GB`],
    ["concurrency", `${conc} · ${DEFAULTS.concurrency_duration} s each`],
  ];
  $("spec").innerHTML = items.map(([k, v]) => `<div><dt>${k}</dt><dd>${esc(v)}</dd></div>`).join("");
}

function results() {
  const {shown, below, missing} = view();
  const qmax = Math.max(0, ...shown.map(r => r.p.qps || 0));
  const lmax = Math.max(0, ...shown.map(r => r.p.p99_ms || 0));
  const sb = (k, t, cls = "") => `<button class="sort ${cls}${S.key === k ? " on" : ""}${S.key === k && S.dir > 0 ? " asc" : ""}" data-k="${k}">${t}</button>`;
  let h = `<div class="rh">
    <span class="rk">#</span>${sb("engine", "engine", "nm")}
    <span class="bars"><span class="lt">${sb("p99", "p99 ms")}</span><span class="qp">${sb("qps", "QPS")}</span></span>
    ${sb("recall", "recall", "rc")}${sb("measured", "measured", "md")}<span class="cv"></span>
  </div>`;
  h += shown.map(r => {
    const {e, p} = r, id = e.engine_id, en = ENGINES[id] || {}, o = open.has(id);
    return `<div class="row${o ? " open" : ""}">
      <button class="rr" data-id="${esc(id)}" aria-expanded="${o}" aria-controls="d-${esc(id)}">
        <span class="rk">${r.rank}</span>
        <span class="nm"><b>${esc(name(id))}</b><small>${esc(short(e.config))}</small></span>
        <span class="bars">
          <span class="lt"><span class="v">${f1(p.p99_ms)}</span><span class="bar" style="--w:${pct(p.p99_ms || 0, lmax)}"></span></span>
          <span class="qp"><span class="bar" style="--w:${pct(p.qps || 0, qmax)}"></span><span class="v">${f1(p.qps)}</span></span>
        </span>
        <span class="rc">${f4(p.recall)}</span>
        <span class="md">${esc(p.measured)}</span>
        <span class="cv" aria-hidden="true">${CHEVRON}</span>
      </button>
      <div class="det" id="d-${esc(id)}"${o ? "" : " hidden"}><dl>
        <div class="sm"><dt>measured</dt><dd>${esc(p.measured)}</dd></div>
        <div><dt>version</dt><dd>${esc(e.version)}</dd></div>
        <div><dt>index</dt><dd>${esc(short(e.config))}</dd></div>
        <div><dt>search</dt><dd>${esc(params(p.params))}</dd></div>
        <div><dt>peak concurrency</dt><dd>${p.peak_c ?? ""}</dd></div>
        <div><dt>runs as</dt><dd>${esc(en.runs_as)}</dd></div>
        <div class="wide"><dt>result</dt><dd><a href="${REPO}/blob/main/${esc(p.source_file)}">${esc(p.source_file)} &#x2197;</a></dd></div>
      </dl></div>
    </div>`;
  }).join("");
  if (!shown.length) h += `<div class="none">no measured point at recall ≥ ${S.bar.toFixed(2)}</div>`;
  if (below.length) h += `<div class="grp"><span class="fl">no point at recall ≥ ${S.bar.toFixed(2)}</span><div class="chips">`
    + below.map(r => `<span>${esc(name(r.e.engine_id))}<i>max ${f4(maxRecall(r.e))}</i></span>`).join("") + `</div></div>`;
  if (missing.length) h += `<div class="grp"><span class="fl">no ${label(S.case)} result</span><div class="chips">`
    + missing.map(id => `<span>${esc(name(id))}</span>`).join("") + `</div></div>`;
  $("rs").innerHTML = h;
}

const val = p => S.metric === "qps" ? p.qps : p.p99_ms;
const axv = v => v >= 1000 ? `${+(v / 1000).toPrecision(3)}k` : String(+v.toPrecision(3));

function chart() {
  const svg = $("chart"), W = 960, H = 430, m = {l: 58, r: 150, t: 28, b: 46};
  const {es} = view();
  const lines = es.filter(e => (e.curve || []).length);
  const pts = lines.flatMap(e => e.curve.filter(p => val(p) > 0).map(p => ({e, p})));
  if (!pts.length) { svg.innerHTML = ""; PTS = []; return; }
  const i0 = Math.min(17, Math.floor(Math.min(...pts.map(o => o.p.recall)) * 20));
  const x0 = i0 / 20, x1 = 1;
  // Log axis on a 1-2-5 grid, bounded by the ticks either side of the data.
  const vs = pts.map(o => val(o.p)), lo = Math.min(...vs), hi = Math.max(...vs);
  const ticks = [];
  for (let k = Math.floor(Math.log10(lo)) - 1; k <= Math.ceil(Math.log10(hi)) + 1; k++)
    for (const s of [1, 2, 5]) ticks.push(+(s * 10 ** k).toPrecision(3));
  const ya = Math.max(...ticks.filter(t => t <= lo)), yb = Math.min(...ticks.filter(t => t >= hi));
  const y0 = Math.log10(ya), y1 = Math.log10(yb) > y0 ? Math.log10(yb) : y0 + 1;
  const X = r => m.l + (r - x0) / (x1 - x0) * (W - m.l - m.r);
  const Y = v => m.t + (1 - (Math.log10(v) - y0) / (y1 - y0)) * (H - m.t - m.b);
  const shown = ticks.filter(t => t >= ya && t <= yb);
  const every = shown.length <= 9;
  let g = "";
  for (const t of shown) {
    const y = Y(t).toFixed(1), major = /^1/.test(String(t));
    g += `<line class="${major ? "gl" : "gl2"}" x1="${m.l}" x2="${W - m.r}" y1="${y}" y2="${y}"/>`;
    if (every || major)
      g += `<text class="ax" x="${m.l - 10}" y="${y}" text-anchor="end" dominant-baseline="middle">${axv(t)}</text>`;
  }
  for (let i = i0; i <= 20; i++) {
    const x = X(i / 20).toFixed(1);
    g += `<line class="gl" x1="${x}" x2="${x}" y1="${m.t}" y2="${H - m.b}"/>`
       + `<text class="ax${Math.abs(i / 20 - S.bar) < 1e-9 ? " acc" : ""}" x="${x}" y="${H - m.b + 20}" text-anchor="middle">${(i / 20).toFixed(2)}</text>`;
  }
  const tx = X(S.bar).toFixed(1);
  g += `<line class="thr" x1="${tx}" x2="${tx}" y1="${m.t}" y2="${H - m.b}"/>`;
  if (Math.abs(S.bar * 20 - Math.round(S.bar * 20)) > 1e-9)
    g += `<text class="ax acc" x="${tx}" y="${H - m.b + 20}" text-anchor="middle">${S.bar.toFixed(2)}</text>`;
  g += `<text class="at" x="${W - m.r}" y="${H - 6}" text-anchor="end">recall</text>`
     + `<text class="at" x="${m.l}" y="${m.t - 14}">${S.metric === "qps" ? "QPS" : "p99 ms"}</text>`;
  const active = focus.size > 0 || hover != null;
  const on = e => focus.has(e.engine_id) || hover === e.engine_id;
  const cls = e => on(e) ? "f" : active ? "dim" : "";
  const weight = e => on(e) ? 2 : active ? 0 : 1;
  PTS = [];
  const labs = [];
  let marks = "";
  for (const e of lines.slice().sort((a, b) => weight(a) - weight(b))) {
    const c = cls(e), col = COLOR[e.engine_id] || "#A0A4AE";
    const line = e.curve.filter(p => val(p) > 0).slice().sort((a, b) => a.recall - b.recall);
    marks += `<g class="${c}"><path class="ln${c === "f" ? " f" : ""}" stroke="${col}" d="${line.map((p, i) => `${i ? "L" : "M"}${X(p.recall).toFixed(1)} ${Y(val(p)).toFixed(1)}`).join("")}"/>`;
    for (const p of line) {
      const sx = X(p.recall), sy = Y(val(p));
      PTS.push({e, p, sx, sy});
      marks += `<circle fill="${col}" cx="${sx.toFixed(1)}" cy="${sy.toFixed(1)}" r="${c === "f" ? 3.2 : 2.6}"/>`;
    }
    const w = pick(e);
    if (w && val(w) > 0)
      marks += `<circle class="rp" stroke="${col}" cx="${X(w.recall).toFixed(1)}" cy="${Y(val(w)).toFixed(1)}" r="5"/>`;
    marks += "</g>";
    const last = line[line.length - 1];
    if (last) labs.push({e, c, col, x: X(last.recall), y: Y(val(last))});
  }
  // Names sit in the right margin beside where each line ends, spread so none
  // overlap, with a leader back to the line. Lines that end at the same height
  // are ordered by name: ordering them by draw order would move a name when it
  // is hovered, putting a different name under the pointer.
  labs.sort((a, b) => a.y - b.y || name(a.e.engine_id).localeCompare(name(b.e.engine_id)));
  const gap = 13.5, gx = W - m.r + 14;
  labs.forEach((l, i) => { l.ly = Math.max(l.y, i ? labs[i - 1].ly + gap : m.t); });
  for (let i = labs.length - 1; i >= 0; i--) {
    const cap = i === labs.length - 1 ? H - m.b : labs[i + 1].ly - gap;
    if (labs[i].ly > cap) labs[i].ly = cap;
  }
  LABS = [];
  let names = "";
  for (const l of labs) {
    const d = l.c === "dim" ? " dim" : "";
    names += `<path class="ld${d}" stroke="${l.col}" d="M${(l.x + 5).toFixed(1)} ${l.y.toFixed(1)}L${gx - 5} ${l.ly.toFixed(1)}"/>`
          + `<text class="lb${l.c ? " " + l.c : ""}" fill="${l.col}" x="${gx}" y="${l.ly.toFixed(1)}">${esc(name(l.e.engine_id))}</text>`;
    LABS.push({id: l.e.engine_id, x: gx, y: l.ly});
  }
  svg.innerHTML = g + marks + names;
}

function legend() {
  const {es} = view();
  const order = es.filter(e => (e.curve || []).length).sort((a, b) => (pick(b)?.qps || 0) - (pick(a)?.qps || 0));
  $("legend").innerHTML = order.map(e => {
    const col = COLOR[e.engine_id] || "#A0A4AE";
    return `<button class="chip" data-id="${esc(e.engine_id)}" aria-pressed="${focus.has(e.engine_id)}" style="--c:${col}"><i class="sw" style="background:${col}"></i>${esc(name(e.engine_id))}</button>`;
  }).join("");
}

function render() { controls(); results(); chart(); legend(); }
function setSort(k) {
  if (S.key === k) S.dir = -S.dir; else { S.key = k; S.dir = DIR[k]; }
}
function onSeg(id, fn) {
  $(id).addEventListener("click", ev => {
    const b = ev.target.closest("button");
    if (b) { fn(b.dataset.v); render(); }
  });
}
onSeg("f-case", v => {
  S.case = CASES.find(c => String(c.case_id) === v) || S.case;
  try { history.replaceState(null, "", `#${label(S.case).toLowerCase()}`); } catch (_) {}
});
onSeg("f-idx", v => { S.idx = v; });
onSeg("f-rec", v => { S.bar = Number(v); });
onSeg("f-ord", v => { if (S.key !== v) setSort(v); });

$("rs").addEventListener("click", ev => {
  const s = ev.target.closest(".sort");
  if (s) { setSort(s.dataset.k); controls(); results(); return; }
  const r = ev.target.closest(".rr");
  if (!r) return;
  const id = r.dataset.id, row = r.parentElement, det = row.querySelector(".det");
  open.has(id) ? open.delete(id) : open.add(id);
  const o = open.has(id);
  row.classList.toggle("open", o);
  r.setAttribute("aria-expanded", String(o));
  det.hidden = !o;
});
$("metric").addEventListener("click", ev => {
  const b = ev.target.closest("button");
  if (!b) return;
  S.metric = b.dataset.m;
  for (const x of $("metric").querySelectorAll("button")) x.setAttribute("aria-pressed", String(x === b));
  chart();
});
$("legend").addEventListener("click", ev => {
  const b = ev.target.closest("button");
  if (!b) return;
  const id = b.dataset.id;
  focus.has(id) ? focus.delete(id) : focus.add(id);
  b.setAttribute("aria-pressed", String(focus.has(id)));
  chart();
});

const svg = $("chart"), tip = $("tip");
svg.addEventListener("pointermove", ev => {
  const ctm = svg.getScreenCTM();
  if (!ctm) return;
  const q = new DOMPoint(ev.clientX, ev.clientY).matrixTransform(ctm.inverse());
  // A name in the margin highlights its line.
  const lab = LABS.find(l => q.x >= l.x - 8 && Math.abs(q.y - l.y) < 7);
  if (lab) {
    if (lab.id !== hover) { hover = lab.id; chart(); }
    tip.hidden = true;
    return;
  }
  let best = null, bd = 22 * 22;
  for (const o of PTS) {
    const d = (o.sx - q.x) ** 2 + (o.sy - q.y) ** 2;
    if (d < bd) { bd = d; best = o; }
  }
  const id = best ? best.e.engine_id : null;
  if (id !== hover) { hover = id; chart(); }
  if (!best) { tip.hidden = true; return; }
  tip.innerHTML = `<b><i class="sw" style="background:${COLOR[best.e.engine_id] || "#A0A4AE"}"></i>${esc(name(best.e.engine_id))}</b><span>${esc(params(best.p.params))}</span>`
    + `<span>recall ${f4(best.p.recall)}</span><span>QPS ${f1(best.p.qps)}</span><span>p99 ${f1(best.p.p99_ms)} ms</span>`;
  tip.style.left = `${best.sx / 960 * 100}%`;
  tip.style.top = `${best.sy / 430 * 100}%`;
  tip.hidden = false;
});
svg.addEventListener("pointerleave", () => { hover = null; tip.hidden = true; chart(); });

const want = (location.hash || "").slice(1).toLowerCase();
S.case = CASES.find(c => label(c).toLowerCase() === want) || CASES[0];
render();
</script>
'''

REPO = "https://github.com/infino-ai/open-vdbbench"
URL = "https://infino.ai/open-vdbbench/"
DESCRIPTION = ("Vector databases measured on one Azure Standard_D16ads_v7 with "
               "VectorDBBench at 1M and 10M Cohere vectors.")

# The hosted copy is a whole document. The artifact copy must not be one,
# because the artifact wraps the page in its own skeleton.
HEAD = """<!doctype html>
<!-- Generated by scripts/build_page.py in https://github.com/infino-ai/open-vdbbench. Edit it there, not here. -->
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="description" content="{description}">
<link rel="canonical" href="{url}">
<meta property="og:title" content="Open Vector Database Benchmark">
<meta property="og:description" content="{description}">
<meta property="og:url" content="{url}">
<meta property="og:type" content="website">
"""


def runs_as(e):
    """What was actually started for the engine: an image, compose, or a package."""
    image = e.get("image")
    if image == "compose":
        return "docker compose"
    if image:
        return image
    how = f"pip {e['pip_pins']}" if e.get("pip_pins") else "in-process"
    if (e.get("env") or {}).get("INFINO_BENCH_SERVE"):
        how += ", loopback server"
    return how


def main(root="."):
    with open(os.path.join(root, "data", "leaderboard_data.json"), encoding="utf-8") as fh:
        data = json.load(fh)
    with open(os.path.join(root, "harness", "matrix.json"), encoding="utf-8") as fh:
        matrix = json.load(fh)
    engines = {e["id"]: {"display": e["display"], "runs_as": runs_as(e),
                         "quantized": e.get("quantized")}
               for e in matrix["engines"]}
    html = (HTML
            .replace("__DATA__", json.dumps(data, separators=(",", ":")))
            .replace("__ENGINES__", json.dumps(engines, separators=(",", ":")))
            .replace("__DEFAULTS__", json.dumps(matrix["defaults"], separators=(",", ":")))
            .replace("__REPO__", REPO))
    # site/index.html is the document served at infino.ai/open-vdbbench.
    # leaderboard/index.html is the same page without the document wrapper,
    # for previews, and is not committed.
    head_end = html.index("</style>") + len("</style>")
    hosted = (HEAD.format(description=DESCRIPTION, url=URL) + html[:head_end]
              + "\n</head>\n<body>\n" + html[head_end:] + "</body>\n</html>\n")
    for out, text in ((os.path.join(root, "leaderboard", "index.html"), html),
                      (os.path.join(root, "site", "index.html"), hosted)):
        os.makedirs(os.path.dirname(out), exist_ok=True)
        # The page carries arrows and middle dots, and the default encoding on
        # Windows cannot represent them.
        with open(out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        print(f"wrote {out} ({len(text):,} bytes)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".")

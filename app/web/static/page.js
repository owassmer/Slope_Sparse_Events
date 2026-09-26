// The one-screen analysis page. Path probabilities, tiles, outcomes and each question's 0% / Jev / 100% bar are
// recomputed here from the payload's edges and per-path means; the daily series come from the server's reweight.
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  let D = JSON.parse($("page-data").textContent);
  const API = document.querySelector(".app").dataset.api;
  const S = { view: "lit", mtab: "exposure", rtab: "probs", tile: null, overrides: {}, sel: {}, cls: null,
              omode: "weighted", detail: null, event: D.event, settings: {}, lat: [] };
  window.__page = S;
  const CLS_COL = ["#b91c1c", "#dc2626", "#f87171", "#15803d", "#22c55e", "#2563eb", "#9ca3af", "#a16207"];
  const NEUTRAL = D.meta.judgments === "neutral";

  // --- formatting ---------------------------------------------------------------------------------------------
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const money = (c, dp) => {
    const v = c / 100, a = Math.abs(v), s = v < 0 ? "−" : "";
    if (a >= 1e6) return `${s}$${(a / 1e6).toFixed(dp ?? 2)}M`;
    if (a >= 1e3) return `${s}$${(a / 1e3).toFixed(dp ?? 0)}k`;
    return `${s}$${a.toFixed(0)}`;
  };
  const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const pct = (p, dp = 1) => `${(100 * p).toFixed(dp)}%`;
  const fdate = (iso) => new Date(iso + "T12:00:00").toLocaleDateString("en-GB", { day: "numeric", month: "short" });
  const fdateY = (iso) => new Date(iso + "T12:00:00").toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });

  // --- probability arithmetic ---------------------------------------------------------------------------------
  const N = D.nodes.length, P = D.paths.edges.length;
  const jevDist = (i) => D.nodes[i].jev;
  const dist = (i, ov = S.overrides) => ov[D.nodes[i].key] || jevDist(i);
  const neutralDist = (i) => D.nodes[i].branches.map(() => 1 / D.nodes[i].branches.length);
  function pathProbs(get) {
    const node = []; for (let i = 0; i < N; i++) node.push(get(i));
    const comp = D.composites.map((conj) => {
      let y = 0;
      for (const c of conj) { let p = 1; for (const [n, b] of c) p *= node[n][b]; y += p; }
      y = Math.min(Math.max(y, 0), 1); return [y, 1 - y];
    });
    const out = new Float64Array(P);
    for (let i = 0; i < P; i++) {
      const e = D.paths.edges[i]; let p = 1;
      for (let j = 0; j < e.length; j += 2) p *= e[j] >= 0 ? node[e[j]][e[j + 1]] : comp[-e[j] - 1][e[j + 1]];
      out[i] = p;
    }
    return out;
  }
  const expect = (probs, key) => { const x = D.paths.scalars[key]; let s = 0; for (let i = 0; i < P; i++) s += probs[i] * x[i]; return s; };
  // A slider sets the selected branch; the other branches keep their proportions in `base`, the distribution when
  // the drag started (uniform if they were all zero), so the result depends only on where the slider ends up.
  function withBranch(i, b, x, base = dist(i)) {
    const rest = base.reduce((s, v, k) => s + (k === b ? 0 : v), 0), n = base.length;
    return base.map((v, k) => (k === b ? x : rest > 0 ? (1 - x) * v / rest : (1 - x) / (n - 1)));
  }

  // --- tiles ------------------------------------------------------------------------------------------------------
  const TILES = [
    { key: "collected", label: "Collected", fmt: money, good: +1, tab: "collections", hl: "collected" },
    { key: "unrecovered", label: "Unrecovered", fmt: money, good: -1, tab: "collections", hl: "past_due" },
    { key: "petition_p", label: "Bankruptcy probability", fmt: (p) => pct(p), good: -1, tab: "exposure", hl: "petition" },
    { key: "peak_outstanding", label: "Peak outstanding", fmt: money, good: 0, tab: "exposure", hl: "outstanding" },
    { key: "stayed", label: "Frozen by bankruptcy", fmt: money, good: -1, tab: "exposure", hl: "frozen" },
    { key: "preference", label: "Clawback exposure", fmt: money, good: -1, tab: "collections", hl: "clawback" },
  ];
  let probs = pathProbs((i) => dist(i));
  let steps = null;  // [bank, + case facts, + Jev] per tile key
  function attribution() {
    const neu = pathProbs(neutralDist);
    steps = {};
    for (const t of TILES) steps[t.key] = [D.bank.scalars[t.key], expect(neu, t.key), expect(probs, t.key)];
  }
  function renderTiles() {
    attribution();
    $("tiles").innerHTML = TILES.map((t) => {
      const [b, , e] = steps[t.key], d = e - b, main = S.view === "bank" ? b : e;
      const dcls = Math.abs(d) < 1e-9 || !t.good ? "" : (d > 0) === (t.good < 0) ? "up" : "down";
      const dtxt = (d >= 0 ? "+" : "") + (t.key === "petition_p" ? pct(d) : money(d));
      return `<div class="tile${S.tile === t.key ? " sel" : ""}" data-k="${t.key}"><div class="l">${t.label}</div>
        <div class="v">${t.fmt(main)}</div>
        <div class="s">Bank only ${t.fmt(b)} · With litigation ${t.fmt(e)}</div><div class="s">Difference <span class="d ${dcls}">${dtxt}</span></div></div>`;
    }).join("");
    document.querySelectorAll(".tile").forEach((el) => {
      el.onclick = () => { const t = TILES.find((x) => x.key === el.dataset.k); S.tile = t.key; S.mtab = t.tab; renderTabs(); renderTiles(); renderMain(); };
      el.onmousemove = (ev) => {
        const t = TILES.find((x) => x.key === el.dataset.k), [b, r, e] = steps[t.key];
        showTip(ev, `<table><tr><td>Bank only</td><td>${t.fmt(b)}</td></tr><tr><td>+ case facts</td><td>${t.fmt(r)}</td></tr>
          <tr><td>+ Jev${NEUTRAL ? " (no answers yet)" : ""}</td><td>${t.fmt(e)}</td></tr></table>`);
      };
      el.onmouseleave = hideTip;
    });
  }
  function showTip(ev, html) {
    const tip = $("tip"); tip.innerHTML = html; tip.style.display = "block";
    const w = tip.offsetWidth, h = tip.offsetHeight;
    tip.style.left = `${Math.min(ev.clientX + 14, innerWidth - w - 8)}px`;
    tip.style.top = `${Math.min(ev.clientY + 14, innerHeight - h - 8)}px`;
  }
  const hideTip = () => { $("tip").style.display = "none"; };

  // --- charts -----------------------------------------------------------------------------------------------------
  const days = D.dates.length, ix = (iso) => D.dates.indexOf(iso);
  function niceMax(v) { if (v <= 0) return 1; const e = 10 ** Math.floor(Math.log10(v)), m = v / e; return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 5 ? 5 : 10) * e; }
  function chart(host, o) {
    const W = Math.max(host.clientWidth, 300), H = Math.max(host.clientHeight - 22, 160);
    const m = { l: 62, r: o.right ? 46 : 12, t: 10, b: 22 }, w = W - m.l - m.r, h = H - m.t - m.b;
    const all = [...o.series.filter((s) => !s.right).flatMap((s) => s.y), ...(o.bands || []).flatMap((b) => [...b.lo, ...b.hi])];
    let lo = Math.min(0, ...all), hi = niceMax(Math.max(...all, 1));
    if (lo < 0) lo = -niceMax(-lo);
    const X = (t) => m.l + (w * t) / (days - 1), Y = (v) => m.t + h - (h * (v - lo)) / (hi - lo), Y2 = (p) => m.t + h - h * p;
    const path = (ys, f) => ys.map((v, t) => `${t ? "L" : "M"}${X(t).toFixed(1)},${f(v).toFixed(1)}`).join("");
    let g = "";
    for (let k = 0; k <= 4; k++) {
      const v = lo + ((hi - lo) * k) / 4;
      g += `<line x1="${m.l}" x2="${m.l + w}" y1="${Y(v)}" y2="${Y(v)}" stroke="#f1f5f9"/><text x="${m.l - 6}" y="${Y(v) + 4}" text-anchor="end">${money(v, 1)}</text>`;
      if (o.right) g += `<text x="${m.l + w + 6}" y="${Y2(k / 4) + 4}">${(25 * k).toFixed(0)}%</text>`;
    }
    D.dates.forEach((d, t) => { if (d.endsWith("-01")) g += `<text x="${X(t)}" y="${H - 6}" text-anchor="middle">${MON[+d.slice(5, 7) - 1]}</text><line x1="${X(t)}" x2="${X(t)}" y1="${m.t + h}" y2="${m.t + h + 4}" stroke="#d1d5db"/>`; });
    for (const b of o.windows || []) {
      const a = Math.max(0, ix(b.from) < 0 ? 0 : ix(b.from)), z = ix(b.to) < 0 ? days - 1 : ix(b.to);
      g += `<rect x="${X(a)}" y="${m.t}" width="${Math.max(X(z) - X(a), 1)}" height="${h}" fill="#fef3c7" opacity=".55"/><text x="${X(a) + 4}" y="${m.t + 12}" fill="#92400e">${esc(b.label)}</text>`;
    }
    for (const b of o.bands || []) g += `<path d="${path(b.hi, Y)}L${[...b.lo].reverse().map((v, k) => `${X(days - 1 - k).toFixed(1)},${Y(v).toFixed(1)}`).join("L")}Z" fill="${b.color}" opacity=".18"/>`;
    for (const s of o.series) g += `<path d="${path(s.y, s.right ? Y2 : Y)}" fill="none" stroke="${s.color}" stroke-width="${s.width || 1.8}"${s.dash ? ' stroke-dasharray="5 4"' : ""}/>`;
    for (const p of o.pins || []) {
      const t = ix(p.date); if (t < 0) continue;
      g += `<line x1="${X(t)}" x2="${X(t)}" y1="${m.t}" y2="${m.t + h}" stroke="var(--pin)" stroke-dasharray="3 3"/><text x="${t > 0.85 * days ? X(t) - 3 : X(t) + 3}" y="${m.t + 24 + 12 * (p.row || 0)}" fill="#92400e"${t > 0.85 * days ? ' text-anchor="end"' : ""}>${esc(p.label)}</text>`;
    }
    g += `<line id="hx" x1="0" x2="0" y1="${m.t}" y2="${m.t + h}" stroke="#9ca3af" visibility="hidden"/><rect x="${m.l}" y="${m.t}" width="${w}" height="${h}" fill="transparent" id="hov"/>`;
    host.querySelector("svg").setAttribute("viewBox", `0 0 ${W} ${H}`);
    host.querySelector("svg").innerHTML = g;
    const hov = host.querySelector("#hov"), hx = host.querySelector("#hx");
    hov.onmousemove = (ev) => {
      const r = hov.getBoundingClientRect(), t = Math.round(((ev.clientX - r.left) / r.width) * (days - 1));
      hx.setAttribute("x1", X(t)); hx.setAttribute("x2", X(t)); hx.setAttribute("visibility", "visible");
      showTip(ev, `<b>${fdateY(D.dates[t])}</b><table>${o.hover(t).map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join("")}</table>`);
    };
    hov.onmouseleave = () => { hx.setAttribute("visibility", "hidden"); hideTip(); };
  }
  const cur = () => (S.view === "bank" ? D.bank : S.event);
  const legend = (items) => `<div class="legend">${items.map(([label, c, k]) => `<span><i class="${k || ""}" style="--c:${c}"></i>${label}</span>`).join("")}</div>`;
  function exposure(host) {
    const v = cur().daily, hl = S.tile, col = S.view === "bank" ? "var(--bank)" : "var(--lit)";
    const pins = [];
    if (D.pins.briefing_close) pins.push({ date: D.pins.briefing_close, label: "Briefing closes", row: 0 });
    if (D.pins.nasdaq) pins.push({ date: D.pins.nasdaq, label: "Nasdaq deadline", row: 1 });
    if (D.pins.coupon) pins.push({ date: D.pins.coupon, label: "Coupon", row: 2 });
    const windows = D.pins.ruling_window ? [{ from: D.pins.ruling_window[0], to: D.pins.ruling_window[1], label: "Ruling window" }] : [];
    const series = [{ y: v.limit_mean, color: "#94a3b8", dash: true, width: 1.4 },
                    { y: v.outstanding_mean, color: col, width: hl === "outstanding" ? 3 : 2 },
                    { y: v.frozen_mean, color: "#ea580c", width: hl === "frozen" ? 3 : 1.5 },
                    { y: v.petition_cum_p, color: "#b91c1c", right: true, width: hl === "petition" ? 3 : 1.6 }];
    host.innerHTML = legend([["Limit", "#94a3b8", "dash"], ["Outstanding", col], ["Frozen by bankruptcy", "#ea580c"], ["Bankruptcy probability, cumulative (right axis)", "#b91c1c"]]) + `<svg class="chart"></svg>`;
    chart(host, { series, pins, windows, right: true, hover: (t) => [["Limit", money(v.limit_mean[t])], ["Outstanding", money(v.outstanding_mean[t])],
      ["Frozen by bankruptcy", money(v.frozen_mean[t])], ["Bankruptcy probability", pct(v.petition_cum_p[t])]] });
  }
  function cash(host) {
    const v = cur().daily, col = S.view === "bank" ? "#64748b" : "#7c3aed";
    host.innerHTML = legend([["Available cash P5–P95", col, "band"], ["Median", col], ["30-day operating need", "#111827", "dash"]]) + `<svg class="chart"></svg>`;
    chart(host, { bands: [{ lo: v.cash_p5, hi: v.cash_p95, color: col }],
      series: [{ y: v.cash_p50, color: col }, { y: D.need_mean, color: "#111827", dash: true, width: 1.3 }],
      hover: (t) => [["P95", money(v.cash_p95[t])], ["Median", money(v.cash_p50[t])], ["P5", money(v.cash_p5[t])], ["30-day need", money(D.need_mean[t])]] });
  }
  const COLS = [["drawn", "Drawn"], ["due", "Due"], ["collected", "Collected"], ["past_due", "Past due"], ["frozen_due", "Frozen, due"], ["frozen_not_due", "Frozen, not yet due"],
                ["clawback", "Clawback exposure"], ["above_need_p5", "Cash above 30-day need after the amount due, P5"]];
  function collections(host) {
    const rows = cur().monthly, hl = { unrecovered: ["past_due", "frozen_due", "frozen_not_due"], stayed: ["frozen_due", "frozen_not_due"], preference: ["clawback"] }[S.tile] || [S.tile];
    const mname = (m) => `${MON[+m.slice(5, 7) - 1]} ${m.slice(0, 4)}`;
    host.innerHTML = `<table class="t"><tr><th>Month</th>${COLS.map(([k, l]) => `<th class="${hl.includes(k) ? "hl" : ""}">${l}</th>`).join("")}</tr>
      ${rows.map((r) => `<tr><td>${mname(r.month)}</td>${COLS.map(([k]) => `<td class="${hl.includes(k) ? "hl" : ""}">${r[k] === null ? "–" : money(r[k])}</td>`).join("")}</tr>`).join("")}</table>
      <p><button id="csv">Export CSV</button></p>`;
    $("csv").onclick = () => {
      const lines = [["month", ...COLS.map(([k]) => `${k}_cents`)].join(","), ...rows.map((r) => [r.month, ...COLS.map(([k]) => r[k] ?? "")].join(","))];
      const a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob([lines.join("\n") + "\n"], { type: "text/csv" }));
      a.download = `collections_${S.view === "bank" ? "bank_only" : "with_litigation"}.csv`; a.click();
    };
  }
  function renderMain() {
    const host = $("mview");
    ({ exposure, cash, collections })[S.mtab](host);
    $("filter").textContent = S.cls !== null && S.view === "lit" ? `Showing: ${D.classes[S.cls]}` : "";
  }
  function renderTabs() {
    document.querySelectorAll("#mtabs button").forEach((b) => b.classList.toggle("on", b.dataset.t === S.mtab));
    document.querySelectorAll("#rtabs button[data-t]").forEach((b) => b.classList.toggle("on", b.dataset.t === S.rtab));
    document.querySelectorAll("#viewsw button").forEach((b) => b.classList.toggle("on", b.dataset.v === S.view));
  }

  // --- outcomes ---------------------------------------------------------------------------------------------------
  // Each path carries its share of draws in each class ([class, share] pairs): a path's draws whose petition falls
  // inside the horizon are Filed, the rest keep the path's other outcome. So the Filed shares sum to the tile.
  function classProbs() { const c = new Float64Array(D.classes.length); for (let i = 0; i < P; i++) for (const [k, s] of D.paths.class[i]) c[k] += probs[i] * s; return c; }
  const shareIn = (i, cls) => { for (const [k, s] of D.paths.class[i]) if (k === cls) return s; return 0; };
  function topSequences(cls, n = 3) {
    const m = new Map();
    for (let i = 0; i < P; i++) { const w = cls === null ? probs[i] : probs[i] * shareIn(i, cls); if (w > 0) m.set(D.paths.seq[i], (m.get(D.paths.seq[i]) || 0) + w); }
    return [...m.entries()].sort((a, b) => b[1] - a[1]).slice(0, n);
  }
  function renderOutcomes() {
    const cp = classProbs(), el = $("outcomes");
    let html = `<div class="ohead"><b>Outcomes by ${fdateY(D.meta.horizon)}</b><div class="seg" id="osw"><button data-m="weighted" class="${S.omode === "weighted" ? "on" : ""}">Weighted</button><button data-m="worst" class="${S.omode === "worst" ? "on" : ""}">Worst paths</button></div>${S.cls !== null ? `<button id="clr">All paths</button>` : ""}</div>`;
    if (S.omode === "weighted") {
      html += `<div class="sbar">${[...cp].map((p, c) => p > 0 ? `<div data-c="${c}" class="${S.cls === c ? "sel" : ""}" style="width:${100 * p}%;background:${CLS_COL[c]}" title="${esc(D.classes[c])} ${pct(p)}"></div>` : "").join("")}</div>
        <div class="skeys">${[...cp].map((p, c) => p > 0 ? `<span data-c="${c}"><i style="background:${CLS_COL[c]}"></i>${esc(D.classes[c])} ${pct(p)}</span>` : "").join("")}</div>
        <div class="seqs">${topSequences(S.cls).map(([s, p]) => `<div><b>${pct(S.cls === null ? p : p / (cp[S.cls] || 1))}</b>${esc(D.sequences[s])}</div>`).join("")}</div>`;
    } else {
      html += `<table class="t"><tr><th>Path</th><th>Unrecovered</th><th>Frozen if a filing lands at peak outstanding</th><th>Peak day</th></tr>
        ${D.worst.slice(0, 12).map((w) => `<tr><td>${esc(w.sequence)}</td><td>${money(w.unrecovered)}</td><td>${money(w.frozen_at_peak)}</td><td>${fdate(w.peak_day)}</td></tr>`).join("")}</table>`;
    }
    el.innerHTML = html;
    el.querySelectorAll("[data-c]").forEach((s) => (s.onclick = () => { S.cls = +s.dataset.c === S.cls ? null : +s.dataset.c; if (S.view === "bank") S.view = "lit"; renderTabs(); renderOutcomes(); refreshCharts(); }));
    el.querySelectorAll("#osw button").forEach((b) => (b.onclick = () => { S.omode = b.dataset.m; renderOutcomes(); }));
    if ($("clr")) $("clr").onclick = () => { S.cls = null; renderOutcomes(); refreshCharts(); };
  }

  // --- server reweight of the daily series ----------------------------------------------------------------------
  let ctrl = null;
  async function refreshCharts(t0) {
    if (ctrl) ctrl.abort();
    ctrl = new AbortController();
    try {
      const r = await fetch(`${API}/reweight`, { method: "POST", headers: { "Content-Type": "application/json" }, signal: ctrl.signal,
        body: JSON.stringify({ overrides: S.overrides, classes: S.cls === null ? null : [S.cls] }) });
      const v = await r.json();
      if (v) S.event = v;
      renderMain();
      if (t0) S.lat.push(performance.now() - t0);
    } catch (e) { if (e.name !== "AbortError") console.error(e); }
  }

  // --- probabilities --------------------------------------------------------------------------------------------
  const DECIDERS = ["Court", "Qorvo", "Akoustis", "Noteholders and Nasdaq"];
  const selB = (i) => S.sel[D.nodes[i].key] ?? 0;
  let sens = [];
  const dragBase = {};  // node index -> its distribution when the current drag started (cleared on change)
  function computeSens() {
    const at = expect(probs, "unrecovered");
    sens = D.nodes.map((n, i) => {
      const b = selB(i), var_ = (x) => expect(pathProbs((k) => (k === i ? withBranch(i, b, x) : dist(k))), "unrecovered");
      const lo = var_(0), hi = var_(1);
      return { lo, hi, at, range: Math.abs(hi - lo) };
    });
  }
  // The effect bar: a line from the value at 0% to the value at 100%, an arrowhead at the 100% end (the way 'yes'
  // moves Unrecovered) and a mark at the current value, all on the panel's shared scale.
  function effBar(s, pos) {
    const a = Math.min(s.lo, s.hi), z = Math.max(s.lo, s.hi), up = s.hi >= s.lo;
    return `<div class="ln" style="left:${pos(a)};width:calc(${pos(z)} - ${pos(a)})"></div>
      <div class="mk" style="left:${pos(s.lo)}" title="At 0% ${money(s.lo)}"></div>
      <div class="ah ${up ? "r" : "l"}" style="left:${pos(s.hi)}" title="At 100% ${money(s.hi)}"></div>
      <div class="mk j" style="left:${pos(s.at)}" title="Now ${money(s.at)}"></div>`;
  }
  function rowHtml(i, scale) {
    const n = D.nodes[i], b = selB(i), d = dist(i), ch = n.key in S.overrides, s = sens[i];
    const pos = (v) => `${(100 * (v - scale[0])) / (scale[1] - scale[0] || 1)}%`;
    const yesName = n.branches.length > 2 ? `More “${n.branches[b].replace(/_/g, " ")}”` : "Yes";
    const sel = n.branches.length > 2 ? `<select data-i="${i}">${n.branches.map((x, k) => `<option value="${k}"${k === b ? " selected" : ""}>${esc(x.replace(/_/g, " "))}</option>`).join("")}</select>` : `<span class="ctx">yes</span>`;
    return `<div class="row${ch ? " changed" : ""}" data-i="${i}"><div class="q" data-i="${i}" title="${esc(n.question)}">${esc(n.label || n.question)}${ch ? '<span class="dot"></span>' : ""}</div>
      ${n.sub ? `<div class="ctx">${esc(n.sub)}</div>` : ""}
      <div class="ctl">${sel}<input type="range" min="0" max="1000" value="${Math.round(1000 * d[b])}" data-i="${i}"><span class="p" id="p${i}">${pct(d[b], 0)}</span>${ch ? `<button class="rs" data-i="${i}">Reset</button>` : "<span></span>"}</div>
      <div class="eff"><div class="rng">${effBar(s, pos)}</div><span class="dir">${Math.abs(s.hi - s.lo) < 100 ? "No effect on Unrecovered" : `${esc(yesName)} ${s.hi > s.lo ? "raises" : "lowers"} Unrecovered ${money(Math.abs(s.hi - s.lo))}`}</span></div>
      <div class="rngv"><span>0%: <b>${money(s.lo)}</b></span><span>${NEUTRAL ? "now" : "Jev"}: <b>${money(s.at)}</b></span><span>100%: <b>${money(s.hi)}</b></span></div></div>`;
  }
  function renderProbs() {
    if (S.detail !== null) return renderDetail();
    const lo = Math.min(...sens.map((s) => Math.min(s.lo, s.hi))), hi = Math.max(...sens.map((s) => Math.max(s.lo, s.hi)));
    const label = NEUTRAL ? D.meta.judgments_note : D.meta.probability_label || "";  // says 'Model judgment' once
    let html = `<div class="ohead"><span class="ctx">${esc(label)}</span><div class="sp" style="flex:1"></div>${Object.keys(S.overrides).length ? '<button id="rsall">Reset all</button>' : ""}</div>`;
    for (const g of DECIDERS) {
      const rows = D.nodes.map((n, i) => i).filter((i) => D.nodes[i].decider === g).sort((a, b) => sens[b].range - sens[a].range);
      if (rows.length) html += `<div class="ghead">${g}</div>` + rows.map((i) => rowHtml(i, [lo, hi])).join("");
    }
    const panel = $("rpanel"), top = panel.scrollTop;
    panel.innerHTML = html;
    panel.scrollTop = top;
    panel.querySelectorAll("input[type=range]").forEach((r) => {
      const start = () => { const i = +r.dataset.i; if (!(i in dragBase)) dragBase[i] = dist(i).slice(); };
      r.onpointerdown = start;
      r.oninput = () => {
        start();
        const t0 = performance.now(), i = +r.dataset.i, b = selB(i);
        S.overrides[D.nodes[i].key] = withBranch(i, b, r.value / 1000, dragBase[i]);
        probs = pathProbs((k) => dist(k));
        $(`p${i}`).textContent = pct(r.value / 1000, 0);
        renderTiles(); renderOutcomes(); refreshCharts(t0);
      };
      r.onchange = () => { delete dragBase[+r.dataset.i]; computeSens(); renderProbs(); };
    });
    panel.querySelectorAll("select").forEach((s) => (s.onchange = () => { S.sel[D.nodes[+s.dataset.i].key] = +s.value; computeSens(); renderProbs(); }));
    panel.querySelectorAll(".rs").forEach((b) => (b.onclick = () => { delete S.overrides[D.nodes[+b.dataset.i].key]; recompute(); }));
    panel.querySelectorAll(".q").forEach((q) => (q.onclick = () => { S.detail = +q.dataset.i; renderProbs(); }));
    if ($("rsall")) $("rsall").onclick = () => { S.overrides = {}; recompute(); };
    atEnd();
  }
  function recompute() { probs = pathProbs((k) => dist(k)); computeSens(); renderTiles(); renderOutcomes(); renderRight(); refreshCharts(); }

  function renderDetail() {
    const i = S.detail, n = D.nodes[i], det = n.detail, d = dist(i);
    const dl = n.branches.map((b, k) => `${esc(b.replace(/_/g, " "))} ${pct(d[k])}`).join(" · ");
    const facts = (det.assumptions || []).map((a) => `<li>Given: ${esc(a)}</li>`).join("")
      + Object.entries(det.facts || {}).map(([k, v]) => `<li>${esc(k.replace(/_/g, " "))}: ${esc(typeof v === "object" ? JSON.stringify(v) : v)}</li>`).join("");
    const quotes = (det.quotes || []).map((q) => `<blockquote>“${esc(q.quote)}”<br><span class="ctx">${esc(q.source)}${q.date ? `, ${esc(q.date)}` : ""}${q.link ? ` · <a href="${esc(q.link)}" target="_blank" rel="noopener">source</a>` : ""}</span></blockquote>`).join("");
    const ans = det.answer ? `<pre>${esc(JSON.stringify(det.answer, null, 1))}</pre>` : `<p class="ctx">No Jev answer yet.</p>`;
    $("rpanel").innerHTML = `<div class="detail"><button class="back" id="back">← Probabilities</button>
      <div style="font-weight:600">${esc(n.question)}</div><div class="ctx">${esc(n.context)} · decided by ${esc(n.actor)}</div>
      <p><b>${dl}</b>${n.key in S.overrides ? " (changed)" : ""}</p>
      ${det.steps.map((s) => `<div class="step"><span class="tag ${s.tag}">${s.tag}</span><span>${esc(s.text)}</span></div>`).join("")}
      <div class="ghead">Facts given to Jev</div><ul class="ctx">${facts || "<li>None</li>"}</ul>
      <div class="ghead">Sources</div>${quotes || '<p class="ctx">No quoted passages on this page yet.</p>'}
      <div class="ghead">Jev's answer</div>${ans}</div>`;
    $("back").onclick = () => { S.detail = null; renderProbs(); };
    atEnd();
  }
  function renderTerms() {
    const t = D.case_terms;
    $("rpanel").innerHTML = `<div class="ghead">Judgment components</div><table class="t"><tr><th>Component</th><th>Amount</th><th>Status</th><th>Source</th></tr>
      ${(t.components || []).map((c) => `<tr><td>${esc(c.label)}${c.remittitur ? `<div class="ctx">remittitur scenario ${esc(c.remittitur)}</div>` : ""}</td><td>${esc(c.amount)}</td><td>${esc(c.status)}</td><td>${esc(c.source)}</td></tr>`).join("")}</table>
      ${(t.notes || []).map((n) => `<div class="ghead">${esc(n.title)}</div><table class="t">${n.terms.map(([k, v]) => `<tr><td>${esc(k)}</td><td style="text-align:left">${esc(v)}</td></tr>`).join("")}</table>`).join("")}
      <div class="ghead">Deadlines</div><table class="t">${(t.deadlines || []).map((d) => `<tr><td>${fdateY(d.date)}</td><td style="text-align:left">${esc(d.what)}</td></tr>`).join("")}</table>`;
  }
  let polling = null;
  function renderSettings(prog) {
    const cur = D.settings_value || {};
    const want = { ...cur, ...S.settings };
    const changed = D.settings.some((s) => JSON.stringify(want[s.key] ?? s.value) !== JSON.stringify(s.value));
    $("rpanel").innerHTML = D.settings.map((s) => {
      const v = want[s.key] ?? s.value, dot = JSON.stringify(v) !== JSON.stringify(s.value) ? '<span class="dot"></span>' : "";
      return `<div class="setting"><span>${esc(s.label)}${dot}</span><div class="seg">${s.options.map(([o, l]) => `<button data-k="${s.key}" data-o='${JSON.stringify(o)}' class="${JSON.stringify(o) === JSON.stringify(v) ? "on" : ""}">${esc(l)}</button>`).join("")}</div></div>`;
    }).join("") + `<p>${changed ? '<button id="sreset">Reset</button>' : ""}</p>`
      + (prog && prog.running ? `<div class="prog"><i style="width:${(100 * prog.done) / Math.max(prog.total, 1)}%"></i></div><div class="ctx">${esc({ tree: "Building the event tree", bins: "Event cash", simulate: "Simulating", stress: "Worst paths" }[prog.phase] || prog.phase)} ${prog.done} / ${prog.total}</div>` : "")
      + (prog && prog.error ? `<p class="ctx">${esc(prog.error)}</p>` : "");
    $("rpanel").querySelectorAll(".seg button").forEach((b) => (b.onclick = () => { S.settings[b.dataset.k] = JSON.parse(b.dataset.o); applySettings(); }));
    if ($("sreset")) $("sreset").onclick = () => { S.settings = Object.fromEntries(D.settings.map((s) => [s.key, s.value])); applySettings(); };
  }
  async function applySettings() {
    const want = { ...(D.settings_value || {}), ...S.settings };
    const r = await (await fetch(`${API}/settings`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ settings: want }) })).json();
    if (!r.progress.running) return reload();
    renderSettings(r.progress);
    clearInterval(polling);
    polling = setInterval(async () => {
      const p = await (await fetch(`${API}/progress`)).json();
      if (S.rtab === "settings") renderSettings(p);
      if (!p.running) { clearInterval(polling); if (!p.error) reload(); }
    }, 1000);
  }
  async function reload() {
    D = await (await fetch(`${API}/payload`)).json();
    S.event = D.event; S.overrides = {}; S.cls = null; S.settings = {};
    recompute();
  }
  function renderRight() { ({ probs: renderProbs, terms: renderTerms, settings: () => renderSettings() })[S.rtab](); atEnd(); }
  // The bottom fade shows while more of the list lies below.
  function atEnd() { const p = $("rpanel"); $("right").classList.toggle("atend", p.scrollTop + p.clientHeight >= p.scrollHeight - 2); }

  // --- init -------------------------------------------------------------------------------------------------------
  const lim = Math.floor(D.meta.limit_cents / 100).toLocaleString("en-US");
  $("title").textContent = `${D.meta.borrower.replace(/,? Inc\.?$/, "")} · Review date ${fdateY(D.meta.review)} · Limit $${lim} · Through ${fdateY(D.meta.horizon)}`;
  document.querySelectorAll("#mtabs button").forEach((b) => (b.onclick = () => { S.mtab = b.dataset.t; renderTabs(); renderMain(); }));
  document.querySelectorAll("#rtabs button[data-t]").forEach((b) => (b.onclick = () => { S.rtab = b.dataset.t; S.detail = null; renderTabs(); renderRight(); }));
  document.querySelectorAll("#viewsw button").forEach((b) => (b.onclick = () => { S.view = b.dataset.v; renderTabs(); renderTiles(); renderMain(); }));
  $("rpanel").addEventListener("scroll", atEnd);
  $("drawer").onclick = () => { $("right").classList.toggle("open"); atEnd(); };
  $("drawer-close").onclick = () => $("right").classList.remove("open");
  let rz = null;
  window.addEventListener("resize", () => { clearTimeout(rz); rz = setTimeout(renderMain, 120); });
  computeSens(); renderTabs(); renderTiles(); renderOutcomes(); renderRight(); renderMain();
  refreshCharts();  // warms the server's reweight, so the first slider move is as quick as the rest
})();

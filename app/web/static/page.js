// The analysis page: one forecast of the line's dated cash flows, how the lawsuit can resolve, the judgment that
// matters most, one economic assumption, and what happened. Every number is recomputed here from the payload (path
// probabilities from the edges, expectations from per-path means) or comes from the server's /reweight (the daily and
// monthly series, and an assumption variant's figures). Words come from the payload or /static/words.json; no party
// name or figure is written here.
(async () => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const D = JSON.parse($("page-data").textContent);
  const API = document.body.dataset.api;
  const W = await (await fetch("/static/words.json")).json();
  const S = { overrides: {}, sel: {}, event: D.event, bank: D.bank, assumption: "central", vm: null, compare: null, open: null, reveal: false, outcome: null, focus: null };
  window.__page = S;

  // --- formatting -----------------------------------------------------------------------------------------------
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const fdate = (iso) => `${+iso.slice(8, 10)} ${MON[+iso.slice(5, 7) - 1]}`;
  const fyear = (iso) => `${fdate(iso)} ${iso.slice(0, 4)}`;
  const NAMES = { ...(D.parties || {}), horizon: fdate(D.meta.horizon), review: fdate(D.meta.review) };
  const fill = (t, o = {}) => String(t ?? "").replace(/\{(\w+)\}/g, (m, k) => (k in o ? o[k] : k in NAMES ? NAMES[k] : m));
  const money = (c) => {  // $823k, $1.2M, $66M; whole units unless under 10 of the unit
    if (c === null || c === undefined || Number.isNaN(c)) return "–";
    const v = c / 100, a = Math.abs(v), s = v < 0 ? "−" : "";
    if (a >= 1e6) { const m = a / 1e6; return `${s}$${m >= 100 ? Math.round(m) : +m.toFixed(1)}M`; }
    if (a >= 1e3) return `${s}$${Math.round(a / 1e3)}k`;
    return `${s}$${Math.round(a)}`;
  };
  const pct = (p) => (p === null || p === undefined ? "–" : `${Math.round(100 * p)}%`);
  const pct1 = (p) => `${(100 * p).toFixed(1)}%`;
  const pts = (d) => { const r = Math.round(100 * d); return `${r > 0 ? "+" : r < 0 ? "−" : ""}${Math.abs(r)} ${Math.abs(r) === 1 ? "pt" : "pts"}`; };
  const dmoney = (d) => (Math.abs(d) < 50 ? "$0" : `${d > 0 ? "+" : "−"}${money(Math.abs(d))}`);
  const usdText = (t) => String(t ?? "").replace(/\bUSD\s?(?=\d)/g, "$");  // case-input text: "USD 5.0M" -> "$5.0M"
  const cents = (t) => Math.round(100 * parseFloat(String(t).replace(/[$,]/g, "")));
  const cap = (t) => (t ? t[0].toUpperCase() + t.slice(1) : t);
  const T = (k) => fill(W.terms[k]);
  const last = (a) => a[a.length - 1];

  // --- probability arithmetic -------------------------------------------------------------------------------------
  const N = D.nodes.length, P = D.paths.edges.length;
  const jevDist = (i) => D.nodes[i].jev;
  const dist = (i, ov = S.overrides) => ov[D.nodes[i].key] || jevDist(i);
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
  const hasKey = (k) => k in D.paths.scalars;
  const expect = (probs, key) => { const x = D.paths.scalars[key]; if (!x) return null; let s = 0; for (let i = 0; i < P; i++) s += probs[i] * x[i]; return s; };
  const BE = D.bank.edges || [];
  const bankProbs = (get) => BE.map((e) => { let p = 1; for (let j = 0; j < e.length; j += 2) p *= get(e[j])[e[j + 1]]; return p; });
  const bexpect = (bp, key) => {
    const x = (D.bank.path_scalars || {})[key]; if (!x) return (D.bank.scalars || {})[key] ?? null;
    let s = 0; for (let i = 0; i < bp.length; i++) s += bp[i] * x[i]; return s;
  };
  function withBranch(i, b, x, base = dist(i)) {  // branch b takes x; the others keep their proportions
    const rest = base.reduce((s, v, k) => s + (k === b ? 0 : v), 0), n = base.length;
    return base.map((v, k) => (k === b ? x : rest > 0 ? (1 - x) * v / rest : (1 - x) / (n - 1)));
  }
  const isBank = (i) => D.nodes[i].view === "bank";
  const selB = (i) => S.sel[D.nodes[i].key] ?? 0;
  let probs = null, bprobs = null;
  function recompute() { probs = pathProbs((k) => dist(k)); bprobs = bankProbs((k) => dist(k)); }

  // --- the figures: central from the paths here; a variant's from the server (/reweight) ----------------------------
  const KEYS = ["funded", "due", "collected", "past_due", "frozen_due", "not_yet_due", "petition_p", "clawback", "stayed"];
  const variant = (id = S.assumption) => (D.assumptions || []).find((a) => a.id === id);
  function figures() {
    const r = {}, b = {}, v = S.assumption !== "central" ? variant() : null;
    for (const k of KEYS) {
      r[k] = v ? (S.vm || v.metrics)[k] ?? null : expect(probs, k);
      b[k] = v ? (v.ordinary.metrics || {})[k] ?? null : bexpect(bprobs, k);
    }
    r.stayed = r.stayed ?? last(S.event.daily.frozen_mean); b.stayed = b.stayed ?? last(S.bank.daily.frozen_mean);
    return { full: given(r), ordinary: given(b) };
  }
  // the rate on due; the balance at filing and preference exposure given a filing (a lender's exposure at default),
  // from the weighted figures (zero where no filing) and the filing probability
  const given = (f) => ({ ...f, rate: f.due ? f.collected / f.due : null, stayed_if: f.petition_p > 0 && f.stayed != null ? f.stayed / f.petition_p : null,
    clawback_if: f.petition_p > 0 && f.clawback != null ? f.clawback / f.petition_p : null });
  const vFigures = (a) => given(a.metrics);

  // --- hover tips -------------------------------------------------------------------------------------------------
  function showTip(ev, html, above) {
    const tip = $("tip"); tip.innerHTML = html; tip.style.display = "block";
    const w = tip.offsetWidth, h = tip.offsetHeight, up = above ? ev.clientY - h - 14 >= 0 : ev.clientY + 16 + h > innerHeight;
    tip.style.left = `${Math.max(8, Math.min(ev.clientX - (above ? w / 2 : -14), innerWidth - w - 10))}px`;
    tip.style.top = `${up ? ev.clientY - h - 14 : ev.clientY + 16}px`;
  }
  const hideTip = () => { $("tip").style.display = "none"; };
  document.addEventListener("mouseover", (ev) => { const t = ev.target.closest("[data-tip]"); if (t) showTip(ev, esc(t.dataset.tip)); });
  document.addEventListener("mouseout", (ev) => { if (ev.target.closest("[data-tip]")) hideTip(); });

  // --- 1. situation -----------------------------------------------------------------------------------------------
  const notes = ((D.dispute || {}).notes || [])[0] || {};
  const claimBranch = ((D.verdict || {}).branches || []).find((b) => b.key === "claimant_theory");
  function renderSituation() {
    const L = W.line, l = D.line || {}, inst = l.installments || [];
    const owed = inst.reduce((s, x) => s + x[1], 0);
    const vals = { commenced: D.dispute.commenced ? fyear(D.dispute.commenced) : "–", trial_started: D.dispute.trial_started ? fyear(D.dispute.trial_started) : "–",
      claim: claimBranch ? money(claimBranch.lo) : "–", cash: money(D.opening_cash_cents), notes: money(notes.principal_cents),
      threshold: money(notes.default_threshold_cents), days: notes.default_days ?? "–", listing: notes.listing_deadline ? fyear(notes.listing_deadline) : "–" };
    const facts = [[W.facts.claim, vals.claim], [W.facts.cash, vals.cash], [W.facts.notes, vals.notes], [W.facts.listing, vals.listing]];
    $("s-situation").innerHTML = `<div class="sit"><div><h3>${esc(fill(W.sections.situation))} · ${esc(fyear(D.meta.review))}</h3><h1>${esc(D.meta.borrower)}</h1>
        ${D.meta.judgments_note ? `<p class="mute small">${esc(D.meta.judgments_note)}</p>` : ""}
        ${(D.narrative || []).map((t) => `<p>${esc(fill(t, vals))}</p>`).join("")}</div>
      <div class="line"><h3>${esc(L.title)}</h3><dl>
        <dt>${esc(L.limit)}</dt><dd>${money(D.meta.limit_cents)}<span class="mute small"> · ${esc(L.limit_note)}</span></dd>
        <dt>${esc(L.fee)}</dt><dd>${esc(fill(L.fee_value, { fee: `${(D.meta.fee_bps / 100).toFixed(1)}%` }))}</dd>
        <dt>${esc(L.repaid)}</dt><dd>${esc(fill(L.repaid_value, { n: D.meta.installments }))}</dd>
        <dt>${esc(L.draws)}</dt><dd>${esc(l.draw_rule || "")}</dd>
        ${l.opened ? `<dt>${esc(L.opened)}</dt><dd>${esc(fyear(l.opened))}</dd>` : ""}</dl>
        <h3>${esc(fill(L.state))}</h3><dl>
        <dt>${esc(L.outstanding)}</dt><dd>${money(l.principal_cents)}</dd>
        ${inst.length ? `<dt>${esc(L.due_next)}</dt><dd>${esc(fill(L.due_next_value, { amount: money(owed), first: fdate(inst[0][0]), last: fdate(last(inst)[0]) }))}</dd>` : ""}</dl></div></div>
      <div class="facts">${facts.map(([k, v]) => `<div><span class="mute small">${esc(k)}</span><b>${esc(v)}</b></div>`).join("")}</div>`;
  }

  // --- 2. the line's forecast -------------------------------------------------------------------------------------
  function tileHtml(f) {
    const nyd = f.not_yet_due === null || f.not_yet_due === undefined;
    const t = [["due", money(f.due)], ["collected", money(f.collected), `${T("rate")} ${pct(f.rate)}`], ["past_due", money(f.past_due)],
      ["frozen_due", money(f.frozen_due)], ["not_yet_due", nyd ? "–" : money(f.not_yet_due), nyd ? W.notes.unavailable : ""],
      ["funded", money(f.funded)], ["filing", pct(f.petition_p)], ["stayed", money(f.stayed_if), "", money(f.stayed)],
      ["preference", money(f.clawback_if), "", money(f.clawback)]];
    return `<div class="tiles">${t.map(([k, v, s, wt]) => `<div><span class="mute small${W.notes[k] ? " hastip" : ""}"${W.notes[k] ? ` data-tip="${esc(fill(W.notes[k], { weighted: wt ?? "" }))}"` : ""}>${esc(T(k))}</span><b>${esc(v)}</b>${s ? `<span class="sub">${esc(s)}</span>` : ""}</div>`).join("")}</div>`;
  }
  const monthEnd = (ym) => { let t = -1; D.dates.forEach((d, k) => { if (d.startsWith(ym)) t = k; }); return t; };
  function ladder(host) {
    const rows = S.event.monthly, Wd = host.clientWidth || 1000, H = 300, m = { l: 56, r: 16, t: 16, b: 30 }, w = Wd - m.l - m.r, h = H - m.t - m.b;
    const out = rows.map((r) => S.event.daily.outstanding_mean[monthEnd(r.month)]);
    const raw = Math.max(...rows.map((r) => Math.max(r.drawn, r.due, r.collected)), ...out, (D.line || {}).principal_cents || 0) || 1;
    const mag = 10 ** Math.floor(Math.log10(raw / 4)), step = [1, 2, 2.5, 5, 10].map((s) => s * mag).find((s) => s * 4 >= raw), top = step * 4;
    const Y = (v) => m.t + h - (h * v) / top, gw = w / rows.length, bw = Math.min(26, gw / 4.5);
    const COL = { drawn: "#b8c2cf", due: "#8a9199", collected: "var(--teal)" };
    let g = "";
    for (let k = 0; k <= 4; k++) { const v = step * k; g += `<line x1="${m.l}" x2="${m.l + w}" y1="${Y(v)}" y2="${Y(v)}" stroke="${k ? "#eceef0" : "#c9cdd2"}"/><text x="${m.l - 8}" y="${Y(v) + 4}" text-anchor="end">${money(v)}</text>`; }
    const hz = D.meta.horizon, partial = (ym) => hz.startsWith(ym) && last(D.dates) === hz && +hz.slice(8) < 28;
    const range = S.assumption === "central" && !Object.keys(S.overrides).length ? D.collected_range || [] : [];
    rows.forEach((r, j) => {
      const x0 = m.l + gw * j + gw / 2 - 1.5 * bw;
      ["drawn", "due", "collected"].forEach((k, q) => { g += `<rect x="${x0 + q * bw}" y="${Y(r[k])}" width="${bw - 2}" height="${Y(0) - Y(r[k])}" fill="${COL[k]}"/>`; });
      g += `<text x="${m.l + gw * j + gw / 2}" y="${H - 8}" text-anchor="middle">${partial(r.month) ? fill(W.forecast.to, { date: fdate(hz) }) : MON[+r.month.slice(5) - 1]}</text>`;
      g += `<rect class="hov" data-j="${j}" x="${m.l + gw * j}" y="${m.t}" width="${gw}" height="${h}" fill="transparent"/>`;
    });
    const px = (j) => m.l + gw * j + gw / 2;
    g += `<path d="${out.map((v, j) => `${j ? "L" : "M"}${px(j)},${Y(v)}`).join("")}" fill="none" stroke="#c0842b" stroke-width="2"/>` + out.map((v, j) => `<circle cx="${px(j)}" cy="${Y(v)}" r="3.5" fill="#c0842b"/>`).join("");
    host.innerHTML = `<svg class="ladder" viewBox="0 0 ${Wd} ${H}" style="height:${H}px">${g}</svg>
      <div class="lg"><span><i style="background:#b8c2cf"></i>${esc(T("funded"))}</span><span><i style="background:#8a9199"></i>${esc(W.terms.due.replace(" by {horizon}", ""))}</span><span><i style="background:var(--teal)"></i>${esc(T("collected"))}</span><span><i style="background:#c0842b;height:3px;vertical-align:3px"></i>${esc(T("outstanding"))}</span></div>`;
    host.querySelectorAll(".hov").forEach((el) => {
      el.onmousemove = (ev) => {
        const j = +el.dataset.j, r = rows[j], cum = S.event.daily.collected_mean[monthEnd(r.month)];
        const rg = range[j] ? `<br>${esc(W.forecast.range)}: ${money(range[j][0])} – ${money(range[j][1])} (${esc(W.terms.collected)} ${money(cum)})` : "";
        showTip(ev, esc(fill(W.forecast.months_hover, { month: `${MON[+r.month.slice(5) - 1]} ${r.month.slice(0, 4)}`, funded: money(r.drawn), due: money(r.due), collected: money(r.collected), outstanding: money(out[j]) })) + rg);
      };
      el.onmouseleave = hideTip;
    });
  }
  function cumChart(host) {  // collected to date: the mean, and the P5–P95 range at each month end where the data has it
    const Wd = host.clientWidth || 1000, H = 220, m = { l: 56, r: 16, t: 12, b: 28 }, w = Wd - m.l - m.r, h = H - m.t - m.b, days = D.dates.length;
    const mean = S.event.daily.collected_mean, rows = S.event.monthly, ends = rows.map((r) => monthEnd(r.month));
    const range = S.assumption === "central" && !Object.keys(S.overrides).length ? D.collected_range || [] : [];
    const raw = Math.max(...mean, ...range.map((x) => x[1])) || 1, mag = 10 ** Math.floor(Math.log10(raw / 4));
    const step = [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10].map((s) => s * mag).find((s) => s * 4 >= raw), top = step * 4;
    const X = (t) => m.l + (w * t) / (days - 1), Y = (v) => m.t + h - (h * v) / top;
    let g = "";
    for (let k = 0; k <= 4; k++) { const v = step * k; g += `<line x1="${m.l}" x2="${m.l + w}" y1="${Y(v)}" y2="${Y(v)}" stroke="${k ? "#eceef0" : "#c9cdd2"}"/><text x="${m.l - 8}" y="${Y(v) + 4}" text-anchor="end">${money(v)}</text>`; }
    D.dates.forEach((d, t) => { if (d.endsWith("-01")) g += `<text x="${X(t)}" y="${H - 8}" text-anchor="middle">${MON[+d.slice(5, 7) - 1]}</text>`; });
    const pts = ends.map((t, j) => [t, range[j]]).filter(([t, r]) => t >= 0 && r);
    g += pts.map(([t, r]) => { const x = X(t); return `<rect x="${x - 5}" y="${Y(r[1])}" width="10" height="${Math.max(Y(r[0]) - Y(r[1]), 1)}" fill="#cfe6e3"/>`
      + `<line x1="${x - 7}" x2="${x + 7}" y1="${Y(r[1])}" y2="${Y(r[1])}" stroke="#5fa69d" stroke-width="1.5"/><line x1="${x - 7}" x2="${x + 7}" y1="${Y(r[0])}" y2="${Y(r[0])}" stroke="#5fa69d" stroke-width="1.5"/>`
      + (x > m.l + w - 60 ? `<text x="${x - 9}" y="${Y(r[1]) + 4}" class="rl" text-anchor="end">${money(r[1])}</text>` : `<text x="${x + 9}" y="${Y(r[1]) + 4}" class="rl">${money(r[1])}</text>`); }).join("");
    g += `<path d="${mean.map((v, t) => `${t ? "L" : "M"}${X(t).toFixed(1)},${Y(v).toFixed(1)}`).join("")}" fill="none" stroke="var(--teal)" stroke-width="2.2"/>`;
    host.innerHTML = `<svg class="ladder" viewBox="0 0 ${Wd} ${H}" style="height:${H}px">${g}</svg>
      <div class="lg"><span><i style="background:var(--teal);height:3px;vertical-align:3px"></i>${esc(W.forecast.cum_mean)}</span>${pts.length ? `<span><i style="background:#cfe6e3"></i>${esc(W.forecast.cum_range)}</span>` : ""}</div>`;
  }
  function renderForecast() {
    const sec = $("s-forecast"), f = figures().full;
    if (!sec.querySelector(".lad")) {
      sec.innerHTML = `<h2>${esc(fill(W.sections.forecast))}</h2><div class="tl"></div><h4 class="sub">${esc(W.forecast.chart)}</h4><div class="lad"></div><h4 class="sub">${esc(W.forecast.cum_chart)}</h4><div class="cum"></div>
        <details class="assume"><summary>${esc(W.forecast.assumptions)}</summary><p class="mute small">${esc(W.forecast.assumptions_note)}</p>
        <dl class="common">${(D.common || []).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl></details>`;
    }
    sec.querySelector(".tl").innerHTML = tileHtml(f);
    ladder(sec.querySelector(".lad")); cumChart(sec.querySelector(".cum"));
  }

  // --- steps of a path ------------------------------------------------------------------------------------------------
  const FILING = new Set(D.filing_steps || []);
  const parseStep = (s) => { const m = /^(.*) \(([^)]*)\)$/.exec(s); return m ? { base: m[1], when: m[2] } : { base: s.replace(/, after the period$/, ""), when: "" }; };
  const SEQ = D.sequences.map((s) => (s ? s.split(" → ").map(parseStep) : []));
  function stepsOf(q) {  // up to and including the first filing (after a filing the line has stopped)
    const out = []; for (const st of SEQ[q]) { out.push(st); if (FILING.has(st.base)) return { steps: out, filed: true }; }
    return { steps: out, filed: false };
  }
  const linesHtml = (ls) => `<table class="pl">${ls.map(([a, b]) => `<tr><td>${esc(a)}</td><td>${esc(b)}</td></tr>`).join("")}</table>`;

  // --- 3. how the lawsuit can resolve ---------------------------------------------------------------------------------
  const V = D.verdict || {};
  function verdictGroups() {
    const bs = V.branches || [], groups = [{ k: -1, label: V.before, lo: null, hi: null }, ...bs.map((b, k) => ({ k, label: b.label, text: b.text, lo: b.lo, hi: b.hi })), { k: -2, label: W.resolve.other, lo: null, hi: null }];
    const pp = D.paths.scalars.petition_p, co = D.paths.scalars.collected;
    for (const g of groups) { g.p = 0; g.c = 0; g.f = 0; g.seq = new Map(); }
    const at = new Map(groups.map((g) => [g.k, g]));
    for (let i = 0; i < P; i++) {
      const g = at.get((V.path || [])[i] ?? -2); g.p += probs[i]; g.c += probs[i] * co[i]; g.f += probs[i] * pp[i];
      const q = D.paths.seq[i]; g.seq.set(q, (g.seq.get(q) || 0) + probs[i]);
    }
    return groups.filter((g) => g.p > 1e-9);
  }
  function follows(g) {
    const top = [...g.seq.entries()].sort((a, b) => b[1] - a[1]).slice(0, 3);
    return top.map(([q, m]) => {
      const st = stepsOf(q).steps.filter((s) => s.base !== g.label && s.base !== V.before);
      const txt = st.length ? st.map((s) => `${s.base}${s.when ? ` (${s.when})` : ""}`).join(" → ") : W.resolve.nothing;
      return `<div>${esc(fill(W.resolve.of_outcome, { p: pct(m / g.p) }))} · ${esc(txt)}</div>`;
    }).join("");
  }
  const amountText = (g) => (g.lo === null ? "–" : g.hi === 0 ? W.resolve.none : g.lo === g.hi ? money(g.lo) : `${money(g.lo)} – ${money(g.hi)}`);
  function attributionRows(f) {
    const R = W.resolve, rows = [["filing", "petition_p", pct, -1], ["collected", "collected", money, 1], ["rate", "rate", pct, 1], ["past_due", "past_due", money, -1], ["frozen_due", "frozen_due", money, -1], ["stayed", "stayed_if", money, -1]];
    return rows.map(([t, k, fmt, good]) => {
      const rd = (v) => (fmt === pct ? Math.round(100 * v) / 100 : Math.round(v / 1e5) * 1e5), a = f.ordinary[k], b = f.full[k], d = a === null || b === null ? null : rd(b) - rd(a);
      const cls = !d ? "" : (d > 0) === (good > 0) ? "good" : "bad";
      return `<tr><td>${esc(T(t))}</td><td>${fmt(a)}</td><td>${fmt(b)}</td><td class="${cls}">${d === null ? "–" : fmt === money ? dmoney(d) : pts(d)}</td></tr>`;
    }).join("") + `<tr><td colspan="4" class="mute small" style="text-align:left">${esc(R.attribution_note)}</td></tr>`;
  }
  const CLS = D.classes;
  const CCOL = ["#e3a008", "#e0582a", "#9b1c1c", "#4d7c5a", "#6b7280", "#5b7aa8", "#cfd4da", "#b8955a"];
  const isFiled = (k) => /^Filed/.test(CLS[k]);
  function classProbs() { const c = new Float64Array(CLS.length); for (let i = 0; i < P; i++) for (const [k, s] of D.paths.class[i]) c[k] += probs[i] * s; return c; }
  function apportion(shares, total = 100) {  // largest remainder
    const tot = shares.reduce((a, b) => a + b, 0) || 1, raw = shares.map((s) => (total * s) / tot), n = raw.map(Math.floor);
    let left = total - n.reduce((a, b) => a + b, 0);
    raw.map((r, k) => [r - n[k], k]).sort((a, b) => b[0] - a[0]).forEach(([, k]) => { if (left > 0) { n[k]++; left--; } });
    return n;
  }
  function dots() {  // filed first (so the filed squares equal the rounded filing probability), largest first
    const sh = [...classProbs()], ks = sh.map((_, k) => k), fi = ks.filter(isFiled), nf = ks.filter((k) => !isFiled(k));
    const sum = (g) => g.reduce((a, k) => a + sh[k], 0), [nF, nN] = apportion([sum(fi), sum(nf)]), n = [];
    for (const [g, t] of [[fi, nF], [nf, nN]]) apportion(g.map((k) => sh[k]), t).forEach((v, j) => (n[g[j]] = v));
    return ks.map((k) => ({ k, n: n[k], share: sh[k], filed: isFiled(k) })).sort((a, b) => (b.filed - a.filed) || (b.share - a.share));
  }
  function gridHtml() {
    const g = dots(), filed = g.filter((x) => x.filed).reduce((s, x) => s + x.n, 0);
    let sq = ""; g.forEach((x) => { for (let r = 0; r < x.n; r++) sq += `<i style="background:${CCOL[x.k]}" data-tip="${esc(`${CLS[x.k]}: ${x.n}%`)}"></i>`; });
    const item = (x) => `<li><i style="background:${CCOL[x.k]}"></i><span>${esc(CLS[x.k])}</span><b>${x.n}%</b></li>`;
    return `<div class="gbody"><div class="waffle">${sq}</div><div class="gleg"><h4>${esc(fill(W.resolve.filed))} <b>${filed}%</b></h4><ul>${g.filter((x) => x.filed && x.n).map(item).join("")}</ul>
      <h4>${esc(fill(W.resolve.not_filed))} <b>${100 - filed}%</b></h4><ul>${g.filter((x) => !x.filed && x.n).map(item).join("")}</ul></div></div>`;
  }
  const box = (x, y, anchor, size, text, cls = "", fillc = "", bg = "#fff") => {
    const w = text.length * size * 0.6 + 8, x0 = anchor === "end" ? x - w + 4 : x - 4;
    return `<rect x="${x0}" y="${y - size}" width="${w}" height="${size + 5}" fill="${bg}"/><text x="${x}" y="${y}"${anchor === "end" ? ' text-anchor="end"' : ""}${cls ? ` class="${cls}"` : ""}${fillc ? ` fill="${fillc}"` : ""}>${esc(text)}</text>`;
  };
  function renderWhen(host) {
    const Wd = host.clientWidth || 1000, H = 380, days = D.dates.length, B = S.bank.daily.petition_cum_p, R = S.event.daily.petition_cum_p;
    const top = Math.min(1, Math.max(0.1, Math.ceil(Math.max(last(B), last(R)) * 10 + 1) / 10));
    const m = { l: 40, r: 210, t: 70, b: 30 }, w = Wd - m.l - m.r, h = H - m.t - m.b;
    const X = (t) => m.l + (w * t) / (days - 1), Y = (p) => m.t + h - (h * p) / top, ix = (iso) => D.dates.indexOf(iso);
    let g = "";
    const rw = D.pins.ruling_window; if (rw && ix(rw[0]) >= 0) g += `<rect x="${X(ix(rw[0]))}" y="${m.t}" width="${X(ix(rw[1]) >= 0 ? ix(rw[1]) : days - 1) - X(ix(rw[0]))}" height="${h}" fill="#f4f5f6"/>`;
    for (let k = 0; k <= 4; k++) { const v = (top * k) / 4; g += `<line x1="${m.l}" x2="${m.l + w}" y1="${Y(v)}" y2="${Y(v)}" stroke="${k ? "#eceef0" : "#c9cdd2"}"/><text x="${m.l - 8}" y="${Y(v) + 4}" text-anchor="end">${pct(v)}</text>`; }
    D.dates.forEach((d, t) => { if (d.endsWith("-01")) g += `<text x="${X(t)}" y="${H - 8}" text-anchor="middle">${MON[+d.slice(5, 7) - 1]}</text>`; });
    const pins = [["ruling_window", rw && rw[0]], ["listing", D.pins.listing], ["coupon", D.pins.coupon]].filter(([, d]) => d && ix(d) >= 0).sort((a, b) => (a[1] < b[1] ? -1 : 1));
    let pt = ""; pins.forEach(([k, d], row) => { const x = X(ix(d)), y = 14 + (row % 3) * 17; g += `<line x1="${x}" x2="${x}" y1="${y + 4}" y2="${m.t + h}" stroke="#b9bec5" stroke-dasharray="2 3"/>`; pt += box(x + 5, y, "start", 12, `${fdate(d)} · ${W.dates[k]}`, "pin"); });
    const line = (ys, off = 0) => ys.map((v, t) => `${t ? "L" : "M"}${X(t).toFixed(1)},${(Y(v) - off).toFixed(1)}`).join("");
    g += `<path d="${line(B, 1.5)}" fill="none" stroke="#6b7178" stroke-width="2.2" stroke-dasharray="6 4"/><path d="${line(R)}" fill="none" stroke="var(--teal)" stroke-width="2.8"/>`;
    let yb = Y(last(B)), yr = Y(last(R)); if (Math.abs(yb - yr) < 36) { const mid = (yb + yr) / 2, s = yb >= yr ? 1 : -1; yb = mid + 18 * s; yr = mid - 18 * s; }
    const endLab = (y, col, name, p) => `<text x="${m.l + w + 10}" y="${y - 2}" fill="${col}" class="end">${esc(name)}</text><text x="${m.l + w + 10}" y="${y + 15}" fill="${col}" class="endv">${esc(pct(p))}</text>`;
    g += endLab(yb, "#6b7178", W.resolve.without, last(B)) + endLab(yr, "var(--teal)", W.resolve.with, last(R)) + pt;
    g += `<line id="whx" y1="${m.t}" y2="${m.t + h}" stroke="#9aa0a8" visibility="hidden"/><rect id="whov" x="${m.l}" y="${m.t}" width="${w}" height="${h}" fill="transparent"/>`;
    host.innerHTML = `<svg class="when" viewBox="0 0 ${Wd} ${H}" style="height:${H}px">${g}</svg>`;
    const svg = host.querySelector("svg"), hov = svg.querySelector("#whov"), hx = svg.querySelector("#whx");
    hov.onmousemove = (ev) => {
      const r = hov.getBoundingClientRect(), t = Math.max(0, Math.min(days - 1, Math.round(((ev.clientX - r.left) / r.width) * (days - 1))));
      hx.setAttribute("x1", X(t)); hx.setAttribute("x2", X(t)); hx.setAttribute("visibility", "visible");
      showTip(ev, `<b>${fdate(D.dates[t])}</b><br>${esc(W.resolve.without)}: ${pct(B[t])}<br>${esc(W.resolve.with)}: ${pct(R[t])}`);
    };
    hov.onmouseleave = () => { hx.setAttribute("visibility", "hidden"); hideTip(); };
  }
  function renderResolve() {
    const sec = $("s-resolve"), R = W.resolve, f = figures();
    if (!sec.querySelector(".vt")) sec.innerHTML = `<h2>${esc(W.sections.resolve)}</h2><p class="mute small cn" hidden>${esc(W.assumption.path_note)}</p><h4 class="sub">${esc(R.verdict)}</h4><table class="vt"></table>
      <h4 class="sub">${esc(R.attribution)}</h4><table class="at" style="max-width:760px"></table><h4 class="sub">${esc(R.when)}</h4><div class="wh"></div>
      <h4 class="sub">${esc(fill(R.grid))}</h4><p class="mute small">${esc(R.grid_note)}</p><div class="gd"></div>`;
    sec.querySelector(".cn").hidden = S.assumption === "central";
    sec.querySelector(".vt").innerHTML = `<tr><th></th><th>${esc(R.p)}</th><th>${esc(R.judgment)}</th><th>${esc(R.collected)}</th><th>${esc(fill(R.filing))}</th><th style="text-align:left">${esc(R.follows)}</th></tr>`
      + verdictGroups().map((g) => `<tr${g.k === -2 ? ' class="sub"' : ""}><td${g.text ? ` class="hastip" data-tip="${esc(cap(g.text))}"` : ""}><b>${esc(g.label)}</b></td><td><b>${pct(g.p)}</b></td><td>${esc(amountText(g))}</td><td>${money(g.c / g.p)}</td><td>${pct(g.f / g.p)}</td><td class="fl">${follows(g)}</td></tr>`).join("");
    sec.querySelector(".at").innerHTML = `<tr><th></th><th>${esc(R.without)}</th><th>${esc(R.with)}</th><th>${esc(R.difference)}</th></tr>${attributionRows(f)}`;
    renderWhen(sec.querySelector(".wh"));
    sec.querySelector(".gd").innerHTML = gridHtml();
  }

  // --- 4. the judgment that matters most ------------------------------------------------------------------------------
  const dec = (i) => (W.decisions || {})[D.nodes[i].node] || {};
  const decShort = (i) => { const n = D.nodes[i], s = dec(i).short; return s ? fill(s) + (n.form ? ` (${n.form.form_question.split(":")[0]})` : "") : n.label; };
  const earlier = (f) => f.earlier_plain || f.earlier_answers || [];
  const situ = (i) => { const n = D.nodes[i]; return n.form ? (earlier(n.form).join("; ") || W.judgment.first) : n.sub; };
  const blabel = (i, b) => { const f = D.nodes[i].form; return (f && f.answers && f.answers[b]) || fill((dec(i).branches || {})[b]) || b.replace(/_/g, " "); };
  const PNODES = D.paths.edges.map((e) => {  // every node a path passes through, including inside its composites
    const s = new Set(); for (let j = 0; j < e.length; j += 2) { if (e[j] >= 0) s.add(e[j]); else for (const c of D.composites[-e[j] - 1]) for (const [n] of c) s.add(n); }
    return Int32Array.from(s);
  });
  function nodeReach(i) {  // share of outcomes that meet node i: each path's probability is linear in the node's answer
    if (isBank(i)) return 1;
    const qs = D.nodes[i].branches.map((_, b) => pathProbs((k) => (k === i ? D.nodes[i].branches.map((__, c) => +(c === b)) : dist(k))));
    let r = 0; for (let p = 0; p < P; p++) { let lo = Infinity, hi = -Infinity; for (const q of qs) { if (q[p] < lo) lo = q[p]; if (q[p] > hi) hi = q[p]; } r += hi - lo; }
    return Math.min(1, r / qs.length);
  }
  function effOf(i, at = null) {  // collections and the filing probability at 0%, the current answer (or `at`) and 100% of the selected branch
    const b = selB(i), get = (x) => (k) => (k === i ? withBranch(i, b, x) : dist(k));
    if (at) { const g = (k) => (k === i ? at : dist(k)); if (isBank(i)) { const bp = bankProbs(g); return { at: bexpect(bp, "collected"), fat: bexpect(bp, "petition_p") }; }
      const pp = pathProbs(g); return { at: expect(pp, "collected"), fat: expect(pp, "petition_p") }; }
    const v = (x) => { if (isBank(i)) { const bp = bankProbs(get(x)); return [bexpect(bp, "collected"), bexpect(bp, "petition_p")]; }
      const pp = pathProbs(get(x)); return [expect(pp, "collected"), expect(pp, "petition_p")]; };
    const [c0, f0] = v(0), [c1, f1] = v(1);
    return { lo: c0, hi: c1, at: isBank(i) ? bexpect(bprobs, "collected") : expect(probs, "collected"), flo: f0, fhi: f1, fat: isBank(i) ? bexpect(bprobs, "petition_p") : expect(probs, "petition_p") };
  }
  let sens = [];
  function computeSens() { sens = D.nodes.map((n, i) => { if (isBank(i)) return null; const e = effOf(i), p = dist(i)[selB(i)]; return { i, ...e, range: Math.abs(e.hi - e.lo), score: Math.abs(e.hi - e.lo) * 2 * p * (1 - p) }; }); }
  function inputs(f) {  // the facts Jev was given, as label: value
    const out = [], m = (v) => money(cents(v)), rng = (o, fmt) => { const a = fmt(o.p50); return o.p5 && o.p95 && o.p5 !== o.p95 ? `${a} (${fmt(o.p5)} – ${fmt(o.p95)})` : a; };
    const L = W.inputs;
    for (const [k, lab] of Object.entries(L)) {
      const v = f[k]; if (!v) continue;
      out.push([lab, k === "decision_date" ? rng(v, (t) => t) : k === "settlement_offer" ? (v.amount ? rng(v.amount, m) : "–") : typeof v === "object" ? rng(v, m) : m(v)]);
    }
    for (const c of f.components || []) out.push([c.component, `${c.amount === "unknown" ? W.judgment.amount_unknown : money(cents(c.amount))}, ${statusText(c.component, c.status)}`]);
    return out;
  }
  const THEORY = new Map(((D.case_terms || {}).components || []).map((c) => [c.label, c.theory]));
  const statusText = (label, status) => fill((W.status || {})[THEORY.get(label) === "defense" ? "defense" : status] || status);
  const link = (l) => `<a href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.text)}</a>`;
  const dragBase = {};
  function closeupHtml(i, id) {
    const n = D.nodes[i], det = n.detail || {}, J = W.judgment, b = selB(i);
    const q = n.form ? n.form.form_question : fill(dec(i).question || n.question);
    const ask = n.form && (n.form.plain_asks || n.form.asked);
    const asked = n.form ? `${ask ? `<p class="mute small">${esc(cap(ask))}</p>` : ""}<dl class="meta"><dt>${esc(J.earlier)}</dt><dd>${esc(earlier(n.form).join("; ") || J.none)}</dd></dl>`
      : `<dl class="meta"><dt>${esc(J.asked)}</dt><dd>${esc(cap(n.context) || "–")}</dd></dl>`;
    const quotes = (det.quotes || []).slice(0, 3).map((x) => `<blockquote><p>“${esc(x.quote)}”</p><cite>${esc(x.source)}${x.link ? ` · <a href="${esc(x.link)}" target="_blank" rel="noopener">${esc(W.ui.source)}</a>` : ""}</cite></blockquote>`).join("");
    const ins = inputs(det.facts || {});
    return `<h3>${esc(J.question)} · <span class="nt">${esc(decShort(i))}</span></h3><p class="q">${esc(q)}</p>${asked}<p class="mute small" id="${id}-reach"></p>
      <h3>${esc(J.forecast)}</h3><div class="ans"><span id="${id}-blab"></span><span class="big" id="${id}-big"></span><span class="mute small" id="${id}-jev"></span></div>
      <div class="jbar" id="${id}-bar"></div><div class="slabs" id="${id}-slabs"></div>
      <div class="sw"><input type="range" id="${id}-slider" min="0" max="100" step="1" value="${Math.round(100 * dist(i)[b])}"><span class="jm" style="left:${100 * jevDist(i)[b]}%" title="Jev"></span></div>
      <p class="mute small">${esc(J.drag)} <a href="#" id="${id}-reset"${n.key in S.overrides ? "" : " hidden"}>${esc(J.reset)}</a></p>
      <h3>${esc(J.effect)}</h3><p class="mute small" id="${id}-of"></p><table class="eff"><tr><th></th><th id="${id}-h0"></th><th class="j" id="${id}-hj"></th><th class="y" id="${id}-hy"></th><th id="${id}-h1"></th></tr>
        <tr><td>${esc(T("collected"))}</td><td id="${id}-lo"></td><td class="j" id="${id}-at"></td><td class="y" id="${id}-yat"></td><td id="${id}-hi"></td></tr>
        <tr><td>${esc(T("filing"))}</td><td id="${id}-flo"></td><td class="j" id="${id}-fat"></td><td class="y" id="${id}-yfat"></td><td id="${id}-fhi"></td></tr></table>
      ${ins.length ? `<h3>${esc(J.inputs)}</h3><dl class="inputs">${ins.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl>` : ""}
      <h3>${esc(J.evidence)}</h3>${quotes || `<p class="mute">${esc(J.none)}</p>`}
      <details class="law"><summary>${esc(J.sources)}</summary>${(det.steps || []).map((x) => `<div class="step"><span class="tag">${esc(x.tag)}</span><span>${esc(x.text)}${(x.links || []).length ? ` · ${x.links.map(link).join(" · ")}` : ""}</span></div>`).join("")}</details>`;
  }
  const BCOL = ["var(--teal)", "#8ea3bd", "#d8c9a8"];
  function updateCloseup(i, id) {
    if (!$(`${id}-big`)) return;
    const n = D.nodes[i], b = selB(i), d = dist(i), e = effOf(i), J = W.judgment, bn = blabel(i, n.branches[b]);
    $(`${id}-big`).textContent = pct(d[b]); $(`${id}-blab`).textContent = `${cap(bn)}:`;
    $(`${id}-jev`).textContent = n.key in S.overrides ? fill(J.jev_said, { p: pct(jevDist(i)[b]) }) : "";
    $(`${id}-bar`).innerHTML = d.map((p, k) => `<i style="width:${100 * p}%;background:${BCOL[k % 3]}"></i>`).join("");
    $(`${id}-slabs`).innerHTML = `<span>${esc(n.branches.map((x, k) => `${blabel(i, x)} ${pct(d[k])}`).join(" · "))}</span>`;
    $(`${id}-reach`).textContent = fill(J.reach, { p: pct(nodeReach(i)) });
    const ov = n.key in S.overrides, jv = ov ? effOf(i, jevDist(i)) : e;  // Jev's column at Jev's answer; the reader's beside it
    $(`${id}-h0`).textContent = "0%"; $(`${id}-h1`).textContent = "100%"; $(`${id}-of`).textContent = fill(J.of, { branch: bn }); $(`${id}-hj`).textContent = fill(J.at, { p: pct(jevDist(i)[b]) });
    $(`${id}-lo`).textContent = money(e.lo); $(`${id}-at`).textContent = money(jv.at); $(`${id}-hi`).textContent = money(e.hi);
    $(`${id}-flo`).textContent = pct(e.flo); $(`${id}-fat`).textContent = pct(jv.fat); $(`${id}-fhi`).textContent = pct(e.fhi);
    document.querySelectorAll(`#${id}-hy, #${id}-yat, #${id}-yfat`).forEach((el) => { el.hidden = !ov; });
    $(`${id}-hy`).textContent = fill(J.yours, { p: pct(d[b]) }); $(`${id}-yat`).textContent = money(e.at); $(`${id}-yfat`).textContent = pct(e.fat);
    $(`${id}-reset`).hidden = !(n.key in S.overrides);
  }
  function bindCloseup(i, id) {
    const n = D.nodes[i], sl = $(`${id}-slider`);
    sl.onpointerdown = () => { if (!(i in dragBase)) dragBase[i] = dist(i).slice(); };
    sl.oninput = () => { if (!(i in dragBase)) dragBase[i] = dist(i).slice(); S.overrides[n.key] = withBranch(i, selB(i), sl.value / 100, dragBase[i]); onChange(false); };
    sl.onchange = () => { delete dragBase[i]; onChange(true); };
    $(`${id}-reset`).onclick = (ev) => { ev.preventDefault(); delete S.overrides[n.key]; delete dragBase[i]; onChange(true); rebuildCloseups(); };
  }
  function openNode(i) {
    if (i < 0) return; S.open = i; hideTip();
    $("drawer").innerHTML = `<button class="x" id="dx" aria-label="${esc(W.ui.close)}">×</button>${closeupHtml(i, "d")}`;
    $("drawer").classList.add("open"); $("drawer").setAttribute("aria-hidden", "false"); $("drawer").scrollTop = 0;
    updateCloseup(i, "d"); bindCloseup(i, "d"); $("dx").onclick = closeDrawer;
  }
  function closeDrawer() { S.open = null; $("drawer").classList.remove("open"); $("drawer").setAttribute("aria-hidden", "true"); }
  document.addEventListener("keydown", (ev) => { if (ev.key === "Escape") closeDrawer(); });
  function rebuildCloseups() { renderJudgment(true); if (S.open !== null) openNode(S.open); }

  // the path tree: a prefix tree over each path's first steps, sized by probability
  const DEPTH = 3, MERGE = 0.02, month = (when) => when.split(" ").pop();
  const TRIE = { kids: new Map(), paths: [], depth: 0 };
  for (let i = 0; i < P; i++) {
    TRIE.paths.push(i); const { steps } = stepsOf(D.paths.seq[i]); let t = TRIE;
    for (let d = 0; d < DEPTH; d++) {
      const st = steps[d]; if (!st && d > 0 && FILING.has(steps[d - 1].base)) break;
      const key = st ? `${st.base}|${month(st.when)}` : "quiet";
      if (!t.kids.has(key)) t.kids.set(key, { key, base: st ? st.base : "quiet", when: st ? month(st.when) : "", kids: new Map(), paths: [], depth: d + 1 });
      t = t.kids.get(key); t.paths.push(i); if (!st) break;
    }
  }
  function tree() {
    const pp = D.paths.scalars.petition_p;
    const weigh = (t) => {
      let m = 0, f = 0; for (const i of t.paths) { m += probs[i]; f += probs[i] * pp[i]; }
      const node = { key: t.key, base: t.base, when: t.when, depth: t.depth, mass: m, filed: f, kids: [] };
      const mo = (k) => (k.base === "quiet" ? 98 : MON.indexOf(k.when) < 0 ? 97 : MON.indexOf(k.when));
      const kids = [...t.kids.values()].map(weigh).sort((a, b) => b.mass - a.mass), other = { key: "other", base: "other", when: "", depth: t.depth + 1, mass: 0, filed: 0, kids: [] };
      for (const k of kids) { if (k.mass >= MERGE) node.kids.push(k); else { other.mass += k.mass; other.filed += k.filed; } }
      node.kids.sort((a, b) => (mo(a) - mo(b)) || (b.mass - a.mass));
      if (other.mass > 1e-9 && node.kids.length) node.kids.push(other);
      if (node.kids.length === 1 && node.kids[0].base === "quiet") node.kids = [];
      return node;
    };
    const Tr = weigh(TRIE), share = (t, n) => { t.pct = n; if (t.kids.length) apportion(t.kids.map((k) => k.mass), n).forEach((v, j) => share(t.kids[j], v)); };
    share(Tr, 100); return Tr;
  }
  const nodeLabel = (t) => (t.base === "quiet" ? W.resolve.nothing : t.base === "other" ? W.resolve.other : t.base);
  function wrap(text, max) { const out = [""]; for (const w of text.split(" ")) { const cur = last(out); if ((cur + " " + w).trim().length > max && cur) { if (out.length === 2) { out[1] += "…"; break; } out.push(w); } else out[out.length - 1] = (cur + " " + w).trim(); } return out; }
  function renderPaths(host) {
    const Tr = tree(), Wd = host.clientWidth || 1000, root = 64, colW = Math.min(305, (Wd - root - 40) / DEPTH - 12), gap = (Wd - root - DEPTH * colW - 4) / DEPTH, pad = 4, H = 480, MINH = 15, TOP = 8;
    const cols = Array.from({ length: DEPTH + 1 }, () => []), all = [];
    const walk = (t, parent) => { t.parent = parent; cols[t.depth].push(t); all.push(t); t.kids.forEach((k) => walk(k, t)); }; walk(Tr, null);
    const scale = Math.min(...cols.map((c) => (c.length ? (H - pad * (c.length - 1)) / c.reduce((a, t) => a + t.mass, 0) : Infinity)));
    const X = (d) => (d === 0 ? 0 : root + gap + (d - 1) * (colW + gap)), wOf = (d) => (d === 0 ? root - 8 : colW);
    Tr.y = TOP; Tr.h = Tr.mass * scale;
    for (let d = 1; d <= DEPTH; d++) { let end = TOP - pad; for (const t of cols[d]) { const p = t.parent; p.off = p.off ?? p.y; t.slice = p.off; p.off += t.mass * scale; t.h = Math.max(t.mass * scale, MINH); t.y = Math.max(end + pad, t.slice); end = t.y + t.h; } }
    const Hmax = Math.max(...all.map((t) => t.y + t.h)) + 4;
    const mix = (f) => { const a = [223, 234, 226], b = [243, 205, 196]; return `rgb(${a.map((v, k) => Math.round(v + (b[k] - v) * f)).join(",")})`; };
    let links = "", nodes = "";
    all.forEach((t, id) => {
      t.id = id;
      if (t.parent) { const x0 = X(t.depth - 1) + wOf(t.depth - 1), x1 = X(t.depth), y0 = t.slice, y1 = t.y, h = Math.max(t.mass * scale, 1), mx = (x0 + x1) / 2;
        links += `<path class="lk${t.base === "quiet" ? " q" : FILING.has(t.base) ? " f" : ""}" data-id="${id}" d="M${x0},${y0}C${mx},${y0} ${mx},${y1} ${x1},${y1}L${x1},${y1 + h}C${mx},${y1 + h} ${mx},${y0 + h} ${x0},${y0 + h}Z"/>`; }
      const x = X(t.depth), w = wOf(t.depth); let txt = "";
      if (t.depth === 0) txt = `<text x="${x + 6}" y="${t.y + 16}" class="tn b">${esc(fill(W.ui.root))}</text>`;
      else if (t.h >= 13) { const label = (t.when ? `${t.when} · ` : "") + nodeLabel(t), two = t.h >= 30 ? wrap(label, Math.floor((w - 48) / 6.4)) : [label.length > (w - 48) / 6.3 ? label.slice(0, Math.floor((w - 48) / 6.3) - 1) + "…" : label], y0 = t.y + t.h / 2 - (two.length - 1) * 7.5 + 4;
        txt = two.map((l, k) => `<text x="${x + 8}" y="${y0 + 15 * k}" class="tn">${esc(l)}</text>`).join("") + `<text x="${x + w - 8}" y="${t.y + t.h / 2 + 4}" class="tn b" text-anchor="end">${t.pct}%</text>`; }
      nodes += `<g class="nd" data-id="${id}"><rect x="${x}" y="${t.y}" width="${w}" height="${Math.max(t.h, 1)}" rx="2" fill="${t.depth ? (t.kids.length ? "#e3e6ea" : mix(t.mass > 0 ? t.filed / t.mass : 0)) : "#dfe2e6"}"/>${txt}</g>`;
    });
    nodes = nodes.replace(`height="${Math.max(Tr.h, 1)}" rx="2" fill="#dfe2e6"`, `height="${Hmax - 4 - TOP}" rx="2" fill="#dfe2e6"`);
    host.innerHTML = `<p class="mute small">${esc(fill(W.judgment.paths_note))}</p><div class="tleg"><span><i style="background:#f3cdc4"></i>${esc(W.ui.legend_filed)}</span><span><i style="background:#dfeae2"></i>${esc(fill(W.ui.legend_quiet))}</span><span><i style="background:#e3e6ea"></i>${esc(W.ui.legend_open)}</span></div><svg class="tree" viewBox="0 0 ${Wd} ${Hmax}" style="height:${Hmax}px">${links}${nodes}</svg>`;
    const svg = host.querySelector("svg"), chain = (t) => { const s = new Set(); for (let u = t; u; u = u.parent) s.add(u.id); return s; };
    svg.querySelectorAll(".nd").forEach((g) => { const t = all[+g.dataset.id];
      g.onmouseenter = (ev) => { const on = chain(t); svg.classList.add("hl"); svg.querySelectorAll(".nd, .lk").forEach((e) => e.classList.toggle("on", on.has(+e.dataset.id)));
        const steps = []; for (let u = t; u && u.depth; u = u.parent) steps.unshift([u.when, nodeLabel(u)]); showTip(ev, `${linesHtml(steps)}<div class="mute" style="margin-top:6px">${t.pct}% · ${esc(fill(W.judgment.paths_filing))} ${pct(t.mass > 0 ? t.filed / t.mass : 0)}</div>`); };
      g.onmousemove = (ev) => showTip(ev, $("tip").innerHTML); g.onmouseleave = () => { svg.classList.remove("hl"); hideTip(); }; });
  }
  function renderTornado(host) {
    const L = sens.filter(Boolean).sort((a, b) => b.range - a.range).slice(0, 8), lo0 = Math.min(...L.map((l) => Math.min(l.lo, l.hi, l.at))), hi0 = Math.max(...L.map((l) => Math.max(l.lo, l.hi, l.at)));
    const pd = 0.06 * (hi0 - lo0 || 1), lo = lo0 - pd, hi = hi0 + pd, pos = (v) => (100 * (v - lo)) / (hi - lo), J = W.judgment;
    host.innerHTML = `<p class="mute small">${esc(J.tornado_note)}</p><div class="levers">${L.map((l, r) => {
      const n = D.nodes[l.i], bn = blabel(l.i, n.branches[selB(l.i)]), a0 = fill(J.zero, { branch: bn }), a1 = fill(J.hundred, { branch: bn });
      const left = l.lo <= l.hi ? [a0, l.lo] : [a1, l.hi], right = l.lo <= l.hi ? [a1, l.hi] : [a0, l.lo];
      return `<button class="lever" data-i="${l.i}"><span class="ll"><b>${esc(decShort(l.i))}</b><span class="lm">${esc(`${fill(n.key in S.overrides ? J.yours : J.at, { p: pct(dist(l.i)[selB(l.i)]) })} ${bn}`)} · ${esc(situ(l.i))}</span></span>
        <span class="lt"><span class="rng" style="left:${pos(left[1])}%;width:${pos(right[1]) - pos(left[1])}%"></span><span class="mk" style="left:${pos(l.at)}%"></span>${r === 0 ? `<span class="mkl" style="left:${pos(l.at)}%">${esc(fill(n.key in S.overrides ? J.yours : J.at, { p: "" }).trim())} ${money(l.at)}</span>` : ""}
        <span class="endl" style="right:${100 - pos(left[1])}%">${esc(left[0])} · <b>${money(left[1])}</b></span><span class="endr" style="left:${pos(right[1])}%"><b>${money(right[1])}</b> · ${esc(right[0])}</span></span></button>`; }).join("")}</div>`;
    host.querySelectorAll(".lever").forEach((x) => (x.onclick = () => openNode(+x.dataset.i)));
  }
  function renderJudgment(rebuild) {
    const sec = $("s-judgment"), J = W.judgment;
    if (!sec.querySelector(".card") || rebuild) {
      sec.innerHTML = `<h2>${esc(W.sections.judgment)}</h2><p>${esc(J.jev)}</p><p class="mute small">${esc(J.why)}</p><div class="card"></div>
        <h4 class="sub">${esc(J.paths)}</h4><div class="pt"></div><h4 class="sub">${esc(J.tornado)}</h4><div class="tnd"></div>`;
      const c = sec.querySelector(".card"), html = closeupHtml(S.focus, "c"), cut = html.indexOf(`<h3>${esc(J.forecast)}</h3>`), cut2 = html.indexOf(`<h3>${esc(J.evidence)}</h3>`);
      const ins = html.indexOf(`<h3>${esc(J.inputs)}</h3>`), mid = html.slice(cut, ins >= 0 ? ins : cut2);  // left: question and inputs; right: forecast, effect, evidence
      c.innerHTML = `<div>${html.slice(0, cut)}${ins >= 0 ? html.slice(ins, cut2) : ""}</div><div>${mid}${html.slice(cut2)}</div>`;
      bindCloseup(S.focus, "c");
    }
    updateCloseup(S.focus, "c"); renderPaths(sec.querySelector(".pt")); renderTornado(sec.querySelector(".tnd"));
  }

  // --- 5. one economic assumption -----------------------------------------------------------------------------------
  const AS = D.assumptions || [], CEN = AS.find((a) => a.central), AT = D.assumption_text || {};
  const aLabel = (a) => usdText((AT[a.central ? "central" : a.id] || {}).label || a.label), aNote = (a) => usdText((AT[a.central ? "central" : a.id] || {}).note || "");
  const aDiff = (a) => ({ f: a.metrics.petition_p - CEN.metrics.petition_p, c: a.metrics.collected - CEN.metrics.collected });
  const aScore = (a) => { const d = aDiff(a); return Math.abs(d.f) + Math.abs(d.c) / (CEN.metrics.due || 1); };  // points of filing + points of collection rate
  S.compare = AS.filter((a) => !a.central).sort((a, b) => aScore(b) - aScore(a)).map((a) => a.id)[0] || null;
  function renderAssumption() {
    const sec = $("s-assumption"), A = W.assumption; if (!CEN) { sec.hidden = true; return; }
    const cmp = variant(S.compare), cf = vFigures(CEN), vf = cmp ? vFigures(cmp) : null;
    const rows = [["due", "due", money], ["collected", "collected", money], ["rate", "rate", pct], ["past_due", "past_due", money], ["frozen_due", "frozen_due", money], ["not_yet_due", "not_yet_due", money], ["funded", "funded", money], ["filing", "petition_p", pct], ["stayed", "stayed_if", money], ["preference", "clawback_if", money]];
    const rd = (v, fmt) => (fmt === pct ? Math.round(100 * v) / 100 : Math.round(v / 1e5) * 1e5);
    const diff = (k, fmt) => { const a = cf[k], b = vf && vf[k]; return a === undefined || b === undefined || a === null || b === null ? "–" : fmt === money ? dmoney(rd(b, fmt) - rd(a, fmt)) : pts(rd(b, fmt) - rd(a, fmt)); };
    sec.innerHTML = `<h2>${esc(W.sections.assumption)}</h2><p class="mute small">${esc(A.shown)}</p>
      <div class="apick"><div class="ah"><span></span><span></span><span class="d">${esc(T("filing_short"))}</span><span class="d">${esc(T("collected"))}</span></div>${AS.map((a) => { const d = aDiff(a);
        const used = a.central ? S.assumption === "central" : a.id === S.assumption;
        return `<label class="${used ? "on" : ""}${!a.central && a.id === S.compare ? " cmp" : ""}"><input type="radio" name="asm" value="${esc(a.id)}"${used ? " checked" : ""}><span>${esc(a.central ? A.central : aLabel(a))}${!a.central && a.id === S.compare ? ` <em class="tag">${esc(A.compared)}</em>` : ""}</span>
          <span class="d">${a.central ? pct(a.metrics.petition_p) : pts(d.f)}</span><span class="d">${a.central ? money(a.metrics.collected) : dmoney(d.c)}</span></label>`; }).join("")}</div>
      ${cmp ? `<table class="at" style="max-width:720px"><tr><th></th><th>${esc(A.col_central)}</th><th>${esc(A.col_variant)}</th><th>${esc(A.col_diff)}</th></tr>
        ${rows.map(([t, k, fmt]) => `<tr><td>${esc(T(t))}</td><td>${fmt(cf[k])}</td><td>${fmt(vf[k])}</td><td>${diff(k, fmt)}</td></tr>`).join("")}</table>
        ${aNote(cmp) ? `<p class="basis"><b>${esc(aLabel(cmp))}.</b> ${esc(aNote(cmp))}</p>` : ""}` : ""}`;
    sec.querySelectorAll('input[name="asm"]').forEach((r) => (r.onchange = () => { if (r.value !== CEN.id) S.compare = r.value; setAssumption(r.value === CEN.id ? "central" : r.value); }));
  }
  function setAssumption(id) {
    S.assumption = id; S.vm = null; renderAssumption(); renderSwitched();
    if (id === "central" && !Object.keys(S.overrides).length) { S.event = D.event; S.bank = D.bank; renderSeries(); } else refreshSeries();
  }
  function renderSwitched() {
    const el = $("switched"), v = S.assumption !== "central" ? variant() : null; el.hidden = !v; if (!v) return;
    el.innerHTML = `${esc(fill(W.assumption.switched, { label: aLabel(v) }))} <a href="#" id="sw-back">${esc(W.assumption.back)}</a>`;
    $("sw-back").onclick = (ev) => { ev.preventDefault(); setAssumption("central"); };
  }

  // --- 6. what actually happened, and the footer --------------------------------------------------------------------
  function renderActual() {
    $("actual-h").textContent = W.sections.actual;
    const btn = $("actual"); if (!btn) return;
    btn.textContent = S.reveal ? W.actual.hide : W.actual.button;
    btn.onclick = async () => { if (!S.outcome) S.outcome = await (await fetch(`${API}/outcome`)).json(); S.reveal = !S.reveal; renderActual(); };
    const o = S.outcome, body = $("actual-body"); if (!S.reveal || !o) { body.innerHTML = ""; return; }
    const pd = o.petition && o.petition.date, t = pd ? D.dates.indexOf(pd) : -1, cum = S.event.daily.petition_cum_p;
    const filed = pd ? fill(t >= 0 ? W.actual.filed : W.actual.filed_after, { date: fyear(pd), p: pct(t >= 0 ? cum[t] : last(cum)) }) : "";
    body.innerHTML = `<p>${esc(W.actual.intro)}</p>${filed ? `<p class="filed">${esc(filed)}</p>` : ""}
      <ol class="events">${o.events.map((e) => `<li><span class="mute">${esc(fyear(e.date))}</span><span>${esc(e.description)}${e.source_url ? ` <a href="${esc(e.source_url)}" target="_blank" rel="noopener">${esc(W.actual.source)}</a>` : ""}</span></li>`).join("")}</ol>`;
  }
  function renderFoot() {
    if ($("record")) $("record").textContent = W.ui.record;
    const t = D.case_terms || {}, U = W.ui;
    $("terms").innerHTML = `<details><summary>${esc(U.terms)}</summary><h3>${esc(U.components)}</h3><table class="terms">${(t.components || []).map((c) => `<tr><td>${esc(c.label)}</td><td>${esc(c.amount)}</td><td>${esc(statusText(c.label, c.status))}</td><td>${c.link ? `<a href="${esc(c.link)}" target="_blank" rel="noopener">${esc(c.source)}</a>` : esc(c.source)}</td></tr>`).join("")}</table>
      ${(t.notes || []).map((x) => `<h3>${esc(x.title)}</h3><table class="terms">${x.terms.map(([k, v]) => `<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>`).join("")}</table>`).join("")}
      <h3>${esc(U.deadlines)}</h3><table class="terms">${(t.deadlines || []).map((x) => `<tr><td>${esc(fyear(x.date))}</td><td>${esc(x.what)}</td></tr>`).join("")}</table></details>`;
  }

  // --- any change: figures recomputed here, the series (and a variant's figures) from the server ---------------------
  let ctrl = null, deb = null;
  function renderSeries() { renderForecast(); renderResolve(); renderActual(); }
  function refreshSeries() {
    clearTimeout(deb);
    deb = setTimeout(async () => {
      if (ctrl) ctrl.abort(); ctrl = new AbortController();
      try {
        const r = await fetch(`${API}/reweight`, { method: "POST", headers: { "Content-Type": "application/json" }, signal: ctrl.signal, body: JSON.stringify({ assumption: S.assumption, overrides: S.overrides }) });
        const v = await r.json();
        if (v && v.daily) { S.event = v; S.vm = S.assumption !== "central" ? v.metrics : null; if (v.bank) S.bank = { ...D.bank, ...v.bank }; renderSeries(); }
      } catch (e) { if (e.name !== "AbortError") console.error(e); }
    }, 200);
  }
  function renderChanged() {
    const k = Object.keys(S.overrides).length, el = $("changed"); el.hidden = !k; if (!k) return;
    el.innerHTML = `${esc(fill(W.ui.changed, { n: k }))} <a href="#" id="rs-all">${esc(W.ui.reset_all)}</a>`;
    $("rs-all").onclick = (ev) => { ev.preventDefault(); S.overrides = {}; onChange(true); rebuildCloseups(); };
  }
  function onChange(settled) {
    recompute(); if (settled) computeSens();
    renderForecast(); renderResolve(); renderJudgment(false); if (S.open !== null) updateCloseup(S.open, "d"); renderChanged();
    refreshSeries();
  }

  // --- init -------------------------------------------------------------------------------------------------------
  recompute(); computeSens();
  S.focus = sens.filter(Boolean).sort((a, b) => b.score - a.score)[0].i;
  window.__figures = () => ({ ...figures(), assumption: S.assumption, focus: D.nodes[S.focus].key, compare: S.compare });
  renderSituation(); renderForecast(); renderResolve(); renderJudgment(true); renderAssumption(); renderActual(); renderFoot(); renderChanged();
  let rz = null; window.addEventListener("resize", () => { clearTimeout(rz); rz = setTimeout(() => { renderForecast(); renderResolve(); renderJudgment(false); }, 150); });
  refreshSeries();  // warms the server's reweight
})();

// The analysis page: a guided explanation that becomes an explorer. Every number is recomputed here from the
// payload (path probabilities from the edges, expectations from per-path means); the daily series come from the
// server's /reweight. Every word about the case, the law or the loan comes from /static/words.json.
(async () => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const D = JSON.parse($("page-data").textContent);
  const API = document.body.dataset.api;
  const W = await (await fetch("/static/words.json")).json();
  const S = { overrides: {}, sel: {}, event: D.event, bank: D.bank, variant: "paths", open: null, reveal: false, outcome: null, lat: [] };
  window.__page = S;

  // --- formatting -----------------------------------------------------------------------------------------------
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fill = (t, o = {}) => String(t).replace(/\{(\w+)\}/g, (m, k) => (k in o ? o[k] : m));
  // money: $339k, $1.2M; whole units unless a value is under 10 of its unit
  const money = (c) => {
    const v = c / 100, a = Math.abs(v), s = v < 0 ? "−" : "";
    if (a >= 1e6) { const m = a / 1e6; return `${s}$${m >= 100 ? Math.round(m) : +m.toFixed(1)}M`; }
    if (a >= 1e3) return `${s}$${Math.round(a / 1e3)}k`;
    return `${s}$${Math.round(a)}`;
  };
  const freq = (p) => `${Math.round(100 * p)} in 100`;
  const n100 = (p) => Math.round(100 * p);
  const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const fdate = (iso) => `${+iso.slice(8, 10)} ${MON[+iso.slice(5, 7) - 1]}`;
  const HOR = fdate(D.meta.horizon);
  const WH = { horizon: HOR };

  // --- probability arithmetic (as the one-screen page computed it) ----------------------------------------------
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
  const expect = (probs, key) => { const x = D.paths.scalars[key]; let s = 0; for (let i = 0; i < P; i++) s += probs[i] * x[i]; return s; };
  const BE = D.bank.edges || [];
  const bankProbs = (get) => BE.map((e) => { let p = 1; for (let j = 0; j < e.length; j += 2) p *= get(e[j])[e[j + 1]]; return p; });
  const bexpect = (bp, key) => {
    const x = (D.bank.path_scalars || {})[key]; if (!x) return D.bank.scalars[key];
    let s = 0; for (let i = 0; i < bp.length; i++) s += bp[i] * x[i]; return s;
  };
  // A slider sets the selected branch; the other branches keep their proportions in `base`.
  function withBranch(i, b, x, base = dist(i)) {
    const rest = base.reduce((s, v, k) => s + (k === b ? 0 : v), 0), n = base.length;
    return base.map((v, k) => (k === b ? x : rest > 0 ? (1 - x) * v / rest : (1 - x) / (n - 1)));
  }
  const isBank = (i) => D.nodes[i].view === "bank";
  const selB = (i) => S.sel[D.nodes[i].key] ?? 0;

  // --- the two views' figures -----------------------------------------------------------------------------------
  let probs = null, bprobs = null;
  const KEYS = ["funded", "due", "collected", "unpaid", "petition_p", "clawback"];
  function figures() {
    const r = {}, b = {};
    for (const k of KEYS) { r[k] = expect(probs, k); b[k] = bexpect(bprobs, k); }
    const last = (d) => d.frozen_mean[d.frozen_mean.length - 1];
    r.stuck = last(S.event.daily); b.stuck = last(S.bank.daily);
    r.rate = r.due ? r.collected / r.due : 0; b.rate = b.due ? b.collected / b.due : 0;
    return { research: r, bank: b };
  }

  // --- outcomes and the 100 dots --------------------------------------------------------------------------------
  const OUT = W.outcomes;
  const CLS = D.classes;
  const COLOR = { "Filed: notes": "#8f1d1d", "Filed: short of cash": "#c2412d", "Filed: Qorvo enforcement": "#e0826b",
    "bank_filed": "#c2412d", "Settled": "#5f8f6c", "Paid": "#6f7780", "Stayed on appeal": "#8fa3b3",
    "Vacated or new trial": "#c9b99c", "Unresolved": "#dde0e4", "bank_not_filed": "#dde0e4" };
  const outLabel = (c) => fill((OUT[c] || { label: c }).label, WH);
  const isFiled = (c) => !!(OUT[c] || {}).filed;
  function classProbs() { const c = new Float64Array(CLS.length); for (let i = 0; i < P; i++) for (const [k, s] of D.paths.class[i]) c[k] += probs[i] * s; return c; }
  // largest-remainder rounding of shares to a whole number of dots
  function apportion(shares, total = 100) {
    const tot = shares.reduce((a, b) => a + b, 0) || 1, raw = shares.map((s) => (total * s) / tot);
    const n = raw.map(Math.floor); let left = total - n.reduce((a, b) => a + b, 0);
    raw.map((r, k) => [r - n[k], k]).sort((a, b) => b[0] - a[0]).forEach(([, k]) => { if (left > 0) { n[k]++; left--; } });
    return n;
  }
  const allocate = (shares) => apportion(shares, 100);
  // dot groups in display order: filed first (largest cause first), then not filed. Filed against not filed is
  // rounded first, so the filed dots always equal the rounded chance of a filing.
  function dotGroups(names, shares) {
    const ks = names.map((c, k) => k), fi = ks.filter((k) => isFiled(names[k])), nf = ks.filter((k) => !isFiled(names[k]));
    const sum = (g) => g.reduce((a, k) => a + shares[k], 0), [nF, nN] = allocate([sum(fi), sum(nf)]), n = [];
    for (const [g, t] of [[fi, nF], [nf, nN]]) apportion(g.map((k) => shares[k]), t).forEach((v, j) => (n[g[j]] = v));
    const g = names.map((c, k) => ({ c, share: shares[k], n: n[k], k, filed: isFiled(c) }));
    return g.sort((a, b) => (b.filed - a.filed) || (b.share - a.share));
  }
  const researchDots = () => dotGroups(CLS, [...classProbs()]);
  const bankDots = () => { const p = bexpect(bprobs, "petition_p"); return dotGroups(["bank_filed", "bank_not_filed"], [p, 1 - p]); };

  // --- steps of a path, in plain words ----------------------------------------------------------------------------
  const FILING = new Set(["Akoustis files, short of cash", "Akoustis files", "Noteholders accelerate; filing"]);
  const parseStep = (s) => { const m = /^(.*) \(([^)]*)\)$/.exec(s); return m ? { base: m[1], when: m[2] } : { base: s, when: "" }; };
  function phrase(base) {
    if (base in W.steps) return W.steps[base];
    if (base.startsWith("Ruling leaves ")) return fill(W.steps["Ruling leaves"], { amount: base.slice(14) });
    return base;
  }
  const SEQ = D.sequences.map((s) => (s ? s.split(" → ").map(parseStep) : []));
  // A path's steps up to and including its first filing (after a filing the line has stopped); `cut` drops the filing.
  function stepsOf(q, cut) {
    const out = [];
    for (const st of SEQ[q]) { if (FILING.has(st.base)) { if (!cut) out.push(st); return { steps: out, filed: true }; } out.push(st); }
    return { steps: out, filed: false };
  }
  function pathLines(q, filed) {  // [[when, what], ...]
    const { steps, filed: hasFiling } = stepsOf(q, !filed);
    const parts = steps.map((st) => [st.when, phrase(st.base)]);
    if (!filed || !hasFiling) parts.push([`by ${HOR}`, W.steps.quiet]);
    return parts;
  }
  const linesHtml = (ls) => `<table class="pl">${ls.map(([a, b]) => `<tr><td>${esc(a)}</td><td>${esc(b)}</td></tr>`).join("")}</table>`;
  // Representative paths for each outcome's dots: its most probable sequences, in proportion to their mass.
  function dotPaths(groups, bank) {
    if (bank) return groups.map(() => []);
    const by = CLS.map(() => new Map());
    for (let i = 0; i < P; i++) for (const [k, s] of D.paths.class[i]) {
      const m = by[k], q = D.paths.seq[i]; m.set(q, (m.get(q) || 0) + probs[i] * s);
    }
    return groups.map((g) => {
      if (!g.n) return [];
      const top = [...by[g.k].entries()].sort((a, b) => b[1] - a[1]).slice(0, g.n), n = allocate(top.map((t) => t[1]));
      const out = []; top.forEach(([q], j) => { for (let r = 0; r < n[j]; r++) out.push(q); });
      while (out.length < g.n && top.length) out.push(top[0][0]);
      return out;
    });
  }

  // --- which decision governs a step --------------------------------------------------------------------------------
  const STEP_DEC = {
    "Qorvo executes before the ruling": ["execute_pre_ruling"], "Akoustis files, short of cash": ["petition_cash_floor", "petition_cash_out"],
    "Akoustis seeks a sale or financing": ["debtor_response"], "Akoustis pays": ["debtor_response"], "Akoustis files": ["debtor_response"],
    "Stay approved": ["stay_approved"], "Qorvo levies": ["enforce_after_final", "registration_early"],
    "Early registration allowed": ["registration_early"], "Akoustis appeals": ["appeal"],
    "Noteholders accelerate; no filing": ["holders_act_judgment"], "Noteholders accelerate; filing": ["petition_on_notes"],
    "Noteholders accelerate on the delisting; no filing": ["holders_act_delisting"], "Nasdaq suspends": ["nasdaq_hearing"],
    "Nasdaq delists": ["panel_exception"], "Ruling vacates the judgment": ["ts_liability_jmol"], "Ruling orders a new trial": ["ts_damages_ruling"],
  };
  const stepTypes = (base) => STEP_DEC[base] || (base.startsWith("Ruling leaves") ? ["ts_damages_ruling"] : base.startsWith("Settles") ? ["settlement_accept"] : []);
  // every node a path passes through, including the nodes inside its composite edges
  const PNODES = D.paths.edges.map((e) => {
    const s = new Set();
    for (let j = 0; j < e.length; j += 2) {
      if (e[j] >= 0) s.add(e[j]); else for (const c of D.composites[-e[j] - 1]) for (const [n] of c) s.add(n);
    }
    return Int32Array.from(s);
  });
  const TYPE_NODES = {}; D.nodes.forEach((n, i) => { if (!isBank(i)) (TYPE_NODES[n.node] = TYPE_NODES[n.node] || []).push(i); });
  function nodeMass(paths) {  // probability mass through each node, over the given paths (all paths if none given)
    const m = new Float64Array(N), it = paths || D.paths.edges.map((_, i) => i);
    for (const i of it) for (const n of PNODES[i]) m[n] += probs[i];
    return m;
  }
  function pickNode(types, paths) {
    const m = nodeMass(paths); let best = -1, bm = -1;
    for (const t of types) for (const i of TYPE_NODES[t] || []) if (m[i] > bm) { bm = m[i]; best = i; }
    return best;
  }

  // --- the path tree: a prefix tree over each path's first steps ----------------------------------------------------
  const DEPTH = 4, MERGE = 0.02;
  const month = (when) => when.split(" ").pop();
  const TRIE = { kids: new Map(), paths: [], depth: 0 };
  for (let i = 0; i < P; i++) {
    TRIE.paths.push(i);
    const { steps } = stepsOf(D.paths.seq[i], false);
    let t = TRIE;
    for (let d = 0; d < DEPTH; d++) {
      const st = steps[d];
      const key = st ? `${st.base}|${month(st.when)}` : "quiet";
      if (!st && d > 0 && FILING.has(steps[d - 1].base)) break;  // a filing ends the path; nothing after it
      if (!t.kids.has(key)) t.kids.set(key, { key, base: st ? st.base : "quiet", when: st ? month(st.when) : "", kids: new Map(), paths: [], depth: d + 1 });
      t = t.kids.get(key); t.paths.push(i);
      if (!st) break;
    }
  }
  function tree() {  // the trie weighed by the current path probabilities, small branches merged
    const pp = D.paths.scalars.petition_p;
    const weigh = (t) => {
      let m = 0, f = 0; for (const i of t.paths) { m += probs[i]; f += probs[i] * pp[i]; }
      const node = { key: t.key, base: t.base, when: t.when, depth: t.depth, mass: m, filed: f, paths: t.paths, kids: [] };
      const kids = [...t.kids.values()].map(weigh).sort((a, b) => b.mass - a.mass), other = { key: "other", base: "other", when: "", depth: t.depth + 1, mass: 0, filed: 0, paths: [], kids: [] };
      for (const k of kids) { if (k.mass >= MERGE) node.kids.push(k); else { other.mass += k.mass; other.filed += k.filed; other.paths = other.paths.concat(k.paths); } }
      if (other.mass > 1e-9) node.kids.push(other);
      return node;
    };
    return weigh(TRIE);
  }

  // --- how much each judgment moves collections (0% / Jev / 100% for its selected branch) ---------------------------
  let sens = [];
  function computeSens() {
    const at = expect(probs, "collected"), bat = bexpect(bprobs, "collected");
    sens = D.nodes.map((n, i) => {
      const b = selB(i), get = (x) => (k) => (k === i ? withBranch(i, b, x) : dist(k));
      const v = isBank(i) ? (x) => bexpect(bankProbs(get(x)), "collected") : (x) => expect(pathProbs(get(x)), "collected");
      const lo = v(0), hi = v(1);
      return { lo, hi, at: isBank(i) ? bat : at, range: Math.abs(hi - lo) };
    });
  }
  const effOf = (i) => {  // the same arithmetic for one node, on demand
    const b = selB(i), get = (x) => (k) => (k === i ? withBranch(i, b, x) : dist(k));
    const v = isBank(i) ? (x) => bexpect(bankProbs(get(x)), "collected") : (x) => expect(pathProbs(get(x)), "collected");
    return { lo: v(0), hi: v(1), at: isBank(i) ? bexpect(bprobs, "collected") : expect(probs, "collected") };
  };
  const levers = () => sens.map((s, i) => ({ i, ...s })).filter((s) => !isBank(s.i)).sort((a, b) => b.range - a.range).slice(0, 6);

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

  // --- 1. the situation -----------------------------------------------------------------------------------------
  const factCash = (() => {  // the cash before the ruling, from the pre-ruling settlement question's facts
    const n = D.nodes.find((x) => x.node === "settlement_offer" && /before the ruling/.test(x.context)) || D.nodes.find((x) => x.detail.facts.projected_available_cash_at_decision_date);
    const v = n && n.detail.facts.projected_available_cash_at_decision_date; return v ? Math.round(100 * parseFloat(String(v.p50).replace(/[$,]/g, ""))) : null;
  })();
  const factOffer = (() => {
    const n = D.nodes.find((x) => x.node === "settlement_offer" && /before the ruling/.test(x.context)), o = n && n.detail.facts.settlement_offer;
    return o ? Math.round(100 * parseFloat(String(o.amount.p50).replace(/[$,]/g, ""))) : null;
  })();
  const judgmentDefault = (() => {  // the notes' 60-day clock on the judgment as entered
    for (const n of D.nodes) for (const [k, v] of Object.entries(n.detail.facts.contract_dates || {})) if (/as entered/.test(k)) {
      const m = /^(\d+) (\w{3}) (\d{4})$/.exec(v); if (m) return `${m[3]}-${String(MON.indexOf(m[2]) + 1).padStart(2, "0")}-${m[1].padStart(2, "0")}`;
    }
    return null;
  })();
  function renderSituation() {
    const U = W.ui, L = U.line_rows, lim = money(D.meta.limit_cents);
    const rule = W.situation.line.split(". ").slice(1).join(". ");
    const cents = (t) => Math.round(100 * parseFloat(String(t).replace(/[$,]/g, "")));
    const judgment = (D.case_terms.components || []).filter((c) => c.status === "awarded").reduce((a, c) => a + cents(c.amount), 0);
    const notes = ((D.case_terms.notes || [])[0] || { terms: [] }).terms.find(([k]) => k === "Principal");
    const facts = [[U.facts.judgment, money(judgment)], [U.facts.cash, factCash ? money(factCash) : "–"], [U.facts.notes, notes ? money(cents(notes[1])) : "–"], [U.facts.nasdaq, fdate(D.pins.nasdaq)]];
    $("s-situation").innerHTML = `<div class="sit">
      <div><h1>${esc(W.situation.title)}</h1>${W.situation.lines.map((l) => `<p>${esc(fill(l, { cash: money(factCash) }))}</p>`).join("")}
        <p class="question">${esc(fill(W.situation.question, WH))}</p></div>
      <div class="line"><h3>${esc(U.line_title)}</h3><dl>
        <dt>${esc(L.limit)}</dt><dd>${lim}<span class="mute small"> · ${esc(L.limit_note)}</span></dd>
        <dt>${esc(L.fee)}</dt><dd>${esc(fill(L.fee_value, { fee: `${(D.meta.fee_bps / 100).toFixed(1)}%` }))}</dd>
        <dt>${esc(L.repaid)}</dt><dd>${esc(fill(L.repaid_value, { n: D.meta.installments }))}</dd></dl>
        <p class="rule">${esc(rule)}</p></div></div>
      <div class="facts">${facts.map(([k, v]) => `<div><span class="mute small">${esc(k)}</span><b>${esc(v)}</b></div>`).join("")}</div>
      <p class="mute small">${esc(W.situation.bank_note)}</p>`;
  }

  // --- 2. the 100 dots and the comparison -------------------------------------------------------------------------
  let DOTS = { bank: [], research: [] };
  function dotRow(view, groups) {
    const reps = dotPaths(groups, view === "bank"), filed = groups.filter((g) => g.filed).reduce((s, g) => s + g.n, 0);
    let dots = "", k = 0; const list = [];
    groups.forEach((g, gi) => { for (let r = 0; r < g.n; r++) { list.push({ c: g.c, n: g.n, filed: g.filed, q: reps[gi][r] }); dots += `<i data-v="${view}" data-d="${k++}" style="background:${COLOR[g.c]}"></i>`; } });
    DOTS[view] = list;
    const name = view === "bank" ? W.views.bank.name : W.ui.col_research;
    const leg = groups.filter((g) => g.n).map((g) => `<span><i style="background:${COLOR[g.c]}"></i>${esc(outLabel(g.c))} <b>${g.n}</b></span>`).join("");
    return `<div class="drow ${view}"><div class="dhead"><b>${esc(name)}</b><span>${esc(fill(W.ui.files_by, { n: filed, horizon: HOR }))}</span></div>
      <div class="dots">${dots}</div><div class="dleg">${leg}</div></div>`;
  }
  function renderFutures() {
    const f = figures(), U = W.ui, M = W.measures;
    const rate = (v) => fill(U.rate_fmt, { n: Math.round(100 * v) });
    const sgn = (d, fmt) => `${d > 0 ? "+" : d < 0 ? "−" : ""}${fmt(Math.abs(d))}`;
    const rows = [["funded", money], ["collected", money], ["rate", rate], ["stuck", money], ["clawback", money]].map(([k, fmt]) => {
      const m = M[k], shown = (v) => (k === "rate" ? Math.round(100 * v) / 100 : Math.round(v / 1e5) * 1e5), d = shown(f.research[k]) - shown(f.bank[k]);
      return `<tr><td${m.note ? ` data-tip="${esc(m.note)}" class="hastip"` : ""}>${esc(fill(m.label, WH))}</td><td>${fmt(f.bank[k])}</td><td>${fmt(f.research[k])}</td>
        <td>${k === "rate" ? fill(U.rate_diff, { n: sgn(Math.round(100 * d), (x) => `$${x}`) }) : sgn(d, fmt)}</td></tr>`;
    }).join("");
    $("s-futures").innerHTML = `<h2>${esc(W.futures.title)}</h2><p class="mute">${esc(W.futures.dot)}</p>
      ${dotRow("bank", bankDots())}${dotRow("research", researchDots())}
      <table class="cmp"><tr><th></th><th>${esc(W.views.bank.name)}</th><th>${esc(U.col_research)}</th><th>${esc(W.views.difference)}</th></tr>${rows}</table>
      <p class="why">${esc(W.why_lower)}</p>`;
    $("s-futures").querySelectorAll(".dots i").forEach((el) => {
      el.onmouseenter = (ev) => {
        const d = DOTS[el.dataset.v][+el.dataset.d];
        const r = el.getBoundingClientRect(), at = { clientX: r.left + r.width / 2, clientY: r.top };
        showTip(at, `<div class="tt"><i style="background:${COLOR[d.c]}"></i><b>${esc(outLabel(d.c))}</b></div><div class="mute">${esc(fill(U.dot_hover, { n: d.n }))}</div>`
          + (d.q === undefined ? "" : linesHtml(pathLines(d.q, d.filed))), true);
      };
      el.onmouseleave = hideTip;
      el.onclick = () => {
        const d = DOTS[el.dataset.v][+el.dataset.d];
        if (d.q === undefined) return openNode(D.nodes.findIndex((n, i) => isBank(i)));
        const st = stepsOf(d.q, !d.filed).steps, last = st[st.length - 1];
        const paths = []; for (let i = 0; i < P; i++) if (D.paths.seq[i] === d.q) paths.push(i);
        const i = last ? pickNode(stepTypes(last.base), paths) : -1; if (i >= 0) openNode(i);
      };
    });
  }

  // --- 3. when trouble arrives ------------------------------------------------------------------------------------
  // The most probable cause of filings dated on a day: paths whose filing step falls on it (or in its month, for a
  // step dated only by month), weighted by their filed share per outcome.
  function filingCause(iso) {
    const day = fdate(iso), mon = MON[+iso.slice(5, 7) - 1], c = new Float64Array(CLS.length);
    for (let i = 0; i < P; i++) {
      const st = SEQ[D.paths.seq[i]].find((x) => FILING.has(x.base));
      if (!st || (st.when !== day && st.when !== mon)) continue;
      for (const [k, s] of D.paths.class[i]) if (isFiled(CLS[k])) c[k] += probs[i] * s;
    }
    const k = c.indexOf(Math.max(...c)); return c[k] > 0 ? outLabel(CLS[k]) : "";
  }
  function renderWhen() {
    const host = $("s-when");
    if (!host.querySelector("svg")) host.innerHTML = `<h2>${esc(W.ui.when_title)}</h2><p class="mute">${esc(W.ui.when_sub)}</p><svg class="when"></svg>`;
    const svg = host.querySelector("svg"), Wd = host.clientWidth, H = 500, days = D.dates.length;
    const B = S.bank.daily.petition_cum_p, R = S.event.daily.petition_cum_p;
    const m = { l: 40, r: 200, t: 92, b: 30 }, w = Wd - m.l - m.r, h = H - m.t - m.b;
    const X = (t) => m.l + (w * t) / (days - 1), Y = (p) => m.t + h - h * p, ix = (iso) => D.dates.indexOf(iso);
    let g = "";
    const rw = D.pins.ruling_window; if (rw && ix(rw[0]) >= 0) g += `<rect x="${X(ix(rw[0]))}" y="${m.t}" width="${X(days - 1) - X(ix(rw[0]))}" height="${h}" fill="#f4f5f6"/>`;
    for (const v of [0, 25, 50, 75, 100]) g += `<line x1="${m.l}" x2="${m.l + w}" y1="${Y(v / 100)}" y2="${Y(v / 100)}" stroke="${v ? "#eceef0" : "#c9cdd2"}"/><text x="${m.l - 8}" y="${Y(v / 100) + 4}" text-anchor="end">${v}</text>`;
    D.dates.forEach((d, t) => { if (d.endsWith("-01")) g += `<text x="${X(t)}" y="${H - 8}" text-anchor="middle">${MON[+d.slice(5, 7) - 1]}</text><line x1="${X(t)}" x2="${X(t)}" y1="${m.t + h}" y2="${m.t + h + 5}" stroke="#c9cdd2"/>`; });
    const pins = [["briefing_close", D.pins.briefing_close], ["judgment_default", judgmentDefault], ["ruling_window", rw && rw[0]], ["nasdaq", D.pins.nasdaq], ["coupon", D.pins.coupon]]
      .filter(([, d]) => d && ix(d) >= 0).sort((a, b) => (a[1] < b[1] ? -1 : 1));
    const ends = [];
    pins.forEach(([k, d]) => {
      const x = X(ix(d)), label = `${fdate(d)} · ${W.dates[k]}`, len = 6.4 * label.length, flip = x + 6 + len > m.l + w + m.r - 8;
      const [a, z] = flip ? [x - 5 - len, x] : [x, x + 5 + len];
      let row = ends.findIndex((e) => e.every(([p, q]) => z + 10 < p || a > q + 10)); if (row < 0) { row = ends.length; ends.push([]); }
      ends[row].push([a, z]); const y = 14 + row * 17;
      g += `<line x1="${x}" x2="${x}" y1="${y + 4}" y2="${m.t + h}" stroke="#b9bec5" stroke-dasharray="2 3"/><text class="pin" x="${flip ? x - 5 : x + 5}" y="${y}"${flip ? ' text-anchor="end"' : ""}>${esc(label)}</text>`;
    });
    const line = (ys) => ys.map((v, t) => `${t ? "L" : "M"}${X(t).toFixed(1)},${Y(v).toFixed(1)}`).join("");
    g += `<path d="${line(B)}" fill="none" stroke="var(--bank)" stroke-width="2.2" stroke-dasharray="6 4"/><path d="${line(R)}" fill="none" stroke="var(--teal)" stroke-width="2.8"/>`;
    // the lines labelled at their ends, pushed apart if they would overlap
    let yb = Y(B[days - 1]), yr = Y(R[days - 1]);
    if (Math.abs(yb - yr) < 36) { const mid = (yb + yr) / 2, s = yb >= yr ? 1 : -1; yb = mid + 18 * s; yr = mid - 18 * s; }
    const endLab = (y, col, name, p) => `<text x="${m.l + w + 10}" y="${y - 2}" fill="${col}" class="end">${esc(name)}</text><text x="${m.l + w + 10}" y="${y + 15}" fill="${col}" class="endv">${esc(freq(p))}</text>`;
    g += endLab(yb, "var(--bank)", W.views.bank.name, B[days - 1]) + endLab(yr, "var(--teal)", W.ui.col_research, R[days - 1]);
    // the research curve's biggest one-day step, and where the bank line starts rising
    let tj = 1; for (let t = 2; t < days; t++) if (R[t] - R[t - 1] > R[tj] - R[tj - 1]) tj = t;
    const jump = R[tj] - R[tj - 1];
    if (jump > 0.005) {
      const x = X(tj), y0 = Y(R[tj - 1]), cause = filingCause(D.dates[tj]);
      const txt = fill(W.ui.biggest_step, { date: fdate(D.dates[tj]), n: n100(jump) }), txt2 = cause ? fill(W.ui.biggest_cause, { cause }) : "";
      const y1 = Y(R[tj]), ya = Y(Math.max(0.62, R[tj] + 0.25));
      g += `<line x1="${x}" x2="${x + 12}" y1="${y1 - 5}" y2="${ya + 6}" stroke="var(--teal)"/><circle cx="${x}" cy="${y1}" r="4" fill="#fff" stroke="var(--teal)" stroke-width="1.5"/>
        <text class="ann" x="${x + 16}" y="${ya}" fill="var(--teal)">${esc(txt)}</text><text class="ann2" x="${x + 16}" y="${ya + 17}" fill="var(--teal)">${esc(txt2)}</text>`;
    }
    const tb = B.findIndex((p) => p >= 0.005);
    if (tb > 0) g += `<line x1="${X(tb)}" x2="${X(tb) - 14}" y1="${Y(0.01)}" y2="${Y(0.09)}" stroke="var(--bank)"/><text class="ann" x="${X(tb) - 18}" y="${Y(0.1)}" text-anchor="end" fill="#6b7178">${esc(fill(W.ui.bank_rises, { date: fdate(D.dates[tb]) }))}</text>`;
    g += `<line id="whx" y1="${m.t}" y2="${m.t + h}" stroke="#9aa0a8" visibility="hidden"/><rect id="whov" x="${m.l}" y="${m.t}" width="${w}" height="${h}" fill="transparent"/>`;
    svg.setAttribute("viewBox", `0 0 ${Wd} ${H}`); svg.style.height = `${H}px`; svg.innerHTML = g;
    const hov = svg.querySelector("#whov"), hx = svg.querySelector("#whx");
    hov.onmousemove = (ev) => {
      const r = hov.getBoundingClientRect(), t = Math.max(0, Math.min(days - 1, Math.round(((ev.clientX - r.left) / r.width) * (days - 1))));
      hx.setAttribute("x1", X(t)); hx.setAttribute("x2", X(t)); hx.setAttribute("visibility", "visible");
      showTip(ev, `<b>${fdate(D.dates[t])}</b><br>${esc(W.views.bank.name)}: ${freq(B[t])}<br>${esc(W.ui.col_research)}: ${freq(R[t])}`);
    };
    hov.onmouseleave = () => { hx.setAttribute("visibility", "hidden"); hideTip(); };
  }

  // --- the server's daily series (debounced) ------------------------------------------------------------------------
  let ctrl = null, deb = null;
  function refreshSeries(t0) {
    clearTimeout(deb);
    deb = setTimeout(async () => {
      if (ctrl) ctrl.abort();
      ctrl = new AbortController();
      try {
        const r = await fetch(`${API}/reweight`, { method: "POST", headers: { "Content-Type": "application/json" }, signal: ctrl.signal, body: JSON.stringify({ overrides: S.overrides }) });
        const v = await r.json();
        if (v && v.daily) { S.event = v; if (v.bank) S.bank = { ...D.bank, ...v.bank }; renderWhen(); renderFutures(); }
        if (t0) S.lat.push(performance.now() - t0);
      } catch (e) { if (e.name !== "AbortError") console.error(e); }
    }, 250);
  }

  function openNode(i) { S.open = i; }  // @@DRAWER (replaced in step 4)
  // @@PART3B
  // --- init -------------------------------------------------------------------------------------------------------
  function recompute() { probs = pathProbs((k) => dist(k)); bprobs = bankProbs((k) => dist(k)); }
  recompute();
  const t0 = performance.now(); computeSens(); const tSens = performance.now() - t0;
  function summary() {
    const f = figures(), line = (v) => `funded ${money(v.funded)} · due ${money(v.due)} · collected ${money(v.collected)} · filing ${(100 * v.petition_p).toFixed(1)}% · stuck ${money(v.stuck)} · give back ${money(v.clawback)}`;
    const dots = (g) => g.map((x) => `${x.c} ${x.n}`).join(", ");
    const out = { bank: line(f.bank), research: line(f.research), bankDots: dots(bankDots()), researchDots: dots(researchDots()),
      levers: levers().map((l) => `${D.nodes[l.i].node} [${D.nodes[l.i].context}] no ${money(l.lo)} · Jev ${money(l.at)} · yes ${money(l.hi)}`),
      sensMs: Math.round(tSens), figures: f };
    console.log(["BANK      " + out.bank, "RESEARCH  " + out.research, "DOTS bank " + out.bankDots, "DOTS res  " + out.researchDots,
      ...out.levers.map((l, k) => `LEVER ${k + 1} ${l}`), `sens ${out.sensMs} ms`].join("\n"));
    return out;
  }
  window.__summary = summary();
  renderSituation(); renderFutures(); renderWhen();
  // @@RENDER
  let rz = null; window.addEventListener("resize", () => { clearTimeout(rz); rz = setTimeout(renderWhen, 150); });
  refreshSeries();  // warms the server's reweight, so the first slider move is as quick as the rest
  // @@PART3
})();

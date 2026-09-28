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
  const fill = (t, o = {}) => String(t).replace(/\{(\w+)\}/g, (m, k) => (k in o ? o[k] : m)).replace(/(\d+) in 100/g, "$1\u00a0in\u00a0100");
  // money: $339k, $1.2M; whole units unless a value is under 10 of its unit
  const money = (c) => {
    const v = c / 100, a = Math.abs(v), s = v < 0 ? "−" : "";
    if (a >= 1e6) { const m = a / 1e6; return `${s}$${m >= 100 ? Math.round(m) : +m.toFixed(1)}M`; }
    if (a >= 1e3) return `${s}$${Math.round(a / 1e3)}k`;
    return `${s}$${Math.round(a)}`;
  };
  const freq = (p) => `${Math.round(100 * p)}\u00a0in\u00a0100`;
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
  const DEPTH = 3, MERGE = 0.02;
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
      if (other.mass > 1e-9 && node.kids.length) node.kids.push(other);  // no lone 'other ways': the node ends there
      if (node.kids.length === 1 && node.kids[0].base === "quiet") node.kids = [];  // a lone 'nothing forces a filing' adds nothing
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
  let ctrl = null, deb = null, first = null;
  function refreshSeries(t0) {
    clearTimeout(deb); first = first ?? t0; t0 = first;
    deb = setTimeout(async () => {
      if (ctrl) ctrl.abort();
      ctrl = new AbortController();
      try {
        const r = await fetch(`${API}/reweight`, { method: "POST", headers: { "Content-Type": "application/json" }, signal: ctrl.signal, body: JSON.stringify({ overrides: S.overrides }) });
        const v = await r.json(); first = null;
        if (v && v.daily) { S.event = v; if (v.bank) S.bank = { ...D.bank, ...v.bank }; renderWhen(); renderFutures(); renderActual(); }
        if (t0) S.lat.push(["server", performance.now() - t0]);
      } catch (e) { if (e.name !== "AbortError") console.error(e); }
    }, 250);
  }

  // --- 4. how it could unfold: the path tree (variant A) and the decision map (variant B) ---------------------------
  const nodeLabel = (t) => (t.base === "quiet" ? W.steps.quiet : t.base === "other" ? W.ui.tree_other : phrase(t.base));
  const shortLabel = (t) => {  // the tree's short step names (words.steps_short); the full phrase shows on hover
    const S2 = W.steps_short || {};
    if (t.base in S2) return S2[t.base];
    if (t.base.startsWith("Ruling leaves ") && S2["Ruling leaves"]) return fill(S2["Ruling leaves"], { amount: t.base.slice(14).replace(/\.\d(?=M)/g, "") });
    return nodeLabel(t);
  };
  function wrap(text, max) {  // up to two lines of at most `max` characters
    const words = text.split(" "), out = [""];
    for (const w of words) { const cur = out[out.length - 1]; if ((cur + " " + w).trim().length > max && cur) { if (out.length === 2) { out[1] += "…"; break; } out.push(w); } else out[out.length - 1] = (cur + " " + w).trim(); }
    return out;
  }
  function renderPaths(host) {
    const T = tree(), Wd = host.clientWidth, root = 64, colW = 290, gap = (Wd - root - DEPTH * colW - 4) / DEPTH, pad = 4, H = 560, MINH = 15;
    const cols = Array.from({ length: DEPTH + 1 }, () => []), all = [];
    const walk = (t, parent) => { t.parent = parent; cols[t.depth].push(t); all.push(t); t.kids.forEach((k) => walk(k, t)); };
    walk(T, null);
    const scale = Math.min(...cols.map((c) => (c.length ? (H - pad * (c.length - 1)) / c.reduce((a, t) => a + t.mass, 0) : Infinity)));
    const X = (d) => (d === 0 ? 0 : root + gap + (d - 1) * (colW + gap)), wOf = (d) => (d === 0 ? root - 8 : colW);
    T.y = 0; T.h = T.mass * scale;
    for (let d = 1; d <= DEPTH; d++) {
      let end = -pad;
      for (const t of cols[d]) {  // children stay beside their parent's slice where there is room
        const p = t.parent; p.off = p.off ?? p.y; t.slice = p.off; p.off += t.mass * scale;
        t.h = Math.max(t.mass * scale, MINH); t.y = Math.max(end + pad, t.slice); end = t.y + t.h;
      }
    }
    const Hmax = Math.max(...all.map((t) => t.y + t.h)) + 4;
    const fillOf = (t) => (t.base === "quiet" ? "#e3ece5" : FILING.has(t.base) ? "#f3d9d3" : t.base === "other" ? "#f5f6f7" : "#eceef1");
    let links = "", nodes = "";
    all.forEach((t, id) => {
      t.id = id;
      if (t.parent) {
        const x0 = X(t.depth - 1) + wOf(t.depth - 1), x1 = X(t.depth), y0 = t.slice, y1 = t.y, h = Math.max(t.mass * scale, 1), mx = (x0 + x1) / 2;
        links += `<path class="lk${t.base === "quiet" ? " q" : FILING.has(t.base) ? " f" : ""}" data-id="${id}" d="M${x0},${y0}C${mx},${y0} ${mx},${y1} ${x1},${y1}L${x1},${y1 + h}C${mx},${y1 + h} ${mx},${y0 + h} ${x0},${y0 + h}Z"/>`;
      }
      const leaf = !t.kids.length, x = X(t.depth), w = wOf(t.depth), share = n100(t.mass), fshare = t.mass > 0 ? t.filed / t.mass : 0;
      let txt = "";
      const fit = (str, px) => { const n = Math.floor(px / 6.3); return str.length > n ? str.slice(0, n - 1).trimEnd() + "…" : str; };
      if (t.depth === 0) txt = `<text x="${x + 6}" y="${t.y + t.h / 2 - 4}" class="tn m">${esc(W.ui.tree_root_sub)}</text><text x="${x + 6}" y="${t.y + t.h / 2 + 12}" class="tn b">${esc(W.ui.tree_root)}</text>`;
      else if (t.h >= 13) {
        const label = (t.when ? `${t.when} · ` : "") + shortLabel(t), two = t.h >= 36 ? wrap(label, Math.floor((w - 84) / 6.4)) : [fit(label, w - 84)];
        const y0 = t.y + t.h / 2 - (two.length - 1) * 7.5 + 4;
        txt = two.map((l, k) => `<text x="${x + 8}" y="${y0 + 15 * k}" class="tn">${esc(l)}</text>`).join("")
          + `<text x="${x + w - 12}" y="${t.y + t.h / 2 + 4}" class="tn m" text-anchor="end">${esc(fill(W.ui.leaf, { n: share }))}</text>`;
      }
      const bar = leaf && t.depth ? `<rect x="${x + w - 5}" y="${t.y}" width="5" height="${t.h}" fill="#dde0e4"/><rect x="${x + w - 5}" y="${t.y}" width="5" height="${t.h * fshare}" fill="#c2412d"/>` : "";
      nodes += `<g class="nd${t.depth && t.base !== "other" && t.base !== "quiet" ? " click" : ""}" data-id="${id}"><rect x="${x}" y="${t.y}" width="${w}" height="${Math.max(t.h, 1)}" rx="2" fill="${t.depth ? fillOf(t) : "#dfe2e6"}"/>${bar}${txt}</g>`;
    });
    const LG = W.ui.tree_legend;
    host.innerHTML = `<p class="mute small">${esc(W.ui.tree_note)}</p><div class="tleg"><span><i style="background:#f3d9d3"></i>${esc(LG.filed)}</span><span><i style="background:#e3ece5"></i>${esc(fill(LG.quiet, WH))}</span><span><i class="bar"></i>${esc(fill(LG.bar, WH))}</span></div><svg class="tree" viewBox="0 0 ${Wd} ${Hmax}" style="height:${Hmax}px">${links}${nodes}</svg>`;
    const svg = host.querySelector("svg"), chain = (t) => { const s = new Set(); for (let u = t; u; u = u.parent) s.add(u.id); return s; };
    svg.querySelectorAll(".nd").forEach((g) => {
      const t = all[+g.dataset.id];
      g.onmouseenter = (ev) => {
        const on = chain(t); svg.classList.add("hl");
        svg.querySelectorAll(".nd, .lk").forEach((e) => e.classList.toggle("on", on.has(+e.dataset.id)));
        const steps = []; for (let u = t; u && u.depth; u = u.parent) steps.unshift([u.when, nodeLabel(u)]);
        showTip(ev, `${linesHtml(steps)}<div class="mute" style="margin-top:6px">${esc(fill(W.ui.tree_hover, { n: n100(t.mass), f: n100(t.filed), horizon: HOR }))}</div>`);
      };
      g.onmousemove = (ev) => showTip(ev, $("tip").innerHTML);
      g.onmouseleave = () => { svg.classList.remove("hl"); hideTip(); };
      if (g.classList.contains("click")) g.onclick = () => { const i = pickNode(stepTypes(t.base), t.paths); if (i >= 0) openNode(i); };
    });
  }
  const LANES = Object.keys(W.lanes);
  function renderMap(host) {
    const mass = nodeMass(), U = W.ui;
    const cell = (type) => {
      const dec = W.decisions[type], ids = TYPE_NODES[type] || []; if (!ids.length) return "";
      const i = ids.reduce((a, b) => (mass[b] > mass[a] ? b : a)), d = dist(i), br = D.nodes[i].branches, bl = (b) => (dec.branches || {})[b] || b;
      const COLS = br.length > 2 ? ["var(--teal)", "#7fb0b3", "#c9ced4"] : ["var(--teal)", "#bfc5cc"];
      return `<button class="fork" data-i="${i}"><span class="fs">${esc(dec.short)}</span><span class="fw">${esc(W.deciders[dec.who] || dec.who)}</span>
        <span class="sb">${d.map((p, k) => `<i style="width:${100 * p}%;background:${COLS[k]}"></i>`).join("")}</span>
        <span class="fb">${br.map((b, k) => `<span><i style="background:${COLS[k]}"></i>${esc(bl(b))}&nbsp;${n100(d[k])}</span>`).join(" ")}</span>
        ${ids.length > 1 ? `<span class="fa">${esc(fill(U.asked_in, { n: ids.length }))}</span>` : ""}</button>`;
    };
    host.innerHTML = `<p class="mute small">${esc(U.map_note)}</p><div class="lanes">` + LANES.map((l) => `<div class="lane"><h3>${esc(W.lanes[l])}</h3><div class="forks">
      ${Object.entries(W.decisions).filter(([, v]) => v.lane === l).map(([k]) => cell(k)).join("")}</div></div>`).join("") + "</div>";
    host.querySelectorAll(".fork").forEach((b) => (b.onclick = () => openNode(+b.dataset.i)));
  }
  function renderTree() {
    const sec = $("s-tree"), U = W.ui;
    if (!sec.querySelector(".tv")) {
      sec.innerHTML = `<div class="thead"><h2>${esc(U.tree_title)}</h2><div class="seg" id="tv">${Object.entries(U.tree_toggle).map(([k, v]) => `<button data-v="${k}">${esc(v)}</button>`).join("")}</div></div>
        <p>${esc(W.jev.intro)}</p><div class="tv"></div>`;
      sec.querySelectorAll("#tv button").forEach((b) => (b.onclick = () => { S.variant = b.dataset.v; renderTree(); }));
    }
    sec.querySelectorAll("#tv button").forEach((b) => b.classList.toggle("on", b.dataset.v === S.variant));
    (S.variant === "paths" ? renderPaths : renderMap)(sec.querySelector(".tv"));
  }

  // --- 5. the close-up: Jev's work on one fork ----------------------------------------------------------------------
  const cap = (t) => (t ? t[0].toUpperCase() + t.slice(1) : t);
  function situation(ctx) {  // the node's context string in plain words (words.situations)
    const SI = W.situations;
    if (!ctx) return cap(SI[""]);
    return cap(ctx.split("; ").map((f) => {
      if (f in SI) return SI[f];
      if (f.startsWith("ruling leaves ")) { const nt = ", new trial on damages", rest = f.slice(14); return rest.endsWith(nt) ? `${SI["ruling leaves"]} ${rest.slice(0, -nt.length)}${SI[nt]}` : `${SI["ruling leaves"]} ${rest} owed`; }
      return f;
    }).join(", "));
  }
  const decOf = (i) => W.decisions[D.nodes[i].node] || {};
  const blabel = (i, b) => (decOf(i).branches || {})[b] || b.replace(/_/g, " ");
  const atLabels = (i) => {  // 'If no' / 'If yes' for a yes-no question, else never / always the selected answer
    const n = D.nodes[i], b = selB(i);
    return n.branches.length > 2 ? [fill(W.ui.at_never, { branch: blabel(i, n.branches[b]) }), fill(W.ui.at_always, { branch: blabel(i, n.branches[b]) })] : [W.levers.at0, W.levers.at100];
  };
  const dragBase = {};
  function openNode(i) {
    if (i < 0) return;
    S.open = i; hideTip();
    const n = D.nodes[i], det = n.detail || {}, dec = decOf(i), U = W.ui, J = W.jev, b = selB(i), d = dist(i);
    const others = (TYPE_NODES[n.node] || []).filter((k) => k !== i), mass = nodeMass();
    others.sort((a, c) => mass[c] - mass[a]);
    const link = (l) => `<a href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.text)}</a>`;
    const quotes = (det.quotes || []).slice(0, 3).map((q) => `<blockquote><p>“${esc(q.quote)}”</p><cite>${esc(q.source)}${q.link ? ` · <a href="${esc(q.link)}" target="_blank" rel="noopener">${esc(U.source)}</a>` : ""}</cite></blockquote>`).join("");
    const pick = n.branches.length > 2 ? `<div class="pick"><span class="mute small">${esc(U.branch_pick)}</span>${n.branches.map((x, k) => `<label><input type="radio" name="br" value="${k}"${k === b ? " checked" : ""}> ${esc(blabel(i, x))}</label>`).join("")}</div>` : "";
    const [l0, l1] = atLabels(i);
    $("drawer").innerHTML = `<button class="x" id="dx" aria-label="${esc(U.close)}">×</button>
      <div class="who">${esc(U.decided_by)} <b>${esc(W.deciders[dec.who] || n.decider)}</b></div>
      <h2>${esc(dec.question || n.question)}</h2><p>${esc(dec.why || "")}</p>
      <h3>${esc(U.asked_when)}</h3><p>${esc(situation(n.context))}</p>
      <h3>${esc(J.read)}</h3>${quotes || `<p class="mute">${esc(U.no_quotes)}</p>`}
      <h3>${esc(J.answer)}</h3>${pick}
      <div class="ans"><span id="d-blab"></span><span class="big" id="d-big"></span><span class="mute small" id="d-jev"></span></div>
      <input type="range" id="d-slider" min="0" max="100" step="1" value="${Math.round(100 * d[b])}"><div class="slabs" id="d-slabs"></div>
      <p class="mute small">${esc(U.conditional)}</p>
      <p class="mute small">${esc(J.drag)} <a href="#" id="d-reset"${n.key in S.overrides ? "" : " hidden"}>${esc(U.reset)}</a></p>
      <h3>${esc(J.effect)}</h3><p class="mute small">${esc(fill(U.collected_short, WH))}</p>
      <div class="eff"><div><span class="mute small">${esc(l0)}</span><b id="d-lo"></b></div><div class="j"><span class="mute small">${esc(U.at_jev)}</span><b id="d-at"></b></div><div><span class="mute small">${esc(l1)}</span><b id="d-hi"></b></div></div>
      <details class="law"><summary>${esc(J.sources)}</summary>${(det.steps || []).map((s) => `<div class="step"><span class="tag">${esc(s.tag)}</span><span>${esc(s.text)}${(s.links || []).length ? ` · ${s.links.map(link).join(" · ")}` : ""}</span></div>`).join("")}</details>
      ${others.length ? `<h3>${esc(U.other_situations)}</h3><div class="others">${others.slice(0, 5).map((k) => `<button data-i="${k}"><span>${esc(situation(D.nodes[k].context))}</span><b>${esc(blabel(k, D.nodes[k].branches[0]))} ${n100(dist(k)[0])}</b></button>`).join("")}</div>` : ""}`;
    $("drawer").classList.add("open"); $("drawer").setAttribute("aria-hidden", "false"); $("drawer").scrollTop = 0;
    updateDrawer();
    $("dx").onclick = closeDrawer;
    $("drawer").querySelectorAll(".others button").forEach((x) => (x.onclick = () => openNode(+x.dataset.i)));
    $("drawer").querySelectorAll(".pick input").forEach((r) => (r.onchange = () => { S.sel[n.key] = +r.value; openNode(i); computeSens(); renderLevers(); }));
    const sl = $("d-slider");
    sl.onpointerdown = () => { if (!(i in dragBase)) dragBase[i] = dist(i).slice(); };
    sl.oninput = () => {
      if (!(i in dragBase)) dragBase[i] = dist(i).slice();
      const t0 = performance.now();
      S.overrides[n.key] = withBranch(i, selB(i), sl.value / 100, dragBase[i]);
      onChange(t0, false);
    };
    sl.onchange = () => { delete dragBase[i]; onChange(null, true); };
    $("d-reset").onclick = (ev) => { ev.preventDefault(); delete S.overrides[n.key]; delete dragBase[i]; onChange(null, true); openNode(i); };
  }
  function updateDrawer() {  // the numbers in the open close-up, without rebuilding its slider
    const i = S.open; if (i === null || !$("d-big")) return;
    const n = D.nodes[i], b = selB(i), d = dist(i), e = effOf(i);
    $("d-big").textContent = freq(d[b]); $("d-blab").textContent = `${blabel(i, n.branches[b])}:`;
    $("d-jev").textContent = n.key in S.overrides ? fill(W.ui.jev_said, { n: n100(jevDist(i)[b]) }) : "";
    $("d-slabs").innerHTML = [b, ...n.branches.map((x, k) => k).filter((k) => k !== b)].map((k) => `<span>${esc(blabel(i, n.branches[k]))} ${freq(d[k])}</span>`).join("");
    $("d-lo").textContent = money(e.lo); $("d-at").textContent = money(e.at); $("d-hi").textContent = money(e.hi);
    $("d-reset").hidden = !(n.key in S.overrides);
  }
  function closeDrawer() { S.open = null; $("drawer").classList.remove("open"); $("drawer").setAttribute("aria-hidden", "true"); }
  document.addEventListener("keydown", (ev) => { if (ev.key === "Escape") closeDrawer(); });

  // --- 6. which judgments matter most -------------------------------------------------------------------------------
  function renderLevers() {
    const L = levers(), lo = Math.min(...L.map((l) => Math.min(l.lo, l.hi, l.at))), hi = Math.max(...L.map((l) => Math.max(l.lo, l.hi, l.at)));
    const pos = (v) => `${(100 * (v - lo)) / (hi - lo || 1)}%`;
    $("s-levers").innerHTML = `<h2>${esc(W.levers.title)}</h2><p class="mute">${esc(W.levers.sub)} ${esc(fill(W.ui.collected_short, WH))}.</p>
      <div class="levers">${L.map((l) => {
        const [a0, a1] = atLabels(l.i), dec = decOf(l.i);
        return `<button class="lever" data-i="${l.i}"><span class="ll"><b>${esc(dec.short || D.nodes[l.i].label)}</b><span class="mute">${esc(situation(D.nodes[l.i].context))}</span></span>
          <span class="lt"><span class="rng" style="left:${pos(Math.min(l.lo, l.hi))};width:calc(${pos(Math.max(l.lo, l.hi))} - ${pos(Math.min(l.lo, l.hi))})"></span><span class="mk" style="left:${pos(l.at)}"></span></span>
          <span class="lv">${(l.hi < l.lo ? [[a1, l.hi], [W.ui.at_jev, l.at], [a0, l.lo]] : [[a0, l.lo], [W.ui.at_jev, l.at], [a1, l.hi]]).map(([a, v]) => `${esc(a)} <b>${money(v)}</b>`).join(" · ")}</span></button>`;
      }).join("")}</div>`;
    $("s-levers").querySelectorAll(".lever").forEach((x) => (x.onclick = () => openNode(+x.dataset.i)));
  }

  // --- any change to an answer: every number recomputed here, the chart from the server ------------------------------
  function onChange(t0, settled) {
    recompute();
    renderFutures(); renderTree(); updateDrawer(); renderChanged();
    if (settled) { computeSens(); renderLevers(); }
    if (t0) S.lat.push(["local", performance.now() - t0]);
    refreshSeries(t0);
  }
  function renderChanged() {
    const k = Object.keys(S.overrides).length, el = $("changed");
    el.hidden = !k; if (!k) return;
    el.innerHTML = `${esc(fill(W.ui.changed, { n: k }))} <a href="#" id="rs-all">${esc(W.ui.reset_all)}</a>`;
    $("rs-all").onclick = (ev) => { ev.preventDefault(); S.overrides = {}; onChange(null, true); if (S.open !== null) openNode(S.open); };
  }

  // --- 7. what actually happened, and the footer --------------------------------------------------------------------
  function renderActual() {
    const btn = $("actual"); if (!btn) return;
    btn.textContent = S.reveal ? W.ui.reveal_hide : W.reveal.button;
    btn.onclick = async () => {
      if (!S.outcome) S.outcome = await (await fetch(`${API}/outcome`)).json();
      S.reveal = !S.reveal; renderActual();
    };
    const o = S.outcome, body = $("actual-body");
    if (!S.reveal || !o) { body.innerHTML = ""; return; }
    const fy = (iso) => `${fdate(iso)} ${iso.slice(0, 4)}`, t = D.dates.indexOf(o.petition.date);
    const by = t >= 0 ? n100(S.event.daily.petition_cum_p[t]) : null;
    body.innerHTML = `<p>${esc(W.reveal.intro)}</p>${by !== null ? `<p class="filed">${esc(fill(W.reveal.filed, { date: fy(o.petition.date), n: by }))}</p>` : ""}
      <ol class="events">${o.events.map((e) => `<li><span class="mute">${esc(fy(e.date))}</span><span>${esc(e.description)}${e.source_url ? ` <a href="${esc(e.source_url)}" target="_blank" rel="noopener">${esc(W.ui.source)}</a>` : ""}</span></li>`).join("")}</ol>`;
  }
  function renderFoot() {
    if ($("record")) $("record").textContent = W.ui.record_link;
    const t = D.case_terms || {}, U = W.ui, fy = (iso) => `${fdate(iso)} ${iso.slice(0, 4)}`;
    $("terms").innerHTML = `<details><summary>${esc(U.terms)}</summary>
      <h3>${esc(U.terms_components)}</h3><table class="terms">${(t.components || []).map((c) => `<tr><td>${esc(c.label)}</td><td>${esc(c.amount)}</td><td>${esc(c.status)}</td><td>${c.link ? `<a href="${esc(c.link)}" target="_blank" rel="noopener">${esc(c.source)}</a>` : esc(c.source)}</td></tr>`).join("")}</table>
      ${(t.notes || []).map((x) => `<h3>${esc(x.title)}</h3><table class="terms">${x.terms.map(([k, v]) => `<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>`).join("")}</table>`).join("")}
      <h3>${esc(U.terms_deadlines)}</h3><table class="terms">${(t.deadlines || []).map((x) => `<tr><td>${esc(fy(x.date))}</td><td>${esc(x.what)}</td></tr>`).join("")}</table></details>`;
  }
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
  renderSituation(); renderFutures(); renderWhen(); renderTree(); renderLevers(); renderActual(); renderFoot(); renderChanged();
  // @@RENDER
  let rz = null; window.addEventListener("resize", () => { clearTimeout(rz); rz = setTimeout(() => { renderWhen(); renderTree(); }, 150); });
  refreshSeries();  // warms the server's reweight, so the first slider move is as quick as the rest
  // @@PART3
})();

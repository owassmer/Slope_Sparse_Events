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
  const pct = (p) => `${Math.round(100 * p)}%`;
  const pts = (d) => `${d > 0 ? "+" : d < 0 ? "−" : ""}${Math.abs(Math.round(100 * d))} pts`;
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
  const COLOR = { "Filed: notes": "#e0582a", "Filed: short of cash": "#9b1c1c", "Filed: Qorvo enforcement": "#e3a008",
    "bank_filed": "#9b1c1c", "Settled": "#4d7c5a", "Paid": "#6b7280", "Stayed on appeal": "#5b7aa8",
    "Vacated or new trial": "#b8955a", "Unresolved": "#cfd4da", "bank_not_filed": "#cfd4da" };
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
    if (!filed || !hasFiling) parts.push([HOR, fill(W.steps.quiet, WH)]);
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
  function nodeReach(i) {  // probability that a path meets node i: each path's probability is linear in the node's answer
    if (isBank(i)) return 1;
    const qs = D.nodes[i].branches.map((_, b) => pathProbs((k) => (k === i ? D.nodes[i].branches.map((__, c) => +(c === b)) : dist(k))));
    let r = 0; for (let p = 0; p < P; p++) { let lo = Infinity, hi = -Infinity; for (const q of qs) { if (q[p] < lo) lo = q[p]; if (q[p] > hi) hi = q[p]; } r += hi - lo; }
    return Math.min(1, r / qs.length);  // every answer's subtree moves by the full reach, so the spread counts it once per answer
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
      const mo = (k) => (k.base === "quiet" ? 98 : MON.indexOf(k.when) < 0 ? 97 : MON.indexOf(k.when));
      const kids = [...t.kids.values()].map(weigh).sort((a, b) => b.mass - a.mass), other = { key: "other", base: "other", when: "", depth: t.depth + 1, mass: 0, filed: 0, paths: [], kids: [] };
      for (const k of kids) { if (k.mass >= MERGE) node.kids.push(k); else { other.mass += k.mass; other.filed += k.filed; other.paths = other.paths.concat(k.paths); } }
      node.kids.sort((a, b) => (mo(a) - mo(b)) || (b.mass - a.mass));
      if (other.mass > 1e-9 && node.kids.length) node.kids.push(other);  // no lone 'other': the node ends there
      if (node.kids.length === 1 && node.kids[0].base === "quiet") node.kids = [];  // a lone 'nothing forces a filing' adds nothing
      return node;
    };
    const T = weigh(TRIE), share = (t, n) => { t.pct = n; if (t.kids.length) apportion(t.kids.map((k) => k.mass), n).forEach((v, j) => share(t.kids[j], v)); };
    share(T, 100);
    return T;
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
  const effOf = (i) => {  // collections and the filing probability at 0%, the current value and 100%, for one node
    const b = selB(i), get = (x) => (k) => (k === i ? withBranch(i, b, x) : dist(k));
    const at = (key) => (isBank(i) ? bexpect(bprobs, key) : expect(probs, key));
    const v = (x) => { if (isBank(i)) { const bp = bankProbs(get(x)); return [bexpect(bp, "collected"), bexpect(bp, "petition_p")]; }
      const pp = pathProbs(get(x)); return [expect(pp, "collected"), expect(pp, "petition_p")]; };
    const [c0, f0] = v(0), [c1, f1] = v(1);
    return { lo: c0, hi: c1, at: at("collected"), flo: f0, fhi: f1, fat: at("petition_p") };
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
        <dt>${esc(L.repaid)}</dt><dd>${esc(fill(L.repaid_value, { n: D.meta.installments }))}</dd>
        <dt>${esc(L.draws)}</dt><dd>${esc(L.draws_value)}</dd></dl></div></div>
      <div class="facts">${facts.map(([k, v]) => `<div><span class="mute small">${esc(k)}</span><b>${esc(v)}</b></div>`).join("")}</div>
      <p class="mute small">${esc(W.situation.bank_note)}</p>`;
  }

  // --- 2. outcomes: two 10 x 10 grids, each square 1% of outcomes, and the comparison ------------------------------
  let DOTS = { bank: [], research: [] };
  function grid(view, groups) {
    const reps = dotPaths(groups, view === "bank"), filed = groups.filter((g) => g.filed).reduce((s, g) => s + g.n, 0);
    let sq = "", k = 0; const list = [];
    groups.forEach((g, gi) => { for (let r = 0; r < g.n; r++) { list.push({ c: g.c, n: g.n, filed: g.filed, q: reps[gi][r] }); sq += `<i data-v="${view}" data-d="${k++}" style="background:${COLOR[g.c]}"></i>`; } });
    DOTS[view] = list;
    const F = W.futures, name = view === "bank" ? W.views.bank.name : W.views.research.name;
    const item = (g) => `<li><i style="background:${COLOR[g.c]}"></i><span>${esc(outLabel(g.c))}</span><b>${g.n}%</b></li>`;
    const part = (isF) => groups.filter((g) => g.n && g.filed === isF);
    return `<div class="gblock ${view}"><div class="ghead"><span class="gname">${esc(name)}</span>
        <span class="gbig">${filed}%</span><span class="gsub">${esc(fill(W.ui.files_by, WH))}</span></div>
      <div class="gbody"><div class="waffle">${sq}</div>
        <div class="gleg"><h4>${esc(fill(F.filed_group, WH))} <b>${filed}%</b></h4><ul>${part(true).map(item).join("")}</ul>
          <h4>${esc(fill(F.not_filed_group, WH))} <b>${100 - filed}%</b></h4><ul>${part(false).map(item).join("")}</ul></div></div></div>`;
  }
  function renderFutures() {
    const f = figures(), M = W.measures;
    const GOOD = { funded: 0, due: 0, collected: 1, rate: 1, unpaid: -1, stuck: -1, clawback: -1 };
    const rows = ["funded", "due", "collected", "rate", "unpaid", "stuck", "clawback"].map((k) => {
      const m = M[k], rate = k === "rate", fmt = rate ? pct : money;
      const shown = (v) => (rate ? Math.round(100 * v) / 100 : Math.round(v / 1e5) * 1e5), d = shown(f.research[k]) - shown(f.bank[k]);
      const cls = !GOOD[k] || !d ? "" : (d > 0) === (GOOD[k] > 0) ? "good" : "bad";
      const dtxt = rate ? pts(d) : `${d > 0 ? "+" : d < 0 ? "−" : ""}${money(Math.abs(d))}`;
      return `<tr><td${m.note ? ` data-tip="${esc(fill(m.note, WH))}" class="hastip"` : ""}>${esc(fill(m.label, WH))}</td><td>${fmt(f.bank[k])}</td><td>${fmt(f.research[k])}</td><td class="${cls}">${dtxt}</td></tr>`;
    }).join("");
    $("s-futures").innerHTML = `<h2>${esc(fill(W.futures.title, WH))}</h2><p class="mute">${esc(W.futures.sub)}</p>
      <div class="grids">${grid("bank", bankDots())}${grid("research", researchDots())}</div>
      <table class="cmp"><tr><th></th><th>${esc(W.views.bank.name)}</th><th>${esc(W.views.research.name)}</th><th>${esc(W.views.difference)}</th></tr>${rows}</table>
      <p class="mute small">${esc(W.ui.change_note)}</p><p class="why">${esc(W.why_lower)}</p>`;
    $("s-futures").querySelectorAll(".waffle i").forEach((el) => {
      el.onmouseenter = () => {
        const d = DOTS[el.dataset.v][+el.dataset.d];
        const r = el.getBoundingClientRect(), at = { clientX: r.left + r.width / 2, clientY: r.top };
        showTip(at, `<div class="tt"><i style="background:${COLOR[d.c]}"></i><b>${esc(outLabel(d.c))}</b></div>`
          + (d.q === undefined ? `<div class="mute">${esc(`${d.n}%`)}</div>` : `<div class="mute">${esc(fill(W.ui.square_hover, { p: `${d.n}%` }))}</div>${linesHtml(pathLines(d.q, d.filed))}`), true);
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
  const box = (x, y, anchor, size, text, cls = "", fillc = "", bg = "#fff") => {  // a label with a box behind it
    const w = text.length * size * 0.6 + 8, x0 = anchor === "end" ? x - w + 4 : x - 4;
    return `<rect x="${x0}" y="${y - size}" width="${w}" height="${size + 5}" fill="${bg}"/><text x="${x}" y="${y}"${anchor === "end" ? ' text-anchor="end"' : ""}${cls ? ` class="${cls}"` : ""}${fillc ? ` fill="${fillc}"` : ""}>${esc(text)}</text>`;
  };
  function renderWhen() {
    const host = $("s-when");
    if (!host.querySelector("svg")) host.innerHTML = `<h2>${esc(W.ui.when_title)}</h2><p class="mute">${esc(W.ui.when_sub)}</p><svg class="when"></svg>`;
    const svg = host.querySelector("svg"), Wd = host.clientWidth, H = 500, days = D.dates.length;
    const B = S.bank.daily.petition_cum_p, R = S.event.daily.petition_cum_p;
    const m = { l: 40, r: 200, t: 92, b: 30 }, w = Wd - m.l - m.r, h = H - m.t - m.b;
    const X = (t) => m.l + (w * t) / (days - 1), Y = (p) => m.t + h - h * p, ix = (iso) => D.dates.indexOf(iso);
    let g = "";
    const rw = D.pins.ruling_window; if (rw && ix(rw[0]) >= 0) g += `<rect x="${X(ix(rw[0]))}" y="${m.t}" width="${X(days - 1) - X(ix(rw[0]))}" height="${h}" fill="#f4f5f6"/>`;
    const bandLabel = rw && ix(rw[0]) >= 0 ? box(X(days - 1) - 8, Y(0.97), "end", 12, W.ui.ruling_band, "band", "", "#f4f5f6") : "";
    for (const v of [0, 25, 50, 75, 100]) g += `<line x1="${m.l}" x2="${m.l + w}" y1="${Y(v / 100)}" y2="${Y(v / 100)}" stroke="${v ? "#eceef0" : "#c9cdd2"}"/><text x="${m.l - 8}" y="${Y(v / 100) + 4}" text-anchor="end">${v}%</text>`;
    D.dates.forEach((d, t) => { if (d.endsWith("-01")) g += `<text x="${X(t)}" y="${H - 8}" text-anchor="middle">${MON[+d.slice(5, 7) - 1]}</text><line x1="${X(t)}" x2="${X(t)}" y1="${m.t + h}" y2="${m.t + h + 5}" stroke="#c9cdd2"/>`; });
    const pins = [["briefing_close", D.pins.briefing_close], ["judgment_default", judgmentDefault], ["ruling_window", rw && rw[0]], ["nasdaq", D.pins.nasdaq], ["coupon", D.pins.coupon]]
      .filter(([, d]) => d && ix(d) >= 0).sort((a, b) => (a[1] < b[1] ? -1 : 1));
    const ends = []; let plines = "", ptexts = "";
    pins.forEach(([k, d]) => {
      const x = X(ix(d)), label = `${fdate(d)} · ${W.dates[k]}`, len = 6.4 * label.length, flip = x + 6 + len > m.l + w + m.r - 8;
      const [a, z] = flip ? [x - 5 - len, x] : [x, x + 5 + len];
      let row = ends.findIndex((e) => e.every(([p, q]) => z + 10 < p || a > q + 10)); if (row < 0) { row = ends.length; ends.push([]); }
      ends[row].push([a, z]); const y = 14 + row * 17;
      plines += `<line x1="${x}" x2="${x}" y1="${y + 4}" y2="${m.t + h}" stroke="#b9bec5" stroke-dasharray="2 3"/>`;
      ptexts += box(flip ? x - 5 : x + 5, y, flip ? "end" : "start", 12, label, "pin");
    });
    g += plines;
    const line = (ys) => ys.map((v, t) => `${t ? "L" : "M"}${X(t).toFixed(1)},${Y(v).toFixed(1)}`).join("");
    const lineUp = (ys) => ys.map((v, t) => `${t ? "L" : "M"}${X(t).toFixed(1)},${(Y(v) - 1.5).toFixed(1)}`).join("");
    g += `<path d="${lineUp(B)}" fill="none" stroke="#6b7178" stroke-width="2.2" stroke-dasharray="6 4"/><path d="${line(R)}" fill="none" stroke="var(--teal)" stroke-width="2.8"/>`;
    // the lines labelled at their ends, pushed apart if they would overlap
    let yb = Y(B[days - 1]), yr = Y(R[days - 1]);
    if (Math.abs(yb - yr) < 36) { const mid = (yb + yr) / 2, s = yb >= yr ? 1 : -1; yb = mid + 18 * s; yr = mid - 18 * s; }
    const endLab = (y, col, name, p) => `<text x="${m.l + w + 10}" y="${y - 2}" fill="${col}" class="end">${esc(name)}</text><text x="${m.l + w + 10}" y="${y + 15}" fill="${col}" class="endv">${esc(pct(p))}</text>`;
    g += endLab(yb, "#6b7178", W.views.bank.name, B[days - 1]) + endLab(yr, "var(--teal)", W.views.research.name, R[days - 1]);
    // the research curve's biggest one-day step, and where the bank line starts rising
    let tj = 1; for (let t = 2; t < days; t++) if (R[t] - R[t - 1] > R[tj] - R[tj - 1]) tj = t;
    const jump = R[tj] - R[tj - 1];
    if (jump > 0.005) {
      const x = X(tj), y0 = Y(R[tj - 1]), cause = filingCause(D.dates[tj]);
      const txt = fill(W.ui.biggest_step, { date: fdate(D.dates[tj]), n: n100(jump) }), txt2 = cause ? fill(W.ui.biggest_cause, { cause: cause[0].toLowerCase() + cause.slice(1) }) : "";
      const y1 = Y(R[tj]), ya = Y(Math.max(0.62, R[tj] + 0.25));
      g += `<line x1="${x}" x2="${x + 12}" y1="${y1 - 5}" y2="${ya + 6}" stroke="var(--teal)"/><circle cx="${x}" cy="${y1}" r="4" fill="#fff" stroke="var(--teal)" stroke-width="1.5"/>
        ${box(x + 16, ya, "start", 13, txt, "ann", "var(--teal)")}${txt2 ? box(x + 16, ya + 17, "start", 13, txt2, "ann2", "var(--teal)") : ""}`;
    }
    const tb = B.findIndex((p) => p >= 0.005);
    if (tb > 0) g += `<line x1="${X(tb)}" x2="${X(tb) - 14}" y1="${Y(0.01)}" y2="${Y(0.09)}" stroke="var(--bank)"/>${box(X(tb) - 18, Y(0.1), "end", 13, fill(W.ui.bank_rises, { date: fdate(D.dates[tb]) }), "ann", "#6b7178")}`;
    g += ptexts + bandLabel;
    g += `<line id="whx" y1="${m.t}" y2="${m.t + h}" stroke="#9aa0a8" visibility="hidden"/><rect id="whov" x="${m.l}" y="${m.t}" width="${w}" height="${h}" fill="transparent"/>`;
    svg.setAttribute("viewBox", `0 0 ${Wd} ${H}`); svg.style.height = `${H}px`; svg.innerHTML = g;
    const hov = svg.querySelector("#whov"), hx = svg.querySelector("#whx");
    hov.onmousemove = (ev) => {
      const r = hov.getBoundingClientRect(), t = Math.max(0, Math.min(days - 1, Math.round(((ev.clientX - r.left) / r.width) * (days - 1))));
      hx.setAttribute("x1", X(t)); hx.setAttribute("x2", X(t)); hx.setAttribute("visibility", "visible");
      showTip(ev, `<b>${fdate(D.dates[t])}</b><br>${esc(W.views.bank.name)}: ${pct(B[t])}<br>${esc(W.views.research.name)}: ${pct(R[t])}`);
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
  const nodeLabel = (t) => (t.base === "quiet" ? fill(W.steps.quiet, WH) : t.base === "other" ? W.ui.tree_other : phrase(t.base));
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
    const T = tree(), Wd = host.clientWidth, root = 64, colW = 305, gap = (Wd - root - DEPTH * colW - 4) / DEPTH, pad = 4, H = 560, MINH = 15, TOP = 24;
    const cols = Array.from({ length: DEPTH + 1 }, () => []), all = [];
    const walk = (t, parent) => { t.parent = parent; cols[t.depth].push(t); all.push(t); t.kids.forEach((k) => walk(k, t)); };
    walk(T, null);
    const scale = Math.min(...cols.map((c) => (c.length ? (H - pad * (c.length - 1)) / c.reduce((a, t) => a + t.mass, 0) : Infinity)));
    const X = (d) => (d === 0 ? 0 : root + gap + (d - 1) * (colW + gap)), wOf = (d) => (d === 0 ? root - 8 : colW);
    T.y = TOP; T.h = T.mass * scale;
    for (let d = 1; d <= DEPTH; d++) {
      let end = TOP - pad;
      for (const t of cols[d]) {  // children stay beside their parent's slice where there is room
        const p = t.parent; p.off = p.off ?? p.y; t.slice = p.off; p.off += t.mass * scale;
        if (t === p.kids[0] && end > TOP) end += 8;
        t.h = Math.max(t.mass * scale, MINH); t.y = Math.max(end + pad, t.slice); end = t.y + t.h;
      }
    }
    const Hmax = Math.max(...all.map((t) => t.y + t.h)) + 4;
    const mix = (f) => { const a = [223, 234, 226], b = [243, 205, 196]; return `rgb(${a.map((v, k) => Math.round(v + (b[k] - v) * f)).join(",")})`; };
    const fillOf = (t) => (t.kids.length ? "#e3e6ea" : mix(t.mass > 0 ? t.filed / t.mass : 0));
    let links = "", nodes = "", conds = "";
    all.forEach((t, id) => {
      t.id = id;
      if (t.parent) {
        const x0 = X(t.depth - 1) + wOf(t.depth - 1), x1 = X(t.depth), y0 = t.slice, y1 = t.y, h = Math.max(t.mass * scale, 1), mx = (x0 + x1) / 2;
        links += `<path class="lk${t.base === "quiet" ? " q" : FILING.has(t.base) ? " f" : ""}" data-id="${id}" d="M${x0},${y0}C${mx},${y0} ${mx},${y1} ${x1},${y1}L${x1},${y1 + h}C${mx},${y1 + h} ${mx},${y0 + h} ${x0},${y0 + h}Z"/>`;
        if (t.parent.depth) conds += `<text class="cond" x="${x1 - 5}" y="${y1 + Math.max(t.h, 1) / 2 + 4}" text-anchor="end">${Math.round((100 * t.mass) / (t.parent.mass || 1))}%</text>`;
      }
      const leaf = !t.kids.length, x = X(t.depth), w = wOf(t.depth), fshare = t.mass > 0 ? t.filed / t.mass : 0;
      const mixedAll = leaf && t.depth && t.base !== "quiet" && !FILING.has(t.base);
      const full = mixedAll ? (t.when ? `${t.when} · ` : "") + shortLabel(t) : "", mixed = mixedAll && full.length * 6.4 <= wOf(t.depth) - 104;
      let txt = "";
      const fit = (str, px) => { const n = Math.floor(px / 6.3); return str.length > n ? str.slice(0, n - 1).trimEnd() + "…" : str; };
      if (t.depth === 0) txt = `<text x="${x + 6}" y="${t.y + t.h / 2 - 4}" class="tn m">${esc(W.ui.tree_root_sub)}</text><text x="${x + 6}" y="${t.y + t.h / 2 + 12}" class="tn b">${esc(W.ui.tree_root)}</text>`;
      else if (t.h >= 13) {
        const room = mixed ? 104 : 48, label = (t.when ? `${t.when} · ` : "") + shortLabel(t), two = t.h >= 30 ? wrap(label, Math.floor((w - room) / 6.4)) : [fit(label, w - room)];
        const y0 = t.y + t.h / 2 - (two.length - 1) * 7.5 + 4;
        txt = two.map((l, k) => `<text x="${x + 8}" y="${y0 + 15 * k}" class="tn">${esc(l)}</text>`).join("")
          + `<text x="${x + w - 8}" y="${t.y + t.h / 2 + 4}" class="tn b" text-anchor="end">${t.pct}%</text>`
          + (mixed ? `<text x="${x + w - 44}" y="${t.y + t.h / 2 + 4}" class="tn m" text-anchor="end">${esc(fill(W.ui.leaf_filed, { f: pct(fshare) }))}</text>` : "");
      }
      nodes += `<g class="nd${t.depth && t.base !== "other" && t.base !== "quiet" ? " click" : ""}" data-id="${id}"><rect x="${x}" y="${t.y}" width="${w}" height="${Math.max(t.h, 1)}" rx="2" fill="${t.depth ? fillOf(t) : "#dfe2e6"}"/>${txt}</g>`;
    });
    nodes = nodes.replace(`height="${Math.max(T.h, 1)}" rx="2" fill="#dfe2e6"`, `height="${Hmax - 4 - TOP}" rx="2" fill="#dfe2e6"`);
    const LG = W.ui.tree_legend, heads = W.ui.tree_cols.map((c, k) => `<text class="colh" x="${X(k + 1)}" y="14">${esc(c)}</text>`).join("");
    host.innerHTML = `<p class="mute small">${esc(W.ui.tree_note)}</p><div class="tleg"><span><i style="background:#f3cdc4"></i>${esc(LG.filed)}</span><span><i style="background:#dfeae2"></i>${esc(fill(LG.quiet, WH))}</span><span><i style="background:#e3e6ea"></i>${esc(LG.open)}</span></div><svg class="tree" viewBox="0 0 ${Wd} ${Hmax}" style="height:${Hmax}px">${heads}${links}${conds}${nodes}</svg>`;
    const svg = host.querySelector("svg"), chain = (t) => { const s = new Set(); for (let u = t; u; u = u.parent) s.add(u.id); return s; };
    svg.querySelectorAll(".nd").forEach((g) => {
      const t = all[+g.dataset.id];
      g.onmouseenter = (ev) => {
        const on = chain(t); svg.classList.add("hl");
        svg.querySelectorAll(".nd, .lk").forEach((e) => e.classList.toggle("on", on.has(+e.dataset.id)));
        const steps = []; for (let u = t; u && u.depth; u = u.parent) steps.unshift([u.when, nodeLabel(u)]);
        showTip(ev, `${linesHtml(steps)}<div class="mute" style="margin-top:6px">${esc(fill(W.ui.tree_hover, { p: `${t.pct}%`, f: pct(t.mass > 0 ? t.filed / t.mass : 0), horizon: HOR }))}</div>`);
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
      const COLS = br.length > 2 ? ["var(--teal)", "#8ea3bd", "#d8c9a8"] : ["var(--teal)", "#d8c9a8"];
      const ys = ids.map((k) => dist(k)[0]), lo = Math.min(...ys), hi = Math.max(...ys);
      return `<button class="fork" data-i="${i}"><span class="fs">${esc(dec.short)}</span><span class="fw">${esc(W.deciders[dec.who] || dec.who)}</span>
        <span class="sb">${d.map((p, k) => `<i style="width:${100 * p}%;background:${COLS[k]}"></i>`).join("")}</span>
        <span class="fb">${br.map((b, k) => `<span><i style="background:${COLS[k]}"></i>${esc(bl(b))}&nbsp;${pct(d[k])}</span>`).join(" ")}</span>
        <span class="fa">${ids.length > 1 ? esc(fill(U.asked_in, { n: ids.length, lo: Math.round(100 * lo), hi: pct(hi) })).replace(/(Jev \S+)$/, '<span class="nw">$1</span>') : esc(fill(U.reach_card, { p: pct(nodeReach(i)) }))}</span></button>`;
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
    return ["0%", "100%"];
  };
  const cents = (t) => Math.round(100 * parseFloat(String(t).replace(/[$,]/g, "")));
  function inputs(f) {  // the facts Jev was given, as label: value
    const U = W.ui, out = [], m = (v) => money(cents(v));
    const rng = (o, fmt) => { const a = fmt(o.p50); return o.p5 && o.p95 && o.p5 !== o.p95 ? `${a} (${fmt(o.p5)} – ${fmt(o.p95)})` : a; };
    const dd = (t) => t;
    if (f.decision_date) out.push([U.in_date, rng(f.decision_date, dd)]);
    if (f.projected_available_cash_at_decision_date) out.push([U.in_cash, rng(f.projected_available_cash_at_decision_date, m)]);
    if (f.operating_need_30_days_at_decision) out.push([U.in_need, m(f.operating_need_30_days_at_decision.p50)]);
    if (f.amount_owed_at_decision) out.push([U.in_owed, m(f.amount_owed_at_decision.p50)]);
    if (f.judgment_after_ruling) out.push([U.in_after, String(f.judgment_after_ruling).split(" to ").map(m).join(" – ")]);
    if (f.settlement_offer) out.push([U.in_offer, rng(f.settlement_offer.amount, m)]);
    if (f.bond_collateral_required) out.push([U.in_bond, m(f.bond_collateral_required)]);
    if (f.reduced_security_offered) out.push([U.in_security, rng(f.reduced_security_offered, m)]);
    return out;
  }
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
    const [l0, l1] = atLabels(i), ins = inputs(det.facts || {});
    $("drawer").innerHTML = `<button class="x" id="dx" aria-label="${esc(U.close)}">×</button>
      <h3>${esc(J.question)}</h3>
      <h2>${esc(dec.question || n.question)}</h2>
      <dl class="meta"><dt>${esc(U.decided_by)}</dt><dd>${esc(W.deciders[dec.who] || n.decider)}</dd>
        <dt>${esc(U.asked_when)}</dt><dd>${esc(situation(n.context))}</dd>
        <dt>${esc(U.reached)}</dt><dd id="d-reach"></dd></dl>
      <p class="mute small">${esc(fill(dec.why || "", { offer: factOffer ? money(factOffer) : "–" }))}</p>
      <h3>${esc(J.forecast)}</h3>${pick}
      <div class="ans"><span id="d-blab"></span><span class="big" id="d-big"></span><span class="mute small" id="d-jev"></span></div>
      <div class="sw"><input type="range" id="d-slider" min="0" max="100" step="1" value="${Math.round(100 * d[b])}"><span class="jm" style="left:${100 * jevDist(i)[b]}%" title="Jev"></span></div>
      <div class="slabs" id="d-slabs"></div>
      <p class="mute small">${esc(J.drag)} <a href="#" id="d-reset"${n.key in S.overrides ? "" : " hidden"}>${esc(U.reset)}</a></p>
      <h3>${esc(J.effect)}</h3><p class="mute small" id="d-ah"></p>
      <table class="eff"><tr><th></th><th>${esc(l0)}</th><th class="j" id="d-jh"></th><th>${esc(l1)}</th></tr>
        <tr><td>${esc(fill(U.row_collected, WH))}</td><td id="d-lo"></td><td class="j" id="d-at"></td><td id="d-hi"></td></tr>
        <tr><td>${esc(fill(U.row_filing, WH))}</td><td id="d-flo"></td><td class="j" id="d-fat"></td><td id="d-fhi"></td></tr></table>
      ${ins.length ? `<h3>${esc(J.inputs)}</h3><dl class="inputs">${ins.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl>` : ""}
      <h3>${esc(J.evidence)}</h3>${quotes || `<p class="mute">${esc(U.no_quotes)}</p>`}
      <details class="law"><summary>${esc(J.sources)}</summary>${(det.steps || []).map((x) => `<div class="step"><span class="tag">${esc(x.tag)}</span><span>${esc(x.text)}${(x.links || []).length ? ` · ${x.links.map(link).join(" · ")}` : ""}</span></div>`).join("")}</details>
      ${others.length ? `<h3>${esc(U.other_situations)}</h3><div class="others">${others.slice(0, 5).map((k) => `<button data-i="${k}"><span>${esc(situation(D.nodes[k].context))}</span><b>${esc(blabel(k, D.nodes[k].branches[0]))} ${pct(dist(k)[0])}</b></button>`).join("")}</div>` : ""}`;
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
    $("d-big").textContent = pct(d[b]); $("d-blab").textContent = `${blabel(i, n.branches[b])}:`;
    $("d-jev").textContent = n.key in S.overrides ? fill(W.ui.jev_said, { p: pct(jevDist(i)[b]) }) : "";
    $("d-slabs").innerHTML = `<span>0%</span><span>${esc(n.branches.map((x, k) => `${blabel(i, x)} ${pct(d[k])}`).join(" · "))}</span><span>100%</span>`;
    $("d-reach").textContent = fill(W.jev.reach, { p: pct(nodeReach(i)) });
    $("d-jh").textContent = `${W.ui.at_jev} ${pct(d[b])}`; $("d-ah").textContent = fill(W.ui.override_of, { branch: blabel(i, n.branches[b]), horizon: HOR });
    $("d-lo").textContent = money(e.lo); $("d-at").textContent = money(e.at); $("d-hi").textContent = money(e.hi);
    $("d-flo").textContent = pct(e.flo); $("d-fat").textContent = pct(e.fat); $("d-fhi").textContent = pct(e.fhi);
    $("d-reset").hidden = !(n.key in S.overrides);
  }
  function closeDrawer() { S.open = null; $("drawer").classList.remove("open"); $("drawer").setAttribute("aria-hidden", "true"); }
  document.addEventListener("keydown", (ev) => { if (ev.key === "Escape") closeDrawer(); });

  // --- 6. which judgments matter most -------------------------------------------------------------------------------
  function renderLevers() {
    const L = levers(), lo0 = Math.min(...L.map((l) => Math.min(l.lo, l.hi, l.at))), hi0 = Math.max(...L.map((l) => Math.max(l.lo, l.hi, l.at)));
    const padd = 0.06 * (hi0 - lo0 || 1), lo = lo0 - padd, hi = hi0 + padd, pos = (v) => (100 * (v - lo)) / (hi - lo);
    $("s-levers").innerHTML = `<h2>${esc(W.levers.title)}</h2><p class="mute">${esc(fill(W.levers.sub, WH))}</p>
      <div class="levers">${L.map((l, r) => {
        const [a0, a1] = atLabels(l.i), dec = decOf(l.i), b = selB(l.i), sit = situation(D.nodes[l.i].context);
        const left = l.lo <= l.hi ? [a0, l.lo] : [a1, l.hi], right = l.lo <= l.hi ? [a1, l.hi] : [a0, l.lo];
        return `<button class="lever" data-i="${l.i}"><span class="ll"><b>${esc(dec.short || D.nodes[l.i].label)}</b>
            <span class="lm" title="${esc(sit)}">${esc(fill(W.ui.lever_jev, { p: pct(dist(l.i)[b]), branch: blabel(l.i, D.nodes[l.i].branches[b]) }))} · ${esc(sit)}</span></span>
          <span class="lt"><span class="rng" style="left:${pos(left[1])}%;width:${pos(right[1]) - pos(left[1])}%"></span>
            <span class="mk" style="left:${pos(l.at)}%"></span>${r === 0 ? `<span class="mkl" style="left:${pos(l.at)}%">${esc(W.ui.at_jev)} ${money(l.at)}</span>` : ""}
            <span class="endl" style="right:${100 - pos(left[1])}%">${esc(left[0])} · <b>${money(left[1])}</b></span>
            <span class="endr" style="left:${pos(right[1])}%"><b>${money(right[1])}</b> · ${esc(right[0])}</span></span></button>`;
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
    const by = t >= 0 ? pct(S.event.daily.petition_cum_p[t]) : null, bb = t >= 0 ? pct(S.bank.daily.petition_cum_p[t]) : null;
    body.innerHTML = `<p>${esc(W.reveal.intro)}</p>${by !== null ? `<p class="filed">${esc(fill(W.reveal.filed, { date: fy(o.petition.date), r: by, b: bb }))}</p>` : ""}
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

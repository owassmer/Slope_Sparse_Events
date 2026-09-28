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
    if (a >= 1e6) { const m = a / 1e6; return `${s}$${m < 10 ? m.toFixed(1) : m >= 100 ? Math.round(m) : m.toFixed(1)}M`; }
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
    "bank_filed": "#c2412d", "Settled": "#5f8f6c", "Paid": "#8a9199", "Stayed on appeal": "#a9aeb5",
    "Vacated or new trial": "#c3c8ce", "Unresolved": "#dde0e4", "bank_not_filed": "#dde0e4" };
  const outLabel = (c) => fill((OUT[c] || { label: c }).label, WH);
  const isFiled = (c) => !!(OUT[c] || {}).filed;
  function classProbs() { const c = new Float64Array(CLS.length); for (let i = 0; i < P; i++) for (const [k, s] of D.paths.class[i]) c[k] += probs[i] * s; return c; }
  // largest-remainder rounding of shares to 100 dots
  function allocate(shares) {
    const tot = shares.reduce((a, b) => a + b, 0) || 1, raw = shares.map((s) => (100 * s) / tot);
    const n = raw.map(Math.floor); let left = 100 - n.reduce((a, b) => a + b, 0);
    raw.map((r, k) => [r - n[k], k]).sort((a, b) => b[0] - a[0]).forEach(([, k]) => { if (left > 0) { n[k]++; left--; } });
    return n;
  }
  // dot groups in display order: filed first (largest cause first), then not filed
  function dotGroups(names, shares) {
    const n = allocate(shares), g = names.map((c, k) => ({ c, share: shares[k], n: n[k], k, filed: isFiled(c) }));
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
  function sentence(q, filed) {
    const { steps, filed: hasFiling } = stepsOf(q, !filed);
    const parts = steps.map((st) => `${st.when ? `${st.when}: ` : ""}${phrase(st.base)}`);
    if (!filed || !hasFiling) parts.push(`${HOR}: ${W.steps.quiet}`);
    return parts.join("; ") + ".";
  }
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
  // @@PART3
})();

// The analysis page: evidence -> Jev judgment -> financial mechanism -> financial impact, with controls.
// Everything renders from one analysis object; controls POST to /runs/{id}/analysis and re-render.
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  let D = JSON.parse($("analysis-data").textContent);
  const RUN = location.pathname.split("/")[2];
  const BASE = D.setup;
  const state = { controls: {}, overrides: {}, selected: null, showAllPaths: false, showAllStress: false };
  const COL = { bank: "#64748b", event: "#7c3aed", contract: "#0f766e", mark: "#b45309" };
  const ORDER = ["settle_before_ruling", "amount_fixed", "settle_after_judgment", "appeal", "secured_stay", "security_form",
                 "settle_during_appeal", "voluntary_payment", "enforcement"];

  // --- formatting -------------------------------------------------------------------------------
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const money = (cents, dp) => {
    const v = cents / 100, a = Math.abs(v), s = v < 0 ? "−" : "";
    if (a >= 1e6) return `${s}$${(a / 1e6).toFixed(dp ?? (a >= 1e8 ? 0 : 2))}M`;
    if (a >= 1e3) return `${s}$${(a / 1e3).toFixed(dp ?? 0)}k`;
    return `${s}$${a.toFixed(0)}`;
  };
  const delta = (cents) => (Math.abs(cents) < 100 ? "no change" : (cents > 0 ? "+" : "") + money(cents));
  const pct = (p, dp = 0) => `${(100 * p).toFixed(dp)}%`;
  const dd = (x) => (x >= 1e6 ? `${(x / 1e6).toFixed(1)}M` : `${Math.round(x / 1e3)}k`);
  const fdate = (iso) => new Date(iso + "T12:00:00").toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
  const cls = (x, eps) => (Math.abs(x) < eps ? "flat" : x > 0 ? "up" : "down");
  const shortTitle = (id) => (D.disputes.find((x) => x.id === id) || {}).short || id;
  const yesP = (dist) => (dist && "yes" in dist ? dist.yes : null);

  // --- header, kpis, lead -----------------------------------------------------------------------
  function header() {
    const s = D.setup;
    $("hdr-title").textContent = `${D.borrower}: how its disputes move the loan's cash flows`;
    $("hdr-sub").textContent = `As of ${fdate(s.review)} · ${money(s.amount_cents)} of a ${money(s.invoice_cents)} supplier invoice financed by Slope, `
      + `funded ${fdate(s.funding)}, ${s.installments} monthly installments, ${(s.fee_bps / 100).toFixed(2)}% fee · analysed to ${fdate(s.horizon)} `
      + `across ${s.draws} operating draws and ${D.scenarios.length} event paths`;
  }

  function kpis() {
    const b = D.views.bank_only.metrics, e = D.views.event_adjusted.metrics;
    const tile = (label, v, d, c) => `<div class="kpi"><div class="l">${label}</div><div class="v">${v}</div><div class="d ${c}">${d}</div></div>`;
    $("kpis").innerHTML = [
      tile("Lender cash flows, discounted", money(e.lender_pv_cents, 1), `bank data only ${money(b.lender_pv_cents, 1)} · ${delta(e.lender_pv_cents - b.lender_pv_cents)}`, cls(e.lender_pv_cents - b.lender_pv_cents, 100)),
      tile("Capital tied up (principal dollar-days)", dd(e.dollar_days), `bank data only ${dd(b.dollar_days)} · ${Math.abs(e.dollar_days - b.dollar_days) < 1 ? "no change" : dd(e.dollar_days - b.dollar_days)}`, cls(b.dollar_days - e.dollar_days, 1)),
      tile("Collected in full by maturity", pct(e.full_collection_by_maturity_p, 1), `bank data only ${pct(b.full_collection_by_maturity_p, 1)} · uncollected ${money(e.uncollected_maturity_cents)}`, cls(e.full_collection_by_maturity_p - b.full_collection_by_maturity_p, 1e-4)),
      tile("Borrower's lowest cash, 5th percentile", money(e.min_cash_p5_cents), `bank data only ${money(b.min_cash_p5_cents)} · ${delta(e.min_cash_p5_cents - b.min_cash_p5_cents)}`, cls(e.min_cash_p5_cents - b.min_cash_p5_cents, 100)),
      tile("Chance of a cash shortfall", pct(e.shortfall_p, 1), `bank data only ${pct(b.shortfall_p, 1)}${e.peak_locked_cents > 0 ? ` · collateral locked ${money(e.peak_locked_cents)} (expected peak)` : ""}`, cls(b.shortfall_p - e.shortfall_p, 1e-4)),
    ].join("");
    const loanFlat = Math.abs(e.lender_pv_cents - b.lender_pv_cents) < 100 && Math.abs(e.full_collection_by_maturity_p - b.full_collection_by_maturity_p) < 1e-6;
    const cash = `expected lowest cash moves from ${money(b.min_cash_mean_cents)} to ${money(e.min_cash_mean_cents)}, and the 5th percentile from ${money(b.min_cash_p5_cents)} to ${money(e.min_cash_p5_cents)}`;
    $("lead").innerHTML = loanFlat
      ? `The loan is collected in full, on schedule, on every modelled path and draw: the disputes do not change its cash flows. They do change the borrower's cash: ${cash}. The drivers below are ranked by that effect.`
      : `The disputes change the loan: discounted lender cash flows ${delta(e.lender_pv_cents - b.lender_pv_cents)}, collection in full by maturity ${pct(b.full_collection_by_maturity_p, 1)} → ${pct(e.full_collection_by_maturity_p, 1)}, capital tied up ${dd(e.dollar_days - b.dollar_days)} dollar-days. Behind it, ${cash}.`;
  }

  // --- what the research found ------------------------------------------------------------------
  function findings() {
    const J = Object.values(D.judgments);
    const cards = D.disputes.map((d) => {
      const est = d.established.map((e) => `<li>${esc(e.event)} <span class="muted">(source of ${fdate(e.date)})</span></li>`).join("");
      const facts = d.factors.filter((f) => f.reading && f.reading !== "unknown" && f.reading !== "not established")
        .map((f) => `<li>${esc(f.label)}: <b>${esc(f.reading)}</b></li>`).join("");
      const cons = "";
      const fc = J.filter((j) => j.dispute === d.id).sort((a, b) => ORDER.indexOf(a.node) - ORDER.indexOf(b.node) || a.condition.localeCompare(b.condition)).map((j) => {
        const p = yesP(j.distribution);
        const val = p === null ? Object.entries(j.distribution).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${k.replace(/_/g, " ")} ${pct(v)}`).slice(0, 1).join("") : pct(p);
        const cond = j.condition ? ` <span class="muted">${esc(j.condition)}</span>` : "";
        return `<div class="ev" data-key="${esc(j.key)}">${esc(j.label)}${cond}</div><div class="pbar"><div class="bar"><i style="width:${p === null ? 100 * Math.max(...Object.values(j.distribution)) : 100 * p}%"></i></div><b>${val}</b></div>`;
      }).join("");
      return `<div class="dcard"><h4>${esc(d.short)}</h4>
        <div class="muted">${esc(d.nature)}, ${esc(d.reference)} · <b>${esc(d.payer)}</b> pays <b>${esc(d.payee)}</b> ${money(d.amount_cents)} (${esc(d.amount_status)})</div>
        <ul class="muted" style="margin:.4rem 0 0 1rem;font-size:.82rem">${est}${facts}${cons}</ul>
        <div class="fc">${fc}</div></div>`;
    }).join("");
    const nm = (D.not_modelled || []).map((n) => `<p class="muted">${esc(n.title)}: not modelled (${esc(n.requests.join("; "))})</p>`).join("");
    $("findings").innerHTML = `<h3>What the research found, and what might happen next</h3>
      <p class="sub">Present state read by Jev from the cited passages; each bar is Jev's probability of the next development, given the assumptions shown. ${esc(D.probability_label)}</p>
      <div class="disputes">${cards}</div>${nm}`;
    document.querySelectorAll(".fc .ev").forEach((el) => el.addEventListener("click", () => select(el.dataset.key, true)));
  }

  // --- charts -----------------------------------------------------------------------------------
  function scaleFor(values, lo0) {
    let lo = Math.min(...values), hi = Math.max(...values);
    if (lo0) lo = Math.min(0, lo);
    const pad = (hi - lo) * 0.08 || 1; lo -= lo0 ? 0 : pad; hi += pad;
    const step = niceStep((hi - lo) / 4);
    return { lo: Math.floor(lo / step) * step, hi: Math.ceil(hi / step) * step, step };
  }
  function niceStep(raw) {
    const p = 10 ** Math.floor(Math.log10(raw)); const f = raw / p;
    return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * p;
  }
  function chart(svgId, tipId, series, opts) {
    const svg = $(svgId), W = 960, H = +svg.getAttribute("viewBox").split(" ")[3], L = 62, R = 12, T = 10, B = 24;
    const n = D.dates.length, x = (i) => L + (i / (n - 1)) * (W - L - R);
    const all = series.flatMap((s) => [...(s.lo || []), ...(s.hi || []), ...(s.line || [])]);
    const sc = scaleFor(all, opts.zero), y = (v) => T + (1 - (v - sc.lo) / (sc.hi - sc.lo)) * (H - T - B);
    let g = "";
    for (let v = sc.lo; v <= sc.hi + 1e-6; v += sc.step) g += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" stroke="currentColor" opacity=".08"/><text x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${money(v, sc.step >= 1e8 ? 0 : 1)}</text>`;
    D.dates.forEach((d, i) => { if (d.endsWith("-01")) g += `<line x1="${x(i)}" x2="${x(i)}" y1="${T}" y2="${H - B}" stroke="currentColor" opacity=".06"/><text x="${x(i)}" y="${H - 6}" text-anchor="middle">${new Date(d + "T12:00:00").toLocaleDateString("en-GB", { month: "short" })}</text>`; });
    const path = (arr, step) => arr.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}${step && i < arr.length - 1 ? ` L${x(i + 1).toFixed(1)},${y(v).toFixed(1)}` : ""}`).join("");
    for (const s of series) {
      if (s.lo) g += `<path d="${path(s.hi)} ${s.lo.map((v, i) => `L${x(s.lo.length - 1 - i).toFixed(1)},${y(s.lo[s.lo.length - 1 - i]).toFixed(1)}`).join("")} Z" fill="${s.color}" opacity="${s.opacity ?? 0.16}"/>`;
      if (s.line) g += `<path d="${path(s.line, s.step)}" fill="none" stroke="${s.color}" stroke-width="${s.width ?? 2}" ${s.dash ? `stroke-dasharray="${s.dash}"` : ""}/>`;
    }
    (opts.marks || []).forEach((m) => { const i = D.dates.indexOf(m.date); if (i >= 0) g += `<circle cx="${x(i)}" cy="${y(opts.markY ? opts.markY(i) : sc.lo) - (opts.markY ? 0 : -2)}" r="3.5" fill="${COL.mark}"><title>${esc(m.label)}</title></circle>`; });
    g += `<line id="${svgId}-hover" x1="0" x2="0" y1="${T}" y2="${H - B}" stroke="currentColor" opacity="0"/>`;
    svg.innerHTML = g;
    const tip = $(tipId), hover = $(`${svgId}-hover`);
    svg.onmousemove = (ev) => {
      const r = svg.getBoundingClientRect(), px = ((ev.clientX - r.left) / r.width) * W;
      const i = Math.max(0, Math.min(n - 1, Math.round(((px - L) / (W - L - R)) * (n - 1))));
      hover.setAttribute("x1", x(i)); hover.setAttribute("x2", x(i)); hover.setAttribute("opacity", ".35");
      tip.style.display = "block";
      tip.innerHTML = `<b>${fdate(D.dates[i])}</b><br>` + series.filter((s) => s.tip).map((s) => s.tip(i)).join("<br>");
      const left = (x(i) / W) * r.width; tip.style.left = `${left > r.width * 0.65 ? left - tip.offsetWidth - 12 : left + 12}px`; tip.style.top = "8px";
    };
    svg.onmouseleave = () => { tip.style.display = "none"; hover.setAttribute("opacity", "0"); };
  }

  function charts() {
    const b = D.views.bank_only.daily, e = D.views.event_adjusted.daily;
    const marks = D.setup.schedule.map((p) => ({ date: p.due, label: `Installment ${money(p.amount_cents)} due ${fdate(p.due)}` }));
    $("legend-cash").innerHTML = `<span style="--c:${COL.bank}">Bank data only (median, 5–95%)</span><span style="--c:${COL.event}">With the disputes (median, 5–95%)</span><span style="--c:${COL.mark}">Loan payment dates</span>`;
    chart("chart-cash", "tip-cash", [
      { lo: b.cash_p5, hi: b.cash_p95, line: b.cash_p50, color: COL.bank, opacity: 0.14, dash: "5 4", tip: (i) => `Bank only: ${money(b.cash_p50[i])} <span class="muted">(${money(b.cash_p5[i])}–${money(b.cash_p95[i])})</span>` },
      { lo: e.cash_p5, hi: e.cash_p95, line: e.cash_p50, color: COL.event, opacity: 0.18, tip: (i) => `With disputes: ${money(e.cash_p50[i])} <span class="muted">(${money(e.cash_p5[i])}–${money(e.cash_p95[i])})</span>${e.locked_mean[i] > 0 ? `<br><span class="muted">collateral locked (expected) ${money(e.locked_mean[i])}</span>` : ""}` },
    ], { marks });
    $("legend-coll").innerHTML = `<span style="--c:${COL.contract}">Contractual schedule</span><span style="--c:${COL.event}">Collected with the disputes (expected, 5–95%)</span><span style="--c:${COL.bank}">Collected, bank data only (expected)</span>`;
    chart("chart-coll", "tip-coll", [
      { line: e.contractual, color: COL.contract, step: true, width: 2.5, tip: (i) => `Contractual: ${money(e.contractual[i])}` },
      { lo: e.collected_p5, hi: e.collected_p95, line: e.collected_mean, color: COL.event, opacity: 0.18, tip: (i) => `Collected with disputes: ${money(e.collected_mean[i])} <span class="muted">(${money(e.collected_p5[i])}–${money(e.collected_p95[i])})</span>` },
      { line: b.collected_mean, color: COL.bank, dash: "5 4", tip: (i) => `Collected, bank only: ${money(b.collected_mean[i])}<br><span class="muted">principal outstanding ${money(e.outstanding_mean[i])}</span>` },
    ], { zero: true, marks });
  }

  // --- drivers: which judgments matter ----------------------------------------------------------
  function driverRows() {
    const rows = D.sensitivity.judgments;
    const loanMoves = rows.some((r) => r.range.lender_pv >= 100 || r.range.uncollected_maturity >= 100);
    const tr = rows.map((r, i) => {
      const j = D.judgments[r.key], p = yesP(r.jev);
      const lo = r.variants["0%"], hi = r.variants["100%"];
      const effLoan = r.range.lender_pv < 100 && r.range.uncollected_maturity < 100 ? `<span class="flat">unchanged</span>` :
        (lo ? `${money(lo.lender_pv, 1)} → ${money(hi.lender_pv, 1)}` : money(r.range.lender_pv, 1) + " range");
      const cash = lo ? `${money(lo.min_cash)} → ${money(hi.min_cash)}` :
        Object.entries(r.variants).map(([k, v]) => `${k.replace(/_/g, " ")} ${money(v.min_cash)}`).join(" · ");
      const prob = p === null ? Object.entries(r.jev).map(([k, v]) => `${k.replace(/_/g, " ")} ${pct(v)}`).join(", ") : `<b>${pct(p)}</b>${state.overrides[r.key] ? ` <span class="muted">(Jev ${pct(yesP(r.jev_model))})</span>` : ""}`;
      return `<tr class="drv${state.selected === r.key ? " sel" : ""}" data-key="${esc(r.key)}"><td>${i + 1}</td><td><b>${esc(j.label)}</b>${j.condition ? ` <span class="muted">${esc(j.condition)}</span>` : ""}<div class="muted">${esc(j.dispute_short)}</div></td>
        <td>${prob}</td><td>${cash}</td><td>${effLoan}</td></tr>${state.selected === r.key ? `<tr><td colspan="5">${drill(r)}</td></tr>` : ""}`;
    });
    const head = loanMoves ? "Ranked by the change in the lender's discounted cash flows, then capital tied up." : "The loan's collections do not change under any single judgment, so these are ranked by their effect on the borrower's lowest cash.";
    const selIdx = rows.findIndex((r) => r.key === state.selected);
    const visible = state.showAllDrivers ? tr : tr.filter((_, i) => i < 6 || i === selIdx);
    $("drivers").innerHTML = `<h3>Which judgments matter</h3>
      <p class="sub">Each of Jev's forecasts set to 0% and 100%, everything else held fixed (same draws). ${head} Select a row to follow it from the evidence to the money.</p>
      <table class="t"><thead><tr><th>#</th><th>Development</th><th>Jev</th><th>Borrower's lowest cash, 0% → 100% (expected)</th><th>Loan: discounted cash flows, 0% → 100%</th></tr></thead><tbody>${visible.join("")}</tbody></table>
      ${rows.length > 6 ? `<span class="more" id="more-drivers">${state.showAllDrivers ? "Show fewer" : `Show all ${rows.length}`}</span>` : ""}`;
    document.querySelectorAll("tr.drv").forEach((el) => el.addEventListener("click", () => select(el.dataset.key === state.selected ? null : el.dataset.key)));
    const more = $("more-drivers"); if (more) more.onclick = () => { state.showAllDrivers = !state.showAllDrivers; driverRows(); };
    bindDrill();
  }

  function drill(r) {
    const j = D.judgments[r.key];
    const ev = (j.evidence || []).map((p) => {
      let ctx = esc(p.context || "");
      for (const q of p.quotes) { const e = esc(q); if (ctx.includes(e)) ctx = ctx.replace(e, `<mark>${e}</mark>`); }
      if (!ctx.includes("<mark>")) ctx = p.quotes.map((q) => `<mark>${esc(q)}</mark>`).join(" … ");
      return `<blockquote>${ctx}<footer class="muted">${esc(p.source)}, ${esc(p.date)}${p.heading ? " · " + esc(p.heading) : ""}</footer></blockquote>`;
    }).join("");
    const readings = Object.entries(j.readings || {}).map(([label, v]) => {
      if (v.probability_present !== undefined) return `<div>${esc(label)}: ${pct(v.probability_present)} present</div>`;
      const top = Object.entries(v.distribution).sort((a, b) => b[1] - a[1]).slice(0, 2).map(([k, p]) => `${esc(k)} <b>${pct(p)}</b>`).join(" · ");
      return `<div>${esc(label)}: ${top}${v.conflicting_readings ? ` <span class="muted">(conflicting passages)</span>` : ""}</div>`;
    }).join("");
    const dist = Object.entries(r.jev).sort((a, b) => (b[0] === "yes") - (a[0] === "yes")).map(([k, v]) => `<div class="pbar" style="max-width:28rem"><span style="width:8rem;font-size:.8rem">${esc(k.replace(/_/g, " "))}</span><div class="bar"><i style="width:${100 * v}%"></i></div><b>${pct(v)}</b></div>`).join("");
    const variants = Object.entries(r.variants).map(([k, v]) => `<tr><td>${esc(k.replace(/_/g, " "))}</td><td>${money(v.min_cash)}</td><td>${pct(v.shortfall_p, 1)}</td><td>${money(v.peak_locked)}</td><td>${money(v.lender_pv, 1)}</td><td>${dd(v.dollar_days)}</td><td>${money(v.uncollected_maturity)}</td></tr>`).join("");
    const at = r.at_jev;
    const p = yesP(r.jev);
    const control = p === null ? Object.keys(r.jev).map((k) => `<button class="outline setopt" data-opt="${esc(k)}">${esc(k.replace(/_/g, " "))} 100%</button>`).join(" ")
      : `<input type="range" min="0" max="100" step="1" value="${Math.round(100 * p)}" id="node-slider" style="max-width:14rem"> <b id="node-val">${pct(p)}</b>
         <button class="outline setp" data-p="0">0%</button><button class="outline setp" data-p="jev">Jev ${pct(yesP(r.jev_model))}</button><button class="outline setp" data-p="1">100%</button>`;
    return `<div class="drill">
      <div class="chain"><b>Evidence</b> → <b>present-state reading</b> → <b>Jev's forecast</b> → <b>cash mechanism</b> → <b>financial effect</b></div>
      <h5>Evidence</h5>${ev || '<p class="muted">No passage beyond the case facts.</p>'}
      ${readings ? `<h5>Present-state readings (Jev)</h5><div style="font-size:.84rem">${readings}</div>` : ""}
      <h5>Jev's forecast</h5>
      <div style="font-size:.86rem"><b>Event:</b> ${esc(j.event.charAt(0).toUpperCase() + j.event.slice(1))}. <b>Window:</b> ${esc(j.window)}.${j.assumptions.length ? `<div class="muted"><b>Assuming:</b> ${j.assumptions.map(esc).join(" ")}</div>` : ""}</div>
      ${dist}
      <div class="node" style="margin:.4rem 0">${control}${state.overrides[r.key] ? ` <button class="secondary resetnode">Reset to Jev</button>` : ""}</div>
      <h5>Cash mechanism</h5><div style="font-size:.86rem">${esc(j.mechanism)}</div>
      <h5>Financial effect (expected, same draws)</h5>
      <table class="t"><thead><tr><th>This development</th><th>Borrower's lowest cash</th><th>Shortfall chance</th><th>Peak collateral locked</th><th>Lender cash flows, discounted</th><th>Capital tied up</th><th>Uncollected at maturity</th></tr></thead>
      <tbody>${variants}<tr><td><b>at ${p === null ? "Jev's mix" : pct(p)}</b></td><td>${money(at.min_cash)}</td><td>${pct(at.shortfall_p, 1)}</td><td>${money(at.peak_locked)}</td><td>${money(at.lender_pv, 1)}</td><td>${dd(at.dollar_days)}</td><td>${money(at.uncollected_maturity)}</td></tr></tbody></table>
    </div>`;
  }

  function bindDrill() {
    const key = state.selected; if (!key) return;
    const j = D.judgments[key];
    const slider = $("node-slider");
    if (slider) slider.oninput = () => { $("node-val").textContent = `${slider.value}%`; setNode(key, slider.value / 100); };
    document.querySelectorAll(".setp").forEach((b) => b.onclick = (ev) => {
      ev.stopPropagation();
      if (b.dataset.p === "jev") { delete state.overrides[key]; recalc(); } else setNode(key, +b.dataset.p);
    });
    document.querySelectorAll(".setopt").forEach((b) => b.onclick = (ev) => {
      ev.stopPropagation(); state.overrides[key] = Object.fromEntries(Object.keys(j.distribution).map((k) => [k, k === b.dataset.opt ? 1 : 0])); recalc();
    });
    document.querySelectorAll(".resetnode").forEach((b) => b.onclick = (ev) => { ev.stopPropagation(); delete state.overrides[key]; recalc(); });
    document.querySelectorAll(".drill").forEach((el) => el.addEventListener("click", (ev) => ev.stopPropagation()));
  }

  function setNode(key, p) { state.overrides[key] = { yes: p, no: 1 - p }; recalc(); }

  function select(key, scroll) {
    state.selected = key;
    if (key) { const idx = D.sensitivity.judgments.findIndex((r) => r.key === key); if (idx >= 6) state.showAllDrivers = true; }
    driverRows();
    if (scroll && key) document.querySelector("tr.drv.sel")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // --- financial parameters, paths, stress -----------------------------------------------------
  function params() {
    const names = { collateral_share: "Surety collateral share", exposure: "Delaware exposure (share of the figure)", operating_variability: "Operating variability" };
    const rows = (D.sensitivity.parameters || []).map((p) => `<tr><td>${esc(names[p.parameter] || p.parameter)}</td>${p.variants.map((v) => `<td><b>${esc(v.label)}</b>: lowest cash ${money(v.min_cash)} (P5 ${money(v.min_cash_p5)}) · lender ${money(v.lender_pv, 1)}</td>`).join("")}</tr>`).join("");
    $("params").innerHTML = rows ? `<h3>Financial parameters</h3><p class="sub">Held apart from the evidence: the same analysis with each parameter at three settings (base setup).</p><table class="t"><tbody>${rows}</tbody></table>` : "";
  }

  function pathCells(r) {
    return D.disputes.map((d) => { const p = r.paths.find((x) => x.dispute === d.id); return `<td>${p ? esc(p.label) : ""}</td>`; }).join("");
  }
  function paths() {
    const rows = D.scenarios, lim = state.showAllPaths ? rows.length : 8;
    const hd = D.disputes.map((d) => `<th>${esc(shortTitle(d.id))}</th>`).join("");
    $("paths").innerHTML = `<h3>Event paths</h3><p class="sub">Joint paths through both disputes, with the probability Jev's conditional judgments give each (${rows.length} paths, all simulated).</p>
      <table class="t"><thead><tr><th>Probability</th>${hd}<th>Lowest cash (expected)</th><th>Lowest cash, P5</th><th>Lender, discounted</th><th>Uncollected at maturity</th></tr></thead><tbody>
      ${rows.slice(0, lim).map((r) => `<tr><td><b>${pct(r.probability, 1)}</b></td>${pathCells(r)}<td>${money(r.min_cash_mean_cents)}</td><td>${money(r.min_cash_p5_cents)}</td><td>${money(r.lender_pv_cents, 1)}</td><td>${money(r.uncollected_maturity_cents)}</td></tr>`).join("")}
      </tbody></table><span class="more" id="more-paths">${state.showAllPaths ? "Show fewer" : `Show all ${rows.length}`}</span>`;
    $("more-paths").onclick = () => { state.showAllPaths = !state.showAllPaths; paths(); };
  }
  function stress() {
    const rows = D.stress, lim = state.showAllStress ? rows.length : 6;
    const hd = D.disputes.map((d) => `<th>${esc(shortTitle(d.id))}</th>`).join("");
    $("stress").innerHTML = `<h3>Stress</h3><p class="sub">Every feasible path under adverse placement (payments and collateral early and high, receipts late and low), whatever its probability. Not weighted; shown beside, not inside, the distribution.</p>
      <table class="t"><thead><tr>${hd}<th>Probability</th><th>Lowest cash, P5</th><th>Lowest cash, median</th><th>Lender, discounted</th><th>Uncollected at maturity</th></tr></thead><tbody>
      ${rows.slice(0, lim).map((r) => `<tr>${pathCells(r)}<td>${pct(r.probability, 1)}</td><td><b>${money(r.min_cash_p5_cents)}</b></td><td>${money(r.min_cash_p50_cents)}</td><td>${money(r.lender_pv_cents, 1)}</td><td>${money(r.uncollected_maturity_cents)}</td></tr>`).join("")}
      </tbody></table><span class="more" id="more-stress">${state.showAllStress ? "Show fewer" : `Show all ${rows.length}`}</span>`;
    $("more-stress").onclick = () => { state.showAllStress = !state.showAllStress; stress(); };
  }

  // --- controls ---------------------------------------------------------------------------------
  function controls() {
    const c = { amount: BASE.amount_cents, fee: BASE.fee_bps, exposure: 1, clo: 50, chi: 100, variability: 1, ...state.ui };
    state.ui = c;
    const debtor = D.disputes.find((d) => d.borrower_role === "debtor");
    const sl = (id, label, min, max, step, val, fmt) => `<label>${label} <span class="val" id="${id}-v">${fmt(val)}</span><input type="range" id="${id}" min="${min}" max="${max}" step="${step}" value="${val}"></label>`;
    const nOver = Object.keys(state.overrides).length;
    $("controls").innerHTML = `<div class="row">
      ${sl("c-amount", "Financed amount", 0, BASE.invoice_cents, 5000000, c.amount, (v) => money(+v))}
      ${sl("c-fee", "Fee", 0, 1000, 10, c.fee, (v) => `${(v / 100).toFixed(1)}%`)}
      ${debtor ? sl("c-exposure", `${esc(debtor.short.split(" ·")[0])} exposure`, 0.25, 1.5, 0.05, c.exposure, (v) => `${money(debtor.amount_cents * v)}`) : ""}
      ${sl("c-clo", "Surety collateral, low", 0, 100, 5, c.clo, (v) => `${v}%`)}
      ${sl("c-chi", "Surety collateral, high", 0, 100, 5, c.chi, (v) => `${v}%`)}
      ${sl("c-var", "Operating variability", 0, 2, 0.1, c.variability, (v) => `${(+v).toFixed(1)}×`)}
      </div><div class="node"><span class="status" id="status">${nOver ? `${nOver} forecast${nOver > 1 ? "s" : ""} overridden` : "Jev's forecasts as judged"}</span>
      <button class="secondary" id="reset-all">Reset all</button></div>`;
    const bind = (id, key, fmt) => { const el = $(id); if (!el) return; el.oninput = () => { $(`${id}-v`).textContent = fmt(el.value); state.ui[key] = +el.value; recalc(); }; };
    bind("c-amount", "amount", (v) => money(+v)); bind("c-fee", "fee", (v) => `${(v / 100).toFixed(1)}%`);
    bind("c-exposure", "exposure", (v) => money(debtor.amount_cents * v)); bind("c-clo", "clo", (v) => `${v}%`);
    bind("c-chi", "chi", (v) => `${v}%`); bind("c-var", "variability", (v) => `${(+v).toFixed(1)}×`);
    $("reset-all").onclick = () => { state.ui = undefined; state.overrides = {}; controls(); recalc(true); };
  }

  let timer = null, seq = 0;
  function recalc(immediate) {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const u = state.ui, my = ++seq;
      const body = { overrides: state.overrides, controls: {
        amount_cents: u.amount, fee_bps: u.fee, exposure_scale: u.exposure,
        collateral_share: [Math.min(u.clo, u.chi) / 100, Math.max(u.clo, u.chi) / 100], variability: u.variability } };
      const st = $("status"); if (st) st.textContent = "Recalculating…";
      const res = await fetch(`/runs/${RUN}/analysis`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      if (my !== seq) return;
      if (!res.ok) { if (st) st.textContent = `Could not recalculate (${res.status})`; return; }
      D = await res.json();
      render(false);
    }, immediate ? 0 : 350);
  }

  function render(first) {
    header(); kpis(); findings(); charts(); driverRows(); params(); paths(); stress();
    if (first) controls();
    else { const st = $("status"), n = Object.keys(state.overrides).length; if (st) st.textContent = n ? `${n} forecast${n > 1 ? "s" : ""} overridden` : "Jev's forecasts as judged"; }
  }
  render(true);
  if (location.hash.startsWith("#driver=")) select(decodeURIComponent(location.hash.slice(8)), true);
})();

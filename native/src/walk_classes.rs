//! Deterministic verdict and ruling partitions, and their cash reach bounds.
//! Configuration, question metadata and input draws cross the Python boundary;
//! products, recursive verdict decisions and numerical reductions run here.
use super::walk::{Conjunctions, Edge};
use ndarray::{Array1, Array2};
use numpy::{IntoPyArray, PyReadonlyArray1, PyReadonlyArray2};
use pyo3::exceptions::{PyIndexError, PyKeyError, PyOverflowError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyModule, PyTuple};
use pyo3::IntoPyObjectExt;
use std::collections::{BTreeMap, BTreeSet};

const BIG: i64 = 1_000_000;
fn add(a: i64, b: i64) -> PyResult<i64> {
    a.checked_add(b)
        .ok_or_else(|| PyOverflowError::new_err("class amount exceeds int64"))
}
fn mul(a: i64, b: i64) -> PyResult<i64> {
    a.checked_mul(b)
        .ok_or_else(|| PyOverflowError::new_err("class amount exceeds int64"))
}
fn item_str(d: &Bound<'_, PyAny>, key: &str) -> PyResult<String> {
    d.get_item(key)?.extract()
}
fn optional_str(d: &Bound<'_, PyAny>, key: &str) -> PyResult<Option<String>> {
    d.call_method1("get", (key,))?.extract()
}
fn dict<'py>(v: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyDict>> {
    Ok(v.cast::<PyDict>()?.clone())
}
fn memo<'py>(fc: &Bound<'py, PyAny>, name: &str) -> PyResult<Bound<'py, PyDict>> {
    dict(
        &fc.getattr("__dict__")?
            .call_method1("setdefault", (name, PyDict::new(fc.py())))?,
    )
}
fn chain<'py>(
    py: Python<'py>,
    fc: &Bound<'py, PyAny>,
    d: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    py.import("app.analysis.events")?.getattr("Chain")?.call1((
        d,
        fc.getattr("setup")?,
        fc.getattr("m")?,
        fc.getattr("draws")?,
        fc.getattr("sens")?,
    ))
}
fn index(day: i64, n: usize) -> PyResult<usize> {
    let wrapped = if day < 0 { day + n as i64 } else { day };
    if wrapped < 0 || wrapped >= n as i64 {
        return Err(PyIndexError::new_err("class cash day outside horizon"));
    }
    Ok(wrapped as usize)
}
fn label(c: &str) -> String {
    let mut parts = c.split(':');
    let head = parts.next().unwrap_or("");
    format!(
        "{head}{}{}",
        if head == "amt" {
            parts.next().unwrap_or("")
        } else {
            ""
        },
        if c.ends_with(":retrial") {
            "_retrial"
        } else {
            ""
        }
    )
}

#[derive(Clone, Default)]
struct Leaf {
    atoms: Vec<Edge>,
    outcome: BTreeMap<String, String>,
}
fn survives(o: &BTreeMap<String, String>) -> bool {
    o.get("liability").is_none_or(|v| v != "granted")
}
fn money(o: &BTreeMap<String, String>) -> bool {
    survives(o)
        && (o.get("damages").is_none_or(|v| v == "stands")
            || o.get("remittitur").is_some_and(|v| v == "accept"))
}
fn split(
    leaves: &mut Vec<Leaf>,
    keys: &BTreeMap<String, String>,
    node: &str,
    field: &str,
    branches: &[&str],
    when: fn(&BTreeMap<String, String>) -> bool,
) {
    let Some(key) = keys.get(node) else {
        return;
    };
    let mut next = Vec::new();
    for leaf in leaves.drain(..) {
        if !when(&leaf.outcome) {
            next.push(leaf);
            continue;
        }
        for &answer in branches {
            let mut child = leaf.clone();
            child.atoms.push((key.clone(), answer.into()));
            child.outcome.insert(field.into(), answer.into());
            next.push(child);
        }
    }
    *leaves = next;
}
struct RulingGroup {
    amount: String,
    retrial: bool,
    parts: Conjunctions,
    members: Vec<(i64, i64)>,
    remitted: bool,
}
#[pyfunction]
fn walk_ruling_classes(
    py: Python<'_>,
    fc: &Bound<'_, PyAny>,
    d: &Bound<'_, PyAny>,
) -> PyResult<Py<PyAny>> {
    let keys: BTreeMap<String, String> = fc.call_method1("merits", (d,))?.extract()?;
    let mut leaves = vec![Leaf::default()];
    split(
        &mut leaves,
        &keys,
        "ts_liability_jmol",
        "liability",
        &["granted", "denied"],
        |_| true,
    );
    split(
        &mut leaves,
        &keys,
        "ts_damages_ruling",
        "damages",
        &["stands", "remit", "new_trial"],
        survives,
    );
    split(
        &mut leaves,
        &keys,
        "remittitur_accepted",
        "remittitur",
        &["accept", "new_trial"],
        |o| o.get("damages").is_some_and(|v| v == "remit"),
    );
    split(
        &mut leaves,
        &keys,
        "patent_jmol",
        "patent",
        &["granted", "denied"],
        |_| true,
    );
    for (node, field) in [
        ("trebling", "trebling"),
        ("fees_awarded", "fees"),
        ("prejudgment_interest", "interest"),
    ] {
        split(
            &mut leaves,
            &keys,
            node,
            field,
            &["granted", "denied"],
            money,
        );
    }
    let model = fc.getattr("m")?;
    let lower = model
        .get_item("parameters")?
        .get_item("bond_collateral_share_bps")?
        .get_item("lower")?
        .extract::<f64>()?
        / 10_000.;
    let reach: Option<i64> = fc.getattr("reach")?.extract()?;
    let events = py.import("app.analysis.events")?;
    let entered: i64 = events.getattr("entered_cents")?.call1((d,))?.extract()?;
    let amount_fn = events.getattr("ruling_amounts")?;
    let mut groups: Vec<RulingGroup> = Vec::new();
    for leaf in leaves {
        let o = PyDict::new(py);
        for (key, answer) in &leaf.outcome {
            o.set_item(key, answer)?;
        }
        let amounts = amount_fn.call1((d, o, &model))?.cast_into::<PyDict>()?;
        let mut total = 0;
        for (_, value) in amounts.iter() {
            total = add(total, value.extract()?)?;
        }
        let fees: i64 = amounts
            .get_item("fees")?
            .ok_or_else(|| PyKeyError::new_err("fees"))?
            .extract()?;
        let retrial = ["damages", "remittitur"]
            .iter()
            .any(|key| leaf.outcome.get(*key).is_some_and(|v| v == "new_trial"));
        let amount = if total == 0 {
            "none".into()
        } else if reach.is_some_and(|r| total as f64 * lower > r as f64) {
            if total > entered {
                "beyond_up".into()
            } else {
                "beyond".into()
            }
        } else {
            format!("amt:{total}:{fees}")
        };
        let remitted = leaf
            .outcome
            .get("remittitur")
            .is_some_and(|v| v == "accept");
        if let Some(g) = groups
            .iter_mut()
            .find(|g| g.amount == amount && g.retrial == retrial)
        {
            g.parts.push(leaf.atoms);
            g.members.push((total, fees));
            g.remitted &= remitted;
        } else {
            groups.push(RulingGroup {
                amount,
                retrial,
                parts: vec![leaf.atoms],
                members: vec![(total, fees)],
                remitted,
            });
        }
    }
    let out = PyDict::new(py);
    let members = fc.getattr("class_members")?;
    let ranges = fc.getattr("class_range")?;
    let remits = fc.getattr("remit_classes")?;
    for g in groups {
        let lo = g
            .members
            .iter()
            .min()
            .copied()
            .ok_or_else(|| PyValueError::new_err("empty ruling class"))?;
        let hi = g
            .members
            .iter()
            .max()
            .copied()
            .ok_or_else(|| PyValueError::new_err("empty ruling class"))?;
        let mut name = if g.amount.starts_with("beyond") {
            format!("{}:{}:{}", g.amount, lo.0, lo.1)
        } else {
            g.amount.clone()
        };
        if g.retrial {
            name = if name == "none" {
                "retrial".into()
            } else {
                format!("{name}:retrial")
            };
        }
        out.set_item(&name, g.parts)?;
        members.set_item(&name, g.members)?;
        if g.remitted {
            remits.call_method1("add", (label(&name),))?;
        }
        if g.amount.starts_with("beyond") {
            ranges.set_item(label(&name), (lo.0, hi.0))?;
        }
    }
    Ok(out.into_any().unbind())
}

#[pyfunction]
#[pyo3(signature=(fc,d,inflows=None))]
fn walk_verdict_lines(
    py: Python<'_>,
    fc: &Bound<'_, PyAny>,
    d: &Bound<'_, PyAny>,
    inflows: Option<&Bound<'_, PyAny>>,
) -> PyResult<Py<PyAny>> {
    let cache = memo(fc, "_lines")?;
    let iid = d.getattr("instance_id")?;
    let hash = if let Some(eq) = inflows {
        py.import("builtins")?
            .getattr("hash")?
            .call1((py
                .import("numpy")?
                .call_method1("asarray", (eq,))?
                .call_method0("tobytes")?,))?
            .unbind()
    } else {
        py.None()
    };
    let key = PyTuple::new(py, [iid.unbind(), hash])?;
    if let Some(value) = cache.get_item(&key)? {
        return Ok(value.unbind());
    }
    let ch = chain(py, fc, d)?;
    ch.call_method0("instrument_cash")?;
    let model = fc.getattr("m")?;
    let events = py.import("app.analysis.events")?;
    let branches = events
        .getattr("pending_template")?
        .call1((&model,))?
        .get_item("verdict_branches")?
        .cast_into::<PyDict>()?;
    let claimant = branches
        .iter()
        .find_map(|(k, v)| match v.call_method1("get", ("adverse",)) {
            Ok(x) => match x.is_truthy() {
                Ok(true) => Some(Ok(k)),
                Ok(false) => None,
                Err(e) => Some(Err(e)),
            },
            Err(e) => Some(Err(e)),
        })
        .transpose()?
        .ok_or_else(|| PyValueError::new_err("verdict has no adverse branch"))?;
    let tr = events.getattr("Trace")?.call1((ch.getattr("ev")?,))?;
    ch.call_method1("advance", (tr, "verdict", "I0", &claimant))?;
    let cum_obj = ch.call_method0("cum")?;
    let cum: PyReadonlyArray2<'_, i64> = cum_obj.extract()?;
    let e_obj = ch.getattr("E_ix")?;
    let e: PyReadonlyArray1<'_, i64> = e_obj.extract()?;
    let basis = ch.getattr("basis")?;
    let inflow_obj = basis.getattr("inflow")?;
    let need_obj = basis.getattr("need")?;
    let op: PyReadonlyArray2<'_, i64> = inflow_obj.extract()?;
    let need: PyReadonlyArray2<'_, i64> = need_obj.extract()?;
    let eq_obj = inflows
        .map(|a| {
            py.import("numpy")?
                .call_method1("asarray", (a, py.import("numpy")?.getattr("int64")?))
        })
        .transpose()?;
    let eq: Option<PyReadonlyArray2<'_, i64>> = eq_obj.as_ref().map(|a| a.extract()).transpose()?;
    let cum = cum.as_array();
    let op = op.as_array();
    let need = need.as_array();
    let e = e.as_array();
    let (n, days) = cum.dim();
    super::events::validate_event_shape(n, days)?;
    if n == 0
        || days == 0
        || e.len() != n
        || op.dim() != (n, days)
        || need.dim() != (n, days)
        || eq.as_ref().is_some_and(|v| v.as_array().dim() != (n, days))
    {
        return Err(PyValueError::new_err(
            "verdict line shapes differ or are empty",
        ));
    }
    let opening: i64 = basis.getattr("opening")?.extract()?;
    let years: f64 = ch
        .call_method1("p", ("bond_forward_interest_years",))?
        .extract()?;
    let setup = fc.getattr("setup")?;
    let share_range: Vec<f64> = setup
        .getattr("collateral_share")?
        .extract::<Option<Vec<f64>>>()?
        .unwrap_or_default();
    let share = if let Some(&s) = share_range.first() {
        s
    } else {
        let sensitivity = fc
            .getattr("sens")?
            .call_method1("get", ("bond_collateral_share_bps",))?
            .is_truthy()?;
        model
            .get_item("parameters")?
            .get_item("bond_collateral_share_bps")?
            .get_item(if sensitivity { "lower" } else { "value" })?
            .extract::<f64>()?
            / 10_000.
    };
    let bps: f64 = ch.getattr("bps")?.extract()?;
    let denominator = share * (1. + bps / 10_000. * years);
    let mut reach = i64::MIN;
    let mut balance_max = i64::MIN;
    let mut end_max = i64::MIN;
    let mut covers_max = f64::NEG_INFINITY;
    for r in 0..n {
        let at = index(e[r].min(days as i64 - 1), days)?;
        reach =
            reach.max(cum[[r, at]].wrapping_add(eq.as_ref().map_or(0, |a| a.as_array()[[r, at]])));
        for day in 0..days {
            let extra = eq.as_ref().map_or(0, |a| a.as_array()[[r, day]]);
            let end = cum[[r, day]].wrapping_add(extra);
            let prev = if day == 0 { opening } else { cum[[r, day - 1]] };
            let balance = prev.wrapping_add(op[[r, day]]).wrapping_add(extra);
            let after = day as i64 >= e[r] && e[r] < days as i64;
            balance_max = balance_max.max(if after { balance } else { 0 });
            end_max = end_max.max(if after { end } else { 0 });
            let cover = if after {
                end.wrapping_sub(need[[r, day]]) as f64 / denominator
            } else {
                0.
            };
            covers_max = if covers_max.is_nan() || cover.is_nan() {
                f64::NAN
            } else {
                covers_max.max(cover)
            };
        }
    }
    let cover = covers_max.ceil();
    if !cover.is_finite() || cover < i64::MIN as f64 || cover >= -(i64::MIN as f64) {
        return Err(PyValueError::new_err(
            "verdict collateral bound cannot be represented as int64",
        ));
    }
    let most = balance_max.max(end_max).max(cover as i64);
    let fin = ch.getattr("fin")?;
    let thr = if fin.is_none() {
        0
    } else {
        let t: i64 = fin.getattr("judgment_default_threshold_cents")?.extract()?;
        if t != 0 {
            add(t, fin.getattr("insured_cents")?.extract()?)?
        } else {
            0
        }
    };
    let up = |x: i64| -> PyResult<i64> {
        mul(x.div_euclid(100) + i64::from(x.rem_euclid(100) != 0), 100)
    };
    let reach = up(reach)?;
    let top = up(most.max(thr))?;
    let mut cuts: BTreeSet<i64> = [
        0,
        up(reach.div_euclid(2))?,
        reach,
        up(add(reach, top)?.div_euclid(2))?,
        top,
    ]
    .into_iter()
    .collect();
    let ordered: Vec<i64> = cuts.iter().copied().collect();
    if thr != 0 && !cuts.contains(&thr) && ordered.windows(2).any(|w| w[0] < thr && thr < w[1]) {
        cuts.insert(thr);
    }
    let top_amount: i64 = events
        .getattr("verdict_amount")?
        .call1((d, &model, claimant, fc.getattr("sens")?))?
        .extract()?;
    if top_amount <= top {
        return Err(PyValueError::new_err(format!(
            "the claimant's amount {top_amount} is not above the J1b top line {top}"
        )));
    }
    let cuts: Vec<i64> = cuts.into_iter().collect();
    let mut bands = vec![(0, 0, 0)];
    for w in cuts.windows(2) {
        bands.push((w[0], w[1], add(w[0], w[1])?.div_euclid(2)));
    }
    bands.push((top, mul(BIG, 1_000_000)?, top_amount));
    let out = PyDict::new(py);
    out.set_item("reach", reach)?;
    out.set_item("top", top)?;
    out.set_item("threshold", thr)?;
    out.set_item("cuts", cuts)?;
    out.set_item("bands", bands)?;
    out.set_item("top_amount", top_amount)?;
    cache.set_item(key, &out)?;
    Ok(out.into_any().unbind())
}

#[pyfunction]
fn walk_equity_inflows(
    py: Python<'_>,
    fc: &Bound<'_, PyAny>,
    d: &Bound<'_, PyAny>,
) -> PyResult<Py<PyAny>> {
    if !fc
        .getattr("m")?
        .get_item("parameters")?
        .call_method1("get", ("share_ledger", PyDict::new(py)))?
        .contains("value")?
    {
        return Ok(py.None());
    }
    let cache = memo(fc, "_inflows")?;
    let iid = d.getattr("instance_id")?;
    if let Some(v) = cache.get_item(&iid)? {
        return Ok(v.unbind());
    }
    let ch = chain(py, fc, d)?;
    ch.call_method0("instrument_cash")?;
    let n: usize = ch.getattr("n")?.extract()?;
    let days: usize = ch.getattr("N")?.extract()?;
    super::events::validate_event_shape(n, days)?;
    let mut day = Array1::<i64>::zeros(n).into_pyarray(py);
    let mut k = 0usize;
    loop {
        let occasion = format!("top_line_{k}");
        let started = ch.call_method1("initiate", (&day, &occasion))?;
        let started: PyReadonlyArray1<'_, bool> = started.extract()?;
        if started.as_array().len() != n {
            return Err(PyValueError::new_err("offering initiation shape differs"));
        }
        if !started.as_array().iter().any(|&on| on) {
            break;
        }
        ch.call_method1("offering_outcome", (&occasion, true))?;
        let offer = ch.getattr("offerings")?.get_item(-1)?;
        let close_obj = offer.get_item(1)?;
        let active_obj = offer.get_item(2)?;
        let close: PyReadonlyArray1<'_, i64> = close_obj.extract()?;
        let active: PyReadonlyArray1<'_, bool> = active_obj.extract()?;
        let close = close.as_array();
        let active = active.as_array();
        if close.len() != n || active.len() != n {
            return Err(PyValueError::new_err("offering close shape differs"));
        }
        day = Array1::from_iter((0..n).map(|r| if active[r] { close[r] } else { BIG }))
            .into_pyarray(py);
        k = k
            .checked_add(1)
            .ok_or_else(|| PyOverflowError::new_err("too many offering occasions"))?;
    }
    let mut per = Array2::<i64>::zeros((n, days));
    for offer in ch.getattr("_offers")?.try_iter()? {
        let offer = offer?;
        let closed_obj = offer.get_item("closed")?;
        let close_obj = offer.get_item("close")?;
        let net_obj = offer.get_item("net")?;
        let closed: PyReadonlyArray1<'_, bool> = closed_obj.extract()?;
        let close: PyReadonlyArray1<'_, i64> = close_obj.extract()?;
        let net: PyReadonlyArray1<'_, i64> = net_obj.extract()?;
        let closed = closed.as_array();
        let close = close.as_array();
        let net = net.as_array();
        if closed.len() != n || close.len() != n || net.len() != n {
            return Err(PyValueError::new_err("offering proceeds shape differs"));
        }
        for r in 0..n {
            if closed[r] {
                let at = index(close[r], days)?;
                per[[r, at]] = per[[r, at]].wrapping_add(net[r]);
            }
        }
    }
    for mut row in per.rows_mut() {
        let mut total = 0i64;
        for value in &mut row {
            total = total.wrapping_add(*value);
            *value = total;
        }
    }
    let out = per.into_pyarray(py);
    cache.set_item(iid, &out)?;
    Ok(out.into_any().unbind())
}

struct Question {
    node: String,
    yes: Option<String>,
    no: Option<String>,
    next: Option<String>,
    zero: Option<String>,
    award: Option<String>,
    exemplary: Option<String>,
    of: Option<String>,
    times: i64,
}
struct Verdict<'py> {
    fc: Bound<'py, PyAny>,
    d: Bound<'py, PyAny>,
    questions: BTreeMap<String, Question>,
    cuts: Vec<i64>,
    top: i64,
    top_amount: i64,
    components: BTreeMap<String, i64>,
    small: bool,
    asks: Bound<'py, PyDict>,
    classes: Vec<(String, Conjunctions)>,
}
impl Verdict<'_> {
    fn finish(&mut self, conj: Vec<Edge>, amt: i64, fixed: i64) -> PyResult<()> {
        let total = add(amt, fixed)?;
        let name = if amt == -1 || total > self.top {
            format!("award:{}:{}:top", self.top_amount, self.top)
        } else if amt == 0 {
            if fixed == 0 {
                "no_award".into()
            } else {
                format!("award:{fixed}:{fixed}:{fixed}")
            }
        } else {
            let (lo, hi) = if total <= 0 {
                (0, 0)
            } else {
                self.cuts
                    .windows(2)
                    .find(|w| w[0] < total && total <= w[1])
                    .map(|w| (w[0], w[1]))
                    .ok_or_else(|| PyValueError::new_err("verdict total outside cuts"))?
            };
            format!("award:{}:{lo}:{hi}", add(lo, hi)?.div_euclid(2))
        };
        if let Some((_, parts)) = self.classes.iter_mut().find(|(label, _)| label == &name) {
            parts.push(conj);
        } else {
            self.classes.push((name, vec![conj]));
        }
        Ok(())
    }
    fn node(
        &self,
        py: Python<'_>,
        node: &str,
        q: &str,
        extra: Option<String>,
        trail: &[String],
    ) -> PyResult<String> {
        let mut args = vec![
            self.d.clone().unbind(),
            node.into_py_any(py)?,
            q.into_py_any(py)?,
        ];
        if let Some(x) = extra {
            args.push(x.into_py_any(py)?);
        }
        for t in trail {
            args.push(t.into_py_any(py)?);
        }
        self.fc
            .getattr("node")?
            .call1(PyTuple::new(py, args)?)?
            .extract()
    }
    fn walk(
        &mut self,
        py: Python<'_>,
        q: &str,
        trail: Vec<String>,
        conj: Vec<Edge>,
        amt: i64,
        fixed: i64,
        hi: BTreeMap<String, i64>,
        depth: usize,
    ) -> PyResult<()> {
        if q == "end" || amt == -1 {
            return self.finish(conj, amt, fixed);
        }
        if depth > 1000 {
            return Err(PyValueError::new_err(
                "verdict form recursion exceeds 1000 questions",
            ));
        }
        let spec = self
            .questions
            .get(q)
            .ok_or_else(|| PyKeyError::new_err(q.to_owned()))?;
        if spec.node == "verdict_finding" {
            if let Some(of) = &spec.exemplary {
                let cap = mul(spec.times, hi.get(of).copied().unwrap_or(0))?;
                let end = add(amt, cap)?;
                if !self.cuts.iter().any(|&c| amt < c && c < end) {
                    let no = spec.no.clone().ok_or_else(|| PyKeyError::new_err("no"))?;
                    return self.walk(py, &no, trail, conj, amt, fixed, hi, depth + 1);
                }
            }
            let node = self.node(py, "verdict_finding", q, None, &trail)?;
            let yes = spec.yes.clone().ok_or_else(|| PyKeyError::new_err("yes"))?;
            let no = spec.no.clone().ok_or_else(|| PyKeyError::new_err("no"))?;
            let award = spec
                .award
                .as_ref()
                .and_then(|a| self.components.get(a))
                .copied()
                .unwrap_or(0);
            for (answer, next) in [("yes", yes), ("no", no)] {
                let mut t = trail.clone();
                t.push(format!("{q}={answer}"));
                let mut c = conj.clone();
                c.push((node.clone(), answer.into()));
                self.walk(
                    py,
                    &next,
                    t,
                    c,
                    amt,
                    add(
                        fixed,
                        if answer == "yes" && self.small {
                            award
                        } else {
                            0
                        },
                    )?,
                    hi.clone(),
                    depth + 1,
                )?;
            }
            return Ok(());
        }
        let next = spec
            .next
            .clone()
            .ok_or_else(|| PyKeyError::new_err("next"))?;
        let zero = spec.zero.clone().unwrap_or_else(|| next.clone());
        let limit = spec
            .of
            .as_ref()
            .map(|of| {
                hi.get(of)
                    .copied()
                    .ok_or_else(|| PyKeyError::new_err(of.clone()))
                    .and_then(|h| add(amt, mul(spec.times, h)?))
            })
            .transpose()?;
        let above: Vec<i64> = self
            .cuts
            .iter()
            .copied()
            .filter(|&c| (c > amt || c == amt && amt == 0) && limit.is_none_or(|l| c < l))
            .collect();
        if above.is_empty() {
            return self.walk(py, &next, trail, conj, amt, fixed, hi, depth + 1);
        }
        let mut t = trail;
        let mut cj = conj;
        for (i, &cut) in above.iter().enumerate() {
            let x = cut
                .checked_sub(amt)
                .ok_or_else(|| PyOverflowError::new_err("verdict threshold exceeds int64"))?;
            let node = self.node(py, "verdict_amount", q, Some(format!("X={x}")), &t)?;
            let ask = PyDict::new(py);
            ask.set_item("item", q)?;
            ask.set_item("threshold", x)?;
            ask.set_item("established", amt)?;
            ask.set_item("line", cut)?;
            self.asks.set_item(&node, ask)?;
            let mut child_t = t.clone();
            child_t.push(format!("{q}>{x}=no"));
            let mut child_c = cj.clone();
            child_c.push((node.clone(), "no".into()));
            let mut child_hi = hi.clone();
            child_hi.insert(q.into(), x);
            let amount = if i == 0 {
                amt
            } else {
                add(above[i - 1], cut)?.div_euclid(2)
            };
            let child_next = if i == 0 && cut == amt && amt == 0 {
                &zero
            } else {
                &next
            };
            self.walk(
                py,
                child_next,
                child_t,
                child_c,
                amount,
                fixed,
                child_hi,
                depth + 1,
            )?;
            t.push(format!("{q}>{x}=yes"));
            cj.push((node, "yes".into()));
        }
        let last = *above
            .last()
            .ok_or_else(|| PyValueError::new_err("empty verdict cuts"))?;
        if last == self.top {
            return self.walk(py, &next, t, cj, -1, fixed, hi, depth + 1);
        }
        let nb = self
            .cuts
            .iter()
            .find(|&&c| c > last)
            .copied()
            .ok_or_else(|| PyValueError::new_err("verdict cut has no next band"))?;
        let limit = limit
            .ok_or_else(|| PyValueError::new_err("verdict capped band has no amount limit"))?;
        let mut hi = hi;
        hi.insert(
            q.into(),
            limit
                .checked_sub(amt)
                .ok_or_else(|| PyOverflowError::new_err("verdict limit exceeds int64"))?,
        );
        self.walk(
            py,
            &next,
            t,
            cj,
            add(last, nb)?.div_euclid(2),
            fixed,
            hi,
            depth + 1,
        )
    }
}
#[pyfunction]
fn walk_verdict_classes(
    py: Python<'_>,
    fc: &Bound<'_, PyAny>,
    d: &Bound<'_, PyAny>,
) -> PyResult<Py<PyAny>> {
    let model = fc.getattr("m")?;
    let form = model.get_item("case_verdict_form")?;
    let inflows = fc.call_method1("equity_inflows", (d,))?;
    let lines = fc.call_method1("verdict_lines", (d, inflows))?;
    let mut questions = BTreeMap::new();
    for (key, value) in form.get_item("questions")?.cast::<PyDict>()?.iter() {
        let node = item_str(&value, "node")?;
        let times = value.call_method1("get", ("times", 2))?.extract()?;
        questions.insert(
            key.extract::<String>()?,
            Question {
                node,
                times,
                yes: optional_str(&value, "yes")?,
                no: optional_str(&value, "no")?,
                next: optional_str(&value, "next")?,
                zero: optional_str(&value, "zero")?,
                award: optional_str(&value, "award")?,
                exemplary: optional_str(&value, "exemplary_of")?,
                of: optional_str(&value, "of")?,
            },
        );
    }
    let mut components = BTreeMap::new();
    for c in d.getattr("components")?.try_iter()? {
        let c = c?;
        components.insert(
            c.getattr("component_id")?.extract()?,
            c.getattr("amount_cents")?
                .extract::<Option<i64>>()?
                .unwrap_or(0),
        );
    }
    let params = model.get_item("parameters")?;
    let small = if params.contains("small_claims_awarded")? {
        py.import("app.analysis.events")?
            .getattr("pval")?
            .call1((
                &model,
                "small_claims_awarded",
                fc.getattr("sens")?
                    .call_method1("get", ("small_claims_awarded", false))?,
            ))?
            .is_truthy()?
    } else {
        true
    };
    let asks = if fc.hasattr("verdict_asks")? {
        dict(&fc.getattr("verdict_asks")?)?
    } else {
        let a = PyDict::new(py);
        fc.setattr("verdict_asks", &a)?;
        a
    };
    let mut v = Verdict {
        fc: fc.clone(),
        d: d.clone(),
        questions,
        cuts: lines.get_item("cuts")?.extract()?,
        top: lines.get_item("top")?.extract()?,
        top_amount: lines.get_item("top_amount")?.extract()?,
        components,
        small,
        asks,
        classes: Vec::new(),
    };
    v.walk(
        py,
        &item_str(&form, "start")?,
        Vec::new(),
        Vec::new(),
        0,
        0,
        BTreeMap::new(),
        0,
    )?;
    let out = PyDict::new(py);
    for (name, parts) in v.classes {
        out.set_item(name, parts)?;
    }
    Ok(out.into_any().unbind())
}
pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(walk_ruling_classes, m)?)?;
    m.add_function(wrap_pyfunction!(walk_verdict_lines, m)?)?;
    m.add_function(wrap_pyfunction!(walk_equity_inflows, m)?)?;
    m.add_function(wrap_pyfunction!(walk_verdict_classes, m)?)?;
    Ok(())
}

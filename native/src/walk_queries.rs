//! Native trajectory-mask and question-situation operations for the walk façade.
use super::walk::Step;
use numpy::{IntoPyArray, PyReadonlyArray1, PyReadonlyArrayDyn};
use pyo3::basic::CompareOp;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyModule, PyTuple};
use pyo3::IntoPyObjectExt;
use std::collections::BTreeSet;
const BIG: i64 = 1_000_000;

fn reach_max<T>(py: Python<'_>, cash: PyReadonlyArrayDyn<'_, T>) -> PyResult<Py<PyAny>>
where
    T: numpy::Element + Copy + PartialOrd,
    for<'a> T: IntoPyObject<'a>,
{
    let view = cash.as_array();
    let mut values = view.iter().copied();
    let first = values.next().ok_or_else(|| {
        PyValueError::new_err(
            "zero-size array to reduction operation maximum which has no identity",
        )
    })?;
    let maximum = values.fold(first, |a, b| {
        // NumPy maximum propagates NaN. Conversion to int below retains the
        // reference's ValueError for NaN and OverflowError for infinities.
        if a.partial_cmp(&a).is_none() || b <= a {
            a
        } else {
            b
        }
    });
    Ok(maximum
        .into_py_any(py)?
        .bind(py)
        .call_method0("__int__")?
        .unbind())
}

#[pyfunction]
fn walk_reach(py: Python<'_>, cash: &Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
    macro_rules! numeric {
        ($($kind:ty),+ $(,)?) => {
            $(if let Ok(array) = cash.extract::<PyReadonlyArrayDyn<'_, $kind>>() {
                return reach_max(py, array);
            })+
        };
    }
    numeric!(i64, i32, i16, i8, u64, u32, u16, u8, bool, f64, f32);
    if cash.is_instance(&py.import("numpy")?.getattr("ndarray")?)?
        && cash
            .getattr("dtype")?
            .getattr("kind")?
            .extract::<String>()?
            == "f"
    {
        // Float16 and platform longdouble have no Rust NumPy Element binding.
        // Keep their scalar comparison/conversion protocol rather than casting
        // away precision; the maximum reduction's control loop remains native.
        let flat = cash.getattr("flat")?;
        let mut values = flat.try_iter()?;
        let mut maximum = values.next().transpose()?.ok_or_else(|| {
            PyValueError::new_err(
                "zero-size array to reduction operation maximum which has no identity",
            )
        })?;
        for value in values {
            let value = value?;
            if maximum.rich_compare(&maximum, CompareOp::Ne)?.is_truthy()? {
                continue;
            }
            if value.rich_compare(&value, CompareOp::Ne)?.is_truthy()?
                || value.rich_compare(&maximum, CompareOp::Gt)?.is_truthy()?
            {
                maximum = value;
            }
        }
        return Ok(maximum.call_method0("__int__")?.unbind());
    }
    Err(PyValueError::new_err(
        "reach cash must be a numeric NumPy array",
    ))
}

/// Borrow compact date columns without widening or copying their buffers.
enum Dates<'py> {
    I32(PyReadonlyArray1<'py, i32>),
    I64(PyReadonlyArray1<'py, i64>),
    Bool(PyReadonlyArray1<'py, bool>),
}
impl<'py> Dates<'py> {
    fn borrow(v: &Bound<'py, PyAny>) -> PyResult<Self> {
        if let Ok(a) = v.extract::<PyReadonlyArray1<'py, i32>>() {
            return Ok(Self::I32(a));
        }
        if let Ok(a) = v.extract::<PyReadonlyArray1<'py, i64>>() {
            return Ok(Self::I64(a));
        }
        if let Ok(a) = v.extract::<PyReadonlyArray1<'py, bool>>() {
            return Ok(Self::Bool(a));
        }
        Err(PyValueError::new_err(
            "question dates must be int32, int64 or bool arrays",
        ))
    }
    fn len(&self) -> usize {
        match self {
            Self::I32(a) => a.as_array().len(),
            Self::I64(a) => a.as_array().len(),
            Self::Bool(a) => a.as_array().len(),
        }
    }
    fn at(&self, i: usize) -> i64 {
        match self {
            Self::I32(a) => i64::from(a.as_array()[i]),
            Self::I64(a) => a.as_array()[i],
            Self::Bool(a) => i64::from(a.as_array()[i]),
        }
    }
}

fn steps_py(py: Python<'_>, steps: &[Step]) -> PyResult<Py<PyAny>> {
    Ok(PyTuple::new(
        py,
        steps
            .iter()
            .map(|s| s.clone().into_py_any(py))
            .collect::<PyResult<Vec<_>>>()?,
    )?
    .into_any()
    .unbind())
}
fn last<'py>(tr: &Bound<'py, PyAny>) -> PyResult<PyReadonlyArray1<'py, i64>> {
    Ok(tr.getattr("day")?.get_item(-1)?.extract()?)
}

#[pyfunction]
fn walk_rows(py: Python<'_>, w: &Bound<'_, PyAny>, steps: Vec<Step>) -> PyResult<Py<PyAny>> {
    if py
        .import("app.disputes.forecast")?
        .getattr("ROWS_OFF")?
        .extract::<bool>()?
    {
        return Ok(py.None());
    }
    let mut out = Vec::with_capacity(steps.len());
    let mut m = py.None();
    for (i, st) in steps.iter().enumerate() {
        if st.2.starts_with('@') {
            m = walk_mask(py, w, steps[..=i].to_vec())?;
        }
        out.push(m.clone_ref(py));
    }
    Ok(PyTuple::new(py, out)?.into_any().unbind())
}
#[pyfunction]
fn walk_groups(py: Python<'_>, w: &Bound<'_, PyAny>, steps: Vec<Step>) -> PyResult<Py<PyAny>> {
    let tr = w.call_method1("_raw", (steps_py(py, &steps)?,))?;
    let pet: PyReadonlyArray1<'_, i64> = tr.getattr("petition")?.extract()?;
    let groups = tr.getattr("groups")?;
    if groups.is_none() {
        return Ok(vec![-1_i8; pet.as_array().len()]
            .into_pyarray(py)
            .into_any()
            .unbind());
    }
    let groups: PyReadonlyArray1<'_, i8> = groups.extract()?;
    let t = last(&tr)?;
    let t = t.as_array();
    let pet = pet.as_array();
    let groups = groups.as_array();
    if t.len() != pet.len() || t.len() != groups.len() {
        return Err(PyValueError::new_err("walk group shapes differ"));
    }
    let horizon: i64 = w.getattr("N")?.extract()?;
    let out: Vec<i8> = t
        .iter()
        .zip(pet)
        .zip(groups)
        .map(|((&t, &p), &g)| {
            if t < horizon && t < if p < 0 { i64::MAX } else { p } && g >= 0 {
                g
            } else {
                -1
            }
        })
        .collect();
    Ok(out.into_pyarray(py).into_any().unbind())
}
#[pyfunction]
fn walk_mask(py: Python<'_>, w: &Bound<'_, PyAny>, steps: Vec<Step>) -> PyResult<Py<PyAny>> {
    let Some(j) = steps.iter().rposition(|s| s.2.starts_with('@')) else {
        return Ok(py.None());
    };
    let key = steps_py(py, &steps[..=j])?;
    let cache = w.getattr("_masks")?;
    let found = cache.call_method1("get", (py.None(), key.bind(py)))?;
    if !found.is_none() {
        return Ok(found.unbind());
    }
    let (node, ctx, branch) = &steps[j];
    let quiet: String = w.call_method1("quiet_of", (node,))?.extract()?;
    let mut probe = steps[..j].to_vec();
    probe.push((node.clone(), ctx.clone(), quiet));
    let group_ids = branch[1..]
        .split_once('=')
        .ok_or_else(|| PyValueError::new_err("grouped step has no answer"))?
        .0;
    let selected: BTreeSet<i8> = group_ids
        .split(',')
        .map(|g| {
            g.parse::<i8>()
                .map_err(|_| PyValueError::new_err("invalid option group"))
        })
        .collect::<PyResult<_>>()?;
    let g = walk_groups(py, w, probe)?;
    let groups: PyReadonlyArray1<'_, i8> = g.bind(py).extract()?;
    let parent = walk_mask(py, w, steps[..j].to_vec())?;
    let parent: Option<PyReadonlyArray1<'_, bool>> = if parent.is_none(py) {
        None
    } else {
        Some(parent.bind(py).extract()?)
    };
    if parent
        .as_ref()
        .is_some_and(|p| p.as_array().len() != groups.as_array().len())
    {
        return Err(PyValueError::new_err("parent mask differs from groups"));
    }
    let groups = groups.as_array();
    let out: Vec<bool> = groups
        .iter()
        .enumerate()
        .map(|(i, g)| selected.contains(g) && parent.as_ref().is_none_or(|p| p.as_array()[i]))
        .collect();
    let m = out.into_pyarray(py);
    Ok(cache.call_method1("put", (py.None(), key, m))?.unbind())
}
#[pyfunction]
fn walk_group_codes(py: Python<'_>, w: &Bound<'_, PyAny>, steps: Vec<Step>) -> PyResult<Py<PyAny>> {
    let g = walk_groups(py, w, steps.clone())?;
    let mask = walk_mask(py, w, steps)?;
    if mask.is_none(py) {
        return Ok(g);
    }
    let groups: PyReadonlyArray1<'_, i8> = g.bind(py).extract()?;
    let mask: PyReadonlyArray1<'_, bool> = mask.bind(py).extract()?;
    let groups = groups.as_array();
    let mask = mask.as_array();
    if groups.len() != mask.len() {
        return Err(PyValueError::new_err("group mask shape differs"));
    }
    let out: Vec<i8> = groups
        .iter()
        .zip(mask)
        .map(|(&g, &on)| if on { g } else { -1 })
        .collect();
    Ok(out.into_pyarray(py).into_any().unbind())
}
#[pyfunction]
fn walk_option_groups(
    py: Python<'_>,
    w: &Bound<'_, PyAny>,
    steps: Vec<Step>,
) -> PyResult<Vec<i64>> {
    let g = walk_groups(py, w, steps.clone())?;
    let mask = walk_mask(py, w, steps)?;
    let groups: PyReadonlyArray1<'_, i8> = g.bind(py).extract()?;
    let mask: Option<PyReadonlyArray1<'_, bool>> = if mask.is_none(py) {
        None
    } else {
        Some(mask.bind(py).extract()?)
    };
    if mask
        .as_ref()
        .is_some_and(|m| m.as_array().len() != groups.as_array().len())
    {
        return Err(PyValueError::new_err("option mask shape differs"));
    }
    Ok(groups
        .as_array()
        .iter()
        .enumerate()
        .filter_map(|(i, &g)| {
            mask.as_ref()
                .is_none_or(|m| m.as_array()[i])
                .then_some(i64::from(g))
        })
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect())
}
#[pyfunction]
fn walk_tags(
    _py: Python<'_>,
    w: &Bound<'_, PyAny>,
    s: &Bound<'_, PyAny>,
    conds: Vec<String>,
    ctx: Vec<String>,
    tr: &Bound<'_, PyAny>,
    _at: &Bound<'_, PyAny>,
) -> PyResult<Vec<String>> {
    let t = last(tr)?;
    let pet: PyReadonlyArray1<'_, i64> = tr.getattr("petition")?.extract()?;
    let t = t.as_array();
    let pet = pet.as_array();
    let horizon: i64 = w.getattr("N")?.extract()?;
    if t.len() != pet.len() {
        return Err(PyValueError::new_err(
            "question date/petition shapes differ",
        ));
    }
    let inside: Vec<usize> = t
        .iter()
        .zip(pet)
        .enumerate()
        .filter_map(|(i, (&t, &p))| {
            (t < horizon && t < if p < 0 { i64::MAX } else { p }).then_some(i)
        })
        .collect();
    let mut held = BTreeSet::new();
    let mut never: BTreeSet<String> = conds.iter().cloned().collect();
    let marks = tr.getattr("marks")?;
    if !inside.is_empty() && !marks.is_none() {
        for c in &conds {
            let m = Dates::borrow(&marks.get_item(c)?)?;
            if m.len() != t.len() {
                return Err(PyValueError::new_err("question mark shape differs"));
            }
            let mut all = true;
            let mut any = false;
            for &i in &inside {
                let on = m.at(i) <= t[i];
                all &= on;
                any |= on;
            }
            if all {
                held.insert(c.clone());
            }
            if any {
                never.remove(c);
            }
        }
    }
    let cls: String = s.getattr("cls")?.extract()?;
    let pending: bool = w.getattr("pend")?.extract()?;
    let mut out = Vec::new();
    for c in conds {
        if c == "ruled" && pending && ["claimed", "no_award", "set_aside"].contains(&cls.as_str()) {
            if !ctx.contains(&cls) {
                out.push(cls.clone());
            }
        } else if c == "ruled" {
            if held.contains("settled") || held.contains("paid") {
                continue;
            }
            if held.contains(&c) && !ctx.contains(&cls) {
                out.push(cls.clone());
            } else if never.contains(&c)
                && !ctx
                    .iter()
                    .any(|c| ["I1", "I2", "I3", "I4", "post", "ripe"].contains(&c.as_str()))
            {
                out.push("motions_pending".into());
            }
        } else if !held.contains(&c)
            || (c == "stay_moved"
                && (ctx.iter().any(|x| x == "stay_pending" || x == "I4")
                    || held.contains("stayed")))
            || (c == "stayed" && ctx.iter().any(|x| x == "I4"))
            || (c == "seeking" && ctx.iter().any(|x| x == "after_seek"))
        {
            continue;
        } else {
            out.push(c);
        }
    }
    Ok(out)
}
fn is_row_array(py: Python<'_>, v: &Bound<'_, PyAny>, n: usize) -> PyResult<bool> {
    Ok(v.is_instance(&py.import("numpy")?.getattr("ndarray")?)?
        && v.getattr("shape")?
            .extract::<Vec<usize>>()?
            .first()
            .copied()
            == Some(n))
}
fn strings(
    py: Python<'_>,
    s: &Bound<'_, PyAny>,
    key: &str,
    n: usize,
    fill: &str,
) -> PyResult<Vec<String>> {
    let v = s.call_method1("get", (key,))?;
    if is_row_array(py, &v, n)? {
        // Sparse fact rows have None placeholders on draws outside their mask.
        // Match NumPy's textual normalization (including None and bytes); the
        // class loop below still reads only the question's live draws.
        v.call_method1("astype", (py.get_type::<pyo3::types::PyString>(),))?
            .call_method0("tolist")?
            .extract()
    } else {
        Ok(vec![fill.into(); n])
    }
}
fn numbers(
    py: Python<'_>,
    s: &Bound<'_, PyAny>,
    key: &str,
    n: usize,
    fill: i64,
) -> PyResult<Vec<i64>> {
    let v = s.call_method1("get", (key,))?;
    if is_row_array(py, &v, n)? {
        v.call_method0("tolist")?.extract()
    } else {
        Ok(vec![fill; n])
    }
}
fn nonpositive(py: Python<'_>, s: &Bound<'_, PyAny>, key: &str, n: usize) -> PyResult<Vec<bool>> {
    let v = s.call_method1("get", (key,))?;
    if is_row_array(py, &v, n)? {
        // Only the sign enters this rule. Float extraction preserves the sign
        // of every integer/boolean amount, fractional values and NaN behavior.
        let values: Vec<f64> = v.call_method0("tolist")?.extract()?;
        Ok(values.into_iter().map(|x| x <= 0.0).collect())
    } else {
        // The reference fills absent or malformed row columns with 1.
        Ok(vec![false; n])
    }
}
#[pyfunction]
#[pyo3(signature=(row,live=None))]
fn walk_situation_class(
    py: Python<'_>,
    row: &Bound<'_, PyAny>,
    live: Option<PyReadonlyArray1<'_, bool>>,
) -> PyResult<Option<Vec<String>>> {
    let s = row.call_method1("get", ("sit",))?;
    let day = row.call_method1("get", ("day",))?;
    if !s.is_instance_of::<PyDict>()
        || !day.is_instance(&py.import("numpy")?.getattr("ndarray")?)?
    {
        return Ok(None);
    }
    let day: PyReadonlyArray1<'_, i64> = day.extract()?;
    let pet: PyReadonlyArray1<'_, i64> = row.get_item("petition")?.extract()?;
    let owed: PyReadonlyArray1<'_, i64> = row.get_item("owed")?.extract()?;
    let cash: PyReadonlyArray1<'_, i64> = row.get_item("cash")?.extract()?;
    let day = day.as_array();
    let pet = pet.as_array();
    let owed = owed.as_array();
    let cash = cash.as_array();
    let n = day.len();
    if pet.len() != n
        || owed.len() != n
        || cash.len() != n
        || live.as_ref().is_some_and(|a| a.as_array().len() != n)
    {
        return Err(PyValueError::new_err("situation input shapes differ"));
    }
    let band = s
        .call_method1("get", ("band",))?
        .extract::<Option<String>>()
        .unwrap_or(None)
        .unwrap_or_else(|| "-".into());
    let standing = strings(py, &s, "standing", n, "none")?;
    let due = numbers(py, &s, "notes_due_day", n, BIG)?;
    let avail = numbers(py, &s, "default_available", n, BIG)?;
    let dl = numbers(py, &s, "delisted", n, BIG)?;
    let npd = numbers(py, &s, "nonpayment_day", n, BIG)?;
    let listing = strings(py, &s, "listing", n, "listed")?;
    let ledger = numbers(py, &s, "ledger", n, 1)?;
    let insufficient = nonpositive(py, &s, "offer_available", n)?;
    let pending = s.call_method1("get", ("offering_pending",))?;
    let pending: Vec<bool> = if is_row_array(py, &pending, n)? {
        pending
            .call_method1("astype", (py.get_type::<pyo3::types::PyBool>(),))?
            .call_method0("tolist")?
            .extract()?
    } else {
        vec![false; n]
    };
    let mut out = vec![String::new(); n];
    for i in 0..n {
        let p = if pet[i] < 0 { BIG } else { pet[i] };
        let on = live
            .as_ref()
            .map_or(day[i] < BIG && day[i] < p, |l| l.as_array()[i]);
        if !on {
            continue;
        }
        let notes = if due[i] <= day[i] {
            "due"
        } else if avail[i] <= day[i] || dl[i] <= day[i] || npd[i] <= day[i] {
            "default"
        } else {
            "current"
        };
        let offer = if listing[i] == "delisted" {
            "delisted"
        } else if p <= day[i] {
            "petition"
        } else if pending[i] {
            "pending"
        } else if ledger[i] <= 0 {
            "nocapacity"
        } else if insufficient[i] {
            "insufficient"
        } else {
            "available"
        };
        let pay = if owed[i] > 0 && cash[i] >= owed[i] {
            "pay"
        } else {
            "nopay"
        };
        out[i] = format!(
            "#{band}.{}.{notes}.{}.{offer}.{pay}",
            standing[i], listing[i]
        );
    }
    Ok(Some(out))
}
// Trace queries are object boundaries; all predicates over their columns run below.
fn with_steps(py: Python<'_>, s: &Bound<'_, PyAny>, extra: &[Step]) -> PyResult<Py<PyAny>> {
    let mut steps: Vec<Step> = s.getattr("steps")?.extract()?;
    steps.extend_from_slice(extra);
    steps_py(py, &steps)
}
fn trace_steps<'py>(
    py: Python<'py>,
    w: &Bound<'py, PyAny>,
    s: &Bound<'py, PyAny>,
    extra: &[Step],
    full: bool,
) -> PyResult<Bound<'py, PyAny>> {
    w.call_method1("_trace", (with_steps(py, s, extra)?, full))
}
fn shape(n: usize, others: &[usize]) -> PyResult<()> {
    if others.iter().any(|&len| len != n) {
        return Err(PyValueError::new_err("walk predicate shapes differ"));
    }
    Ok(())
}
fn mark_sets(
    tr: &Bound<'_, PyAny>,
    conds: &[String],
    horizon: i64,
) -> PyResult<(BTreeSet<String>, BTreeSet<String>)> {
    let mut held = BTreeSet::new();
    let mut never: BTreeSet<String> = conds.iter().cloned().collect();
    let marks = tr.getattr("marks")?;
    if conds.is_empty() || marks.is_none() {
        return Ok((held, never));
    }
    let t = last(tr)?;
    let pet: PyReadonlyArray1<'_, i64> = tr.getattr("petition")?.extract()?;
    let t = t.as_array();
    let pet = pet.as_array();
    shape(t.len(), &[pet.len()])?;
    let inside: Vec<_> = (0..t.len())
        .filter(|&i| t[i] < horizon && t[i] < if pet[i] < 0 { i64::MAX } else { pet[i] })
        .collect();
    if inside.is_empty() {
        return Ok((held, never));
    }
    for cond in conds {
        let col = Dates::borrow(&marks.get_item(cond)?)?;
        shape(t.len(), &[col.len()])?;
        let mut all = true;
        let mut any = false;
        for &i in &inside {
            let on = col.at(i) <= t[i];
            all &= on;
            any |= on;
        }
        if all {
            held.insert(cond.clone());
        }
        if any {
            never.remove(cond);
        }
    }
    Ok((held, never))
}
#[pyfunction]
#[pyo3(signature=(fc,d,steps,conds,tr=None))]
fn walk_fc_situation(
    py: Python<'_>,
    fc: &Bound<'_, PyAny>,
    d: &Bound<'_, PyAny>,
    steps: Vec<Step>,
    conds: Vec<String>,
    tr: Option<&Bound<'_, PyAny>>,
) -> PyResult<Py<PyAny>> {
    let queried;
    let tr = if let Some(t) = tr {
        t
    } else {
        queried = fc.call_method1("trace", (d, steps_py(py, &steps)?))?;
        &queried
    };
    let (held, never) = mark_sets(tr, &conds, fc.getattr("days")?.extract()?)?;
    (
        pyo3::types::PySet::new(py, &held)?,
        pyo3::types::PySet::new(py, &never)?,
    )
        .into_py_any(py)
}
#[pyfunction]
fn walk_inside(py: Python<'_>, w: &Bound<'_, PyAny>, steps: Vec<Step>) -> PyResult<bool> {
    let tr = w.call_method1("_trace", (steps_py(py, &steps)?,))?;
    let t = last(&tr)?;
    let pet: PyReadonlyArray1<'_, i64> = tr.getattr("petition")?.extract()?;
    let t = t.as_array();
    let pet = pet.as_array();
    shape(t.len(), &[pet.len()])?;
    let horizon: i64 = w.getattr("N")?.extract()?;
    Ok((0..t.len()).any(|i| t[i] < horizon && t[i] < if pet[i] < 0 { i64::MAX } else { pet[i] }))
}
#[pyfunction]
fn walk_bank_inside(py: Python<'_>, w: &Bound<'_, PyAny>, steps: Vec<Step>) -> PyResult<bool> {
    let fc = w.getattr("fc")?;
    let tr = fc.call_method1("bank_trace", (steps_py(py, &steps)?,))?;
    let horizon: i64 = fc.getattr("days")?.extract()?;
    Ok(last(&tr)?.as_array().iter().any(|&t| t < horizon))
}
#[pyfunction]
fn walk_fc_arises(
    py: Python<'_>,
    fc: &Bound<'_, PyAny>,
    d: &Bound<'_, PyAny>,
    mut steps: Vec<Step>,
    step: Step,
) -> PyResult<bool> {
    steps.push(step);
    let tr = fc.call_method1("trace", (d, steps_py(py, &steps)?))?;
    let horizon: i64 = fc.getattr("days")?.extract()?;
    Ok(last(&tr)?.as_array().iter().any(|&t| t < horizon))
}
#[pyfunction]
fn walk_arises(
    py: Python<'_>,
    w: &Bound<'_, PyAny>,
    s: &Bound<'_, PyAny>,
    step: Step,
) -> PyResult<bool> {
    let mut steps: Vec<Step> = s.getattr("steps")?.extract()?;
    if w.getattr("pend")?.extract::<bool>()? {
        steps.push(step);
        walk_inside(py, w, steps)
    } else {
        walk_fc_arises(py, &w.getattr("fc")?, &w.getattr("d")?, steps, step)
    }
}
#[pyfunction]
fn walk_pay_possible(
    py: Python<'_>,
    fc: &Bound<'_, PyAny>,
    d: &Bound<'_, PyAny>,
    mut steps: Vec<Step>,
    step: Step,
) -> PyResult<bool> {
    steps.push(step);
    let tr = fc.call_method1("trace", (d, steps_py(py, &steps)?))?;
    let day = last(&tr)?;
    let cash_obj = tr.getattr("cash")?.get_item(-1)?;
    let owed_obj = tr.getattr("owed")?.get_item(-1)?;
    let cash: PyReadonlyArray1<'_, i64> = cash_obj.extract()?;
    let owed: PyReadonlyArray1<'_, i64> = owed_obj.extract()?;
    let day = day.as_array();
    let cash = cash.as_array();
    let owed = owed.as_array();
    shape(day.len(), &[cash.len(), owed.len()])?;
    let horizon: i64 = fc.getattr("days")?.extract()?;
    Ok((0..day.len()).any(|r| day[r] < horizon && cash[r] >= owed[r] && owed[r] > 0))
}
fn parameter_i64(py: Python<'_>, fc: &Bound<'_, PyAny>, key: &str) -> PyResult<i64> {
    py.import("app.analysis.events")?
        .getattr("pval")?
        .call1((
            fc.getattr("m")?,
            key,
            fc.getattr("sens")?.call_method1("get", (key, false))?,
        ))?
        .extract()
}
#[pyfunction]
fn walk_q1_opens_nothing(
    py: Python<'_>,
    w: &Bound<'_, PyAny>,
    s: &Bound<'_, PyAny>,
    yes: Step,
) -> PyResult<bool> {
    let q = trace_steps(py, w, s, std::slice::from_ref(&yes), true)?;
    let t = last(&q)?;
    let pet: PyReadonlyArray1<'_, i64> = q.getattr("petition")?.extract()?;
    let ap = trace_steps(
        py,
        w,
        s,
        &[
            yes.clone(),
            ("court_order".into(), "stay_I1".into(), String::new()),
        ],
        false,
    )?;
    let reg = trace_steps(
        py,
        w,
        s,
        &[
            yes,
            (
                "court_order".into(),
                "registration_I1".into(),
                String::new(),
            ),
        ],
        false,
    )?;
    let approval = last(&ap)?;
    let order = last(&reg)?;
    let t = t.as_array();
    let pet = pet.as_array();
    let approval = approval.as_array();
    let order = order.as_array();
    shape(t.len(), &[pet.len(), approval.len(), order.len()])?;
    let horizon: i64 = w.getattr("N")?.extract()?;
    let lag = parameter_i64(py, &w.getattr("fc")?, "levy_lag_days")?;
    Ok((0..t.len()).all(|i| {
        let p = if pet[i] < 0 { BIG } else { pet[i] };
        !(t[i] < horizon && t[i] < p)
            || approval[i] >= horizon.min(p) && order[i].wrapping_add(lag) >= horizon.min(p)
    }))
}
#[pyfunction]
fn walk_levy_first(py: Python<'_>, w: &Bound<'_, PyAny>, s: &Bound<'_, PyAny>) -> PyResult<bool> {
    let resp: String = w.getattr("resp")?.extract()?;
    let quiet: String = w.getattr("quiet")?.extract()?;
    let lv_tr = trace_steps(
        py,
        w,
        s,
        &[
            ("enforce".into(), "post".into(), "levy".into()),
            (resp, "post".into(), quiet),
        ],
        false,
    )?;
    let window_tr = trace_steps(
        py,
        w,
        s,
        &[("settle".into(), "I3".into(), "no".into())],
        false,
    )?;
    let lv = last(&lv_tr)?;
    let window = last(&window_tr)?;
    let lv = lv.as_array();
    let window = window.as_array();
    shape(lv.len(), &[window.len()])?;
    let horizon: i64 = w.getattr("N")?.extract()?;
    Ok((0..lv.len()).any(|i| lv[i] < horizon && window[i] < horizon && lv[i] < window[i]))
}
#[pyfunction]
fn walk_declared_after(
    py: Python<'_>,
    w: &Bound<'_, PyAny>,
    s: &Bound<'_, PyAny>,
    probe: Step,
) -> PyResult<bool> {
    let tr = trace_steps(py, w, s, &[probe], false)?;
    let day = last(&tr)?;
    let pet: PyReadonlyArray1<'_, i64> = tr.getattr("petition")?.extract()?;
    let day = day.as_array();
    let pet = pet.as_array();
    shape(day.len(), &[pet.len()])?;
    let horizon: i64 = w.getattr("N")?.extract()?;
    let lag = parameter_i64(py, &w.getattr("fc")?, "holder_notice_lag_days")?;
    Ok((0..day.len()).all(|i| {
        !(day[i] < horizon && day[i] < if pet[i] < 0 { BIG } else { pet[i] })
            || day[i].wrapping_add(lag) >= horizon
    }))
}
#[pyfunction]
fn walk_closes_after(
    py: Python<'_>,
    w: &Bound<'_, PyAny>,
    s: &Bound<'_, PyAny>,
    occasion: String,
) -> PyResult<bool> {
    let tr = trace_steps(
        py,
        w,
        s,
        &[("offering".into(), occasion, "no".into())],
        true,
    )?;
    let init = last(&tr)?;
    let pet: PyReadonlyArray1<'_, i64> = tr.getattr("petition")?.extract()?;
    let mask_obj = w.call_method1("mask_of", (s.getattr("steps")?,))?;
    let mask: Option<PyReadonlyArray1<'_, bool>> = if mask_obj.is_none() {
        None
    } else {
        Some(mask_obj.extract()?)
    };
    let init = init.as_array();
    let pet = pet.as_array();
    shape(init.len(), &[pet.len()])?;
    if let Some(m) = &mask {
        shape(init.len(), &[m.as_array().len()])?;
    }
    let horizon: i64 = w.getattr("N")?.extract()?;
    let lag: i64 = w
        .getattr("fc")?
        .getattr("m")?
        .get_item("parameters")?
        .get_item("offering_price")?
        .get_item("close_days")?
        .extract()?;
    Ok((0..init.len()).all(|i| {
        mask.as_ref().is_some_and(|m| !m.as_array()[i])
            || init[i] < BIG
                && init[i].wrapping_add(lag) >= horizon.min(if pet[i] < 0 { BIG } else { pet[i] })
    }))
}
#[pyfunction]
fn walk_entered_class(py: Python<'_>, w: &Bound<'_, PyAny>) -> PyResult<String> {
    let fc = w.getattr("fc")?;
    let total: i64 = py
        .import("app.analysis.events")?
        .getattr("entered_cents")?
        .call1((w.getattr("d")?,))?
        .extract()?;
    let lower = fc
        .getattr("m")?
        .get_item("parameters")?
        .get_item("bond_collateral_share_bps")?
        .get_item("lower")?
        .extract::<f64>()?
        / 10_000.;
    let reach: Option<i64> = fc.getattr("reach")?.extract()?;
    Ok(format!(
        "{}:{total}:0",
        if reach.is_some_and(|r| total as f64 * lower > r as f64) {
            "beyond"
        } else {
            "amt"
        }
    ))
}
#[pyfunction]
fn walk_reduced_band(
    _py: Python<'_>,
    w: &Bound<'_, PyAny>,
    s: &Bound<'_, PyAny>,
) -> PyResult<Option<(i64, i64, i64)>> {
    let steps: Vec<Step> = s.getattr("steps")?.extract()?;
    let Some(label) = steps
        .iter()
        .find(|x| x.0 == "verdict")
        .map(|x| &x.2)
        .filter(|l| l.starts_with("award:"))
    else {
        return Ok(None);
    };
    let total = label
        .split(':')
        .nth(1)
        .ok_or_else(|| PyValueError::new_err("award has no amount"))?
        .parse::<i64>()
        .map_err(|_| PyValueError::new_err("invalid award amount"))?;
    let fc = w.getattr("fc")?;
    let d = w.getattr("d")?;
    let inflows = fc.call_method1("equity_inflows", (&d,))?;
    let bands: Vec<(i64, i64, i64)> = fc
        .call_method1("verdict_lines", (d, inflows))?
        .get_item("bands")?
        .extract()?;
    let positive: Vec<_> = bands.into_iter().filter(|b| b.1 > 0).collect();
    let at = positive
        .iter()
        .position(|&(lo, hi, _)| lo < total && total <= hi)
        .ok_or_else(|| PyValueError::new_err("award outside verdict bands"))?;
    Ok(at.checked_sub(1).map(|i| positive[i]))
}

enum MarkCol<'py> {
    Scalar(i64),
    Array(Dates<'py>),
}
impl<'py> MarkCol<'py> {
    fn borrow(v: &Bound<'py, PyAny>, n: usize) -> PyResult<Self> {
        if let Ok(a) = Dates::borrow(v) {
            if a.len() != n && a.len() != 1 {
                return Err(PyValueError::new_err("mark cannot broadcast to draws"));
            }
            Ok(Self::Array(a))
        } else {
            Ok(Self::Scalar(v.extract()?))
        }
    }
    fn at(&self, i: usize) -> i64 {
        match self {
            Self::Scalar(x) => *x,
            Self::Array(a) => a.at(if a.len() == 1 { 0 } else { i }),
        }
    }
}
fn canonical(steps: &mut [Step]) -> PyResult<()> {
    for (_, _, answer) in steps {
        if answer.starts_with('@') {
            *answer = answer
                .split_once('=')
                .ok_or_else(|| PyValueError::new_err("group answer has no branch"))?
                .1
                .into();
        }
    }
    Ok(())
}
fn boolmask<'py>(m: &Bound<'py, PyAny>, n: usize) -> PyResult<Option<PyReadonlyArray1<'py, bool>>> {
    if m.is_none() {
        return Ok(None);
    }
    let a: PyReadonlyArray1<'py, bool> = m.extract()?;
    shape(n, &[a.as_array().len()])?;
    Ok(Some(a))
}
fn selected(mask: &Option<PyReadonlyArray1<'_, bool>>, i: usize) -> bool {
    mask.as_ref().is_none_or(|m| m.as_array()[i])
}
#[pyfunction]
fn walk_same_after(
    py: Python<'_>,
    w: &Bound<'_, PyAny>,
    s: &Bound<'_, PyAny>,
    a: Step,
    b: Step,
    mask_obj: &Bound<'_, PyAny>,
) -> PyResult<bool> {
    let d = w.getattr("d")?;
    let fc = w.getattr("fc")?;
    let steps_a = with_steps(py, s, &[a])?;
    let steps_b = with_steps(py, s, &[b])?;
    let rows_a = if d.is_none() {
        py.None()
    } else {
        w.call_method1("_rows", (steps_a.bind(py),))?.unbind()
    };
    let rows_b = if d.is_none() {
        py.None()
    } else {
        w.call_method1("_rows", (steps_b.bind(py),))?.unbind()
    };
    let event_chain = py.import("app.analysis.events")?.getattr("event_chain")?;
    let setup = fc.getattr("setup")?;
    let model = fc.getattr("m")?;
    let draws = fc.getattr("draws")?;
    let sens = fc.getattr("sens")?;
    let fin = if d.is_none() {
        w.getattr("fin")?.unbind()
    } else {
        py.None()
    };
    let chain = |steps: &Py<PyAny>, rows: &Py<PyAny>| -> PyResult<Bound<'_, PyAny>> {
        let mut typed: Vec<Step> = steps.extract(py)?;
        canonical(&mut typed)?;
        let kw = PyDict::new(py);
        kw.set_item("fin", fin.bind(py))?;
        kw.set_item("rows", rows.bind(py))?;
        event_chain.call(
            (&d, steps_py(py, &typed)?, &setup, &model, &draws, &sens),
            Some(&kw),
        )
    };
    let ca = chain(&steps_a, &rows_a)?;
    let cb = chain(&steps_b, &rows_b)?;
    let div_obj = ca.call_method1("divergence", (cb,))?;
    let div = Dates::borrow(&div_obj)?;
    let n: usize = draws.getattr("n")?.extract()?;
    let mask = boolmask(mask_obj, n)?;
    if div.len() == n {
        if (0..n).any(|i| selected(&mask, i) && div.at(i) < BIG) {
            return Ok(false);
        }
    } else if (0..div.len()).any(|i| div.at(i) < BIG) {
        return Ok(false);
    }
    let whole = |steps: &Py<PyAny>, rows: &Py<PyAny>| -> PyResult<Bound<'_, PyAny>> {
        if d.is_none() {
            return w.call_method1("_trace", (steps.bind(py), true));
        }
        let kw = PyDict::new(py);
        kw.set_item("rows", rows.bind(py))?;
        kw.set_item("full_rows", true)?;
        let tr = fc
            .getattr("trace")?
            .call((&d, steps.bind(py), true), Some(&kw))?;
        py.import("app.disputes.forecast")?
            .getattr("masked")?
            .call1((tr, w.call_method1("mask_of", (steps.bind(py),))?))
    };
    let ta = whole(&steps_a, &rows_a)?;
    let tb = whole(&steps_b, &rows_b)?;
    let da = ta.getattr("digest")?;
    if da.is_none() || !da.eq(tb.getattr("digest")?)? {
        return Ok(false);
    }
    let cause_a = ta.getattr("cause")?;
    let cause_b = tb.getattr("cause")?;
    if cause_a.is_none() != cause_b.is_none() {
        return Ok(false);
    }
    if !cause_a.is_none() {
        shape(n, &[cause_a.len()?, cause_b.len()?])?;
        for i in 0..n {
            if selected(&mask, i) && !cause_a.get_item(i)?.eq(cause_b.get_item(i)?)? {
                return Ok(false);
            }
        }
    }
    let ma = ta.getattr("marks")?;
    let mb = tb.getattr("marks")?;
    let ma = if ma.is_none() {
        PyDict::new(py)
    } else {
        ma.cast_into::<PyDict>()?
    };
    let mb = if mb.is_none() {
        PyDict::new(py)
    } else {
        mb.cast_into::<PyDict>()?
    };
    let mut keys = BTreeSet::new();
    for (k, _) in ma.iter().chain(mb.iter()) {
        keys.insert(k.extract::<String>()?);
    }
    let horizon: i64 = w.getattr("N")?.extract()?;
    for key in keys {
        let va = match ma.get_item(&key)? {
            Some(v) => v,
            None => BIG.into_py_any(py)?.into_bound(py),
        };
        let vb = match mb.get_item(&key)? {
            Some(v) => v,
            None => BIG.into_py_any(py)?.into_bound(py),
        };
        let a = MarkCol::borrow(&va, n)?;
        let b = MarkCol::borrow(&vb, n)?;
        for i in 0..n {
            if selected(&mask, i) {
                let x = a.at(i);
                let y = b.at(i);
                if (if x < horizon { x } else { BIG }) != (if y < horizon { y } else { BIG }) {
                    return Ok(false);
                }
            }
        }
    }
    Ok(true)
}
#[pyfunction]
#[pyo3(signature=(v,div,full,days,fired=None,lag=0))]
fn walk_cache_valid(
    v: &Bound<'_, PyAny>,
    div: PyReadonlyArray1<'_, i64>,
    full: bool,
    days: i64,
    fired: Option<&Bound<'_, PyAny>>,
    lag: i64,
) -> PyResult<bool> {
    let div = div.as_array();
    let n = div.len();
    let fired = fired.map(|f| MarkCol::borrow(f, n)).transpose()?;
    let date = |i: usize| {
        let original = div[i];
        if let Some(f) = &fired {
            let at = f.at(i);
            original.min(if at < BIG { at.wrapping_add(lag) } else { BIG })
        } else {
            original
        }
    };
    if full {
        return Ok((0..n).all(|i| date(i) >= BIG));
    }
    let as_of_obj = v.getattr("as_of")?;
    if as_of_obj.is_none() {
        return Ok(false);
    }
    let as_of = MarkCol::borrow(&as_of_obj, n)?;
    let t = last(v)?;
    let t = t.as_array();
    shape(n, &[t.len()])?;
    Ok((0..n).all(|i| {
        if t[i] < days {
            as_of.at(i) < date(i)
        } else {
            date(i) >= BIG
        }
    }))
}
#[pyfunction]
fn walk_situation(
    py: Python<'_>,
    w: &Bound<'_, PyAny>,
    s: &Bound<'_, PyAny>,
    probe: &Bound<'_, PyAny>,
    name: String,
    ctx: Vec<String>,
) -> PyResult<Vec<String>> {
    let conds: Vec<String> = w
        .getattr("fc")?
        .getattr("spec")?
        .get_item(name)?
        .call_method1("get", ("situation", Vec::<String>::new()))?
        .try_iter()?
        .map(|x| x?.extract())
        .collect::<PyResult<_>>()?;
    if conds.is_empty() {
        return Ok(Vec::new());
    }
    let at: Vec<Step> = if let Ok(step) = probe.extract::<Step>() {
        vec![step]
    } else {
        probe.extract()?
    };
    let tr = trace_steps(py, w, s, &at, false)?;
    let at = steps_py(py, &at)?;
    let tags = walk_tags(py, w, s, conds.clone(), ctx.clone(), &tr, at.bind(py))?;
    let marks = tr.getattr("marks")?;
    if marks.is_none() {
        return Ok(tags);
    }
    let t = last(&tr)?;
    let n = t.as_array().len();
    for watch in w.getattr("_watch")?.try_iter()? {
        let watch = watch?;
        if watch.getattr("read")?.extract::<bool>()? {
            continue;
        }
        let watched = watch.getattr("marks")?.cast_into::<PyDict>()?;
        if !watched
            .iter()
            .any(|(k, _)| k.extract::<String>().is_ok_and(|s| conds.contains(&s)))
        {
            continue;
        }
        let cf = marks.call_method0("copy")?.cast_into::<PyDict>()?;
        for (k, v) in watched.iter() {
            let current = marks.get_item(&k)?;
            let current = MarkCol::borrow(&current, n)?;
            let other = MarkCol::borrow(&v, n)?;
            cf.set_item(
                k,
                (0..n)
                    .map(|i| current.at(i).min(other.at(i)))
                    .collect::<Vec<_>>()
                    .into_pyarray(py),
            )?;
        }
        let kw = PyDict::new(py);
        kw.set_item("marks", cf)?;
        let hypothetical = py
            .import("dataclasses")?
            .getattr("replace")?
            .call((&tr,), Some(&kw))?;
        watch.setattr(
            "read",
            walk_tags(
                py,
                w,
                s,
                conds.clone(),
                ctx.clone(),
                &hypothetical,
                at.bind(py),
            )? != tags,
        )?;
    }
    Ok(tags)
}
#[pyfunction]
fn walk_event_range(
    py: Python<'_>,
    fc: &Bound<'_, PyAny>,
    tr: &Bound<'_, PyAny>,
    mask_obj: &Bound<'_, PyAny>,
) -> PyResult<()> {
    let ev = tr.getattr("events")?;
    let cash_obj = ev.getattr("cash")?;
    let lock_obj = ev.getattr("lock")?;
    let cash: numpy::PyReadonlyArray2<'_, i64> = cash_obj.extract()?;
    let lock: numpy::PyReadonlyArray2<'_, i64> = lock_obj.extract()?;
    let cash = cash.as_array();
    let lock = lock.as_array();
    let (n, days) = cash.dim();
    if lock.dim() != (n, days) {
        return Err(PyValueError::new_err("event range shapes differ"));
    }
    let on_rows = !tr.getattr("rows")?.is_none();
    let mask = if on_rows {
        None
    } else {
        boolmask(mask_obj, n)?
    };
    if days == 0 || !(0..n).any(|i| selected(&mask, i)) {
        return Ok(());
    }
    let mut lows = vec![i64::MAX; days];
    let mut highs = vec![i64::MIN; days];
    for r in 0..n {
        if selected(&mask, r) {
            let mut total = 0i64;
            for d in 0..days {
                total = total.wrapping_add(cash[[r, d]].wrapping_sub(lock[[r, d]]));
                lows[d] = lows[d].min(total);
                highs[d] = highs[d].max(total);
            }
        }
    }
    let store = fc.getattr("__dict__")?;
    let existing = store.call_method1("get", ("ev_range",))?;
    let ranges = if existing.is_none() {
        let r = PyTuple::new(
            py,
            [
                vec![0f64; days].into_pyarray(py),
                vec![0f64; days].into_pyarray(py),
            ],
        )?;
        let list = py.import("builtins")?.getattr("list")?.call1((r,))?;
        store.set_item("ev_range", &list)?;
        list
    } else {
        existing
    };
    let lo_obj = ranges.get_item(0)?;
    let hi_obj = ranges.get_item(1)?;
    let mut lo: numpy::PyReadwriteArray1<'_, f64> = lo_obj.extract()?;
    let mut hi: numpy::PyReadwriteArray1<'_, f64> = hi_obj.extract()?;
    let mut lo = lo.as_array_mut();
    let mut hi = hi.as_array_mut();
    shape(days, &[lo.len(), hi.len()])?;
    for d in 0..days {
        lo[d] = lo[d].min(lows[d] as f64);
        hi[d] = hi[d].max(highs[d] as f64);
    }
    Ok(())
}
#[pyfunction]
fn walk_ordinary_expand(edges: Vec<super::walk::Edge>) -> PyResult<Vec<Vec<super::walk::Edge>>> {
    let mut out = vec![Vec::new()];
    for (key, branch) in edges {
        let options: super::walk::Conjunctions = if let Some(encoded) = key.strip_prefix('=') {
            if branch != "yes" {
                return Err(PyValueError::new_err(
                    "the ordinary view expands composite edges taken on 'yes' only",
                ));
            }
            serde_json::from_str(encoded)
                .map_err(|e| PyValueError::new_err(format!("invalid composite: {e}")))?
        } else {
            vec![vec![(key, branch)]]
        };
        let mut next = Vec::new();
        for prefix in out {
            for option in &options {
                let mut combined = prefix.clone();
                combined.extend_from_slice(option);
                next.push(combined);
            }
        }
        out = next;
    }
    Ok(out)
}
fn clipped_dates(py: Python<'_>, value: &Bound<'_, PyAny>, horizon: i64) -> PyResult<Py<PyAny>> {
    Ok(match Dates::borrow(value)? {
        Dates::I32(a) => a
            .as_array()
            .iter()
            .map(|&x| {
                if i64::from(x) < horizon {
                    x
                } else {
                    BIG as i32
                }
            })
            .collect::<Vec<_>>()
            .into_pyarray(py)
            .into_any()
            .unbind(),
        Dates::I64(a) => a
            .as_array()
            .iter()
            .map(|&x| if x < horizon { x } else { BIG })
            .collect::<Vec<_>>()
            .into_pyarray(py)
            .into_any()
            .unbind(),
        Dates::Bool(a) => a
            .as_array()
            .iter()
            .map(|&x| {
                if i64::from(x) < horizon {
                    i64::from(x)
                } else {
                    BIG
                }
            })
            .collect::<Vec<_>>()
            .into_pyarray(py)
            .into_any()
            .unbind(),
    })
}
#[pyfunction]
fn walk_equivalence(
    py: Python<'_>,
    w: &Bound<'_, PyAny>,
    s: &Bound<'_, PyAny>,
    outcome: String,
    tr: &Bound<'_, PyAny>,
    mask_obj: &Bound<'_, PyAny>,
) -> PyResult<Py<PyAny>> {
    // NumPy's contiguous byte encoding and xxhash are the output-identity boundary.
    // Column selection/order and every date comparison are native.
    let ev = tr.getattr("events")?;
    let horizon: i64 = w.getattr("N")?.extract()?;
    let h = py.import("xxhash")?.getattr("xxh3_128")?.call0()?;
    let on_rows = tr.hasattr("rows")? && !tr.getattr("rows")?.is_none();
    let mut named = Vec::new();
    for name in ["cash", "lock", "capacity", "petition"] {
        named.push((name.to_owned(), ev.getattr(name)?.unbind(), on_rows));
    }
    for (prefix, attr) in [("k:", "kinds"), ("i:", "incurred"), ("p:", "proceeds")] {
        let values = ev.getattr(attr)?;
        if values.is_none() {
            continue;
        }
        let dict = values.cast::<PyDict>()?;
        let mut keys: Vec<String> = dict
            .iter()
            .map(|(k, _)| k.extract())
            .collect::<PyResult<_>>()?;
        keys.sort();
        for key in keys {
            let value = dict
                .get_item(&key)?
                .ok_or_else(|| PyValueError::new_err("event column disappeared"))?;
            named.push((format!("{prefix}{key}"), value.unbind(), on_rows));
        }
    }
    let cause = tr.getattr("cause")?;
    if !cause.is_none() {
        named.push(("cause".into(), cause.unbind(), false));
    }
    let marks = tr.getattr("marks")?;
    if !marks.is_none() {
        for key in ["stayed", "ruled", "paid", "settled", "raised"] {
            if marks.contains(key)? {
                named.push((
                    format!("m:{key}"),
                    clipped_dates(py, &marks.get_item(key)?, horizon)?,
                    false,
                ));
            }
        }
    }
    let np = py.import("numpy")?;
    for (name, value, cut) in named {
        let a = if mask_obj.is_none() || cut {
            value.into_bound(py)
        } else {
            np.call_method1("asarray", (value,))?.get_item(mask_obj)?
        };
        let a = np.call_method1("ascontiguousarray", (a,))?;
        let prefix = format!(
            "{name}{}{}",
            a.getattr("shape")?.str()?.to_str()?,
            a.getattr("dtype")?.str()?.to_str()?
        );
        h.call_method1(
            "update",
            (pyo3::types::PyBytes::new(py, prefix.as_bytes()),),
        )?;
        h.call_method1("update", (a.call_method0("tobytes")?,))?;
    }
    let packed = if mask_obj.is_none() {
        py.None()
    } else {
        let m: PyReadonlyArray1<'_, bool> = mask_obj.extract()?;
        let m = m.as_array();
        let mut bytes = vec![0u8; m.len().div_ceil(8)];
        for (i, &on) in m.iter().enumerate() {
            if on {
                bytes[i / 8] |= 1 << (7 - i % 8);
            }
        }
        pyo3::types::PyBytes::new(py, &bytes).into_any().unbind()
    };
    let steps: Vec<Step> = s.getattr("steps")?.extract()?;
    let verdict = steps
        .iter()
        .find(|x| x.0 == "verdict")
        .map_or("", |x| x.2.as_str());
    let ruling = steps
        .iter()
        .find(|x| x.0 == "post_trial_ruling")
        .map_or("", |x| x.2.as_str());
    (packed, h.call_method0("digest")?, verdict, ruling, outcome).into_py_any(py)
}

#[pyfunction]
fn walk_raise_available(
    row: &Bound<'_, PyAny>,
    amount: PyReadonlyArray1<'_, i64>,
    days: i64,
) -> PyResult<bool> {
    let day: PyReadonlyArray1<'_, i64> = row.get_item("day")?.extract()?;
    let pet: PyReadonlyArray1<'_, i64> = row.get_item("petition")?.extract()?;
    let day = day.as_array();
    let pet = pet.as_array();
    let amount = amount.as_array();
    shape(day.len(), &[pet.len(), amount.len()])?;
    Ok((0..day.len()).any(|i| {
        day[i] < days && day[i] < if pet[i] < 0 { i64::MAX } else { pet[i] } && amount[i] > 0
    }))
}
#[pyfunction]
fn walk_live<'py>(
    py: Python<'py>,
    fc: &Bound<'py, PyAny>,
    node: &Bound<'py, PyAny>,
    row: &Bound<'py, PyAny>,
) -> PyResult<Py<PyAny>> {
    let day: PyReadonlyArray1<'_, i64> = row.get_item("day")?.extract()?;
    let pet: PyReadonlyArray1<'_, i64> = row.get_item("petition")?.extract()?;
    let day = day.as_array();
    let pet = pet.as_array();
    shape(day.len(), &[pet.len()])?;
    let days: i64 = fc.getattr("days")?.extract()?;
    let oweds = py.import("app.disputes.forecast")?.getattr("OWED")?;
    let requires_amount = oweds.contains(node.getattr("node")?)?
        && !node
            .getattr("context")?
            .extract::<String>()?
            .starts_with("I0");
    let owed: Option<PyReadonlyArray1<'_, i64>> = if requires_amount {
        let a: PyReadonlyArray1<'_, i64> = row.get_item("owed")?.extract()?;
        shape(day.len(), &[a.as_array().len()])?;
        Some(a)
    } else {
        None
    };
    let out: Vec<bool> = (0..day.len())
        .map(|i| {
            day[i] < days
                && day[i] < if pet[i] < 0 { i64::MAX } else { pet[i] }
                && owed.as_ref().is_none_or(|a| a.as_array()[i] > 0)
        })
        .collect();
    Ok(out.into_pyarray(py).into_any().unbind())
}
pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(walk_reach, m)?)?;
    m.add_function(wrap_pyfunction!(walk_rows, m)?)?;
    m.add_function(wrap_pyfunction!(walk_groups, m)?)?;
    m.add_function(wrap_pyfunction!(walk_mask, m)?)?;
    m.add_function(wrap_pyfunction!(walk_group_codes, m)?)?;
    m.add_function(wrap_pyfunction!(walk_option_groups, m)?)?;
    m.add_function(wrap_pyfunction!(walk_tags, m)?)?;
    m.add_function(wrap_pyfunction!(walk_situation_class, m)?)?;
    m.add_function(wrap_pyfunction!(walk_fc_situation, m)?)?;
    m.add_function(wrap_pyfunction!(walk_inside, m)?)?;
    m.add_function(wrap_pyfunction!(walk_bank_inside, m)?)?;
    m.add_function(wrap_pyfunction!(walk_fc_arises, m)?)?;
    m.add_function(wrap_pyfunction!(walk_arises, m)?)?;
    m.add_function(wrap_pyfunction!(walk_pay_possible, m)?)?;
    m.add_function(wrap_pyfunction!(walk_q1_opens_nothing, m)?)?;
    m.add_function(wrap_pyfunction!(walk_levy_first, m)?)?;
    m.add_function(wrap_pyfunction!(walk_declared_after, m)?)?;
    m.add_function(wrap_pyfunction!(walk_closes_after, m)?)?;
    m.add_function(wrap_pyfunction!(walk_entered_class, m)?)?;
    m.add_function(wrap_pyfunction!(walk_reduced_band, m)?)?;
    m.add_function(wrap_pyfunction!(walk_same_after, m)?)?;
    m.add_function(wrap_pyfunction!(walk_cache_valid, m)?)?;
    m.add_function(wrap_pyfunction!(walk_situation, m)?)?;
    m.add_function(wrap_pyfunction!(walk_event_range, m)?)?;
    m.add_function(wrap_pyfunction!(walk_ordinary_expand, m)?)?;
    m.add_function(wrap_pyfunction!(walk_equivalence, m)?)?;
    m.add_function(wrap_pyfunction!(walk_raise_available, m)?)?;
    m.add_function(wrap_pyfunction!(walk_live, m)?)?;
    Ok(())
}

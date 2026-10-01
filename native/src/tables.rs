//! Sharded table reduction. Rust owns grouping, masking, update order and
//! quantile control. NumPy's compiled ndarray reductions/ufuncs are the numeric
//! boundary where integer dtype promotion and floating reduction trees matter.
use numpy::{IntoPyArray, PyArray1, PyArrayMethods};
use pyo3::basic::CompareOp;
use pyo3::exceptions::{PyAssertionError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList, PySlice, PyTuple};
use std::collections::BTreeMap;

const FINE: usize = 1 << 20;
const TRIE_DEPTH: usize = 8;
const STRESS_KEEP: usize = 500;
const SERIES: [&str; 4] = ["collected", "due_cum", "past_due", "outstanding"];
type Obj<'py> = Bound<'py, PyAny>;

fn binary<'py>(a: &Obj<'py>, op: &str, b: &Obj<'py>) -> PyResult<Obj<'py>> {
    match op {
        "__add__" => a.add(b),
        "__sub__" => a.sub(b),
        "__mul__" => a.mul(b),
        "__truediv__" => a.div(b),
        "__and__" => a.bitand(b),
        "__or__" => a.bitor(b),
        "__lt__" => a.rich_compare(b, CompareOp::Lt),
        "__le__" => a.rich_compare(b, CompareOp::Le),
        "__ge__" => a.rich_compare(b, CompareOp::Ge),
        _ => Err(PyValueError::new_err(
            "unsupported table arithmetic operation",
        )),
    }
}
fn add_to(a: &Obj<'_>, b: &Obj<'_>) -> PyResult<()> {
    a.call_method1("__iadd__", (b,))?;
    Ok(())
}
fn sum<'py>(a: &Obj<'py>, axis: Option<usize>) -> PyResult<Obj<'py>> {
    match axis {
        Some(i) => a.call_method1("sum", (i,)),
        None => a.call_method0("sum"),
    }
}
fn cumulative<'py>(np: &Obj<'py>, a: &Obj<'py>, axis: Option<usize>) -> PyResult<Obj<'py>> {
    let _ = np;
    match axis {
        Some(i) => a.call_method1("cumsum", (i,)),
        None => a.call_method0("cumsum"),
    }
}
fn column<'py>(a: &Obj<'py>) -> PyResult<Obj<'py>> {
    a.get_item((PySlice::full(a.py()), a.py().None()))
}
fn row<'py>(a: &Obj<'py>) -> PyResult<Obj<'py>> {
    a.get_item((a.py().None(), PySlice::full(a.py())))
}
fn selected<'py>(a: &Obj<'py>, mask: &Obj<'py>) -> PyResult<Obj<'py>> {
    if mask.is_none() {
        Ok(a.clone())
    } else {
        a.get_item(mask)
    }
}
fn shape(a: &Obj<'_>) -> PyResult<Vec<usize>> {
    a.getattr("shape")?.extract()
}
fn require_shape(a: &Obj<'_>, expected: &[usize], label: &str) -> PyResult<()> {
    if shape(a)? != expected {
        return Err(PyValueError::new_err(format!(
            "{label} has incompatible shape"
        )));
    }
    Ok(())
}
fn require_indices(a: &Obj<'_>, upper: usize, label: &str) -> PyResult<()> {
    let kind = a.getattr("dtype")?.getattr("kind")?.extract::<String>()?;
    if kind != "i" && kind != "u" {
        return Err(PyValueError::new_err(format!(
            "{label} must contain integer indices"
        )));
    }
    let np = a.py().import("numpy")?;
    let values = np.getattr("asarray")?.call1((a, np.getattr("int64")?))?;
    let values = values.cast::<PyArray1<i64>>()?.readonly();
    if values
        .as_array()
        .iter()
        .any(|&v| v < 0 || v as u64 >= upper as u64)
    {
        return Err(PyValueError::new_err(format!(
            "{label} index out of bounds"
        )));
    }
    Ok(())
}
fn dict_attr<'py>(a: &Obj<'py>, name: &str) -> PyResult<Bound<'py, PyDict>> {
    Ok(a.getattr(name)?.cast_into::<PyDict>()?)
}
fn zeros<'py>(np: &Obj<'py>, size: usize) -> PyResult<Obj<'py>> {
    np.call_method1("zeros", (size,))
}
fn ensure<'py>(
    np: &Obj<'py>,
    d: &Bound<'py, PyDict>,
    key: &Obj<'py>,
    size: usize,
) -> PyResult<Obj<'py>> {
    if let Some(v) = d.get_item(key)? {
        return Ok(v);
    }
    let v = zeros(np, size)?;
    d.set_item(key, &v)?;
    Ok(v)
}
fn count_bins<'py>(np: &Obj<'py>, indices: &Obj<'py>, size: usize) -> PyResult<Obj<'py>> {
    let kw = PyDict::new(np.py());
    kw.set_item("minlength", size)?;
    np.call_method("bincount", (indices,), Some(&kw))
}
fn weighted<'py>(prob: &Obj<'py>, values: &Obj<'py>) -> PyResult<Obj<'py>> {
    binary(&column(prob)?, "__mul__", &row(values)?)
}
fn add_histogram(
    np: &Obj<'_>,
    target: &Obj<'_>,
    probs: &Obj<'_>,
    indices: &Obj<'_>,
) -> PyResult<()> {
    let counts = count_bins(np, indices, shape(target)?[1])?;
    let nz = counts.call_method0("nonzero")?.get_item(0)?;
    let at = (PySlice::full(np.py()), &nz);
    let current = target.get_item(at.clone())?;
    let next = binary(
        &current,
        "__add__",
        &weighted(probs, &counts.get_item(&nz)?)?,
    )?;
    target.set_item(at, next)
}
fn increment(a: &Obj<'_>, name: &str, value: &Obj<'_>) -> PyResult<()> {
    a.setattr(name, binary(&a.getattr(name)?, "__add__", value)?)
}
fn keys<'py>(py: Python<'py>, names: &[&str]) -> PyResult<Vec<Obj<'py>>> {
    names
        .iter()
        .map(|name| Ok(name.into_pyobject(py)?.into_any()))
        .collect()
}

// Validate all externally supplied axes before updating any table buffers.
fn validate_add(table: &Obj<'_>, t: &Obj<'_>, groups: &[Obj<'_>]) -> PyResult<()> {
    let n: usize = table.getattr("draws")?.extract()?;
    let days: usize = table.getattr("days")?.extract()?;
    let f = table.getattr("full")?.len()?;
    let l = table.getattr("scalar")?.len()?;
    if n == 0 || days == 0 || (f == 0 && !groups.is_empty()) {
        return Err(PyValueError::new_err(
            "table reduction requires draws, days and a central setting",
        ));
    }
    for key in [
        "cash",
        "due",
        "collections",
        "fundings",
        "outstanding",
        "locked",
        "capacity",
    ] {
        require_shape(&t.getattr(key)?, &[n, days], key)?;
    }
    for key in [
        "petition",
        "stayed",
        "min_cash",
        "collected",
        "min_headroom",
    ] {
        require_shape(&t.getattr(key)?, &[n], key)?;
    }
    let need = table.getattr("need")?;
    if !need.is_none() {
        require_shape(&need, &[n, days], "need")?;
    }
    let hr = t.getattr("headroom")?.len()?;
    for key in ["headroom", "headroom_rows", "headroom_days"] {
        require_shape(&t.getattr(key)?, &[hr], key)?;
    }
    require_indices(&t.getattr("headroom_rows")?, n, "headroom row")?;
    require_indices(&t.getattr("headroom_days")?, days, "headroom day")?;
    require_shape(&table.getattr("month_of_day")?, &[days], "month_of_day")?;
    let bins = table.getattr("bins")?;
    let counts = dict_attr(table, "counts")?;
    for name in ["cash", "collected", "headroom"] {
        let bin = bins.get_item(name)?;
        let lo = bin.getattr("lo")?;
        let rows = if name == "headroom" { lo.len()? } else { days };
        require_shape(&lo, &[rows], "bin lower bounds")?;
        require_shape(&bin.getattr("width")?, &[rows], "bin widths")?;
        let nb: usize = bin.getattr("n")?.extract()?;
        if nb == 0 {
            return Err(PyValueError::new_err(
                "histogram bin count must be positive",
            ));
        }
        let size = rows
            .checked_mul(nb)
            .ok_or_else(|| PyValueError::new_err("histogram dimensions overflow"))?;
        require_shape(
            &counts
                .get_item(name)?
                .ok_or_else(|| PyValueError::new_err("histogram key missing"))?,
            &[f, size],
            "histogram",
        )?;
        if name == "headroom" {
            require_indices(&table.getattr("month_of_day")?, rows, "headroom month")?;
        }
    }
    let processed = t.getattr("processed")?;
    let ar = table
        .py()
        .import("app.analysis.core")?
        .getattr("ARREARS_KEYS")?
        .len()?;
    if !processed.is_none() {
        require_shape(&processed.getattr("arrears")?, &[n, days, ar], "arrears")?;
    }
    for (_, v) in dict_attr(table, "per_day")?.iter() {
        require_shape(&v, &[f, days], "per_day")?;
    }
    let per_day = dict_attr(table, "per_day")?;
    let names = table.py().import("app.analysis.core")?.getattr("DAILY")?;
    for key in names.try_iter()? {
        if !per_day.contains(key?)? {
            return Err(PyValueError::new_err("per-day key missing"));
        }
    }
    if !processed.is_none() {
        for key in table
            .py()
            .import("app.analysis.core")?
            .getattr("ARREARS_KEYS")?
            .try_iter()?
        {
            if !per_day.contains(key?)? {
                return Err(PyValueError::new_err("arrears per-day key missing"));
            }
        }
    }
    for (_, v) in dict_attr(table, "fine")?.iter() {
        require_shape(&v, &[f, FINE], "fine")?;
    }
    for name in ["min_cash", "collected", "min_headroom"] {
        dict_attr(table, "fine")?
            .get_item(name)?
            .ok_or_else(|| PyValueError::new_err("fine histogram missing"))?;
        if table.getattr("ranges")?.get_item(name)?.len()? != 2 {
            return Err(PyValueError::new_err(
                "fine histogram range needs two fields",
            ));
        }
    }
    for (_, v) in dict_attr(table, "counts")?.iter() {
        let s = shape(&v)?;
        if s.len() != 2 || s[0] != f {
            return Err(PyValueError::new_err("histogram axes disagree"));
        }
    }
    for (name, len) in [("means", f), ("lo_means", l), ("trie", 3), ("outcomes", 3)] {
        for (_, v) in dict_attr(table, name)?.iter() {
            require_shape(&v, &[len], name)?;
        }
    }
    for key in ["hr_count", "hr_negative"] {
        require_shape(&table.getattr(key)?, &[f], key)?;
    }
    require_shape(&table.getattr("floor")?, &[f, days + 1], "floor")?;
    let skeys = table.getattr("skeys")?;
    let dser = dict_attr(table, "dser")?;
    for (key, v) in dict_attr(table, "dscal")?.iter() {
        if skeys.is_none() {
            return Err(PyValueError::new_err("scalar keys missing for derivatives"));
        }
        require_shape(&v, &[skeys.len()?], "scalar derivative")?;
        require_shape(
            &dser
                .get_item(key)?
                .ok_or_else(|| PyValueError::new_err("derivative series missing"))?,
            &[SERIES.len(), days],
            "series derivative",
        )?;
    }
    for group in groups {
        if group.len()? != 4 {
            return Err(PyValueError::new_err("a reduction group needs four fields"));
        }
        let mask = group.get_item(0)?;
        if !mask.is_none() {
            require_shape(&mask, &[n], "group mask")?;
            if mask
                .getattr("dtype")?
                .getattr("kind")?
                .extract::<String>()?
                != "b"
            {
                return Err(PyValueError::new_err("group mask must be boolean"));
            }
        }
        require_shape(&group.get_item(1)?, &[f], "full probabilities")?;
        require_shape(&group.get_item(2)?, &[l], "scalar probabilities")?;
        for atom in group.get_item(3)?.try_iter()? {
            let atom = atom?;
            if atom.len()? != 3 {
                return Err(PyValueError::new_err("derivative atom needs three fields"));
            }
            atom.get_item(2)?.extract::<f64>()?;
        }
    }
    Ok(())
}

#[pyfunction]
fn tables_add(
    table: &Obj<'_>,
    p: &Obj<'_>,
    t: &Obj<'_>,
    ev: &Obj<'_>,
    groups: &Obj<'_>,
    stress_row: &Obj<'_>,
) -> PyResult<()> {
    let py = table.py();
    let np = py.import("numpy")?.into_any();
    let groups: Vec<_> = groups.try_iter()?.collect::<PyResult<_>>()?;
    validate_add(table, t, &groups)?;
    let n: usize = table.getattr("draws")?.extract()?;
    let days: usize = table.getattr("days")?.extract()?;
    let f = table.getattr("full")?.len()?;
    let l = table.getattr("scalar")?.len()?;
    let sc = crate::analysis::analysis_scalars(t)?;
    if !ev.is_none() {
        let proceeds = ev.getattr("proceeds")?;
        if !proceeds.is_none() {
            for key in keys(py, &["atm_proceeds", "offering_proceeds"])? {
                sc.set_item(&key, proceeds.get_item(&key)?)?;
            }
        }
    }
    for (_, v) in sc.iter() {
        require_shape(&v, &[n], "trajectory scalar")?;
    }
    let skeys = if table.getattr("skeys")?.is_none() {
        let mut names: Vec<String> = sc.keys().extract()?;
        names.sort();
        PyList::new(py, names)?.into_any()
    } else {
        table.getattr("skeys")?
    };
    for key in skeys.try_iter()? {
        sc.get_item(key?)?
            .ok_or_else(|| PyValueError::new_err("scalar key missing"))?;
    }
    let need = table.getattr("need")?;
    let first = if need.is_none() {
        None
    } else {
        let below = binary(&t.getattr("cash")?, "__lt__", &need)?;
        Some(np.call_method1(
            "where",
            (
                below.call_method1("any", (1,))?,
                below.call_method1("argmax", (1,))?,
                -1,
            ),
        )?)
    };
    let steps = p.getattr("steps")?;
    let outcome = p.getattr("outcome")?;
    let mut verdict = String::new();
    let mut ruling = String::new();
    let mut seen_verdict = false;
    let mut seen_ruling = false;
    for step in steps.try_iter()? {
        let step = step?;
        let name = step.get_item(0)?.extract::<String>()?;
        if name == "verdict" && !seen_verdict {
            verdict = step.get_item(2)?.extract()?;
            seen_verdict = true;
        }
        if name == "post_trial_ruling" && !seen_ruling {
            ruling = step.get_item(2)?.extract()?;
            seen_ruling = true;
        }
    }
    if !stress_row.is_none() {
        stress_row.get_item("min_cash_p5_cents")?.extract::<f64>()?;
    }
    let all_due = cumulative(&np, &t.getattr("due")?, Some(1))?;
    let all_coll = cumulative(&np, &t.getattr("collections")?, Some(1))?;
    if table.getattr("skeys")?.is_none() {
        table.setattr("skeys", &skeys)?;
    }
    increment(table, "paths", &1usize.into_pyobject(py)?.into_any())?;
    let means = dict_attr(table, "means")?;
    let low_means = dict_attr(table, "lo_means")?;
    let per_day = dict_attr(table, "per_day")?;
    let counts = dict_attr(table, "counts")?;
    let bins = table.getattr("bins")?;
    let fine = dict_attr(table, "fine")?;
    let ranges = table.getattr("ranges")?;
    let dscal = dict_attr(table, "dscal")?;
    let dser = dict_attr(table, "dser")?;
    let trie = dict_attr(table, "trie")?;
    let outcomes = dict_attr(table, "outcomes")?;
    for group in groups {
        increment(table, "groups", &1usize.into_pyobject(py)?.into_any())?;
        let mask = group.get_item(0)?;
        let probs = group.get_item(1)?;
        let low_probs = group.get_item(2)?;
        let cnt = if mask.is_none() {
            n
        } else {
            sum(&mask, None)?.extract::<usize>()?
        };
        let mut vals = Vec::with_capacity(skeys.len()?);
        for key in skeys.try_iter()? {
            let key = key?;
            let v = sc
                .get_item(&key)?
                .ok_or_else(|| PyValueError::new_err("scalar key missing"))?;
            let value = sum(&selected(&v, &mask)?, None)?.extract::<f64>()? / n as f64;
            vals.push(value);
            // The reference indexes its float64 s_vals ndarray here. That is
            // a strong NumPy scalar, unlike a weak Python float under NEP 50.
            let scalar = np.getattr("float64")?.call1((value,))?;
            add_to(
                &ensure(&np, &means, &key, f)?,
                &binary(&probs, "__mul__", &scalar)?,
            )?;
            add_to(
                &ensure(&np, &low_means, &key, l)?,
                &binary(&low_probs, "__mul__", &scalar)?,
            )?;
        }
        let s_vals = vals.into_pyarray(py).into_any();
        let cash = selected(&t.getattr("cash")?, &mask)?;
        let coll = selected(&t.getattr("collections")?, &mask)?;
        let fund = selected(&t.getattr("fundings")?, &mask)?;
        let outs = selected(&t.getattr("outstanding")?, &mask)?;
        let idx = np.call_method1("arange", (days,))?;
        let pet = column(&selected(&t.getattr("petition")?, &mask)?)?;
        let zero_pet = pet.call_method1("__ge__", (0,))?;
        let by_day = binary(&zero_pet, "__and__", &binary(&pet, "__le__", &idx)?)?;
        let window = binary(
            &binary(
                &zero_pet,
                "__and__",
                &binary(&idx, "__ge__", &pet.call_method1("__sub__", (90,))?)?,
            )?,
            "__and__",
            &binary(&idx, "__lt__", &pet)?,
        )?;
        let cum_due = selected(&all_due, &mask)?;
        let cum_coll = selected(&all_coll, &mask)?;
        let fs = sum(&fund, Some(0))?;
        let capacity = selected(&t.getattr("capacity")?, &mask)?;
        let facility = table.getattr("facility_cents")?;
        let backup = binary(
            &cash,
            "__add__",
            &np.call_method1("maximum", (binary(&facility, "__sub__", &capacity)?, 0))?,
        )?;
        let debt = binary(&cum_due, "__sub__", &cum_coll)?;
        let d = PyDict::new(py);
        for (key, value) in [
            ("cash", sum(&cash, Some(0))?),
            ("backup", sum(&backup, Some(0))?),
            ("collected", sum(&cum_coll, Some(0))?),
            ("due_cum", sum(&cum_due, Some(0))?),
            ("fundings", fs.clone()),
            ("drawn", cumulative(&np, &fs, None)?),
            ("collections", sum(&coll, Some(0))?),
            ("outstanding", sum(&outs, Some(0))?),
            (
                "locked",
                sum(&selected(&t.getattr("locked")?, &mask)?, Some(0))?,
            ),
            ("capacity", sum(&capacity, Some(0))?),
            ("petitioned", sum(&by_day, Some(0))?),
            (
                "frozen",
                sum(
                    &binary(
                        &by_day,
                        "__mul__",
                        &column(&selected(&t.getattr("stayed")?, &mask)?)?,
                    )?,
                    Some(0),
                )?,
            ),
            (
                "past_due",
                sum(
                    &binary(&debt, "__mul__", &by_day.call_method0("__invert__")?)?,
                    Some(0),
                )?,
            ),
            (
                "frozen_due",
                sum(&binary(&debt, "__mul__", &by_day)?, Some(0))?,
            ),
            (
                "clawback",
                sum(&binary(&coll, "__mul__", &window)?, Some(0))?,
            ),
        ] {
            d.set_item(key, value)?;
        }
        let processed = t.getattr("processed")?;
        if !processed.is_none() {
            let by_class = sum(&selected(&processed.getattr("arrears")?, &mask)?, Some(0))?;
            let names = py.import("app.analysis.core")?.getattr("ARREARS_KEYS")?;
            for (j, key) in names.try_iter()?.enumerate() {
                d.set_item(key?, by_class.get_item((PySlice::full(py), j))?)?;
            }
        }
        for (key, v) in d.iter() {
            let target = per_day
                .get_item(&key)?
                .ok_or_else(|| PyValueError::new_err("per-day key missing"))?;
            add_to(&target, &weighted(&probs, &v)?)?;
        }
        for (name, x) in [("cash", &cash), ("collected", &cum_coll)] {
            let b = bins.get_item(name)?;
            let lo = b.getattr("lo")?;
            let upper = binary(
                &lo,
                "__add__",
                &binary(&b.getattr("n")?, "__mul__", &b.getattr("width")?)?,
            )?;
            let outside = binary(
                &binary(x, "__lt__", &lo)?,
                "__or__",
                &binary(x, "__ge__", &upper)?,
            )?;
            let clipped = sum(&outside, None)?.extract::<usize>()?;
            increment(table, "clipped", &clipped.into_pyobject(py)?.into_any())?;
            let flat = py.import("app._native")?.getattr("bins_flat")?.call1((
                np.call_method1("asarray", (x, np.getattr("float64")?))?,
                lo,
                b.getattr("width")?,
                b.getattr("n")?,
                py.None(),
            ))?;
            let target = counts
                .get_item(name)?
                .ok_or_else(|| PyValueError::new_err("histogram key missing"))?;
            add_histogram(&np, &target, &probs, &flat)?;
        }
        let hr_selector = if mask.is_none() {
            PySlice::full(py).into_any()
        } else {
            mask.get_item(t.getattr("headroom_rows")?)?
        };
        let hv = t.getattr("headroom")?.get_item(&hr_selector)?;
        if hv.len()? > 0 {
            let b = bins.get_item("headroom")?;
            let months = table
                .getattr("month_of_day")?
                .get_item(t.getattr("headroom_days")?.get_item(&hr_selector)?)?;
            let flat = py.import("app._native")?.getattr("bins_flat")?.call1((
                np.call_method1("asarray", (&hv, np.getattr("float64")?))?,
                b.getattr("lo")?,
                b.getattr("width")?,
                b.getattr("n")?,
                np.call_method1("asarray", (months, np.getattr("int64")?))?,
            ))?;
            add_histogram(
                &np,
                &counts
                    .get_item("headroom")?
                    .ok_or_else(|| PyValueError::new_err("headroom histogram missing"))?,
                &probs,
                &flat,
            )?;
        }
        add_to(
            &table.getattr("hr_count")?,
            &probs.call_method1("__mul__", (hv.len()?,))?,
        )?;
        let negative = sum(&hv.call_method1("__lt__", (0,))?, None)?.extract::<f64>()?;
        add_to(
            &table.getattr("hr_negative")?,
            &probs.call_method1("__mul__", (negative,))?,
        )?;
        if let Some(first) = &first {
            let fd = selected(first, &mask)?;
            let indices =
                np.call_method1("where", (fd.call_method1("__ge__", (0,))?, &fd, days))?;
            let freq = count_bins(&np, &indices, days + 1)?.call_method1("__truediv__", (n,))?;
            add_to(&table.getattr("floor")?, &weighted(&probs, &freq)?)?;
        }
        for (name, field) in [
            ("min_cash", "min_cash"),
            ("collected", "collected"),
            ("min_headroom", "min_headroom"),
        ] {
            let mut values = selected(&t.getattr(field)?, &mask)?;
            if name == "min_headroom" {
                values = values.get_item(values.call_method1("__ne__", (i64::MAX,))?)?;
            }
            let span = ranges.get_item(name)?;
            let lo = span.get_item(0)?;
            let width = span.get_item(1)?;
            let indices = binary(&binary(&values, "__sub__", &lo)?, "__truediv__", &width)?
                .call_method1("astype", (np.getattr("int64")?,))?;
            let input = indices.cast::<PyArray1<i64>>()?.readonly();
            let mut clipped = 0usize;
            let mut sparse = BTreeMap::<i64, usize>::new();
            for &index in input.as_array() {
                if index < 0 || index >= FINE as i64 {
                    clipped += 1;
                }
                *sparse.entry(index.clamp(0, FINE as i64 - 1)).or_default() += 1;
            }
            increment(table, "clipped", &clipped.into_pyobject(py)?.into_any())?;
            let nz = sparse
                .keys()
                .copied()
                .collect::<Vec<_>>()
                .into_pyarray(py)
                .into_any();
            let frequencies = sparse
                .values()
                .map(|&count| count as f64 / n as f64)
                .collect::<Vec<_>>()
                .into_pyarray(py)
                .into_any();
            let target = fine
                .get_item(name)?
                .ok_or_else(|| PyValueError::new_err("fine histogram missing"))?;
            let at = (PySlice::full(py), &nz);
            target.set_item(
                at.clone(),
                binary(
                    &target.get_item(at)?,
                    "__add__",
                    &weighted(&probs, &frequencies)?,
                )?,
            )?;
        }
        let series = PyList::new(
            py,
            SERIES
                .iter()
                .map(|k| {
                    d.get_item(k)
                        .and_then(|v| v.ok_or_else(|| PyValueError::new_err("series missing")))
                })
                .collect::<PyResult<Vec<_>>>()?,
        )?;
        let ser = np
            .call_method1("asarray", (series,))?
            .call_method1("__truediv__", (n,))?;
        for atom in group.get_item(3)?.try_iter()? {
            let atom = atom?;
            let key = PyTuple::new(py, [atom.get_item(0)?, atom.get_item(1)?])?.into_any();
            let dp = atom.get_item(2)?;
            if !dscal.contains(&key)? {
                dscal.set_item(&key, zeros(&np, skeys.len()?)?)?;
                dser.set_item(&key, np.call_method1("zeros", ((SERIES.len(), days),))?)?;
            }
            add_to(
                &dscal
                    .get_item(&key)?
                    .ok_or_else(|| PyValueError::new_err("derivative missing"))?,
                &binary(&dp, "__mul__", &s_vals)?,
            )?;
            add_to(
                &dser
                    .get_item(&key)?
                    .ok_or_else(|| PyValueError::new_err("series derivative missing"))?,
                &binary(&dp, "__mul__", &ser)?,
            )?;
        }
        let central = probs.get_item(0)?;
        let mass = central.mul(cnt as f64 / n as f64)?;
        let pp = sum(
            &selected(
                &sc.get_item("petition_p")?
                    .ok_or_else(|| PyValueError::new_err("petition scalar missing"))?,
                &mask,
            )?,
            None,
        )?
        .extract::<f64>()?
            / n as f64;
        let cc = sum(
            &selected(
                &sc.get_item("collected")?
                    .ok_or_else(|| PyValueError::new_err("collected scalar missing"))?,
                &mask,
            )?,
            None,
        )?
        .extract::<f64>()?
            / n as f64;
        let values = np.call_method1(
            "asarray",
            (PyTuple::new(
                py,
                [mass, central.mul(pp)?, central.mul(cc)?],
            )?,),
        )?;
        for dep in 1..=TRIE_DEPTH.min(steps.len()?) {
            let prefix = steps.get_item(PySlice::new(py, 0, dep as isize, 1))?;
            add_to(&ensure(&np, &trie, &prefix, 3)?, &values)?;
        }
        let outcome_key = PyTuple::new(
            py,
            [
                verdict.clone().into_pyobject(py)?.into_any(),
                ruling.clone().into_pyobject(py)?.into_any(),
                outcome.clone(),
            ],
        )?
        .into_any();
        add_to(&ensure(&np, &outcomes, &outcome_key, 3)?, &values)?;
    }
    if !stress_row.is_none() {
        let key = stress_row
            .get_item("min_cash_p5_cents")?
            .call_method0("__neg__")?;
        let item = PyTuple::new(py, [key, table.getattr("paths")?, stress_row.clone()])?.into_any();
        keep_stress(&table.getattr("stress")?, &item)?;
    }
    Ok(())
}

fn keep_stress(heap: &Obj<'_>, item: &Obj<'_>) -> PyResult<()> {
    // _heapq is the compiled container primitive; selection and ordering are
    // controlled here, with Python tuple comparison preserving arbitrary ints.
    let primitive = heap.py().import("_heapq")?;
    if heap.len()? < STRESS_KEEP {
        primitive.call_method1("heappush", (heap, item))?;
    } else if item
        .rich_compare(&heap.get_item(0)?, CompareOp::Gt)?
        .is_truthy()?
    {
        primitive.call_method1("heapreplace", (heap, item))?;
    }
    Ok(())
}

#[pyfunction]
fn tables_merge<'py>(table: &Obj<'py>, other: &Obj<'py>) -> PyResult<Obj<'py>> {
    let np = table.py().import("numpy")?.into_any();
    // Every buffer match is checked before the first inplace update.
    for name in ["per_day", "counts", "fine"] {
        let left = dict_attr(table, name)?;
        let right = dict_attr(other, name)?;
        for (key, v) in left.iter() {
            let r = right
                .get_item(&key)?
                .ok_or_else(|| PyValueError::new_err("merged table keys disagree"))?;
            require_shape(&r, &shape(&v)?, name)?;
        }
    }
    for name in ["hr_count", "hr_negative", "floor"] {
        require_shape(&other.getattr(name)?, &shape(&table.getattr(name)?)?, name)?;
    }
    for name in ["means", "lo_means", "trie", "outcomes"] {
        let a = dict_attr(table, name)?;
        let size = match name {
            "means" => table.getattr("full")?.len()?,
            "lo_means" => table.getattr("scalar")?.len()?,
            _ => 3,
        };
        for (key, v) in dict_attr(other, name)?.iter() {
            require_shape(&v, &[size], name)?;
            if let Some(existing) = a.get_item(key)? {
                require_shape(&existing, &[size], name)?;
            }
        }
    }
    let dscal = dict_attr(table, "dscal")?;
    let dser = dict_attr(table, "dser")?;
    let other_series = dict_attr(other, "dser")?;
    let own_keys = table.getattr("skeys")?;
    let incoming_keys = other.getattr("skeys")?;
    let scalar_size = if own_keys.is_truthy()? {
        own_keys.len()?
    } else if incoming_keys.is_none() {
        0
    } else {
        incoming_keys.len()?
    };
    for (key, value) in dict_attr(other, "dscal")?.iter() {
        require_shape(&value, &[scalar_size], "merged scalar derivative")?;
        let ser = other_series
            .get_item(&key)?
            .ok_or_else(|| PyValueError::new_err("merged derivative series missing"))?;
        require_shape(
            &ser,
            &[SERIES.len(), table.getattr("days")?.extract()?],
            "merged series derivative",
        )?;
        if let Some(existing) = dscal.get_item(&key)? {
            require_shape(&existing, &[scalar_size], "scalar derivative")?;
            require_shape(
                &dser
                    .get_item(&key)?
                    .ok_or_else(|| PyValueError::new_err("derivative series missing"))?,
                &shape(&ser)?,
                "series derivative",
            )?;
        }
    }
    for name in ["means", "lo_means"] {
        let target = dict_attr(table, name)?;
        let size = table
            .getattr(if name == "means" { "full" } else { "scalar" })?
            .len()?;
        for (key, v) in dict_attr(other, name)?.iter() {
            add_to(&ensure(&np, &target, &key, size)?, &v)?;
        }
    }
    for name in ["per_day", "counts", "fine"] {
        let target = dict_attr(table, name)?;
        let source = dict_attr(other, name)?;
        for (key, v) in target.iter() {
            add_to(
                &v,
                &source
                    .get_item(key)?
                    .ok_or_else(|| PyValueError::new_err("merged key missing"))?,
            )?;
        }
    }
    increment(table, "clipped", &other.getattr("clipped")?)?;
    for name in ["hr_count", "hr_negative", "floor"] {
        add_to(&table.getattr(name)?, &other.getattr(name)?)?;
    }
    for (key, v) in dict_attr(other, "dscal")?.iter() {
        let ser = other_series
            .get_item(&key)?
            .ok_or_else(|| PyValueError::new_err("merged derivative series missing"))?;
        if let Some(at) = dscal.get_item(&key)? {
            add_to(&at, &v)?;
            add_to(
                &dser
                    .get_item(&key)?
                    .ok_or_else(|| PyValueError::new_err("derivative series missing"))?,
                &ser,
            )?;
        } else {
            dscal.set_item(&key, v.call_method0("copy")?)?;
            dser.set_item(key, ser.call_method0("copy")?)?;
        }
    }
    if !table.getattr("skeys")?.is_truthy()? {
        table.setattr("skeys", other.getattr("skeys")?)?;
    }
    for name in ["trie", "outcomes"] {
        let target = dict_attr(table, name)?;
        for (key, v) in dict_attr(other, name)?.iter() {
            add_to(&ensure(&np, &target, &key, 3)?, &v)?;
        }
    }
    let stress = table.getattr("stress")?;
    for item in other.getattr("stress")?.try_iter()? {
        let item = item?;
        let offset = 1_000_000_000_000u64
            .into_pyobject(table.py())?
            .mul(stress.len()? + 1)?;
        let tie = item.get_item(1)?.add(offset)?;
        keep_stress(
            &stress,
            &PyTuple::new(table.py(), [item.get_item(0)?, tie, item.get_item(2)?])?.into_any(),
        )?;
    }
    increment(table, "paths", &other.getattr("paths")?)?;
    increment(table, "groups", &other.getattr("groups")?)?;
    Ok(table.clone())
}

#[pyfunction]
fn tables_fine_q<'py>(
    table: &Obj<'py>,
    name: &str,
    i: isize,
    qs: &Obj<'py>,
) -> PyResult<Option<Obj<'py>>> {
    let h = table.getattr("fine")?.get_item(name)?.get_item(i)?;
    let total = sum(&h, None)?;
    if total.rich_compare(0, CompareOp::Le)?.is_truthy()? {
        return Ok(None);
    }
    let np = table.py().import("numpy")?.into_any();
    let cumulative = cumulative(&np, &h, None)?.div(&total)?;
    let span = table.getattr("ranges")?.get_item(name)?;
    let lo = span.get_item(0)?;
    let width = span.get_item(1)?;
    let out = PyList::empty(table.py());
    for q in qs.try_iter()? {
        // Retain strong NumPy scalar promotion, including longdouble thresholds.
        let comparison = cumulative.rich_compare(q?.sub(1e-12)?, CompareOp::Ge)?;
        let comparison = comparison.cast::<PyArray1<bool>>()?.readonly();
        let at = comparison.as_array().iter().position(|&on| on).unwrap_or(0);
        let middle = np.getattr("float64")?.call1((at as f64 + 0.5,))?;
        out.append(lo.add(middle.mul(&width)?)?)?;
    }
    Ok(Some(np.call_method1("asarray", (out,))?))
}

#[pyfunction]
fn tables_first_floor(table: &Obj<'_>, i: isize) -> PyResult<Py<PyDict>> {
    let py = table.py();
    let np = py.import("numpy")?.into_any();
    let days: usize = table.getattr("days")?.extract()?;
    let h = table.getattr("floor")?.get_item(i)?;
    let w = binary(&h, "__truediv__", &sum(&h, None)?)?;
    let share = sum(&w.get_item(PySlice::new(py, 0, days as isize, 1))?, None)?.extract::<f64>()?;
    let k = np
        .call_method1("searchsorted", (cumulative(&np, &w, None)?, 0.5))?
        .extract::<usize>()?;
    let out = PyDict::new(py);
    out.set_item("share", share)?;
    out.set_item("median_day", if share >= 0.5 { Some(k) } else { None })?;
    Ok(out.unbind())
}

#[pyfunction]
fn tables_override(
    table: &Obj<'_>,
    question: &str,
    dist: &Bound<'_, PyDict>,
    base: &Obj<'_>,
) -> PyResult<Py<PyDict>> {
    let py = table.py();
    let skeys = table.getattr("skeys")?;
    if skeys.is_none() {
        return Err(PyAssertionError::new_err(
            "table scalar keys have not been initialized",
        ));
    }
    let np = py.import("numpy")?.into_any();
    let means = table.getattr("means")?;
    let mut values = Vec::with_capacity(skeys.len()?);
    for key in skeys.try_iter()? {
        values.push(means.get_item(key?)?.get_item(0)?);
    }
    let mut values = np.call_method1("asarray", (PyList::new(py, values)?,))?;
    let derivatives = dict_attr(table, "dscal")?;
    for (ans, p) in dist.iter() {
        let key = (question, ans.clone());
        if let Some(d) = derivatives.get_item(key)? {
            let diff = p.sub(base.get_item(&ans)?)?;
            if shape(&d)? != shape(&values)? {
                return Err(PyValueError::new_err("scalar derivative axes disagree"));
            }
            values = values.add(diff.mul(d)?)?;
        }
    }
    let out = PyDict::new(py);
    for (key, value) in skeys
        .try_iter()?
        .zip(values.call_method0("tolist")?.try_iter()?)
    {
        out.set_item(key?, value?)?;
    }
    Ok(out.unbind())
}

fn fine_span(lo: f64, hi: f64) -> (f64, f64) {
    let lo = lo.floor();
    let width = (hi.ceil() + 1.0 - lo) / FINE as f64;
    (lo, if 1.0 > width { 1.0 } else { width })
}

#[pyfunction]
fn tables_fine(lo: &Obj<'_>, hi: &Obj<'_>) -> PyResult<(f64, f64)> {
    // _fine converts to Python float after rounding, not before. Preserve that
    // order for accepted extended-precision input scalars near whole cents.
    let np = lo.py().import("numpy")?;
    let low = np.getattr("floor")?.call1((lo,))?.extract::<f64>()?;
    let high = np.getattr("ceil")?.call1((hi,))?.extract::<f64>()?;
    let width = (high + 1.0 - low) / FINE as f64;
    Ok((low, if 1.0 > width { 1.0 } else { width }))
}

#[pyfunction]
fn tables_fine_ranges(bins: &Obj<'_>) -> PyResult<Py<PyDict>> {
    let out = PyDict::new(bins.py());
    for (name, source, zero) in [
        ("min_cash", "cash", false),
        ("collected", "collected", true),
        ("min_headroom", "headroom", false),
    ] {
        let b = bins.get_item(source)?;
        let lo = b.getattr("lo")?;
        let top = binary(
            &lo,
            "__add__",
            &binary(&b.getattr("n")?, "__mul__", &b.getattr("width")?)?,
        )?
        .call_method0("max")?
        .extract::<f64>()?;
        let low = if zero {
            0.0
        } else {
            lo.call_method0("min")?.extract::<f64>()?
        };
        out.set_item(name, fine_span(low, top))?;
    }
    Ok(out.unbind())
}

fn rounded<'py>(np: &Obj<'py>, value: &Obj<'py>) -> PyResult<Obj<'py>> {
    np.call_method1("rint", (value,))?
        .call_method1("astype", (np.getattr("int64")?,))?
        .call_method0("tolist")
}

#[pyfunction]
fn tables_daily(table: &Obj<'_>, i: isize, limit: &Obj<'_>) -> PyResult<Py<PyDict>> {
    let py = table.py();
    let np = py.import("numpy")?.into_any();
    let days: usize = table.getattr("days")?.extract()?;
    let draws: usize = table.getattr("draws")?.extract()?;
    if shape(limit)?.len() != 2 || shape(limit)?[1] != days {
        return Err(PyValueError::new_err("limit axes disagree with table days"));
    }
    let qs = py.import("app.analysis.core")?.getattr("QS")?;
    let expected = PyDict::new(py);
    for (key, value) in dict_attr(table, "per_day")?.iter() {
        expected.set_item(
            key,
            value.get_item(i)?.call_method1("__truediv__", (draws,))?,
        )?;
    }
    let bins = table.getattr("bins")?;
    let counts = table.getattr("counts")?;
    let quantiles = |name: &str| -> PyResult<Obj<'_>> {
        let b = bins.get_item(name)?;
        let h = counts
            .get_item(name)?
            .get_item(i)?
            .call_method1("reshape", ((b.getattr("lo")?.len()?, b.getattr("n")?),))?;
        let kw = PyDict::new(py);
        kw.set_item("axis", 1)?;
        kw.set_item("keepdims", true)?;
        let den = np.call_method1("maximum", (h.call_method("sum", (), Some(&kw))?, 1e-300))?;
        let normal = binary(&h, "__truediv__", &den)?;
        py.import("app._native")?
            .getattr("histogram_quantiles")?
            .call1((normal, b.getattr("lo")?, b.getattr("width")?, &qs))
    };
    let cash_q = quantiles("cash")?;
    let collected_q = quantiles("collected")?;
    if let Some(last) = tables_fine_q(table, "collected", i, &qs)? {
        collected_q.set_item((PySlice::full(py), -1), last)?;
    }
    let petition = expected
        .get_item("petitioned")?
        .ok_or_else(|| PyValueError::new_err("petitioned series missing"))?;
    let exposure = zeros(&np, days)?;
    let kw = PyDict::new(py);
    kw.set_item("out", &exposure)?;
    kw.set_item("where", petition.call_method1("__gt__", (0,))?)?;
    np.call_method(
        "divide",
        (
            expected
                .get_item("frozen")?
                .ok_or_else(|| PyValueError::new_err("frozen series missing"))?,
            &petition,
        ),
        Some(&kw),
    )?;
    let (limit_mean, limit_lower) = crate::analysis::analysis_limit_summary(limit)?;
    let out = PyDict::new(py);
    out.set_item(
        "cash_mean",
        rounded(
            &np,
            &expected
                .get_item("cash")?
                .ok_or_else(|| PyValueError::new_err("cash series missing"))?,
        )?,
    )?;
    for (key, at) in [("cash_p5", 0), ("cash_p50", 1), ("cash_p95", 2)] {
        out.set_item(key, rounded(&np, &cash_q.get_item(at)?)?)?;
    }
    out.set_item(
        "backup_liquidity_mean",
        rounded(
            &np,
            &expected
                .get_item("backup")?
                .ok_or_else(|| PyValueError::new_err("backup series missing"))?,
        )?,
    )?;
    if table.getattr("facility_cents")?.extract::<i64>()? == 0 {
        out.set_item("backup_liquidity_p5", rounded(&np, &cash_q.get_item(0)?)?)?;
    } else {
        out.set_item("backup_liquidity_p5", py.None())?;
    }
    out.set_item(
        "collected_mean",
        rounded(
            &np,
            &expected
                .get_item("collected")?
                .ok_or_else(|| PyValueError::new_err("collected series missing"))?,
        )?,
    )?;
    for (key, at) in [
        ("collected_p5", 0),
        ("collected_p50", 1),
        ("collected_p95", 2),
    ] {
        out.set_item(key, rounded(&np, &collected_q.get_item(at)?)?)?;
    }
    for (key, source) in [
        ("contractual", "due_cum"),
        ("drawn_mean", "drawn"),
        ("fundings_mean", "fundings"),
        ("collections_mean", "collections"),
        ("outstanding_mean", "outstanding"),
        ("locked_mean", "locked"),
        ("capacity_mean", "capacity"),
    ] {
        out.set_item(
            key,
            rounded(
                &np,
                &expected
                    .get_item(source)?
                    .ok_or_else(|| PyValueError::new_err("daily series missing"))?,
            )?,
        )?;
    }
    out.set_item("limit_mean", rounded(&np, &limit_mean)?)?;
    out.set_item("limit_p5", rounded(&np, &limit_lower)?)?;
    out.set_item("petition_cum_p", petition.call_method0("tolist")?)?;
    out.set_item("petition_exposure_mean", rounded(&np, &exposure)?)?;
    for (key, source) in [
        ("frozen_mean", "frozen"),
        ("frozen_due_mean", "frozen_due"),
        ("past_due_mean", "past_due"),
        ("clawback_mean", "clawback"),
    ] {
        out.set_item(
            key,
            rounded(
                &np,
                &expected
                    .get_item(source)?
                    .ok_or_else(|| PyValueError::new_err("daily series missing"))?,
            )?,
        )?;
    }
    Ok(out.unbind())
}

#[pyfunction]
fn tables_expected(table: &Obj<'_>, i: isize) -> PyResult<Py<PyDict>> {
    let out = PyDict::new(table.py());
    for (key, value) in dict_attr(table, "means")?.iter() {
        out.set_item(key, value.get_item(i)?.extract::<f64>()?)?;
    }
    Ok(out.unbind())
}

#[pyfunction]
fn tables_metrics(table: &Obj<'_>, i: isize) -> PyResult<Py<PyDict>> {
    let py = table.py();
    let expected = tables_expected(table, i)?;
    let expected = expected.bind(py);
    let value = |name: &str| -> PyResult<f64> {
        expected
            .get_item(name)?
            .ok_or_else(|| PyValueError::new_err(format!("missing scalar {name}")))?
            .extract()
    };
    let qs = py.import("app.analysis.core")?.getattr("QS")?;
    let mq = tables_fine_q(table, "min_cash", i, &qs)?;
    let hw = table.getattr("hr_count")?.get_item(i)?.extract::<f64>()?;
    let headroom = if hw > 0.0 {
        let bins = table.getattr("bins")?.get_item("headroom")?;
        let h = table
            .getattr("counts")?
            .get_item("headroom")?
            .get_item(i)?
            .call_method1(
                "reshape",
                ((bins.getattr("lo")?.len()?, bins.getattr("n")?),),
            )?;
        let hq = py
            .import("app._native")?
            .getattr("analysis_pooled_quantiles")?
            .call1((bins, h, &qs))?;
        let out = PyDict::new(py);
        for (j, key) in ["p5_cents", "p50_cents", "p95_cents"].iter().enumerate() {
            out.set_item(key, hq.get_item(j)?.extract::<f64>()?)?;
        }
        out.set_item(
            "negative_p",
            table
                .getattr("hr_negative")?
                .get_item(i)?
                .extract::<f64>()?
                / hw,
        )?;
        out.into_any()
    } else {
        py.None().into_bound(py)
    };
    let low = tables_fine_q(table, "min_headroom", i, &qs)?;
    let kq = tables_fine_q(table, "collected", i, &qs)?;
    let (mq, kq) = match (mq, kq) {
        (Some(mq), Some(kq)) => (mq, kq),
        _ => {
            return Err(PyAssertionError::new_err(
                "a setting with no probability mass",
            ))
        }
    };
    let n = table.getattr("draws")?.extract::<f64>()?;
    let due = table
        .getattr("per_day")?
        .get_item("due_cum")?
        .get_item((i, -1))?
        .extract::<f64>()?
        / n;
    let past = table
        .getattr("per_day")?
        .get_item("past_due")?
        .get_item((i, -1))?
        .extract::<f64>()?
        / n;
    let out = PyDict::new(py);
    out.set_item("due_horizon_cents", due)?;
    out.set_item("past_due_horizon_cents", past)?;
    out.set_item("frozen_due_cents", due - value("collected")? - past)?;
    out.set_item(
        "collection_rate",
        if due != 0.0 {
            Some(value("collected")? / due)
        } else {
            None
        },
    )?;
    for (key, source) in [
        ("drawn_cents", "drawn"),
        ("fees_cents", "fees"),
        ("contractual_cents", "contractual"),
        ("collected_cents", "collected"),
        ("stayed_claim_cents", "stayed"),
    ] {
        out.set_item(key, value(source)?)?;
    }
    for (j, key) in [
        "collected_p5_cents",
        "collected_p50_cents",
        "collected_p95_cents",
    ]
    .iter()
    .enumerate()
    {
        out.set_item(key, kq.get_item(j)?.extract::<f64>()?)?;
    }
    out.set_item(
        "stayed_claim_recovery",
        "unknown: stayed from the petition, recovered (if at all) after the horizon",
    )?;
    for (key, source) in [
        ("stayed_principal_cents", "stayed_principal"),
        ("preference_exposed_cents", "preference"),
        ("not_yet_due_cents", "not_yet_due"),
        ("uncollected_horizon_cents", "uncollected"),
        ("peak_outstanding_cents", "peak_outstanding"),
        ("time_weighted_outstanding_cents", "avg_outstanding"),
        ("dollar_days", "dollar_days"),
        ("lender_pv_cents", "lender_pv"),
        ("pv_fundings_cents", "pv_fundings"),
        ("pv_collections_cents", "pv_collections"),
    ] {
        out.set_item(key, value(source)?)?;
    }
    out.set_item("petition_p", 1.0f64.min(value("petition_p")?))?;
    out.set_item("headroom_at_due", headroom)?;
    for (j, key) in ["min_headroom_p5_cents", "min_headroom_p50_cents"]
        .iter()
        .enumerate()
    {
        out.set_item(
            key,
            match &low {
                Some(q) => Some(q.get_item(j)?.extract::<f64>()?),
                None => None,
            },
        )?;
    }
    out.set_item("min_cash_mean_cents", value("min_cash")?)?;
    out.set_item("min_cash_p5_cents", mq.get_item(0)?.extract::<f64>()?)?;
    out.set_item("shortfall_p", 1.0f64.min(value("shortfall_p")?))?;
    for (key, source) in [
        ("shortfall_mean_cents", "shortfall"),
        ("peak_locked_cents", "peak_locked"),
        ("peak_capacity_cents", "peak_capacity"),
        ("horizon_cash_mean_cents", "horizon_cash"),
    ] {
        out.set_item(key, value(source)?)?;
    }
    out.set_item(
        "full_collection_by_maturity_p",
        1.0f64.min(value("recovered_all")?),
    )?;
    out.set_item("uncollected_maturity_cents", value("unrecovered")?)?;
    Ok(out.unbind())
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(tables_add, m)?)?;
    m.add_function(wrap_pyfunction!(tables_merge, m)?)?;
    m.add_function(wrap_pyfunction!(tables_fine_q, m)?)?;
    m.add_function(wrap_pyfunction!(tables_first_floor, m)?)?;
    m.add_function(wrap_pyfunction!(tables_override, m)?)?;
    m.add_function(wrap_pyfunction!(tables_fine, m)?)?;
    m.add_function(wrap_pyfunction!(tables_fine_ranges, m)?)?;
    m.add_function(wrap_pyfunction!(tables_daily, m)?)?;
    m.add_function(wrap_pyfunction!(tables_metrics, m)?)?;
    m.add_function(wrap_pyfunction!(tables_expected, m)?)?;
    Ok(())
}

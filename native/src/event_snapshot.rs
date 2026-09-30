//! Independent branch snapshots and dated question-state reads.
//! Numerical comparison, petition finalization and date selection execute in Rust.
//! Python objects here are configuration/record boundaries, never oracle calls.
use crate::events::{validate_event_shape, NativeChain, BIG};
use ndarray::Array1;
use numpy::{IntoPyArray, PyArray1, PyArray2, PyArrayDyn, PyArrayMethods, PyUntypedArray};
use pyo3::exceptions::{PyMemoryError, PyNotImplementedError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyInt, PyList, PySet, PyString, PyTuple};
use std::collections::{BTreeMap, BTreeSet};

type Obj = Py<PyAny>;
type Out = PyResult<Obj>;

fn reserved<T>(n: usize) -> PyResult<Vec<T>> {
    let mut out = Vec::new();
    out.try_reserve_exact(n)
        .map_err(|_| PyMemoryError::new_err("unable to allocate native snapshot buffer"))?;
    Ok(out)
}

fn filled<T: Clone>(n: usize, value: T) -> PyResult<Vec<T>> {
    let mut out = reserved(n)?;
    out.resize(n, value);
    Ok(out)
}

const SHARED: &[&str] = &[
    "d",
    "s",
    "m",
    "dr",
    "basis",
    "sens",
    "fin",
    "rows",
    "bookings",
    "merton",
    // The facade binds these immutable numerical inputs once per row subset.
    "_kernel_line",
    "_ops_inflow",
    "_ops_outflow",
    "_ops_total",
];
const REPLACED: &[&str] = &[
    "_cum",
    "_tau",
    "_out",
    "share_price",
    "_atm",
    "_atm_cum",
    "_atm_csold",
    "_atm_sold",
];
const KEYS: &[&str] = &["_price_key", "_stay_owed"];
const KEEP: &[&str] = &[
    "d",
    "s",
    "m",
    "sens",
    "fin",
    "bookings",
    "merton",
    "_atm_memo",
    "_atm_cols",
];
const DROP: &[&str] = &["_tau", "_out", "_offer_memo", "_shares_memo", "_ev_own"];
const DATED: &[&str] = &[
    "suspended",
    "resolved",
    "release_at",
    "adverse_from",
    "adverse_until",
    "early_registration",
    "pending_levy",
    "delisted",
    "stayed_from",
];
const UNSEEN: &[&str] = &[
    "_cum",
    "_tau",
    "_out",
    "_keys",
    "_av",
    "_hd",
    "_cv",
    "_stay_cv",
    "_restaying",
    "_atm_memo",
    "_atm_cols",
    "_atm_v",
    "_eq_v",
    "_offer_memo",
    "_shares_memo",
    "_grp",
    "settle_offer",
    "stay_offer",
    "raise_offer",
    "reads",
    "rec",
    "grec",
    "late",
    "wctx",
    "_atm",
    "_atm_cum",
    "_atm_sold",
    "_atm_nsold",
    "taken",
    "_booked_to",
    "_last_node",
    "ev",
    "marks",
    "waiting",
    "takes",
    "writs",
    "coupons",
    "floor_days",
    "stays",
    "pet_cause",
    "collateral_required",
    "lock_amount",
    "levied",
    "q1",
    "offerings",
    "_at",
    "notes_due_how",
    "appealed",
    "_offers",
    "lock_day",
    "hearing_requested",
    "_vfired",
    "_price_v",
    "_price_key",
    "_ev_own",
];
const KINDS: &[&str] = &[
    "inflow",
    "levy",
    "reduction",
    "settlement",
    "notes_interest",
    "judgment",
];
const TRIGGERS: &[&str] = &[
    "judgment_default_entered",
    "judgment_default_ruling",
    "appeal_deadline",
    "coupon",
    "listing_deadline",
    "repurchase_due",
    "holders_petition_earliest",
];

fn arr(py: Python<'_>, a: Array1<i64>) -> Obj {
    a.into_pyarray(py).into_any().unbind()
}
fn string(py: Python<'_>, s: &str) -> Obj {
    PyString::new(py, s).into_any().unbind()
}
fn dict_keys(v: &Bound<'_, PyDict>) -> PyResult<BTreeSet<String>> {
    Ok(v.keys().extract::<Vec<String>>()?.into_iter().collect())
}
fn is_array(v: &Bound<'_, PyAny>) -> bool {
    v.cast::<PyUntypedArray>().is_ok()
}
fn shape(v: &Bound<'_, PyAny>) -> PyResult<Vec<usize>> {
    v.getattr("shape")?.extract()
}
fn items<'py>(v: &Bound<'py, PyAny>) -> PyResult<Vec<Bound<'py, PyAny>>> {
    v.try_iter()?.collect()
}

/// Copy mutable record containers and arrays; immutable configuration stays shared.
pub(crate) fn copied<'py>(py: Python<'py>, v: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    if is_array(v) {
        return v.call_method0("copy");
    }
    if let Ok(d) = v.cast::<PyDict>() {
        let out = PyDict::new(py);
        for (k, x) in d.iter() {
            out.set_item(k, copied(py, &x)?)?;
        }
        return Ok(out.into_any());
    }
    if let Ok(l) = v.cast::<PyList>() {
        let out = PyList::empty(py);
        for x in l.iter() {
            out.append(copied(py, &x)?)?;
        }
        return Ok(out.into_any());
    }
    if let Ok(t) = v.cast::<PyTuple>() {
        return Ok(PyTuple::new(
            py,
            t.iter()
                .map(|x| copied(py, &x))
                .collect::<PyResult<Vec<_>>>()?,
        )?
        .into_any());
    }
    Ok(v.clone())
}

fn event_copy<'py>(
    py: Python<'py>,
    v: &Bound<'py, PyAny>,
    share: bool,
) -> PyResult<Bound<'py, PyAny>> {
    let fields = [
        "cash", "lock", "capacity", "petition", "kinds", "incurred", "proceeds",
    ];
    let mut args = Vec::with_capacity(fields.len());
    for k in fields {
        let x = v.getattr(k)?;
        let out = if share && matches!(k, "cash" | "lock" | "capacity" | "petition") {
            x
        } else if share && matches!(k, "kinds" | "incurred") {
            x.cast::<PyDict>()?.copy()?.into_any()
        } else {
            copied(py, &x)?
        };
        args.push(out);
    }
    v.get_type().call1(PyTuple::new(py, args)?)
}

fn readonly(v: &Bound<'_, PyAny>) -> PyResult<()> {
    if is_array(v) {
        v.getattr("flags")?.setattr("writeable", false)?;
    }
    Ok(())
}

fn clone_state(c: &NativeChain, py: Python<'_>) -> Out {
    let state = c.state.bind(py);
    let ev = c.get(py, "ev")?;
    for k in ["cash", "lock", "capacity", "petition"] {
        readonly(&ev.getattr(k)?)?;
    }
    for k in ["kinds", "incurred"] {
        for (_, a) in ev.getattr(k)?.cast::<PyDict>()?.iter() {
            readonly(&a)?;
        }
    }
    for k in REPLACED {
        if let Some(a) = state.get_item(k)? {
            readonly(&a)?;
        }
    }
    for l in items(&c.get(py, "rec")?)? {
        for a in items(&l)? {
            readonly(&a)?;
        }
    }
    for (_, e) in c.get(py, "late")?.cast::<PyDict>()?.iter() {
        for (_, a) in e.cast::<PyDict>()?.iter() {
            readonly(&a)?;
        }
    }
    let out = PyDict::new(py);
    for (k, v) in state.iter() {
        let key: String = k.extract()?;
        if matches!(key.as_str(), "ev" | "_ev_own" | "_native_chain") {
            continue;
        }
        let value = if SHARED.contains(&key.as_str())
            || REPLACED.contains(&key.as_str())
            || KEYS.contains(&key.as_str())
        {
            v
        } else if key == "rec" {
            PyTuple::new(
                py,
                items(&v)?
                    .into_iter()
                    .map(|x| PyList::new(py, items(&x)?).map(Bound::into_any))
                    .collect::<PyResult<Vec<_>>>()?,
            )?
            .into_any()
        } else if key == "late" {
            let d = PyDict::new(py);
            for (i, x) in v.cast::<PyDict>()?.iter() {
                d.set_item(i, x.cast::<PyDict>()?.copy()?)?;
            }
            d.into_any()
        } else if matches!(key.as_str(), "_hd" | "_keys") {
            v.cast::<PyDict>()?.copy()?.into_any()
        } else {
            copied(py, &v)?
        };
        out.set_item(k, value)?;
    }
    out.set_item("ev", event_copy(py, &ev, true)?)?;
    state.set_item("_ev_own", PySet::empty(py)?)?;
    out.set_item("_ev_own", PySet::empty(py)?)?;
    Ok(out.into_any().unbind())
}

fn cut<'py>(
    py: Python<'py>,
    v: &Bound<'py, PyAny>,
    sel: &Bound<'py, PyAny>,
    n: usize,
) -> PyResult<Bound<'py, PyAny>> {
    if is_array(v) {
        let sh = shape(v)?;
        return if !sh.is_empty() && sh[0] == n {
            v.get_item(sel)
        } else {
            Ok(v.clone())
        };
    }
    if let Ok(d) = v.cast::<PyDict>() {
        let out = PyDict::new(py);
        for (k, x) in d.iter() {
            out.set_item(k, cut(py, &x, sel, n)?)?;
        }
        return Ok(out.into_any());
    }
    if let Ok(l) = v.cast::<PyList>() {
        let out = PyList::empty(py);
        for x in l.iter() {
            out.append(cut(py, &x, sel, n)?)?;
        }
        return Ok(out.into_any());
    }
    if let Ok(t) = v.cast::<PyTuple>() {
        return Ok(PyTuple::new(
            py,
            t.iter()
                .map(|x| cut(py, &x, sel, n))
                .collect::<PyResult<Vec<_>>>()?,
        )?
        .into_any());
    }
    Ok(v.clone())
}

fn sliced(c: &NativeChain, py: Python<'_>, sel: &Bound<'_, PyAny>, dr: &Bound<'_, PyAny>) -> Out {
    let n = c.n(py)?;
    let rows = sel.len()?;
    validate_event_shape(rows, c.days(py)?)?;
    let mut indices = reserved(rows)?;
    indices.extend((0..rows).map(|r| r as i64));
    let state = c.state.bind(py);
    let out = PyDict::new(py);
    for (k, v) in state.iter() {
        let key: String = k.extract()?;
        if DROP.contains(&key.as_str()) || key == "_native_chain" {
            continue;
        }
        let x = if matches!(key.as_str(), "_cum" | "_price_key") {
            py.None().into_bound(py)
        } else if matches!(key.as_str(), "_keys" | "_hd") {
            PyDict::new(py).into_any()
        } else if KEEP.contains(&key.as_str()) {
            v
        } else if key == "ev" {
            let args = [
                "cash", "lock", "capacity", "petition", "kinds", "incurred", "proceeds",
            ]
            .iter()
            .map(|name| cut(py, &v.getattr(*name)?, sel, n))
            .collect::<PyResult<Vec<_>>>()?;
            v.get_type().call1(PyTuple::new(py, args)?)?
        } else {
            cut(py, &v, sel, n)?
        };
        out.set_item(k, x)?;
    }
    out.set_item("dr", dr)?;
    out.set_item("basis", dr.getattr("basis")?)?;
    out.set_item("n", rows)?;
    out.set_item("rows", Array1::from_vec(indices).into_pyarray(py))?;
    out.set_item("_ev_own", PySet::empty(py)?)?;
    // These are immutable inputs prepared at the Python configuration boundary,
    // and must match the new subset even for internal native sliced callers.
    let basis = dr.getattr("basis")?;
    if !basis.is_none() {
        let line = basis.getattr("line")?;
        if !line.is_none() {
            let kernel = py
                .import("app.analysis.engine")?
                .getattr("_kernel_line")?
                .call1((&line,))?;
            out.set_item("_kernel_line", kernel)?;
            let ops = line.getattr("ops")?;
            for (key, source) in [
                ("_ops_inflow", "inflow"),
                ("_ops_outflow", "outflow"),
                ("_ops_total", "total"),
            ] {
                out.set_item(key, ops.getattr(source)?)?;
            }
        }
    }
    let new = NativeChain {
        state: out.clone().unbind(),
    };
    let old_key = c.call0(py, "_owed_key")?;
    if let Some(k) = state.get_item("_stay_owed")? {
        out.set_item(
            "_stay_owed",
            if same(&k, old_key.bind(py))? {
                new.call0(py, "_owed_key")?
            } else {
                py.None()
            },
        )?;
    }
    if let Some(k) = state.get_item("_price_key")? {
        if !k.is_none() && same(&k, old_key.bind(py))? {
            out.set_item("_price_key", new.call0(py, "_owed_key")?)?;
        }
    }
    Ok(out.into_any().unbind())
}

fn same(a: &Bound<'_, PyAny>, b: &Bound<'_, PyAny>) -> PyResult<bool> {
    if is_array(a) || is_array(b) {
        if !is_array(a)
            || !is_array(b)
            || shape(a)? != shape(b)?
            || !a.getattr("dtype")?.eq(b.getattr("dtype")?)?
        {
            return Ok(false);
        }
        macro_rules! compare {
            ($t:ty) => {
                if let (Ok(x), Ok(y)) = (a.cast::<PyArrayDyn<$t>>(), b.cast::<PyArrayDyn<$t>>()) {
                    let x = x.readonly();
                    let y = y.readonly();
                    return Ok(x
                        .as_array()
                        .iter()
                        .zip(y.as_array().iter())
                        .all(|(p, q)| p == q));
                }
            };
        }
        compare!(i64);
        compare!(i32);
        compare!(i16);
        compare!(i8);
        compare!(u64);
        compare!(u32);
        compare!(u16);
        compare!(u8);
        compare!(f64);
        compare!(f32);
        compare!(bool);
        return same(&a.call_method0("tolist")?, &b.call_method0("tolist")?);
    }
    if let (Ok(x), Ok(y)) = (a.cast::<PyDict>(), b.cast::<PyDict>()) {
        if x.len() != y.len() {
            return Ok(false);
        }
        for (k, v) in x.iter() {
            let Some(w) = y.get_item(&k)? else {
                return Ok(false);
            };
            if !same(&v, &w)? {
                return Ok(false);
            }
        }
        return Ok(true);
    }
    if a.cast::<PyList>().is_ok() || a.cast::<PyTuple>().is_ok() {
        if !a.get_type().is(b.get_type()) || a.len()? != b.len()? {
            return Ok(false);
        }
        for (x, y) in items(a)?.into_iter().zip(items(b)?) {
            if !same(&x, &y)? {
                return Ok(false);
            }
        }
        return Ok(true);
    }
    Ok(a.get_type().is(b.get_type()) && a.eq(b)?)
}

fn row_diff(a: &Bound<'_, PyAny>, b: &Bound<'_, PyAny>, n: usize) -> PyResult<Vec<bool>> {
    if is_array(a) && is_array(b) && shape(a)? == vec![n] && shape(b)? == vec![n] {
        macro_rules! compare {
            ($t:ty) => {
                if let (Ok(x), Ok(y)) = (a.cast::<PyArray1<$t>>(), b.cast::<PyArray1<$t>>()) {
                    let x = x.readonly();
                    let y = y.readonly();
                    return Ok(x
                        .as_array()
                        .iter()
                        .zip(y.as_array().iter())
                        .map(|(p, q)| p != q)
                        .collect());
                }
            };
        }
        compare!(i64);
        compare!(i32);
        compare!(i8);
        compare!(f64);
        compare!(bool);
        return (0..n).map(|r| a.get_item(r)?.ne(b.get_item(r)?)).collect();
    }
    filled(n, !same(a, b)?)
}
fn differs(a: &Bound<'_, PyDict>, b: &Bound<'_, PyDict>, n: usize) -> PyResult<Vec<bool>> {
    let mut out = filled(n, false)?;
    for k in dict_keys(a)?.union(&dict_keys(b)?) {
        let va = a
            .get_item(k)?
            .unwrap_or_else(|| a.py().None().into_bound(a.py()));
        let vb = b
            .get_item(k)?
            .unwrap_or_else(|| b.py().None().into_bound(b.py()));
        for (x, y) in out.iter_mut().zip(row_diff(&va, &vb, n)?) {
            *x |= y;
        }
    }
    Ok(out)
}
fn date_vector(
    c: &NativeChain,
    py: Python<'_>,
    a: Option<&Bound<'_, PyAny>>,
) -> PyResult<Array1<i64>> {
    let mut v = if let Some(a) = a.filter(|x| !x.is_none()) {
        c.per_draw(py, a)?
    } else {
        Array1::from(filled(c.n(py)?, BIG)?)
    };
    for x in &mut v {
        if *x < 0 {
            *x = BIG;
        }
    }
    Ok(v)
}
fn dated(
    c: &NativeChain,
    py: Python<'_>,
    out: &mut [i64],
    a: Option<&Bound<'_, PyAny>>,
    b: Option<&Bound<'_, PyAny>>,
) -> PyResult<()> {
    let a = date_vector(c, py, a)?;
    let b = date_vector(c, py, b)?;
    for ((x, &a), &b) in out.iter_mut().zip(&a).zip(&b) {
        if a != b {
            *x = (*x).min(a.min(b));
        }
    }
    Ok(())
}
fn plain(out: &mut [i64], a: &Bound<'_, PyAny>, b: &Bound<'_, PyAny>) -> PyResult<()> {
    let n = out.len();
    for (x, ne) in out.iter_mut().zip(row_diff(a, b, n)?) {
        if ne {
            *x = 0;
        }
    }
    Ok(())
}
fn cols(
    out: &mut [i64],
    a: Option<&Bound<'_, PyAny>>,
    b: Option<&Bound<'_, PyAny>>,
    days: usize,
) -> PyResult<()> {
    let ar = a
        .map(|x| x.cast::<PyArray2<i64>>().map(|v| v.readonly()))
        .transpose()?;
    let br = b
        .map(|x| x.cast::<PyArray2<i64>>().map(|v| v.readonly()))
        .transpose()?;
    if ar
        .as_ref()
        .is_some_and(|x| x.as_array().dim() != (out.len(), days))
        || br
            .as_ref()
            .is_some_and(|x| x.as_array().dim() != (out.len(), days))
    {
        return Err(PyValueError::new_err("divergence column shapes differ"));
    }
    for (r, x) in out.iter_mut().enumerate() {
        for d in 0..days.min((*x).max(0) as usize) {
            let a = ar.as_ref().map_or(0, |a| a.as_array()[[r, d]]);
            let b = br.as_ref().map_or(0, |b| b.as_array()[[r, d]]);
            if a != b {
                *x = d as i64;
                break;
            }
        }
    }
    Ok(())
}

fn divergence(
    c: &NativeChain,
    py: Python<'_>,
    other: &Bound<'_, PyAny>,
    wait: Option<usize>,
) -> Out {
    let state = if let Ok(d) = other.cast::<PyDict>() {
        d.clone()
    } else {
        other.getattr("__dict__")?.cast_into::<PyDict>()?
    };
    let b = NativeChain {
        state: state.clone().unbind(),
    };
    let n = c.n(py)?;
    let days = c.days(py)?;
    if b.n(py)? != n || b.days(py)? != days {
        return Err(PyValueError::new_err(
            "divergence chains have different draw dimensions",
        ));
    }
    let mut out = filled(n, BIG)?;
    let ea = c.get(py, "ev")?;
    let eb = b.get(py, "ev")?;
    for k in ["cash", "lock", "capacity"] {
        cols(&mut out, Some(&ea.getattr(k)?), Some(&eb.getattr(k)?), days)?;
    }
    let ak = ea.getattr("kinds")?.cast_into::<PyDict>()?;
    let bk = eb.getattr("kinds")?.cast_into::<PyDict>()?;
    for k in dict_keys(&ak)?.union(&dict_keys(&bk)?) {
        cols(
            &mut out,
            ak.get_item(k)?.as_ref(),
            bk.get_item(k)?.as_ref(),
            days,
        )?;
    }
    let pa = ea.getattr("petition")?;
    let pb = eb.getattr("petition")?;
    dated(c, py, &mut out, Some(&pa), Some(&pb))?;
    let ai = ea.getattr("incurred")?.cast_into::<PyDict>()?;
    let bi = eb.getattr("incurred")?.cast_into::<PyDict>()?;
    for k in dict_keys(&ai)?.union(&dict_keys(&bi)?) {
        dated(
            c,
            py,
            &mut out,
            ai.get_item(k)?.as_ref(),
            bi.get_item(k)?.as_ref(),
        )?;
    }
    plain(&mut out, &ea.getattr("proceeds")?, &eb.getattr("proceeds")?)?;
    let da = date_vector(c, py, Some(&pa))?;
    let db = date_vector(c, py, Some(&pb))?;
    let ne = row_diff(&c.get(py, "pet_cause")?, &b.get(py, "pet_cause")?, n)?;
    for r in 0..n {
        if ne[r] {
            out[r] = out[r].min(da[r].min(db[r]));
        }
    }
    for name in ["marks", "floor_days"] {
        let a = c.get(py, name)?.cast_into::<PyDict>()?;
        let z = b.get(py, name)?.cast_into::<PyDict>()?;
        let keys = PySet::empty(py)?;
        for key in a.keys().iter().chain(z.keys().iter()) {
            keys.add(key)?;
        }
        for k in keys.iter() {
            dated(
                c,
                py,
                &mut out,
                a.get_item(&k)?.as_ref(),
                z.get_item(&k)?.as_ref(),
            )?;
        }
    }
    if c.flag(py, "appealed")? != b.flag(py, "appealed")? {
        let ad = c.a1(py, "AD")?;
        let f = c.a1(py, "F")?;
        for r in 0..n {
            if ad[r] >= 0 {
                out[r] = out[r].min(f[r].max(0));
            }
        }
    }
    let ao = items(&c.get(py, "_offers")?)?;
    let bo = items(&b.get(py, "_offers")?)?;
    for i in 0..ao.len().max(bo.len()) {
        let a = ao.get(i);
        let z = bo.get(i);
        let ia = a.map(|x| x.get_item("init")).transpose()?;
        let ib = z.map(|x| x.get_item("init")).transpose()?;
        let da = date_vector(c, py, ia.as_ref())?;
        let db = date_vector(c, py, ib.as_ref())?;
        let ne = match (a, z) {
            (Some(a), Some(b)) => differs(a.cast::<PyDict>()?, b.cast::<PyDict>()?, n)?,
            _ => filled(n, true)?,
        };
        for r in 0..n {
            if ne[r] {
                out[r] = out[r].min(da[r].min(db[r]));
            }
        }
    }
    for name in ["takes", "writs"] {
        let aa = items(&c.get(py, name)?)?;
        let bb = items(&b.get(py, name)?)?;
        for i in 0..aa.len().max(bb.len()) {
            let a = aa.get(i);
            let z = bb.get(i);
            let da = a.map(|v| v.get_item(0)).transpose()?;
            let db = z.map(|v| v.get_item(0)).transpose()?;
            let da = date_vector(c, py, da.as_ref())?;
            let db = date_vector(c, py, db.as_ref())?;
            let xa = a
                .map(|v| v.get_item(1))
                .transpose()?
                .unwrap_or_else(|| PyInt::new(py, 0).into_any());
            let xb = z
                .map(|v| v.get_item(1))
                .transpose()?
                .unwrap_or_else(|| PyInt::new(py, 0).into_any());
            let va = c.per_draw(py, &xa)?;
            let vb = c.per_draw(py, &xb)?;
            for r in 0..n {
                if da[r] != db[r] || va[r] != vb[r] {
                    out[r] = out[r].min(da[r].min(db[r]));
                }
            }
        }
    }
    let aa = items(&c.get(py, "coupons")?)?;
    let bb = items(&b.get(py, "coupons")?)?;
    for i in 0..aa.len().max(bb.len()) {
        let a = aa.get(i);
        let b = bb.get(i);
        let da = a.map(|v| v.get_item(0)?.extract::<i64>()).transpose()?;
        let db = b.map(|v| v.get_item(0)?.extract::<i64>()).transpose()?;
        if da != db {
            let day = da
                .into_iter()
                .chain(db)
                .min()
                .ok_or_else(|| PyValueError::new_err("missing coupon date"))?;
            for x in &mut out {
                *x = (*x).min(day);
            }
        } else if let (Some(a), Some(b), Some(day)) = (a, b, da) {
            let xa = a.get_item(1)?;
            let xb = b.get_item(1)?;
            let va = c.per_draw(py, &xa)?;
            let vb = c.per_draw(py, &xb)?;
            let pa = a
                .get_item(2)?
                .call_method0("tolist")?
                .extract::<Vec<bool>>()?;
            let pb = b
                .get_item(2)?
                .call_method0("tolist")?
                .extract::<Vec<bool>>()?;
            for r in 0..n {
                if va[r] != vb[r] || pa[r] != pb[r] {
                    out[r] = out[r].min(day);
                }
            }
        }
    }
    let sa = c.get(py, "stays")?.cast_into::<PyDict>()?;
    let sb = b.get(py, "stays")?.cast_into::<PyDict>()?;
    let si: BTreeSet<i64> = sa.keys().extract::<Vec<i64>>()?.into_iter().collect();
    let sj: BTreeSet<i64> = sb.keys().extract::<Vec<i64>>()?.into_iter().collect();
    for i in si.union(&sj) {
        let a = sa.get_item(i)?;
        let b = sb.get_item(i)?;
        let da = a.as_ref().map(|v| v.get_item("approval")).transpose()?;
        let db = b.as_ref().map(|v| v.get_item("approval")).transpose()?;
        let da = date_vector(c, py, da.as_ref())?;
        let db = date_vector(c, py, db.as_ref())?;
        if let (Some(a), Some(b)) = (a, b) {
            let ta = PyDict::new(py);
            let tb = PyDict::new(py);
            for k in ["approval", "approved", "stayed_from", "mark"] {
                ta.set_item(k, a.get_item(k)?)?;
                tb.set_item(k, b.get_item(k)?)?;
            }
            let ne = differs(&ta, &tb, n)?;
            for r in 0..n {
                if ne[r] {
                    out[r] = out[r].min(da[r].min(db[r]));
                }
            }
            let a = a.cast::<PyDict>()?;
            let b = b.cast::<PyDict>()?;
            let la = a.get_item("lock")?;
            let lb = b.get_item("lock")?;
            match (la, lb) {
                (Some(a), Some(b)) => {
                    let ne = row_diff(&a, &b, n)?;
                    for r in 0..n {
                        if ne[r] {
                            out[r] = out[r].min(da[r].min(db[r]));
                        }
                    }
                }
                (None, None) => {}
                _ => {
                    for r in 0..n {
                        out[r] = out[r].min(da[r].min(db[r]));
                    }
                }
            }
            if a.contains("lock")? && b.contains("lock")? {
                let ra = a
                    .get_item("rel")?
                    .ok_or_else(|| PyValueError::new_err("stay missing rel"))?;
                let rb = b
                    .get_item("rel")?
                    .ok_or_else(|| PyValueError::new_err("stay missing rel"))?;
                let ha = a
                    .get_item("held")?
                    .ok_or_else(|| PyValueError::new_err("stay missing held"))?;
                let hb = b
                    .get_item("held")?
                    .ok_or_else(|| PyValueError::new_err("stay missing held"))?;
                let nr = row_diff(&ra, &rb, n)?;
                let nh = row_diff(&ha, &hb, n)?;
                let ra = date_vector(c, py, Some(&ra))?;
                let rb = date_vector(c, py, Some(&rb))?;
                for r in 0..n {
                    if nr[r] || nh[r] {
                        out[r] = out[r].min(ra[r].min(rb[r]));
                    }
                }
            }
        } else {
            for r in 0..n {
                out[r] = out[r].min(da[r].min(db[r]));
            }
        }
    }
    let mut wa = BTreeMap::new();
    let mut wb = BTreeMap::new();
    for w in items(&c.get(py, "waiting")?)? {
        wa.insert(w.get_item(0)?.extract::<usize>()?, w);
    }
    for w in items(&b.get(py, "waiting")?)? {
        wb.insert(w.get_item(0)?.extract::<usize>()?, w);
    }
    let mut keys = BTreeSet::new();
    keys.extend(wa.keys().copied());
    keys.extend(wb.keys().copied());
    for i in keys {
        match (wa.get(&i), wb.get(&i)) {
            (Some(a), Some(b))
                if a.get_item(1)?.eq(b.get_item(1)?)?
                    && a.get_item(4)?.eq(b.get_item(4)?)?
                    && (a.get_item(2)?.eq(b.get_item(2)?)? || Some(i) == wait) =>
            {
                plain(&mut out, &a.get_item(3)?, &b.get_item(3)?)?
            }
            _ => out.fill(0),
        }
    }
    c.call0(py, "_reprice")?;
    b.call0(py, "_reprice")?;
    let none = py.None().into_bound(py);
    plain(
        &mut out,
        &c.opt(py, "share_price")?.unwrap_or_else(|| none.clone()),
        &b.opt(py, "share_price")?.unwrap_or_else(|| none.clone()),
    )?;
    for k in dict_keys(c.state.bind(py))?.union(&dict_keys(b.state.bind(py))?) {
        if SHARED.contains(&k.as_str())
            || UNSEEN.contains(&k.as_str())
            || matches!(k.as_str(), "share_price" | "_native_chain")
        {
            continue;
        }
        let a = c.opt(py, k)?;
        let z = b.opt(py, k)?;
        if DATED.contains(&k.as_str()) {
            dated(c, py, &mut out, a.as_ref(), z.as_ref())?;
        } else {
            plain(
                &mut out,
                &a.unwrap_or_else(|| none.clone()),
                &z.unwrap_or_else(|| none.clone()),
            )?;
        }
    }
    for x in &mut out {
        if *x >= days as i64 {
            *x = BIG;
        }
    }
    Ok(arr(py, Array1::from(out)))
}

fn inside(v: &mut Array1<i64>, days: usize) {
    for x in v {
        if *x < 0 || *x >= days as i64 {
            *x = BIG;
        }
    }
}
fn triggers(c: &NativeChain, py: Python<'_>) -> Out {
    let n = c.n(py)?;
    let days = c.days(py)?;
    let out = PyDict::new(py);
    for k in TRIGGERS {
        out.set_item(k, Array1::from(filled(n, BIG)?).into_pyarray(py))?;
    }
    let f = c.get(py, "fin")?;
    if !c.get(py, "d")?.is_none() {
        let mut ad = c.a1(py, "AD")?;
        inside(&mut ad, days);
        out.set_item("appeal_deadline", ad.into_pyarray(py))?;
        if !f.is_none() && f.getattr("judgment_default_days")?.is_truthy()? {
            for (k, ctx) in [
                ("judgment_default_entered", "I1"),
                ("judgment_default_ruling", "post"),
            ] {
                let got = c.invoke_args(py, "judgment_default", vec![string(py, ctx)])?;
                let got = got.bind(py);
                let mut ripe = c.per_draw(py, &got.get_item(0)?)?;
                let cond = got.get_item(1)?.cast_into::<PyArray1<bool>>()?;
                let cond = cond.readonly();
                for (v, &ok) in ripe.iter_mut().zip(cond.as_array()) {
                    if !ok {
                        *v = BIG;
                    }
                }
                inside(&mut ripe, days);
                out.set_item(k, ripe.into_pyarray(py))?;
            }
        }
    }
    if !f.is_none() {
        let dates = items(&f.getattr("interest_dates")?)?;
        if f.getattr("coupon_cents")?.is_truthy()? && !dates.is_empty() {
            let next = py
                .import("app.finance.calendar")?
                .getattr("next_business_day")?;
            let mut pay = i64::MAX;
            for d in dates {
                pay = pay.min(c.ix(py, &next.call1((d,))?)?);
            }
            let mut a = Array1::from(filled(n, pay)?);
            inside(&mut a, days);
            out.set_item("coupon", a.into_pyarray(py))?;
            for coupon in items(&c.get(py, "coupons")?)? {
                if coupon.get_item(0)?.extract::<i64>()? == pay
                    && c.call0(py, "_priced_coupon")?.bind(py).is_truthy()?
                {
                    out.set_item(
                        "coupon_cash",
                        c.per_draw(py, &coupon.get_item(1)?)?.into_pyarray(py),
                    )?;
                    break;
                }
            }
        }
        let listing = f.getattr("listing_deadline")?;
        if !listing.is_none() {
            let mut a = Array1::from(filled(n, c.ix(py, &listing)?)?);
            inside(&mut a, days);
            out.set_item("listing_deadline", a.into_pyarray(py))?;
        }
        let due = c.get(py, "marks")?.get_item("notes_due")?;
        let due = c.per_draw(py, &due)?;
        if f.getattr("kind")?.extract::<String>()? == "convertible_notes"
            && due.iter().any(|&d| d < BIG)
        {
            let route = c.call0(py, "holder_route_days")?;
            let route = c.per_draw(py, route.bind(py))?;
            let mut a = Array1::from_iter(due.iter().zip(&route).map(|(&d, &r)| {
                if d < BIG {
                    d.wrapping_add(r)
                } else {
                    BIG
                }
            }));
            inside(&mut a, days);
            out.set_item("holders_petition_earliest", a.into_pyarray(py))?;
        }
        let delisted = c.a1(py, "delisted")?;
        if f.getattr("repurchase_business_days")?.is_truthy()?
            && delisted.iter().any(|&d| d < days as i64)
        {
            let sel: Vec<usize> = delisted
                .iter()
                .enumerate()
                .filter_map(|(r, &d)| (d < days as i64).then_some(r))
                .collect();
            let got = c.invoke_args(
                py,
                "repurchase_day",
                vec![arr(py, Array1::from_iter(sel.iter().map(|&r| delisted[r])))],
            )?;
            let a = got.bind(py).cast::<PyArray1<i64>>()?.readonly();
            let a = a.as_array();
            let mut rep = Array1::from(filled(n, BIG)?);
            for (&r, &d) in sel.iter().zip(a.iter()) {
                rep[r] = d;
            }
            inside(&mut rep, days);
            out.set_item("repurchase_due", rep.into_pyarray(py))?;
        }
    }
    Ok(out.into_any().unbind())
}

fn c_read(c: &NativeChain, py: Python<'_>, name: &str, args: Vec<Obj>) -> Out {
    if let Some(v) = c.opt(py, name)? {
        return Ok(v.unbind());
    }
    // Inspect the interface declaration only; never execute a reference method.
    // An absent native implementation of a declared method remains a hard error.
    if !py
        .import("app.analysis.events")?
        .getattr("PythonChain")?
        .hasattr(name)?
    {
        return Err(PyNotImplementedError::new_err(format!(
            "Chain.{name} (step-9 interface, not on this branch)"
        )));
    }
    let args = if name == "nonpayment_day" {
        vec![]
    } else {
        args
    };
    match c.invoke_args(py, name, args) {
        Err(e) if e.is_instance_of::<PyNotImplementedError>(py) => Err(e),
        other => other,
    }
}
fn situation(c: &NativeChain, py: Python<'_>, day: &Bound<'_, PyAny>) -> Out {
    let n = c.n(py)?;
    let days = c.days(py)?;
    if days == 0 {
        return Err(PyValueError::new_err(
            "question snapshot requires a nonempty horizon",
        ));
    }
    let d = c.per_draw(py, day)?;
    let day_input = arr(py, d.clone());
    let t = Array1::from_iter(d.iter().map(|&d| d.clamp(0, days as i64 - 1)));
    let out = PyDict::new(py);
    let cash = c.invoke_args(py, "cash_at", vec![arr(py, t)])?;
    let cash = c.per_draw(py, cash.bind(py))?;
    out.set_item(
        "cash_end",
        Array1::from_iter(cash.iter().map(|&x| x.max(0))).into_pyarray(py),
    )?;
    out.set_item(
        "owed",
        c.invoke_args(py, "owed_at", vec![day_input.clone_ref(py)])?,
    )?;
    let entry = if c.call0(py, "has_judgment")?.bind(py).is_truthy()? {
        let e = c.call0(py, "entry_ix")?;
        c.per_draw(py, e.bind(py))?
    } else {
        Array1::from(filled(n, BIG)?)
    };
    out.set_item("entry", entry.into_pyarray(py))?;
    for (key, name) in [
        ("ruling", "F"),
        ("delisted", "delisted"),
        ("stayed_from", "stayed_from"),
    ] {
        out.set_item(key, c.a1(py, name)?.into_pyarray(py))?;
    }
    let reads = [
        ("standing", "judgment_standing", true),
        ("entered", "judgment_amount_entered", false),
        ("band", "band", false),
        ("band_range", "band_range", false),
        ("default_available", "default_available_day", false),
        ("route_days", "holder_route_days_path", false),
        ("remitted", "remitted_amount", false),
        ("listing", "listing_status", true),
        ("atm", "atm_to_date", true),
        ("ledger", "ledger_left", true),
        ("offering_terms", "offering_terms", true),
        ("share_price", "share_price_on", true),
        ("offering_pending", "offering_pending_on", true),
        ("offer_available", "offer_available", true),
        ("offer_shortfall", "offer_shortfall", true),
        ("offerings", "offerings", false),
        ("notes_due_day", "notes_due_day", false),
        ("notes_due_how", "notes_due_how", false),
        ("arrears", "arrears_by_class", true),
        ("first_unpaid", "first_unpaid", false),
        ("nonpayment_day", "nonpayment_day", false),
    ];
    for (key, name, with_day) in reads {
        let args = if with_day {
            vec![day_input.clone_ref(py)]
        } else {
            vec![]
        };
        let v = match c_read(c, py, name, args) {
            Ok(v) => v,
            Err(e) if e.is_instance_of::<PyNotImplementedError>(py) => py
                .import("app.analysis.events")?
                .getattr("Awaiting")?
                .call1((name,))?
                .unbind(),
            Err(e) => return Err(e),
        };
        out.set_item(key, copied(py, v.bind(py))?)?;
    }
    Ok(out.into_any().unbind())
}
fn snapshot_day(c: &NativeChain, py: Python<'_>, i: usize) -> Out {
    let stays = c.get(py, "stays")?.cast_into::<PyDict>()?;
    if let Some(st) = stays.get_item(i)? {
        return Ok(st.get_item("approval")?.unbind());
    }
    Ok(c.get(py, "rec")?.get_item(0)?.get_item(i)?.unbind())
}
fn situations(c: &NativeChain, py: Python<'_>, tr: &Bound<'_, PyAny>) -> Out {
    let out = PyDict::new(py);
    let days = tr.getattr("day")?;
    if !(c.flag(py, "pending")? || c.flag(py, "ordinary")?) || days.len()? == 0 {
        return Ok(out.into_any().unbind());
    }
    let at = PyDict::new(py);
    at.set_item(days.len()? - 1, days.get_item(days.len()? - 1)?)?;
    for (i, _) in tr.getattr("late")?.cast::<PyDict>()?.iter() {
        at.set_item(&i, days.get_item(&i)?)?;
    }
    for (i, st) in tr.getattr("stays")?.cast::<PyDict>()?.iter() {
        at.set_item(i, st.get_item("day")?)?;
    }
    for (i, d) in at.iter() {
        out.set_item(i, situation(c, py, &d)?)?;
    }
    Ok(out.into_any().unbind())
}
fn tail(c: &NativeChain, py: Python<'_>, tr: &Bound<'_, PyAny>, day_only: bool) -> Out {
    let late = PyDict::new(py);
    for (i, v) in c.get(py, "late")?.cast::<PyDict>()?.iter() {
        late.set_item(i, v.cast::<PyDict>()?.copy()?)?;
    }
    tr.setattr("late", &late)?;
    let day = tr.getattr("day")?;
    if day_only && (c.flag(py, "pending")? || c.flag(py, "ordinary")?) {
        let i = day
            .len()?
            .checked_sub(1)
            .ok_or_else(|| PyValueError::new_err("day-only snapshot has no steps"))?;
        let out = PyDict::new(py);
        let d = snapshot_day(c, py, i)?;
        out.set_item(i, situation(c, py, d.bind(py))?)?;
        tr.setattr("situations", out)?;
    } else {
        tr.setattr("situations", situations(c, py, tr)?)?;
    }
    let vf = c
        .opt(py, "_vfired")?
        .unwrap_or_else(|| PyDict::new(py).into_any())
        .cast_into::<PyDict>()?;
    let fired = PyDict::new(py);
    for (i, _) in late.iter() {
        let t = c.per_draw(py, &day.get_item(&i)?)?;
        let v = vf
            .get_item(&i)?
            .unwrap_or_else(|| PyInt::new(py, BIG).into_any());
        let v = c.per_draw(py, &v)?;
        fired.set_item(
            i,
            Array1::from_iter(t.iter().zip(&v).map(|(&a, &b)| a.min(b))).into_pyarray(py),
        )?;
    }
    tr.setattr("fired", fired)?;
    let as_of = if day_only {
        let a = c.a1(py, "_booked_to")?;
        let b = c.a1(py, "reads")?;
        arr(
            py,
            Array1::from_iter(a.iter().zip(&b).map(|(&a, &b)| a.max(b))),
        )
    } else {
        py.None()
    };
    tr.setattr("as_of", as_of)?;
    Ok(tr.clone().unbind())
}

fn finish(
    c: &NativeChain,
    py: Python<'_>,
    tr: &Bound<'_, PyAny>,
    day_only: bool,
    light: bool,
) -> Out {
    let n = c.n(py)?;
    let days = c.days(py)?;
    let levy = c.get(py, "pending_levy")?;
    if !levy.is_none() {
        let levy = c.per_draw(py, &levy)?;
        c.invoke_args(
            py,
            "until",
            vec![arr(
                py,
                Array1::from_iter(
                    levy.iter()
                        .map(|&d| if d < BIG { d.wrapping_add(1) } else { -1 }),
                ),
            )],
        )?;
    }
    c.call0(py, "flush_levy")?;
    c.call0(py, "restay")?;
    let trig = triggers(c, py)?;
    let rec = c.get(py, "rec")?;
    let count = rec.get_item(0)?.len()?;
    let day_only = day_only && c.flag(py, "equity")? && count > 0;
    if day_only {
        c.call0(py, "_book_to_day")?;
    } else {
        c.invoke_args(
            py,
            "upto",
            vec![
                py.None(),
                true.into_pyobject(py)?.to_owned().into_any().unbind(),
            ],
        )?;
    }
    c.call0(py, "restay")?;
    let stays = c.get(py, "stays")?.cast_into::<PyDict>()?;
    if !day_only && !light {
        for (_, st) in stays.iter() {
            if !st.get_item("approved")?.is_truthy()? {
                c.invoke_args(
                    py,
                    "_size_stay",
                    vec![
                        st.unbind(),
                        false.into_pyobject(py)?.to_owned().into_any().unbind(),
                    ],
                )?;
            }
        }
    }
    let out = PyDict::new(py);
    if !day_only && !light {
        for (i, st) in stays.iter() {
            let d = PyDict::new(py);
            for k in [
                "day",
                "cash",
                "owed",
                "collateral",
                "stay_offer",
                "petition",
                "triggers",
            ] {
                d.set_item(k, st.get_item(k)?)?;
            }
            out.set_item(i, d)?;
        }
    }
    tr.setattr("stays", out)?;
    for (i, name) in ["day", "cash", "owed", "collateral"].iter().enumerate() {
        tr.setattr(*name, PyList::new(py, items(&rec.get_item(i)?)?)?)?;
    }
    tr.setattr("late", PyDict::new(py))?;
    let late = c.get(py, "late")?.cast_into::<PyDict>()?;
    let li = late.keys().extract::<Vec<usize>>()?.into_iter().max();
    if li.is_some_and(|i| Some(i) == count.checked_sub(1)) {
        let i = li.ok_or_else(|| PyValueError::new_err("missing last floor"))?;
        let v = late
            .get_item(i)?
            .ok_or_else(|| PyValueError::new_err("missing last floor record"))?
            .get_item("raise_offer")?;
        c.put(py, "raise_offer", &v)?;
    }
    let ev = c.get(py, "ev")?;
    let pet = c.per_draw(py, &ev.getattr("petition")?)?;
    let after = |r: usize, d: usize| pet[r] >= 0 && d as i64 >= pet[r];
    let mut zeroed = Vec::new();
    for name in std::iter::once("cash".to_owned()).chain(KINDS.iter().map(|k| format!("k:{k}"))) {
        let a = if let Some(k) = name.strip_prefix("k:") {
            ev.getattr("kinds")?.get_item(k)?
        } else {
            ev.getattr(&name)?
        };
        let a = a.cast::<PyArray2<i64>>()?;
        let read = a.readonly();
        let nonzero = read
            .as_array()
            .indexed_iter()
            .any(|((r, d), &v)| after(r, d) && v != 0);
        drop(read);
        if nonzero {
            let w = c.invoke_args(py, "_evw", vec![string(py, &name)])?;
            let w = w.bind(py).cast::<PyArray2<i64>>()?;
            let mut w = w.try_readwrite()?;
            for ((r, d), v) in w.as_array_mut().indexed_iter_mut() {
                if after(r, d) {
                    *v = 0;
                }
            }
            zeroed.push(string(py, &name));
        }
    }
    if !zeroed.is_empty() {
        c.invoke_args(py, "_touch", zeroed)?;
    }
    if c.flag(py, "equity")? {
        let mut atm = Array1::from(filled(n, 0_i64)?);
        let at = c.get(py, "_atm")?;
        if !at.is_none() {
            let a = at.cast::<PyArray2<i64>>()?.readonly();
            for ((r, d), &v) in a.as_array().indexed_iter() {
                if !after(r, d) {
                    atm[r] = atm[r].wrapping_add(v);
                }
            }
        }
        let mut off = Array1::from(filled(n, 0_i64)?);
        for o in items(&c.get(py, "_offers")?)? {
            let close = c.per_draw(py, &o.get_item("close")?)?;
            let net = c.per_draw(py, &o.get_item("net")?)?;
            let closed = o.get_item("closed")?.cast_into::<PyArray1<bool>>()?;
            let closed = closed.readonly();
            for r in 0..n {
                if closed.as_array()[r] && close[r] < if pet[r] < 0 { BIG } else { pet[r] } {
                    off[r] = off[r].wrapping_add(net[r]);
                }
            }
        }
        let proceeds = PyDict::new(py);
        proceeds.set_item("atm_proceeds", atm.into_pyarray(py))?;
        proceeds.set_item("offering_proceeds", off.into_pyarray(py))?;
        ev.setattr("proceeds", proceeds)?;
    }
    if c.flag(py, "pending")? || c.flag(py, "ordinary")? {
        let resolved = c.a1(py, "resolved")?;
        let basis = c.get(py, "basis")?;
        let legal = basis.getattr("legal")?;
        let legal = legal.cast::<PyArray2<i64>>()?.readonly();
        let legal = legal.as_array();
        let nonzero = legal
            .indexed_iter()
            .any(|((r, d), &v)| after(r, d) && d as i64 >= resolved[r] && v != 0);
        if nonzero {
            for name in ["cash", "k:reduction"] {
                let w = c.invoke_args(py, "_evw", vec![string(py, name)])?;
                let w = w.bind(py).cast::<PyArray2<i64>>()?;
                let mut w = w.try_readwrite()?;
                for ((r, d), v) in w.as_array_mut().indexed_iter_mut() {
                    if after(r, d) && d as i64 >= resolved[r] {
                        *v = v.wrapping_sub(legal[[r, d]]);
                    }
                }
            }
            c.invoke_args(
                py,
                "_touch",
                vec![string(py, "cash"), string(py, "k:reduction")],
            )?;
        }
    }
    if c.int(py, "_stay_cv")? == c.int(py, "_cv")? {
        c.put_i64(py, "_stay_cv", c.int(py, "_cv")?.wrapping_sub(1))?;
    }
    tr.setattr("events", ev)?;
    let cause = c.get(py, "pet_cause")?.cast_into::<PyArray1<i8>>()?;
    let cause = cause.readonly();
    tr.setattr(
        "cause",
        Array1::from_iter((0..n).map(|r| if pet[r] >= 0 { cause.as_array()[r] } else { 0 }))
            .into_pyarray(py),
    )?;
    let marks = PyDict::new(py);
    for (k, v) in c.get(py, "marks")?.cast::<PyDict>()?.iter() {
        let v = c.per_draw(py, &v)?;
        marks.set_item(
            k,
            Array1::from_iter(v.iter().map(|&x| x as i32)).into_pyarray(py),
        )?;
    }
    tr.setattr("marks", marks)?;
    for k in ["settle_offer", "stay_offer", "raise_offer", "reads"] {
        tr.setattr(k, c.get(py, k)?)?;
    }
    tr.setattr("triggers", trig)?;
    let groups = PyDict::new(py);
    for (i, v) in c.get(py, "grec")?.cast::<PyDict>()?.iter() {
        groups.set_item(i, copied(py, &v)?)?;
    }
    tr.setattr("groups", groups)?;
    c.put(
        py,
        "_finished",
        &PyTuple::new(py, [day_only, light])?.into_any(),
    )?;
    if light {
        tr.setattr("situations", PyDict::new(py))?;
        tr.setattr("fired", PyDict::new(py))?;
        tr.setattr("as_of", py.None())?;
        return Ok(tr.clone().unbind());
    }
    let _ = days;
    tail(c, py, tr, day_only)
}

pub(crate) fn dispatch(
    c: &NativeChain,
    py: Python<'_>,
    name: &str,
    args: &Bound<'_, PyTuple>,
) -> Option<Out> {
    Some(match name {
        "clone" => clone_state(c, py),
        "sliced" => args
            .get_item(0)
            .and_then(|s| args.get_item(1).and_then(|d| sliced(c, py, &s, &d))),
        "divergence" => args.get_item(0).and_then(|b| {
            let wait = args
                .get_item(1)
                .ok()
                .filter(|v| !v.is_none())
                .map(|v| v.extract::<usize>())
                .transpose()?;
            divergence(c, py, &b, wait)
        }),
        "trigger_days" => triggers(c, py),
        "c_read" => args.get_item(0).and_then(|n| {
            let n = n.extract::<String>()?;
            c_read(c, py, &n, args.iter().skip(1).map(Bound::unbind).collect())
        }),
        "c_situation" => args.get_item(0).and_then(|d| situation(c, py, &d)),
        "c_situations" => args.get_item(0).and_then(|tr| situations(c, py, &tr)),
        "_snapshot_day" => args
            .get_item(0)
            .and_then(|i| snapshot_day(c, py, i.extract()?)),
        "_snapshot_tail" => args
            .get_item(0)
            .and_then(|tr| tail(c, py, &tr, args.get_item(1)?.extract()?)),
        "finish" => args.get_item(0).and_then(|tr| {
            finish(
                c,
                py,
                &tr,
                args.get_item(1)
                    .ok()
                    .map(|v| v.extract::<bool>())
                    .transpose()?
                    .unwrap_or(false),
                args.get_item(2)
                    .ok()
                    .map(|v| v.extract::<bool>())
                    .transpose()?
                    .unwrap_or(false),
            )
        }),
        "completed" => args.get_item(0).and_then(|tr| {
            let done = c.opt(py, "_finished")?;
            if done.map(|v| v.extract::<(bool, bool)>()).transpose()? != Some((true, true)) {
                return Ok(py.None());
            }
            c.put(
                py,
                "_finished",
                &PyTuple::new(py, [true, false])?.into_any(),
            )?;
            tail(c, py, &tr, true)
        }),
        _ => return None,
    })
}

//! Financial reduction control over the existing NumPy buffers.
//!
//! Array reductions use NumPy's compiled operations, including its dtype,
//! overflow and summation order; matrix products retain the installed BLAS.
//! Rust owns the financial decisions and sequencing. No retained Python
//! execution implementation is called from this module.
use numpy::{IntoPyArray, PyArray1, PyArrayMethods, PyReadonlyArray1};
use pyo3::basic::CompareOp;
use pyo3::exceptions::{PyIndexError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PySlice};

type Obj<'py> = Bound<'py, PyAny>;

fn np(py: Python<'_>) -> PyResult<Bound<'_, PyModule>> {
    py.import("numpy")
}

fn axis<'py>(a: &Obj<'py>, method: &str, dim: usize) -> PyResult<Obj<'py>> {
    let kw = PyDict::new(a.py());
    kw.set_item("axis", dim)?;
    a.call_method(method, (), Some(&kw))
}

fn cast_float<'py>(a: &Obj<'py>) -> PyResult<Obj<'py>> {
    a.call_method1("astype", (np(a.py())?.getattr("float64")?,))
}

fn as_float<'py>(a: &Obj<'py>) -> PyResult<Obj<'py>> {
    let kw = PyDict::new(a.py());
    kw.set_item("dtype", np(a.py())?.getattr("float64")?)?;
    np(a.py())?.getattr("asarray")?.call((a,), Some(&kw))
}

fn col<'py>(a: &Obj<'py>) -> PyResult<Obj<'py>> {
    a.get_item((PySlice::full(a.py()), a.py().None()))
}

fn on<'py>(a: Obj<'py>, mask: Option<&Obj<'py>>) -> PyResult<Obj<'py>> {
    match mask {
        Some(m) => a.get_item(m),
        None => Ok(a),
    }
}

fn weighted<'py>(values: Obj<'py>, weights: Obj<'py>, qs: &Obj<'py>) -> PyResult<Obj<'py>> {
    values
        .py()
        .import("app._native")?
        .getattr("weighted_quantiles")?
        .call1((values, weights, qs))
}

#[pyfunction]
pub(crate) fn analysis_scalars<'py>(t: &Obj<'py>) -> PyResult<Bound<'py, PyDict>> {
    let py = t.py();
    let numpy = np(py)?;
    let out = PyDict::new(py);
    for name in [
        "lender_pv",
        "pv_fundings",
        "pv_collections",
        "dollar_days",
        "drawn",
        "fees",
        "contractual",
        "collected",
        "stayed",
        "stayed_principal",
        "preference",
        "not_yet_due",
        "uncollected",
        "min_cash",
    ] {
        out.set_item(
            name,
            if name == "fees" {
                analysis_fees(t)?
            } else {
                t.getattr(name)?
            },
        )?;
    }
    out.set_item(
        "petition_p",
        cast_float(&t.getattr("petition")?.rich_compare(0, CompareOp::Ge)?)?,
    )?;
    let min_cash = t.getattr("min_cash")?;
    out.set_item(
        "shortfall_p",
        cast_float(&min_cash.rich_compare(0, CompareOp::Lt)?)?,
    )?;
    out.set_item(
        "shortfall",
        numpy
            .getattr("maximum")?
            .call1((numpy.getattr("negative")?.call1((min_cash,))?, 0))?,
    )?;
    for (key, field, method) in [
        ("peak_outstanding", "outstanding", "max"),
        ("avg_outstanding", "outstanding", "mean"),
        ("peak_locked", "locked", "max"),
        ("peak_capacity", "capacity", "max"),
    ] {
        out.set_item(key, axis(&t.getattr(field)?, method, 1)?)?;
    }
    let unrecovered = t.getattr("stayed")?.add(t.getattr("uncollected")?)?;
    out.set_item("unrecovered", &unrecovered)?;
    out.set_item(
        "recovered_all",
        cast_float(&unrecovered.rich_compare(0, CompareOp::Eq)?)?,
    )?;
    out.set_item(
        "horizon_cash",
        t.getattr("cash")?.get_item((PySlice::full(py), -1))?,
    )?;
    Ok(out)
}

#[pyfunction]
fn analysis_reduce_add(
    r: &Obj<'_>,
    i: usize,
    t: &Obj<'_>,
    ev: &Obj<'_>,
    mask: &Obj<'_>,
) -> PyResult<()> {
    let py = r.py();
    let numpy = np(py)?;
    let mask = if mask.is_none() || mask.call_method0("all")?.is_truthy()? {
        None
    } else {
        Some(mask)
    };
    if let Some(m) = mask {
        r.getattr("mask")?.set_item(i, m)?;
        r.setattr("masked", true)?;
    }
    let sc = analysis_scalars(t)?;
    if !ev.is_none() {
        let proceeds = ev.getattr("proceeds")?;
        if !proceeds.is_none() {
            for k in py
                .import("app.analysis.events")?
                .getattr("PROCEEDS")?
                .try_iter()?
            {
                let k = k?;
                sc.set_item(&k, proceeds.get_item(&k)?)?;
            }
        }
    }
    let means = r.getattr("means")?.cast_into::<PyDict>()?;
    for (k, v) in sc.iter() {
        let target = match means.get_item(&k)? {
            Some(a) => a,
            None => {
                let a = numpy.getattr("zeros")?.call1((r.getattr("n")?,))?;
                means.set_item(&k, &a)?;
                a
            }
        };
        let value = if let Some(m) = mask {
            v.get_item(m)?.call_method0("sum")?.extract::<f64>()? / v.len()? as f64
        } else {
            v.call_method0("mean")?.extract::<f64>()?
        };
        target.set_item(i, value)?;
    }
    for field in ["min_cash", "min_headroom", "collected"] {
        r.getattr(field)?.set_item(i, t.getattr(field)?)?;
    }
    let need = r.getattr("need")?;
    if !need.is_none() {
        let below = t.getattr("cash")?.rich_compare(need, CompareOp::Lt)?;
        let reached = axis(&below, "any", 1)?;
        let reached = match mask {
            Some(m) => reached.bitand(m)?,
            None => reached,
        };
        r.getattr("floor_day")?.set_item(
            i,
            numpy
                .getattr("where")?
                .call1((reached, axis(&below, "argmax", 1)?, -1))?,
        )?;
    }
    let cash = on(t.getattr("cash")?, mask)?;
    let due = on(t.getattr("due")?, mask)?;
    let coll = on(t.getattr("collections")?, mask)?;
    let fund = on(t.getattr("fundings")?, mask)?;
    let outs = on(t.getattr("outstanding")?, mask)?;
    let idx = numpy.getattr("arange")?.call1((r.getattr("days")?,))?;
    let pet = col(&on(t.getattr("petition")?, mask)?)?;
    let by_day = pet
        .rich_compare(0, CompareOp::Ge)?
        .bitand(pet.rich_compare(&idx, CompareOp::Le)?)?;
    let window = pet
        .rich_compare(0, CompareOp::Ge)?
        .bitand(idx.rich_compare(pet.sub(90)?, CompareOp::Ge)?)?
        .bitand(idx.rich_compare(&pet, CompareOp::Lt)?)?;
    let cum_due = axis(&due, "cumsum", 1)?;
    let cum_coll = axis(&coll, "cumsum", 1)?;
    let d = r.getattr("per_day")?.cast_into::<PyDict>()?;
    let put = |key: &str, value: Obj<'_>| -> PyResult<()> {
        d.get_item(key)?
            .ok_or_else(|| PyValueError::new_err(key.to_owned()))?
            .set_item(i, value)
    };
    put("cash", axis(&cash, "sum", 0)?)?;
    let capacity = on(t.getattr("capacity")?, mask)?;
    let free = r
        .getattr("setup")?
        .getattr("facility_cents")?
        .sub(&capacity)?;
    put(
        "backup",
        axis(
            &cash.add(numpy.getattr("maximum")?.call1((free, 0))?)?,
            "sum",
            0,
        )?,
    )?;
    put("collected", axis(&cum_coll, "sum", 0)?)?;
    put("due_cum", axis(&cum_due, "sum", 0)?)?;
    put("fundings", axis(&fund, "sum", 0)?)?;
    put(
        "drawn",
        d.get_item("fundings")?
            .ok_or_else(|| PyValueError::new_err("missing fundings"))?
            .get_item(i)?
            .call_method0("cumsum")?,
    )?;
    put("collections", axis(&coll, "sum", 0)?)?;
    put("outstanding", axis(&outs, "sum", 0)?)?;
    put("locked", axis(&on(t.getattr("locked")?, mask)?, "sum", 0)?)?;
    put("capacity", axis(&capacity, "sum", 0)?)?;
    put("petitioned", axis(&by_day, "sum", 0)?)?;
    put(
        "frozen",
        axis(
            &by_day.mul(col(&on(t.getattr("stayed")?, mask)?)?)?,
            "sum",
            0,
        )?,
    )?;
    let unpaid = cum_due.sub(&cum_coll)?;
    put(
        "past_due",
        axis(
            &unpaid.mul(numpy.getattr("invert")?.call1((&by_day,))?)?,
            "sum",
            0,
        )?,
    )?;
    put("frozen_due", axis(&unpaid.mul(&by_day)?, "sum", 0)?)?;
    put("clawback", axis(&coll.mul(window)?, "sum", 0)?)?;
    let processed = t.getattr("processed")?;
    if !processed.is_none() {
        let by_class = axis(&on(processed.getattr("arrears")?, mask)?, "sum", 0)?;
        for (j, k) in py
            .import("app.analysis.core")?
            .getattr("ARREARS_KEYS")?
            .try_iter()?
            .enumerate()
        {
            let k = k?;
            let array = match d.get_item(&k)? {
                Some(a) => a,
                None => {
                    let a = numpy
                        .getattr("zeros")?
                        .call1(((r.getattr("n")?, r.getattr("days")?),))?;
                    d.set_item(&k, &a)?;
                    a
                }
            };
            array.set_item(i, by_class.get_item((PySlice::full(py), j))?)?;
        }
    }
    let bins = r.getattr("bins")?;
    let counts = r.getattr("counts")?;
    for (name, a) in [("cash", cash), ("collected", cum_coll)] {
        counts
            .get_item(name)?
            .call_method1("add", (bins.get_item(name)?.call_method1("flat", (a,))?,))?;
    }
    let hr = match mask {
        Some(m) => m.get_item(t.getattr("headroom_rows")?)?,
        None => PySlice::full(py).into_any(),
    };
    let values = t.getattr("headroom")?.get_item(&hr)?;
    let months = r
        .getattr("month_of_day")?
        .get_item(t.getattr("headroom_days")?.get_item(&hr)?)?;
    counts.get_item("headroom")?.call_method1(
        "add",
        (bins
            .get_item("headroom")?
            .call_method1("flat", (&values, months))?,),
    )?;
    r.getattr("hr_count")?.set_item(i, values.len()?)?;
    r.getattr("hr_negative")?.set_item(
        i,
        values
            .rich_compare(0, CompareOp::Lt)?
            .call_method0("sum")?
            .extract::<f64>()?,
    )?;
    r.getattr("peak_day")?.set_item(
        i,
        d.get_item("outstanding")?
            .ok_or_else(|| PyValueError::new_err("missing outstanding"))?
            .get_item(i)?
            .call_method0("argmax")?,
    )?;
    Ok(())
}

#[pyfunction]
fn analysis_draw_weights<'py>(r: &Obj<'py>, probs: &Obj<'py>) -> PyResult<Obj<'py>> {
    let p = as_float(probs)?;
    let w = col(&p)?.div(r.getattr("draws")?)?;
    if r.getattr("masked")?.is_truthy()? {
        w.mul(r.getattr("mask")?)
    } else {
        np(r.py())?
            .getattr("broadcast_to")?
            .call1((w, (p.len()?, r.getattr("draws")?)))
    }
}

#[pyfunction]
fn analysis_first_floor<'py>(r: &Obj<'py>, probs: &Obj<'py>) -> PyResult<Bound<'py, PyDict>> {
    let py = r.py();
    let numpy = np(py)?;
    let p = as_float(probs)?;
    let w = if r.getattr("masked")?.is_truthy()? {
        let w = analysis_draw_weights(r, &p)?.call_method0("ravel")?;
        w.div(w.call_method0("sum")?)?
    } else {
        numpy
            .getattr("repeat")?
            .call1((p.div(p.call_method0("sum")?)?, r.getattr("draws")?))?
            .div(r.getattr("draws")?)?
    };
    let day = r.getattr("floor_day")?.call_method0("ravel")?;
    let reached = day.rich_compare(0, CompareOp::Ge)?;
    let share = w
        .get_item(&reached)?
        .call_method0("sum")?
        .extract::<f64>()?;
    let kwargs = PyDict::new(py);
    kwargs.set_item("kind", "stable")?;
    let order = numpy.getattr("argsort")?.call(
        (numpy
            .getattr("where")?
            .call1((reached, &day, r.getattr("days")?))?,),
        Some(&kwargs),
    )?;
    let cum = w.get_item(&order)?.call_method0("cumsum")?;
    let k = numpy.getattr("searchsorted")?.call1((cum, 0.5))?;
    let out = PyDict::new(py);
    out.set_item("share", share)?;
    out.set_item(
        "median_day",
        if share >= 0.5 {
            Some(day.get_item(order)?.get_item(k)?.extract::<i64>()?)
        } else {
            None
        },
    )?;
    Ok(out)
}

#[pyfunction]
fn analysis_collected_quantiles<'py>(r: &Obj<'py>, probs: &Obj<'py>) -> PyResult<Obj<'py>> {
    let p = as_float(probs)?;
    let live = p.rich_compare(0, CompareOp::Gt)?;
    let w = if r.getattr("masked")?.is_truthy()? {
        let w = analysis_draw_weights(r, &p)?
            .get_item(&live)?
            .call_method0("ravel")?;
        w.div(w.call_method0("sum")?)?
    } else {
        let kept = p.get_item(&live)?;
        np(r.py())?
            .getattr("repeat")?
            .call1((kept.div(kept.call_method0("sum")?)?, r.getattr("draws")?))?
            .div(r.getattr("draws")?)?
    };
    weighted(
        cast_float(
            &r.getattr("collected")?
                .get_item(live)?
                .call_method0("ravel")?,
        )?,
        w,
        &r.py().import("app.analysis.core")?.getattr("QS")?,
    )
}

#[pyfunction]
fn analysis_expectation(values: &Obj<'_>, probs: &Obj<'_>) -> PyResult<f64> {
    values.matmul(probs)?.extract()
}

#[pyfunction]
fn analysis_fees<'py>(t: &Obj<'py>) -> PyResult<Obj<'py>> {
    t.getattr("contractual")?
        .sub(t.getattr("drawn")?)?
        .sub(t.getattr("opening_principal")?)
}

/// NumPy's linear quantile for integer-cent observations, including the two
/// interpolation directions used to avoid cancellation near the upper point.
#[pyfunction]
fn analysis_quantile(values: PyReadonlyArray1<'_, i64>, q: f64) -> PyResult<f64> {
    if !q.is_finite() || !(0.0..=1.0).contains(&q) {
        return Err(PyValueError::new_err(
            "quantile must be between zero and one",
        ));
    }
    let view = values.as_array();
    if view.is_empty() {
        return Err(PyIndexError::new_err(
            "cannot take a quantile of empty observations",
        ));
    }
    let mut sorted = crate::price::filled_vec(view.len(), 0i64)?;
    for (to, &from) in sorted.iter_mut().zip(view.iter()) {
        *to = from;
    }
    sorted.sort_unstable();
    let index = (sorted.len() - 1) as f64 * q;
    let lo = index.floor() as usize;
    let hi = index.ceil() as usize;
    let g = index - lo as f64;
    let a = sorted[lo];
    let b = sorted[hi];
    let diff = b.wrapping_sub(a) as f64;
    Ok(if g >= 0.5 {
        b as f64 - diff * (1.0 - g)
    } else {
        a as f64 + diff * g
    })
}

#[pyfunction]
pub(crate) fn analysis_limit_summary<'py>(limit: &Obj<'py>) -> PyResult<(Obj<'py>, Obj<'py>)> {
    let rows = limit.len()?;
    if rows == 0 {
        return Err(PyIndexError::new_err(
            "cannot take a quantile of empty limits",
        ));
    }
    let mean = axis(limit, "mean", 0)?;
    let sorted = limit.call_method0("copy")?;
    axis(&sorted, "sort", 0)?;
    let last = sorted.get_item(-1)?;
    let lower = sorted.get_item(((rows - 1) as f64 * 0.05).floor() as usize)?;
    let numpy = np(limit.py())?;
    let lower =
        numpy
            .getattr("where")?
            .call1((numpy.getattr("isnan")?.call1((&last,))?, last, lower))?;
    Ok((mean, lower))
}

#[pyfunction]
fn analysis_bins_spanning<'py>(
    lo: &Obj<'py>,
    hi: &Obj<'py>,
    n: usize,
) -> PyResult<(Obj<'py>, Obj<'py>)> {
    let numpy = np(lo.py())?;
    let lower = cast_float(&numpy.getattr("floor")?.call1((lo,))?)?;
    let width = numpy.getattr("maximum")?.call1((
        numpy
            .getattr("ceil")?
            .call1((hi,))?
            .add(1)?
            .sub(&lower)?
            .div(n)?,
        1.0,
    ))?;
    Ok((lower, width))
}

#[pyfunction]
fn analysis_pooled_quantiles<'py>(
    bins: &Obj<'py>,
    h: &Obj<'py>,
    qs: &Obj<'py>,
) -> PyResult<Obj<'py>> {
    let py = bins.py();
    let stop = PySlice::new(py, 0, h.len()? as isize, 1);
    let lo = bins.getattr("lo")?.get_item((&stop, py.None()))?;
    let width = bins.getattr("width")?.get_item((&stop, py.None()))?;
    let index = np(py)?
        .getattr("arange")?
        .call1((bins.getattr("n")?,))?
        .add(0.5)?;
    let mids = lo.add(index.mul(width)?)?.call_method0("ravel")?;
    let flat = h.call_method0("ravel")?;
    let keep = flat.rich_compare(0, CompareOp::Gt)?;
    weighted(
        mids.get_item(&keep)?,
        flat.get_item(keep)?.div(h.call_method0("sum")?)?,
        qs,
    )
}

#[pyfunction]
fn analysis_merge_range(
    lo: &Obj<'_>,
    hi: &Obj<'_>,
    lower: &Obj<'_>,
    upper: &Obj<'_>,
) -> PyResult<()> {
    let numpy = np(lo.py())?;
    let kw = PyDict::new(lo.py());
    kw.set_item("out", lo)?;
    numpy.getattr("minimum")?.call((lo, lower), Some(&kw))?;
    kw.set_item("out", hi)?;
    numpy.getattr("maximum")?.call((hi, upper), Some(&kw))?;
    Ok(())
}

#[pyfunction]
fn analysis_event_range(
    lo: &Obj<'_>,
    hi: &Obj<'_>,
    cash: &Obj<'_>,
    lock: &Obj<'_>,
) -> PyResult<()> {
    let cum = axis(&cash.sub(lock)?, "cumsum", 1)?;
    analysis_merge_range(lo, hi, &axis(&cum, "min", 0)?, &axis(&cum, "max", 0)?)
}

#[pyfunction]
fn analysis_bins_from<'py>(
    a: &Obj<'py>,
    lo_ev: &Obj<'py>,
    hi_ev: &Obj<'py>,
) -> PyResult<Bound<'py, PyDict>> {
    let py = a.py();
    let numpy = np(py)?;
    let setup = a.getattr("setup")?;
    let line = a.getattr("line")?;
    let fee = setup
        .getattr("fee_bps")?
        .add(1)?
        .div(10_000)?
        .extract::<f64>()?;
    let lim = numpy
        .getattr("maximum")?
        .call_method1(
            "accumulate",
            (cast_float(&axis(&line.getattr("limit")?, "max", 0)?)?,),
        )?
        .add(10_000)?;
    let first_due = line.getattr("due_idx")?.get_item((PySlice::full(py), 0))?;
    let days = a.getattr("days")?.extract::<usize>()?;
    let mut coll = crate::price::filled_vec(days, 0.0)?;
    let mut fund = crate::price::filled_vec(days, 0.0)?;
    for day in 0..days {
        let repayable = numpy
            .getattr("flatnonzero")?
            .call1((first_due.rich_compare(day, CompareOp::Le)?,))?;
        let bound = lim.get_item(day)?.extract::<f64>()?;
        if repayable.len()? > 0 {
            let index = repayable.call_method0("max")?.extract::<usize>()?;
            let prev = *coll
                .get(index)
                .ok_or_else(|| PyIndexError::new_err("repayable day outside horizon"))?;
            coll[day] = (1.0 + fee) * bound + prev;
        }
        fund[day] = bound + coll[day] / (1.0 + fee);
    }
    let opened = setup.getattr("exposure")?.getattr("owed_cents")?;
    let coll = numpy
        .getattr("maximum")?
        .call_method1("accumulate", (coll.into_pyarray(py),))?
        .add(&opened)?
        .add(10_000)?;
    let margin = lim.add(fund.into_pyarray(py).mul(fee)?)?.add(10_000)?;
    let cash = a.getattr("bank")?.getattr("cash")?;
    let lo = axis(&cash, "min", 0)?.add(lo_ev)?.sub(&margin)?;
    let hi = axis(&cash, "max", 0)?.add(hi_ev)?.add(margin)?;
    let owed = (1.0 + fee) * lim.call_method0("max")?.extract::<f64>()?
        + opened.extract::<f64>()?
        + 10_000.0;
    let need = line
        .getattr("need")?
        .get_item((PySlice::full(py), PySlice::new(py, 0, days as isize, 1)))?;
    let low = lo.sub(axis(&need, "max", 0)?)?.sub(owed)?;
    let month = a.getattr("month_of_day")?;
    let mut h_lo = Vec::new();
    let mut h_hi = Vec::new();
    for k in 0..a.getattr("months")?.len()? {
        let selected = month.rich_compare(k, CompareOp::Eq)?;
        h_lo.push(
            low.get_item(&selected)?
                .call_method0("min")?
                .extract::<f64>()?,
        );
        h_hi.push(
            hi.get_item(selected)?
                .call_method0("max")?
                .extract::<f64>()?,
        );
    }
    let core = py.import("app.analysis.core")?;
    let cls = core.getattr("Bins")?;
    let out = PyDict::new(py);
    for (key, lower, upper, kind) in [
        ("cash", lo, hi, "CASH_BINS"),
        (
            "collected",
            numpy.getattr("zeros")?.call1((days,))?,
            coll,
            "COLLECTED_BINS",
        ),
        (
            "headroom",
            h_lo.into_pyarray(py).into_any(),
            h_hi.into_pyarray(py).into_any(),
            "HEADROOM_BINS",
        ),
    ] {
        let count = core.getattr(kind)?.extract::<usize>()?;
        let (lower, width) = analysis_bins_spanning(&lower, &upper, count)?;
        out.set_item(key, cls.call1((lower, width, count))?)?;
    }
    Ok(out)
}

#[pyfunction]
fn analysis_installments<'py>(
    py: Python<'py>,
    amount: PyReadonlyArray1<'py, i64>,
    fee_bps: i64,
    n: usize,
) -> PyResult<Obj<'py>> {
    if n == 0 {
        return Err(PyValueError::new_err("installment count must be positive"));
    }
    let amounts = amount.as_array();
    let mut out = crate::price::filled_array(amounts.len(), n, 0i64)?;
    let denom = (n as i64)
        .checked_mul(2)
        .ok_or_else(|| PyValueError::new_err("installment count overflows"))?;
    for (row, &a) in amounts.iter().enumerate() {
        let fee = a
            .wrapping_mul(2)
            .wrapping_mul(fee_bps)
            .wrapping_add(10_000)
            .div_euclid(20_000);
        let total = a.wrapping_add(fee);
        let base = total
            .wrapping_mul(2)
            .wrapping_add(n as i64)
            .div_euclid(denom);
        for col in 0..n {
            out[[row, col]] = base;
        }
        out[[row, n - 1]] = total.wrapping_sub(base.wrapping_mul((n - 1) as i64));
    }
    Ok(out.into_pyarray(py).into_any())
}

#[pyfunction]
fn analysis_petition_at<'py>(
    line: &Obj<'py>,
    t: &Obj<'py>,
    petition: &Obj<'py>,
    peak: i64,
) -> PyResult<(Obj<'py>, Obj<'py>)> {
    let py = line.py();
    let numpy = np(py)?;
    let days = line.getattr("days")?;
    let inside = petition
        .rich_compare(0, CompareOp::Ge)?
        .bitand(petition.rich_compare(&days, CompareOp::Lt)?)?;
    let pet = numpy.getattr("minimum")?.call1((
        numpy.getattr("where")?.call1((inside, petition, &days))?,
        peak,
    ))?;
    let idx = numpy
        .getattr("arange")?
        .call1((&days,))?
        .get_item((py.None(), PySlice::full(py)))?;
    let before = idx.rich_compare(col(&pet)?, CompareOp::Lt)?;
    let collections = t.getattr("collections")?;
    let collected = axis(&collections.mul(&before)?, "sum", 1)?;
    let setup = line.getattr("setup")?;
    let amounts = t.getattr("draw_amounts")?;
    let readonly = amounts.cast::<PyArray1<i64>>()?.readonly();
    let booked = axis(
        &analysis_installments(
            py,
            readonly,
            setup.getattr("fee_bps")?.extract()?,
            setup.getattr("installments")?.extract()?,
        )?,
        "sum",
        1,
    )?;
    let draw_rows = t.getattr("draw_rows")?;
    let keep = t
        .getattr("draw_days")?
        .rich_compare(pet.get_item(&draw_rows)?, CompareOp::Lt)?;
    let kw = PyDict::new(py);
    kw.set_item("dtype", numpy.getattr("int64")?)?;
    let contract = numpy.getattr("zeros")?.call((pet.len()?,), Some(&kw))?;
    numpy.getattr("add")?.call_method1(
        "at",
        (
            &contract,
            draw_rows.get_item(&keep)?,
            booked.get_item(keep)?,
        ),
    )?;
    let window = idx
        .rich_compare(col(&pet)?.sub(90)?, CompareOp::Ge)?
        .bitand(before)?;
    Ok((
        contract.sub(collected)?,
        axis(&collections.mul(window)?, "sum", 1)?,
    ))
}

#[pyfunction]
fn analysis_peak_day(outstanding: &Obj<'_>) -> PyResult<usize> {
    axis(outstanding, "mean", 0)?
        .call_method0("argmax")?
        .extract()
}

fn cents_quantile(values: &Obj<'_>, q: f64) -> PyResult<f64> {
    analysis_quantile(values.cast::<PyArray1<i64>>()?.readonly(), q)
}

#[pyfunction]
fn analysis_stress_row<'py>(
    setup: &Obj<'py>,
    t: &Obj<'py>,
    pt: &Obj<'py>,
    peak: i64,
) -> PyResult<Bound<'py, PyDict>> {
    let py = setup.py();
    let out = PyDict::new(py);
    let min_cash = t.getattr("min_cash")?;
    out.set_item("min_cash_p5_cents", cents_quantile(&min_cash, 0.05)?)?;
    out.set_item("min_cash_p50_cents", cents_quantile(&min_cash, 0.5)?)?;
    out.set_item(
        "uncollected_maturity_cents",
        t.getattr("stayed")?
            .add(t.getattr("uncollected")?)?
            .call_method0("mean")?
            .extract::<f64>()?,
    )?;
    out.set_item(
        "stayed_claim_cents",
        t.getattr("stayed")?
            .call_method0("mean")?
            .extract::<f64>()?,
    )?;
    out.set_item(
        "lender_pv_cents",
        t.getattr("lender_pv")?
            .call_method0("mean")?
            .extract::<f64>()?,
    )?;
    let forced = PyDict::new(py);
    let kw = PyDict::new(py);
    kw.set_item("days", peak + 1)?;
    let lag = py
        .import("datetime")?
        .getattr("timedelta")?
        .call((), Some(&kw))?;
    forced.set_item(
        "day",
        setup
            .getattr("review")?
            .add(lag)?
            .call_method0("isoformat")?,
    )?;
    let stayed = pt.get_item(0)?;
    forced.set_item(
        "stayed_claim_mean_cents",
        stayed.call_method0("mean")?.extract::<f64>()?,
    )?;
    forced.set_item("stayed_claim_p95_cents", cents_quantile(&stayed, 0.95)?)?;
    forced.set_item(
        "preference_exposed_mean_cents",
        pt.get_item(1)?.call_method0("mean")?.extract::<f64>()?,
    )?;
    out.set_item("petition_at_peak", forced)?;
    Ok(out)
}

#[pyfunction]
fn analysis_metrics<'py>(r: &Obj<'py>, probs: &Obj<'py>) -> PyResult<Bound<'py, PyDict>> {
    let py = r.py();
    let p = as_float(probs)?;
    let qs = py.import("app.analysis.core")?.getattr("QS")?;
    let expected = PyDict::new(py);
    for (k, v) in r.getattr("means")?.cast::<PyDict>()?.iter() {
        expected.set_item(k, analysis_expectation(&v, &p)?)?;
    }
    let e = |key: &str| -> PyResult<f64> {
        expected
            .get_item(key)?
            .ok_or_else(|| PyValueError::new_err(format!("missing scalar {key}")))?
            .extract()
    };
    let live = p.rich_compare(0, CompareOp::Gt)?;
    let w = analysis_draw_weights(r, &p)?
        .get_item(&live)?
        .call_method0("ravel")?;
    let mq = weighted(
        cast_float(
            &r.getattr("min_cash")?
                .get_item(&live)?
                .call_method0("ravel")?,
        )?,
        w.clone(),
        &qs,
    )?;
    let hw = p.matmul(r.getattr("hr_count")?)?.extract::<f64>()?;
    let headroom = if hw > 0.0 {
        let kw = PyDict::new(py);
        kw.set_item("normalise", false)?;
        let h =
            r.getattr("counts")?
                .get_item("headroom")?
                .call_method("weighted", (&p,), Some(&kw))?;
        let hq = analysis_pooled_quantiles(&r.getattr("bins")?.get_item("headroom")?, &h, &qs)?;
        let h = PyDict::new(py);
        for (i, key) in ["p5_cents", "p50_cents", "p95_cents"].iter().enumerate() {
            h.set_item(key, hq.get_item(i)?.extract::<f64>()?)?;
        }
        h.set_item(
            "negative_p",
            p.matmul(r.getattr("hr_negative")?)?
                .div(hw)?
                .extract::<f64>()?,
        )?;
        h.into_any()
    } else {
        py.None().into_bound(py)
    };
    let mins = r
        .getattr("min_headroom")?
        .get_item(live)?
        .call_method0("ravel")?;
    let has = mins.rich_compare(i64::MAX, CompareOp::Ne)?;
    let low = if has.call_method0("any")?.is_truthy()? {
        let weights = w.get_item(&has)?;
        Some(weighted(
            cast_float(&mins.get_item(has)?)?,
            weights.div(weights.call_method0("sum")?)?,
            &qs,
        )?)
    } else {
        None
    };
    let kq = analysis_collected_quantiles(r, &p)?;
    let per_day = r.getattr("per_day")?;
    let horizon = |key: &str| -> PyResult<f64> {
        p.matmul(
            per_day
                .get_item(key)?
                .get_item((PySlice::full(py), -1))?
                .div(r.getattr("draws")?)?,
        )?
        .extract()
    };
    let due = horizon("due_cum")?;
    let past = horizon("past_due")?;
    let out = PyDict::new(py);
    out.set_item("due_horizon_cents", due)?;
    out.set_item("past_due_horizon_cents", past)?;
    out.set_item("frozen_due_cents", due - e("collected")? - past)?;
    out.set_item(
        "collection_rate",
        if due != 0.0 {
            Some(e("collected")? / due)
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
        out.set_item(key, e(source)?)?;
    }
    for (i, key) in [
        "collected_p5_cents",
        "collected_p50_cents",
        "collected_p95_cents",
    ]
    .iter()
    .enumerate()
    {
        out.set_item(key, kq.get_item(i)?.extract::<f64>()?)?;
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
        out.set_item(key, e(source)?)?;
    }
    // Python min(1.0, NaN) keeps its first argument; f64::min agrees here.
    out.set_item("petition_p", 1.0_f64.min(e("petition_p")?))?;
    out.set_item("headroom_at_due", headroom)?;
    for (i, key) in ["min_headroom_p5_cents", "min_headroom_p50_cents"]
        .iter()
        .enumerate()
    {
        out.set_item(
            key,
            match &low {
                Some(a) => Some(a.get_item(i)?.extract::<f64>()?),
                None => None,
            },
        )?;
    }
    out.set_item("min_cash_mean_cents", e("min_cash")?)?;
    out.set_item("min_cash_p5_cents", mq.get_item(0)?.extract::<f64>()?)?;
    out.set_item("shortfall_p", 1.0_f64.min(e("shortfall_p")?))?;
    for (key, source) in [
        ("shortfall_mean_cents", "shortfall"),
        ("peak_locked_cents", "peak_locked"),
        ("peak_capacity_cents", "peak_capacity"),
        ("horizon_cash_mean_cents", "horizon_cash"),
    ] {
        out.set_item(key, e(source)?)?;
    }
    out.set_item(
        "full_collection_by_maturity_p",
        1.0_f64.min(e("recovered_all")?),
    )?;
    out.set_item("uncollected_maturity_cents", e("unrecovered")?)?;
    Ok(out)
}

fn ints<'py>(a: &Obj<'py>) -> PyResult<Obj<'py>> {
    let numpy = np(a.py())?;
    numpy
        .getattr("rint")?
        .call1((a,))?
        .call_method1("astype", (numpy.getattr("int64")?,))?
        .call_method0("tolist")
}

#[pyfunction]
fn analysis_daily<'py>(
    r: &Obj<'py>,
    probs: &Obj<'py>,
    collected_q: bool,
) -> PyResult<Bound<'py, PyDict>> {
    let py = r.py();
    let p = as_float(probs)?;
    let daily = PyDict::new(py);
    for (k, v) in r.getattr("per_day")?.cast::<PyDict>()?.iter() {
        daily.set_item(k, p.matmul(v)?.div(r.getattr("draws")?)?)?;
    }
    let e = |key: &str| -> PyResult<Obj<'py>> {
        daily
            .get_item(key)?
            .ok_or_else(|| PyValueError::new_err(format!("missing series {key}")))
    };
    let qs = py.import("app.analysis.core")?.getattr("QS")?;
    let quantile = |name: &str| -> PyResult<Obj<'py>> {
        let h = r
            .getattr("counts")?
            .get_item(name)?
            .call_method1("weighted", (&p,))?;
        r.getattr("bins")?
            .get_item(name)?
            .call_method1("quantiles", (h, &qs))
    };
    let cq = quantile("cash")?;
    let kq = if collected_q {
        let q = quantile("collected")?;
        q.set_item(
            (PySlice::full(py), -1),
            analysis_collected_quantiles(r, &p)?,
        )?;
        Some(q)
    } else {
        None
    };
    let cum_p = e("petitioned")?;
    let kw = PyDict::new(py);
    let numpy = np(py)?;
    kw.set_item("out", numpy.getattr("zeros")?.call1((r.getattr("days")?,))?)?;
    kw.set_item("where", cum_p.rich_compare(0, CompareOp::Gt)?)?;
    let exposure = numpy
        .getattr("divide")?
        .call((e("frozen")?, &cum_p), Some(&kw))?;
    let (limit_mean, limit_p5) = analysis_limit_summary(&r.getattr("limit")?)?;
    let out = PyDict::new(py);
    out.set_item("cash_mean", ints(&e("cash")?)?)?;
    for (i, key) in ["cash_p5", "cash_p50", "cash_p95"].iter().enumerate() {
        out.set_item(key, ints(&cq.get_item(i)?)?)?;
    }
    out.set_item("backup_liquidity_mean", ints(&e("backup")?)?)?;
    out.set_item(
        "backup_liquidity_p5",
        if r.getattr("setup")?.getattr("facility_cents")?.eq(0)? {
            ints(&cq.get_item(0)?)?
        } else {
            py.None().into_bound(py)
        },
    )?;
    out.set_item("collected_mean", ints(&e("collected")?)?)?;
    if let Some(q) = kq {
        for (i, key) in ["collected_p5", "collected_p50", "collected_p95"]
            .iter()
            .enumerate()
        {
            out.set_item(key, ints(&q.get_item(i)?)?)?;
        }
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
        out.set_item(key, ints(&e(source)?)?)?;
    }
    out.set_item("limit_mean", ints(&limit_mean)?)?;
    out.set_item("limit_p5", ints(&limit_p5)?)?;
    out.set_item("petition_cum_p", cum_p.call_method0("tolist")?)?;
    out.set_item("petition_exposure_mean", ints(&exposure)?)?;
    for (key, source) in [
        ("frozen_mean", "frozen"),
        ("frozen_due_mean", "frozen_due"),
        ("past_due_mean", "past_due"),
        ("clawback_mean", "clawback"),
    ] {
        out.set_item(key, ints(&e(source)?)?)?;
    }
    Ok(out)
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(analysis_scalars, m)?)?;
    m.add_function(wrap_pyfunction!(analysis_reduce_add, m)?)?;
    m.add_function(wrap_pyfunction!(analysis_draw_weights, m)?)?;
    m.add_function(wrap_pyfunction!(analysis_first_floor, m)?)?;
    m.add_function(wrap_pyfunction!(analysis_collected_quantiles, m)?)?;
    m.add_function(wrap_pyfunction!(analysis_expectation, m)?)?;
    m.add_function(wrap_pyfunction!(analysis_fees, m)?)?;
    m.add_function(wrap_pyfunction!(analysis_quantile, m)?)?;
    m.add_function(wrap_pyfunction!(analysis_limit_summary, m)?)?;
    m.add_function(wrap_pyfunction!(analysis_bins_spanning, m)?)?;
    m.add_function(wrap_pyfunction!(analysis_pooled_quantiles, m)?)?;
    m.add_function(wrap_pyfunction!(analysis_merge_range, m)?)?;
    m.add_function(wrap_pyfunction!(analysis_event_range, m)?)?;
    m.add_function(wrap_pyfunction!(analysis_bins_from, m)?)?;
    m.add_function(wrap_pyfunction!(analysis_installments, m)?)?;
    m.add_function(wrap_pyfunction!(analysis_petition_at, m)?)?;
    m.add_function(wrap_pyfunction!(analysis_peak_day, m)?)?;
    m.add_function(wrap_pyfunction!(analysis_stress_row, m)?)?;
    m.add_function(wrap_pyfunction!(analysis_metrics, m)?)?;
    m.add_function(wrap_pyfunction!(analysis_daily, m)?)?;
    Ok(())
}

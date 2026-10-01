//! Native equity-channel, offering and coupon semantics over the boundary state.
use crate::events::{NativeChain, BIG};
use crate::price::{grid_prices, round_int};
use ndarray::{Array1, Array2};
use numpy::{IntoPyArray, PyArray1, PyArray2, PyArrayMethods, PyReadonlyArray1};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict, PyInt, PyList, PyString, PyTuple};
use pyo3::IntoPyObjectExt;

enum Days<'a> {
    Scalar(i64),
    Vector(PyReadonlyArray1<'a, i64>, bool),
}
impl Days<'_> {
    fn at(&self, i: usize) -> i64 {
        match self {
            Self::Scalar(v) => *v,
            Self::Vector(a, broadcast) => a.as_array()[if *broadcast { 0 } else { i }],
        }
    }
}
fn days<'a>(obj: &'a Bound<'_, PyAny>, n: usize) -> PyResult<Days<'a>> {
    if let Ok(a) = obj.cast::<PyArray1<i64>>() {
        let read = a.try_readonly()?;
        let len = read.as_array().len();
        if len != n && len != 1 {
            return Err(PyValueError::new_err("expected one day per draw"));
        }
        Ok(Days::Vector(read, len == 1))
    } else {
        Ok(Days::Scalar(obj.extract()?))
    }
}
fn array(py: Python<'_>, a: Array1<i64>) -> Py<PyAny> {
    a.into_pyarray(py).into_any().unbind()
}
fn boolean(py: Python<'_>, a: Array1<bool>) -> Py<PyAny> {
    a.into_pyarray(py).into_any().unbind()
}
fn none(py: Python<'_>) -> Py<PyAny> {
    py.None()
}
fn int_obj(py: Python<'_>, v: i64) -> Py<PyAny> {
    PyInt::new(py, v).into_any().unbind()
}
fn str_obj(py: Python<'_>, v: &str) -> Py<PyAny> {
    PyString::new(py, v).into_any().unbind()
}
fn param<'py>(c: &NativeChain, py: Python<'py>, name: &str) -> PyResult<Bound<'py, PyAny>> {
    c.get(py, "m")?.get_item("parameters")?.get_item(name)
}
fn day_arg<'py>(
    c: &NativeChain,
    py: Python<'py>,
    args: &Bound<'py, PyTuple>,
) -> PyResult<Bound<'py, PyAny>> {
    if !args.is_empty() && !args.get_item(0)?.is_none() {
        args.get_item(0)
    } else {
        c.get(py, "_at")
    }
}
fn tuple_call(
    c: &NativeChain,
    py: Python<'_>,
    name: &str,
    args: Vec<Py<PyAny>>,
) -> PyResult<Py<PyAny>> {
    c.invoke_args(py, name, args)
}
fn attr_i(obj: &Bound<'_, PyAny>, name: &str) -> PyResult<i64> {
    obj.getattr(name)?.extract()
}
fn dict_matches(obj: &Bound<'_, PyDict>, key: &str, text: &str) -> PyResult<bool> {
    obj.get_item(key)?
        .map(|v| v.eq(text))
        .transpose()
        .map(|v| v.unwrap_or(false))
}
fn review(c: &NativeChain, py: Python<'_>) -> PyResult<i64> {
    c.get(py, "s")?
        .getattr("review")?
        .call_method0("toordinal")?
        .extract()
}
fn date_ordinal(obj: &Bound<'_, PyAny>) -> PyResult<i64> {
    obj.call_method0("toordinal")?.extract()
}
pub(crate) fn iso_ordinal(value: &str) -> PyResult<i64> {
    let mut parts = value.split('-');
    let mut next = || {
        parts
            .next()
            .ok_or_else(|| PyValueError::new_err("invalid ISO date"))?
            .parse::<i64>()
            .map_err(|_| PyValueError::new_err("invalid ISO date"))
    };
    let p = [next()?, next()?, next()?];
    if parts.next().is_some() || !(1..=9999).contains(&p[0]) || !(1..=12).contains(&p[1]) {
        return Err(PyValueError::new_err("invalid ISO date"));
    }
    let leap = p[0] % 4 == 0 && (p[0] % 100 != 0 || p[0] % 400 == 0);
    let max = match p[1] {
        2 => {
            if leap {
                29
            } else {
                28
            }
        }
        4 | 6 | 9 | 11 => 30,
        _ => 31,
    };
    if !(1..=max).contains(&p[2]) {
        return Err(PyValueError::new_err("invalid ISO date"));
    }
    let y = p[0] - i64::from(p[1] <= 2);
    let era = y.div_euclid(400);
    let yoe = y - era * 400;
    let mp = p[1] + if p[1] > 2 { -3 } else { 9 };
    let doy = (153 * mp + 2) / 5 + p[2] - 1;
    Ok(era * 146097 + yoe * 365 + yoe / 4 - yoe / 100 + doy - 305)
}
fn weekday(d: i64) -> bool {
    (d - 1).rem_euclid(7) < 5
}
fn bank_holiday(d: i64) -> bool {
    const HOLIDAYS: &[&str] = &[
        "2024-01-01",
        "2024-01-15",
        "2024-02-19",
        "2024-05-27",
        "2024-06-19",
        "2024-07-04",
        "2024-09-02",
        "2024-10-14",
        "2024-11-11",
        "2024-11-28",
        "2024-12-25",
        "2025-01-01",
        "2025-01-20",
        "2025-02-17",
        "2025-05-26",
        "2025-06-19",
        "2025-07-04",
        "2025-09-01",
        "2025-10-13",
        "2025-11-11",
        "2025-11-27",
        "2025-12-25",
        "2026-01-01",
        "2026-01-19",
        "2026-02-16",
        "2026-05-25",
        "2026-06-19",
        "2026-09-07",
        "2026-10-12",
        "2026-11-11",
        "2026-11-26",
        "2026-12-25",
    ];
    HOLIDAYS.iter().any(|s| iso_ordinal(s).ok() == Some(d))
}
pub(crate) fn business(d: i64) -> bool {
    weekday(d) && !bank_holiday(d)
}
fn trading(d: i64) -> bool {
    const CLOSED: &[&str] = &["2024-03-29", "2025-04-18", "2026-04-03"];
    const OPEN: &[&str] = &[
        "2024-10-14",
        "2024-11-11",
        "2025-10-13",
        "2025-11-11",
        "2026-10-12",
        "2026-11-11",
    ];
    weekday(d)
        && !CLOSED.iter().any(|s| iso_ordinal(s).ok() == Some(d))
        && (!bank_holiday(d) || OPEN.iter().any(|s| iso_ordinal(s).ok() == Some(d)))
}
pub(crate) fn after(mut d: i64, n: i64, banking: bool) -> i64 {
    let mut k = 0;
    while k < n {
        d += 1;
        if if banking { business(d) } else { weekday(d) } {
            k += 1;
        }
    }
    d
}
fn close_price(c: &NativeChain, py: Python<'_>) -> PyResult<f64> {
    param(c, py, "atm_pace_bps")?
        .get_item("price_cents")?
        .extract()
}
fn floor_div(a: i64, b: i64) -> i64 {
    if b == 0 {
        return 0;
    }
    if a == i64::MIN && b == -1 {
        return i64::MIN;
    }
    let q = a / b;
    let r = a % b;
    if r != 0 && (r < 0) != (b < 0) {
        q - 1
    } else {
        q
    }
}

pub(crate) fn dispatch(
    c: &NativeChain,
    py: Python<'_>,
    name: &str,
    args: &Bound<'_, PyTuple>,
) -> Option<PyResult<Py<PyAny>>> {
    let supported = [
        "_reprice",
        "share_price_on",
        "_atm_schedule",
        "_offer_stack",
        "_offer_shares_on",
        "_eq_key",
        "_lockup",
        "_atm_columns",
        "_atm_rebook",
        "_atm_rebook_fast",
        "_atm_rebook_py",
        "atm_to_date",
        "atm_shares_to_date",
        "ledger_left",
        "offering_price_x1e4",
        "offering_terms",
        "offering_pending_on",
        "offering_pending",
        "notes_due_day",
        "first_unpaid",
        "listing_status",
        "offering_available",
        "offer_available",
        "initiation_rule",
        "offer_shortfall",
        "option_group",
        "book_grouped",
        "initiate",
        "_close",
        "offering_outcome",
        "offering_day",
        "listing_dates",
        "repurchase_day",
        "holder_route_days",
        "coupon_cash_cents",
        "_priced_coupon",
        "instrument_cash",
        "_coupon_rebook",
        "coupon_when_due",
    ];
    if !supported.contains(&name) {
        return None;
    }
    Some(execute(
        c,
        py,
        if name == "_atm_rebook_py" {
            "_atm_rebook_fast"
        } else {
            name
        },
        args,
    ))
}

fn execute(
    c: &NativeChain,
    py: Python<'_>,
    name: &str,
    args: &Bound<'_, PyTuple>,
) -> PyResult<Py<PyAny>> {
    let n = c.n(py)?;
    let horizon = c.days(py)?;
    match name {
        "_reprice" => {
            let model = c.get(py, "merton")?;
            if model.is_none() {
                return Ok(none(py));
            }
            let key = c.call0(py, "_owed_key")?;
            if c.get(py, "_price_key")?.eq(key.bind(py))? {
                return Ok(none(py));
            }
            c.put(py, "_price_key", key.bind(py))?;
            let owed = c.call0(py, "_price_owed_grid")?;
            let owed_array = owed.bind(py).cast::<PyArray2<i64>>()?.readonly();
            let a = owed_array.as_array();
            let v: f64 = model.getattr("V")?.extract()?;
            let notes: i64 = model.getattr("notes_cents")?.extract()?;
            let s: f64 = model.getattr("asset_vol")?.extract()?;
            let rate: f64 = model.getattr("rate")?.extract()?;
            let t: f64 = model.getattr("T")?.extract()?;
            let shares: i64 = model.getattr("shares")?.extract()?;
            let close = close_price(c, py)?;
            let mut price = py.detach(|| grid_prices(a, v, notes, s, rate, t, shares))?;
            for (p, &owed) in price.iter_mut().zip(a.iter()) {
                if owed <= 0 {
                    *p = close;
                }
            }
            let old = c.get(py, "share_price")?;
            let changed = if old.is_none() {
                true
            } else {
                let old = old.cast::<PyArray2<f64>>()?.readonly();
                let old = old.as_array();
                price.dim() != old.dim() || price.iter().zip(old).any(|(a, b)| a != b)
            };
            if changed {
                c.put_i64(py, "_price_v", c.int(py, "_price_v")? + 1)?;
                c.put(py, "share_price", &price.into_pyarray(py).into_any())?;
            }
            Ok(none(py))
        }
        "share_price_on" => {
            let day = day_arg(c, py, args)?;
            if !c.get(py, "share_price")?.is_none() {
                c.call0(py, "_reprice")?;
            }
            let price = c.get(py, "share_price")?;
            let close = close_price(c, py)?;
            if let Ok(day2) = day.cast::<PyArray2<i64>>() {
                let day2 = day2.readonly();
                let d = day2.as_array();
                if d.nrows() != n {
                    return Err(PyValueError::new_err(
                        "share-price days must have one row per draw",
                    ));
                }
                let out = if price.is_none() {
                    Array2::from_elem(d.dim(), close)
                } else {
                    let price = price.cast::<PyArray2<f64>>()?.readonly();
                    let p = price.as_array();
                    Array2::from_shape_fn(d.dim(), |(r, k)| {
                        p[[r, d[[r, k]].clamp(0, horizon as i64 - 1) as usize]]
                    })
                };
                Ok(out.into_pyarray(py).into_any().unbind())
            } else {
                let d = days(&day, n)?;
                let out = if price.is_none() {
                    Array1::from_elem(n, close)
                } else {
                    let price = price.cast::<PyArray2<f64>>()?.readonly();
                    let p = price.as_array();
                    Array1::from_shape_fn(n, |r| {
                        p[[r, d.at(r).clamp(0, horizon as i64 - 1) as usize]]
                    })
                };
                Ok(out.into_pyarray(py).into_any().unbind())
            }
        }
        "_atm_schedule" => {
            if let Some(memo) = c.opt(py, "_atm_memo")? {
                if !memo.is_none() {
                    return Ok(memo.unbind());
                }
            }
            let p = param(c, py, "atm_pace_bps")?;
            let price: i64 = p.get_item("price_cents")?.extract()?;
            if price == 0 {
                return Err(pyo3::exceptions::PyZeroDivisionError::new_err(
                    "zero ATM price",
                ));
            }
            let q = p
                .get_item("adv_cents")?
                .extract::<i64>()?
                .wrapping_mul(c.p_i64(py, "atm_pace_bps")?)
                .div_euclid(10000)
                .div_euclid(price);
            let net = q
                .wrapping_mul(price)
                .wrapping_mul(10000 - p.get_item("commission_bps")?.extract::<i64>()?)
                .div_euclid(10000);
            let mut d = iso_ordinal(&p.get_item("first_sale")?.extract::<String>()?)?;
            let t1 = iso_ordinal(&p.get_item("t1_from")?.extract::<String>()?)?;
            let end = date_ordinal(&c.get(py, "s")?.getattr("horizon")?)?;
            let review = review(c, py)?;
            let mut sale = Vec::new();
            let mut settle = Vec::new();
            while d <= end {
                if trading(d) {
                    sale.push(d - review - 1);
                    settle.push(after(d, if d < t1 { 2 } else { 1 }, true) - review - 1);
                }
                d += 1;
            }
            let out = PyTuple::new(
                py,
                [
                    array(py, Array1::from(sale)),
                    array(py, Array1::from(settle)),
                    int_obj(py, q),
                    int_obj(py, net),
                ],
            )?;
            c.put(py, "_atm_memo", &out.clone().into_any())?;
            Ok(out.into_any().unbind())
        }
        "_offer_stack" => {
            let version = c.int(py, "_eq_v")?;
            if let Some(memo) = c.opt(py, "_offer_memo")? {
                if !memo.is_none() && memo.get_item(0)?.extract::<i64>()? == version {
                    return Ok(memo.get_item(1)?.unbind());
                }
            }
            let offers = c.get(py, "_offers")?.cast_into::<PyList>()?;
            let out = if offers.is_empty() {
                none(py)
            } else {
                let mut init = Array2::<i64>::zeros((offers.len(), n));
                let mut close = Array2::<i64>::zeros((offers.len(), n));
                let mut shares = Array2::<i64>::zeros((offers.len(), n));
                let mut closed = Array2::<bool>::default((offers.len(), n));
                for (o, offer) in offers.iter().enumerate() {
                    let ii = offer.get_item("init")?;
                    let ii = days(&ii, n)?;
                    let cc = offer.get_item("close")?;
                    let cc = days(&cc, n)?;
                    let ss = offer.get_item("shares")?;
                    let ss = days(&ss, n)?;
                    let rr = offer.get_item("rows")?;
                    let rr = rr.cast::<PyArray1<bool>>()?.readonly();
                    let rr = rr.as_array();
                    let dd = offer.get_item("closed")?;
                    let dd = dd.cast::<PyArray1<bool>>()?.readonly();
                    let dd = dd.as_array();
                    for r in 0..n {
                        init[[o, r]] = ii.at(r);
                        close[[o, r]] = cc.at(r);
                        closed[[o, r]] = dd[r];
                        shares[[o, r]] = if rr[r] { ss.at(r) } else { 0 };
                    }
                }
                PyTuple::new(
                    py,
                    [
                        init.into_pyarray(py).into_any().unbind(),
                        close.into_pyarray(py).into_any().unbind(),
                        closed.into_pyarray(py).into_any().unbind(),
                        shares.into_pyarray(py).into_any().unbind(),
                    ],
                )?
                .into_any()
                .unbind()
            };
            let memo = PyTuple::new(py, [int_obj(py, version), out.clone_ref(py)])?;
            c.put(py, "_offer_memo", &memo.into_any())?;
            Ok(out)
        }
        "_offer_shares_on" => {
            let day = day_arg(c, py, args)?;
            let stacked = c.call0(py, "_offer_stack")?;
            if let Ok(d2) = day.cast::<PyArray2<i64>>() {
                let read = d2.readonly();
                let d = read.as_array();
                if d.nrows() != n {
                    return Err(PyValueError::new_err(
                        "offering-share days must have one row per draw",
                    ));
                }
                let mut out = Array2::<i64>::zeros(d.dim());
                if !stacked.bind(py).is_none() {
                    let st = stacked.bind(py).cast::<PyTuple>()?;
                    let io = st.get_item(0)?;
                    let io = io.cast::<PyArray2<i64>>()?.readonly();
                    let init = io.as_array();
                    let co = st.get_item(1)?;
                    let co = co.cast::<PyArray2<i64>>()?.readonly();
                    let close = co.as_array();
                    let do_ = st.get_item(2)?;
                    let do_ = do_.cast::<PyArray2<bool>>()?.readonly();
                    let closed = do_.as_array();
                    let so = st.get_item(3)?;
                    let so = so.cast::<PyArray2<i64>>()?.readonly();
                    let shares = so.as_array();
                    for o in 0..init.nrows() {
                        for r in 0..n {
                            for k in 0..d.ncols() {
                                if d[[r, k]] >= init[[o, r]]
                                    && (d[[r, k]] < close[[o, r]] || closed[[o, r]])
                                {
                                    out[[r, k]] = out[[r, k]].wrapping_add(shares[[o, r]]);
                                }
                            }
                        }
                    }
                }
                return Ok(out.into_pyarray(py).into_any().unbind());
            }
            if stacked.bind(py).is_none() {
                return Ok(array(py, Array1::zeros(n)));
            }
            let d = days(&day, n)?;
            let mut bytes = Vec::with_capacity(n * 8);
            for r in 0..n {
                bytes.extend_from_slice(&d.at(r).to_ne_bytes());
            }
            let key = PyTuple::new(
                py,
                [
                    int_obj(py, c.int(py, "_eq_v")?),
                    PyBytes::new(py, &bytes).into_any().unbind(),
                ],
            )?;
            if let Some(memo) = c.opt(py, "_shares_memo")? {
                if !memo.is_none() && memo.get_item(0)?.eq(&key)? {
                    return Ok(memo.get_item(1)?.unbind());
                }
            }
            let mut out = Array1::<i64>::zeros(n);
            if !stacked.bind(py).is_none() {
                let st = stacked.bind(py).cast::<PyTuple>()?;
                let io = st.get_item(0)?;
                let io = io.cast::<PyArray2<i64>>()?.readonly();
                let init = io.as_array();
                let co = st.get_item(1)?;
                let co = co.cast::<PyArray2<i64>>()?.readonly();
                let close = co.as_array();
                let do_ = st.get_item(2)?;
                let do_ = do_.cast::<PyArray2<bool>>()?.readonly();
                let closed = do_.as_array();
                let so = st.get_item(3)?;
                let so = so.cast::<PyArray2<i64>>()?.readonly();
                let shares = so.as_array();
                for o in 0..init.nrows() {
                    for r in 0..n {
                        if d.at(r) >= init[[o, r]] && (d.at(r) < close[[o, r]] || closed[[o, r]]) {
                            out[r] = out[r].wrapping_add(shares[[o, r]]);
                        }
                    }
                }
            }
            let out = array(py, out);
            let memo = PyTuple::new(py, [key.into_any().unbind(), out.clone_ref(py)])?;
            c.put(py, "_shares_memo", &memo.into_any())?;
            Ok(out)
        }
        "_eq_key" => {
            c.call0(py, "_reprice")?;
            Ok(
                PyTuple::new(py, [c.int(py, "_eq_v")?, c.int(py, "_price_v")?])?
                    .into_any()
                    .unbind(),
            )
        }
        _ => execute_more(c, py, name, args),
    }
}

fn execute_more(
    c: &NativeChain,
    py: Python<'_>,
    name: &str,
    args: &Bound<'_, PyTuple>,
) -> PyResult<Py<PyAny>> {
    let n = c.n(py)?;
    let horizon = c.days(py)?;
    match name {
        "offering_pending" => tuple_call(
            c,
            py,
            "offering_pending_on",
            vec![c.get(py, "_at")?.unbind()],
        ),
        "notes_due_day" => Ok(c.get(py, "marks")?.get_item("notes_due")?.unbind()),
        "first_unpaid" => {
            let processed = c.call0(py, "processed")?;
            let value = processed.bind(py).get_item(1)?;
            Ok(value
                .cast::<PyArray1<i64>>()?
                .readonly()
                .as_array()
                .to_owned()
                .into_pyarray(py)
                .into_any()
                .unbind())
        }
        "_lockup" => {
            let sale = args.get_item(0)?;
            let sale = sale.cast::<PyArray1<i64>>()?.readonly();
            let sale = sale.as_array();
            let mut out = Array2::<bool>::default((n, sale.len()));
            let parameters = c
                .get(py, "m")?
                .get_item("parameters")?
                .cast_into::<PyDict>()?;
            let lock = parameters.get_item("offering_lockup")?;
            let st = c.call0(py, "_offer_stack")?;
            if let Some(lock) = lock {
                if lock.is_truthy()?
                    && !st.bind(py).is_none()
                    && !lock
                        .cast::<PyDict>()?
                        .get_item("atm_carved_out")?
                        .map(|v| v.is_truthy())
                        .transpose()?
                        .unwrap_or(false)
                {
                    let pricing: i64 = lock.get_item("pricing_days")?.extract()?;
                    let length: i64 = lock.get_item("value")?.extract()?;
                    let io = st.bind(py).get_item(0)?;
                    let io = io.cast::<PyArray2<i64>>()?.readonly();
                    let init = io.as_array();
                    let co = st.bind(py).get_item(1)?;
                    let co = co.cast::<PyArray2<i64>>()?.readonly();
                    let close = co.as_array();
                    let do_ = st.bind(py).get_item(2)?;
                    let do_ = do_.cast::<PyArray2<bool>>()?.readonly();
                    let closed = do_.as_array();
                    for o in 0..init.nrows() {
                        for r in 0..n {
                            for s in 0..sale.len() {
                                out[[r, s]] |= init[[o, r]] < BIG
                                    && sale[s] >= init[[o, r]].wrapping_add(pricing)
                                    && (sale[s] < close[[o, r]]
                                        || (closed[[o, r]]
                                            && sale[s] <= close[[o, r]].wrapping_add(length)));
                            }
                        }
                    }
                }
            }
            Ok(out.into_pyarray(py).into_any().unbind())
        }
        "_atm_columns" => {
            if let Some(memo) = c.opt(py, "_atm_cols")? {
                if !memo.is_none() {
                    return Ok(memo.unbind());
                }
            }
            let schedule = c.call0(py, "_atm_schedule")?;
            let settle = schedule.bind(py).get_item(1)?;
            let settle = settle.cast::<PyArray1<i64>>()?.readonly();
            let settle = settle.as_array();
            let mut cols = (0..settle.len())
                .filter(|&j| settle[j] >= 0 && settle[j] < horizon as i64)
                .collect::<Vec<_>>();
            cols.sort_by_key(|&j| settle[j]);
            let mut ds = Vec::new();
            let mut starts = Vec::new();
            for (i, &j) in cols.iter().enumerate() {
                if ds.last() != Some(&settle[j]) {
                    ds.push(settle[j]);
                    starts.push(i as i64);
                }
            }
            let out = PyTuple::new(
                py,
                [
                    array(py, Array1::from_iter(cols.iter().map(|&j| j as i64))),
                    array(py, Array1::from(ds)),
                    array(py, Array1::from(starts)),
                ],
            )?;
            c.put(py, "_atm_cols", &out.clone().into_any())?;
            Ok(out.into_any().unbind())
        }
        "_atm_rebook_fast" => {
            let schedule = c.call0(py, "_atm_schedule")?;
            let sale = schedule.bind(py).get_item(0)?;
            let sale = sale.cast::<PyArray1<i64>>()?.readonly();
            let q = schedule.bind(py).get_item(2)?.extract::<i64>()?;
            let ev = c.get(py, "ev")?;
            let pet = ev.getattr("petition")?;
            let pet = days(&pet, n)?;
            let delisted = c.get(py, "delisted")?;
            let delisted = days(&delisted, n)?;
            let stop = Array1::from_shape_fn(n, |r| {
                if pet.at(r) < 0 { BIG } else { pet.at(r) }.min(delisted.at(r))
            })
            .into_pyarray(py);
            let stack = c.call0(py, "_offer_stack")?;
            let zero_i = Array2::<i64>::zeros((0, n)).into_pyarray(py);
            let zero_b = Array2::<bool>::default((0, n)).into_pyarray(py);
            let mut init = zero_i.clone().into_any();
            let mut close = init.clone();
            let mut shares = init.clone();
            let mut closed = zero_b.into_any();
            if !stack.bind(py).is_none() {
                init = stack.bind(py).get_item(0)?;
                close = stack.bind(py).get_item(1)?;
                closed = stack.bind(py).get_item(2)?;
                shares = stack.bind(py).get_item(3)?;
            }
            let params = c
                .get(py, "m")?
                .get_item("parameters")?
                .cast_into::<PyDict>()?;
            let lock = params.get_item("offering_lockup")?;
            let lock_on = if let Some(lock) = &lock {
                lock.is_truthy()?
                    && !stack.bind(py).is_none()
                    && !lock
                        .cast::<PyDict>()?
                        .get_item("atm_carved_out")?
                        .map(|v| v.is_truthy())
                        .transpose()?
                        .unwrap_or(false)
            } else {
                false
            };
            let (pricing, length) = if lock_on {
                let lock = lock
                    .as_ref()
                    .ok_or_else(|| PyValueError::new_err("missing lockup"))?;
                (
                    lock.get_item("pricing_days")?.extract::<i64>()?,
                    lock.get_item("value")?.extract::<i64>()?,
                )
            } else {
                (0, 0)
            };
            let cols = c.call0(py, "_atm_columns")?;
            let j = cols.bind(py).get_item(0)?;
            let ds = cols.bind(py).get_item(1)?;
            let starts = cols.bind(py).get_item(2)?;
            let mut price = Array2::<f64>::zeros((1, 1)).into_pyarray(py).into_any();
            let mut use_close = true;
            if j.cast::<PyArray1<i64>>()?.len()? != 0 && !c.get(py, "share_price")?.is_none() {
                c.call0(py, "_reprice")?;
                price = c.get(py, "share_price")?;
                use_close = false;
            }
            let old = c.get(py, "_atm")?;
            let have_old = !old.is_none();
            let old = if have_old {
                old
            } else {
                Array2::<i64>::zeros((1, 1)).into_pyarray(py).into_any()
            };
            let pace = param(c, py, "atm_pace_bps")?;
            let output = crate::atm::atm_book(
                py,
                sale,
                stop.readonly(),
                q,
                param(c, py, "share_ledger")?.get_item("value")?.extract()?,
                init.cast::<PyArray2<i64>>()?.readonly(),
                close.cast::<PyArray2<i64>>()?.readonly(),
                closed.cast::<PyArray2<bool>>()?.readonly(),
                shares.cast::<PyArray2<i64>>()?.readonly(),
                lock_on,
                BIG,
                pricing,
                length,
                j.cast::<PyArray1<i64>>()?.readonly(),
                ds.cast::<PyArray1<i64>>()?.readonly(),
                starts.cast::<PyArray1<i64>>()?.readonly(),
                price.cast::<PyArray2<f64>>()?.readonly(),
                use_close,
                pace.get_item("price_cents")?.extract()?,
                horizon,
                pace.get_item("commission_bps")?.extract()?,
                old.cast::<PyArray2<i64>>()?.readonly(),
                have_old,
            )?;
            Ok((output.0, output.1, output.2, output.3, output.4)
                .into_pyobject(py)?
                .into_any()
                .unbind())
        }
        "_atm_rebook" => {
            if !c.flag(py, "equity")? {
                return Ok(none(py));
            }
            let key = c.call0(py, "_eq_key")?;
            if let Some(old) = c.opt(py, "_atm_v")? {
                if old.eq(key.bind(py))? {
                    return Ok(none(py));
                }
            }
            c.put(py, "_atm_v", key.bind(py))?;
            c.call0(py, "_coupon_rebook")?;
            let out = c.call0(py, "_atm_rebook_fast")?;
            for (i, key) in ["_atm_sold", "_atm_csold", "_atm", "_atm_cum"]
                .iter()
                .enumerate()
            {
                c.put(py, key, &out.bind(py).get_item(i)?)?;
            }
            let delta = out.bind(py).get_item(4)?;
            let read = delta.cast::<PyArray2<i64>>()?.readonly();
            let delta = read.as_array();
            if delta.iter().any(|&v| v != 0) {
                for key in ["cash", "k:inflow"] {
                    let writable = tuple_call(c, py, "_evw", vec![str_obj(py, key)])?;
                    let mut write = writable.bind(py).cast::<PyArray2<i64>>()?.try_readwrite()?;
                    let mut target = write.as_array_mut();
                    for (a, &b) in target.iter_mut().zip(delta.iter()) {
                        *a = a.wrapping_add(b);
                    }
                }
                tuple_call(
                    c,
                    py,
                    "_touch",
                    vec![str_obj(py, "cash"), str_obj(py, "k:inflow")],
                )?;
            }
            Ok(none(py))
        }
        "atm_to_date" => {
            if c.get(py, "_atm")?.is_none() {
                return Ok(array(py, Array1::zeros(n)));
            }
            let day = day_arg(c, py, args)?;
            let d = days(&day, n)?;
            let cum = c.get(py, "_atm_cum")?;
            let cum = cum.cast::<PyArray2<i64>>()?.readonly();
            let cum = cum.as_array();
            Ok(array(
                py,
                Array1::from_shape_fn(n, |r| {
                    if d.at(r) < 0 {
                        0
                    } else {
                        cum[[r, d.at(r).clamp(0, horizon as i64 - 1) as usize]]
                    }
                }),
            ))
        }
        "atm_shares_to_date" => {
            if c.get(py, "_atm")?.is_none() {
                return Ok(array(py, Array1::zeros(n)));
            }
            let day = day_arg(c, py, args)?;
            let d = days(&day, n)?;
            let schedule = c.call0(py, "_atm_schedule")?;
            let sale = schedule.bind(py).get_item(0)?;
            let sale = sale.cast::<PyArray1<i64>>()?.readonly();
            let sale = sale.as_array();
            let q = schedule.bind(py).get_item(2)?.extract::<i64>()?;
            let cs = c.get(py, "_atm_csold")?;
            let cs = cs.cast::<PyArray2<i64>>()?.readonly();
            let cs = cs.as_array();
            let out = Array1::from_shape_fn(n, |r| {
                let mut lo = 0;
                let mut hi = sale.len();
                while lo < hi {
                    let mid = (lo + hi) / 2;
                    if sale[mid] <= d.at(r) {
                        lo = mid + 1;
                    } else {
                        hi = mid;
                    }
                }
                if lo == 0 {
                    0
                } else {
                    cs[[r, lo - 1]].wrapping_mul(q)
                }
            });
            Ok(array(py, out))
        }
        "ledger_left" => {
            let day = day_arg(c, py, args)?;
            let atm = tuple_call(c, py, "atm_shares_to_date", vec![day.clone().unbind()])?;
            let held = tuple_call(c, py, "_offer_shares_on", vec![day.clone().unbind()])?;
            let a = atm.bind(py).cast::<PyArray1<i64>>()?.readonly();
            let a = a.as_array();
            let h = held.bind(py).cast::<PyArray1<i64>>()?.readonly();
            let h = h.as_array();
            let ledger = param(c, py, "share_ledger")?
                .get_item("value")?
                .extract::<i64>()?;
            Ok(array(
                py,
                Array1::from_shape_fn(n, |r| ledger.wrapping_sub(a[r]).wrapping_sub(h[r]).max(0)),
            ))
        }
        "offering_price_x1e4" => {
            let day = day_arg(c, py, args)?;
            let px = tuple_call(c, py, "share_price_on", vec![day.clone().unbind()])?;
            let px = px.bind(py).cast::<PyArray1<f64>>()?.readonly();
            let px = px.as_array();
            let p = param(c, py, "offering_price")?;
            let january = p.get_item("january_price_cents")?.extract::<i64>()?;
            let prior = p
                .get_item("january_prior_close_cents_x1e4")?
                .extract::<i64>()?;
            Ok(array(
                py,
                Array1::from_shape_fn(n, |r| {
                    floor_div(
                        floor_div(
                            round_int(px[r] * 10000.0)
                                .wrapping_mul(january)
                                .wrapping_mul(10000)
                                .wrapping_mul(2),
                            prior,
                        )
                        .wrapping_add(1),
                        2,
                    )
                }),
            ))
        }
        "offering_terms" => {
            let day = day_arg(c, py, args)?;
            let prices = tuple_call(c, py, "offering_price_x1e4", vec![day.clone().unbind()])?;
            let capacity = tuple_call(c, py, "ledger_left", vec![day.clone().unbind()])?;
            let p = param(c, py, "offering_price")?;
            let gross0 = p.get_item("gross_cents")?.extract::<i64>()?;
            let net0 = p.get_item("net_cents")?.extract::<i64>()?;
            let close_days = p.get_item("close_days")?.extract::<i64>()?;
            let px = prices.bind(py).cast::<PyArray1<i64>>()?.readonly();
            let px = px.as_array();
            let cap = capacity.bind(py).cast::<PyArray1<i64>>()?.readonly();
            let cap = cap.as_array();
            let full = Array1::from_shape_fn(n, |r| {
                floor_div(
                    floor_div(gross0.wrapping_mul(10000).wrapping_mul(2), px[r]).wrapping_add(1),
                    2,
                )
            });
            let shares = Array1::from_shape_fn(n, |r| full[r].min(cap[r]));
            let gross = Array1::from_shape_fn(n, |r| {
                if shares[r] < full[r] {
                    floor_div(shares[r].wrapping_mul(px[r]), 10000)
                } else {
                    gross0
                }
            });
            let net = Array1::from_shape_fn(n, |r| {
                if shares[r] < full[r] {
                    floor_div(gross[r].wrapping_mul(net0), gross0)
                } else {
                    net0
                }
            });
            let costs = Array1::from_shape_fn(n, |r| gross[r].wrapping_sub(net[r]));
            let out = PyDict::new(py);
            for (key, a) in [("gross", gross), ("costs", costs), ("net", net)] {
                out.set_item(key, a.into_pyarray(py))?;
            }
            out.set_item("price_cents_x1e4", prices)?;
            out.set_item("shares", shares.into_pyarray(py))?;
            out.set_item(
                "close_days",
                Array1::from_elem(n, close_days).into_pyarray(py),
            )?;
            Ok(out.into_any().unbind())
        }
        "offering_pending_on" => {
            let day = args.get_item(0)?;
            let d = days(&day, n)?;
            let offers = c.get(py, "_offers")?.cast_into::<PyList>()?;
            let mut out = Array1::<bool>::default(n);
            for o in offers.iter() {
                let init = o.get_item("init")?;
                let init = days(&init, n)?;
                let close = o.get_item("close")?;
                let close = days(&close, n)?;
                let rows = o.get_item("rows")?;
                let rows = rows.cast::<PyArray1<bool>>()?.readonly();
                let rows = rows.as_array();
                for r in 0..n {
                    out[r] |= rows[r] && d.at(r) >= init.at(r) && d.at(r) < close.at(r);
                }
            }
            Ok(boolean(py, out))
        }
        "listing_status" => {
            let day = day_arg(c, py, args)?;
            let d = days(&day, n)?;
            let delist = c.get(py, "delisted")?;
            let delist = days(&delist, n)?;
            let suspend = c.get(py, "suspended")?;
            let suspend = days(&suspend, n)?;
            let hearing = c.get(py, "hearing_requested")?;
            let hearing = days(&hearing, n)?;
            // Unicode array creation is a representation conversion at the boundary.
            let values = PyList::new(
                py,
                (0..n).map(|r| {
                    if d.at(r) >= delist.at(r) {
                        "delisted"
                    } else if d.at(r) >= suspend.at(r) {
                        "suspended"
                    } else if d.at(r) >= hearing.at(r) {
                        "hearing_requested"
                    } else {
                        "listed"
                    }
                }),
            )?;
            Ok(py
                .import("numpy")?
                .getattr("array")?
                .call1((values, "<U17"))?
                .unbind())
        }
        "offering_available" => {
            let day = args.get_item(0)?;
            let d = days(&day, n)?;
            let ev = c.get(py, "ev")?;
            let pet = ev.getattr("petition")?;
            let pet = days(&pet, n)?;
            let suspend = c.get(py, "suspended")?;
            let suspend = days(&suspend, n)?;
            let delist = c.get(py, "delisted")?;
            let delist = days(&delist, n)?;
            let pending = tuple_call(c, py, "offering_pending_on", vec![day.clone().unbind()])?;
            let pending = pending.bind(py).cast::<PyArray1<bool>>()?.readonly();
            let pending = pending.as_array();
            let cap = tuple_call(c, py, "ledger_left", vec![day.clone().unbind()])?;
            let cap = cap.bind(py).cast::<PyArray1<i64>>()?.readonly();
            let cap = cap.as_array();
            Ok(boolean(
                py,
                Array1::from_shape_fn(n, |r| {
                    d.at(r) >= 0
                        && d.at(r) < horizon as i64
                        && d.at(r) < if pet.at(r) < 0 { BIG } else { pet.at(r) }
                        && d.at(r) < suspend.at(r)
                        && d.at(r) < delist.at(r)
                        && !pending[r]
                        && cap[r] > 0
                }),
            ))
        }
        "initiation_rule" => {
            let parameters = c.get(py, "m")?.get_item("parameters")?;
            if parameters.contains("offering_materiality")? {
                Ok(c.parameter(py, "offering_materiality")?
                    .str()?
                    .into_any()
                    .unbind())
            } else {
                Ok(str_obj(py, "any_proceeds"))
            }
        }
        "offer_shortfall" => {
            if horizon == 0 {
                return Err(PyValueError::new_err(
                    "offering shortfall requires a nonempty horizon",
                ));
            }
            let day = args.get_item(0)?;
            let d = days(&day, n)?;
            let clipped = Array1::from_shape_fn(n, |r| d.at(r).clamp(0, horizon as i64 - 1));
            let cash = tuple_call(c, py, "cash_at", vec![array(py, clipped)])?;
            let cash = cash.bind(py).cast::<PyArray1<i64>>()?.try_readonly()?;
            let cash = cash.as_array();
            let pending = c.get(py, "pending_levy")?;
            let levy = if !pending.is_none() && !c.get(py, "d")?.is_none() {
                let pending_days = days(&pending, n)?;
                let live = tuple_call(c, py, "live", vec![day.clone().unbind()])?;
                let live = live.bind(py).cast::<PyArray1<bool>>()?.try_readonly()?;
                let live = live.as_array();
                let stayed = c.get(py, "stayed_from")?;
                let stayed = days(&stayed, n)?;
                let processing = if c.flag(py, "daily")? {
                    Some(tuple_call(
                        c,
                        py,
                        "processing_balance",
                        vec![day.clone().unbind()],
                    )?)
                } else {
                    None
                };
                let processing = processing
                    .as_ref()
                    .map(|v| -> PyResult<_> {
                        Ok(v.bind(py).cast::<PyArray1<i64>>()?.try_readonly()?)
                    })
                    .transpose()?;
                let reach = processing
                    .as_ref()
                    .map_or_else(|| cash.view(), |v| v.as_array());
                let owed = tuple_call(
                    c,
                    py,
                    "owed_at",
                    vec![day.clone().unbind(), true.into_py_any(py)?],
                )?;
                let owed = owed.bind(py).cast::<PyArray1<i64>>()?.try_readonly()?;
                let owed = owed.as_array();
                Some(Array1::from_shape_fn(n, |r| {
                    if pending_days.at(r) == d.at(r)
                        && live[r]
                        && d.at(r) < stayed.at(r)
                        && d.at(r) < horizon as i64
                    {
                        owed[r].min(reach[r].max(0))
                    } else {
                        0
                    }
                }))
            } else {
                None
            };
            let basis = c.get(py, "basis")?;
            let need = basis.getattr("need")?;
            let need = need.cast::<PyArray2<i64>>()?.try_readonly()?;
            let need = need.as_array();
            let rows = c.get(py, "rows")?;
            let rows = rows.cast::<PyArray1<i64>>()?.try_readonly()?;
            let rows = rows.as_array();
            if cash.len() != n
                || rows.len() != n
                || need.ncols() < horizon
                || rows.iter().any(|&r| r < 0 || r as usize >= need.nrows())
            {
                return Err(PyValueError::new_err("offering shortfall shape mismatch"));
            }
            Ok(array(
                py,
                Array1::from_shape_fn(n, |r| {
                    let available = cash[r].wrapping_sub(levy.as_ref().map_or(0, |a| a[r]));
                    need[[
                        rows[r] as usize,
                        d.at(r).clamp(0, horizon as i64 - 1) as usize,
                    ]]
                    .wrapping_sub(available)
                    .max(0)
                }),
            ))
        }
        "offer_available" => {
            if !c.flag(py, "equity")? {
                return Ok(array(py, Array1::zeros(n)));
            }
            let day = args.get_item(0)?;
            let d = days(&day, n)?;
            let available = tuple_call(c, py, "offering_available", vec![day.clone().unbind()])?;
            let available = available.bind(py).cast::<PyArray1<bool>>()?.readonly();
            let available = available.as_array();
            let terms = tuple_call(c, py, "offering_terms", vec![day.clone().unbind()])?;
            let net = terms.bind(py).get_item("net")?;
            let net = net.cast::<PyArray1<i64>>()?.readonly();
            let net = net.as_array();
            let rule = tuple_call(c, py, "initiation_rule", vec![])?;
            let shortfall = if rule.bind(py).eq("covers_shortfall")? {
                Some(tuple_call(
                    c,
                    py,
                    "offer_shortfall",
                    vec![day.clone().unbind()],
                )?)
            } else {
                None
            };
            let shortfall = shortfall
                .as_ref()
                .map(|v| -> PyResult<_> { Ok(v.bind(py).cast::<PyArray1<i64>>()?.try_readonly()?) })
                .transpose()?;
            let shortfall = shortfall.as_ref().map(|v| v.as_array());
            Ok(array(
                py,
                Array1::from_shape_fn(n, |r| {
                    if d.at(r) < horizon as i64
                        && available[r]
                        && shortfall.as_ref().is_none_or(|short| net[r] >= short[r])
                    {
                        net[r]
                    } else {
                        0
                    }
                }),
            ))
        }
        "option_group" => {
            let node = args.get_item(0)?.extract::<String>()?;
            let day = args.get_item(1)?;
            let d = days(&day, n)?;
            let ev = c.get(py, "ev")?;
            let pet = ev.getattr("petition")?;
            let pet = days(&pet, n)?;
            let mut on = Array1::from_shape_fn(n, |r| {
                d.at(r) >= 0
                    && d.at(r) < horizon as i64
                    && d.at(r) < if pet.at(r) < 0 { BIG } else { pet.at(r) }
            });
            let mut pay = Array1::<bool>::default(n);
            if ["debtor_response", "judgment_response"].contains(&node.as_str()) {
                let owed = tuple_call(c, py, "owed_at", vec![day.clone().unbind()])?;
                let owed = owed.bind(py).cast::<PyArray1<i64>>()?.readonly();
                let owed = owed.as_array();
                let live = tuple_call(c, py, "live", vec![day.clone().unbind()])?;
                let live = live.bind(py).cast::<PyArray1<bool>>()?.readonly();
                let live = live.as_array();
                let cash = tuple_call(c, py, "cash_at", vec![day.clone().unbind()])?;
                let cash = cash.bind(py).cast::<PyArray1<i64>>()?.readonly();
                let cash = cash.as_array();
                for r in 0..n {
                    on[r] &= live[r] && owed[r] > 0;
                    pay[r] = on[r] && cash[r] >= owed[r];
                }
            }
            let offer = tuple_call(c, py, "offer_available", vec![day.clone().unbind()])?;
            let offer = offer.bind(py).cast::<PyArray1<i64>>()?.readonly();
            let offer = offer.as_array();
            Ok(Array1::from_shape_fn(n, |r| {
                if on[r] {
                    i8::from(pay[r]) + 2 * i8::from(offer[r] > 0)
                } else {
                    -1
                }
            })
            .into_pyarray(py)
            .into_any()
            .unbind())
        }
        "book_grouped" => {
            let node = args.get_item(0)?.extract::<String>()?;
            let branch = args.get_item(1)?;
            let day = args.get_item(2)?;
            let book = args.get_item(3)?;
            if [
                "debtor_response",
                "judgment_response",
                "cash_floor",
                "cash_out",
            ]
            .contains(&node.as_str())
                && (c.flag(py, "pending")? || c.flag(py, "equity")?)
            {
                let groups = tuple_call(
                    c,
                    py,
                    "option_group",
                    vec![str_obj(py, &node), day.clone().unbind()],
                )?;
                c.put(py, "_grp", groups.bind(py))?;
            } else {
                c.put(py, "_grp", py.None().bind(py))?;
            }
            let target = book.getattr("__name__")?.extract::<String>()?;
            if !["respond", "decide_distress", "decide_waiting"].contains(&target.as_str()) {
                return Err(PyValueError::new_err(
                    "book_grouped requires a native transition method",
                ));
            }
            tuple_call(c, py, &target, vec![branch.unbind(), day.unbind()])
        }
        "initiate" => {
            let day = args.get_item(0)?;
            let d = days(&day, n)?;
            let occasion = args.get_item(1)?;
            let rows = tuple_call(c, py, "offering_available", vec![day.clone().unbind()])?;
            let rr = rows.bind(py).cast::<PyArray1<bool>>()?.readonly();
            let r = rr.as_array();
            if !r.iter().any(|&v| v) {
                return Ok(rows.clone_ref(py));
            }
            let terms = tuple_call(c, py, "offering_terms", vec![day.clone().unbind()])?;
            let cd = terms.bind(py).get_item("close_days")?;
            let cd = days(&cd, n)?;
            let shares = terms.bind(py).get_item("shares")?;
            let shares = days(&shares, n)?;
            let net = terms.bind(py).get_item("net")?;
            let net = days(&net, n)?;
            let o = PyDict::new(py);
            o.set_item("occasion", occasion)?;
            o.set_item("rows", rows.clone_ref(py))?;
            o.set_item(
                "init",
                Array1::from_shape_fn(n, |i| if r[i] { d.at(i) } else { BIG }).into_pyarray(py),
            )?;
            o.set_item(
                "close",
                Array1::from_shape_fn(n, |i| {
                    if r[i] {
                        d.at(i).wrapping_add(cd.at(i))
                    } else {
                        BIG
                    }
                })
                .into_pyarray(py),
            )?;
            o.set_item(
                "shares",
                Array1::from_shape_fn(n, |i| if r[i] { shares.at(i) } else { 0 }).into_pyarray(py),
            )?;
            o.set_item(
                "net",
                Array1::from_shape_fn(n, |i| if r[i] { net.at(i) } else { 0 }).into_pyarray(py),
            )?;
            o.set_item("closed", Array1::<bool>::default(n).into_pyarray(py))?;
            o.set_item("booked", false)?;
            c.get(py, "_offers")?.cast::<PyList>()?.append(&o)?;
            c.put_i64(py, "_eq_v", c.int(py, "_eq_v")? + 1)?;
            tuple_call(c, py, "_close", vec![o.into_any().unbind()])?;
            c.call0(py, "_atm_rebook")?;
            Ok(rows.clone_ref(py))
        }
        "_close" => {
            let o = args.get_item(0)?;
            let occasion = o.get_item("occasion")?;
            let outcomes = c.get(py, "_n1")?.cast_into::<PyDict>()?;
            let yes = outcomes.get_item(&occasion)?;
            if yes.is_none() || o.get_item("booked")?.extract::<bool>()? {
                return Ok(none(py));
            }
            let yes = yes
                .ok_or_else(|| PyValueError::new_err("missing offering outcome"))?
                .extract::<bool>()?;
            o.set_item("booked", true)?;
            let ev = c.get(py, "ev")?;
            let pet = ev.getattr("petition")?;
            let pet = days(&pet, n)?;
            let rows = o.get_item("rows")?;
            let rows = rows.cast::<PyArray1<bool>>()?.readonly();
            let rows = rows.as_array();
            let close = o.get_item("close")?;
            let close_d = days(&close, n)?;
            let net = o.get_item("net")?;
            let net = days(&net, n)?;
            let closed = Array1::from_shape_fn(n, |r| {
                yes && rows[r]
                    && close_d.at(r) < if pet.at(r) < 0 { BIG } else { pet.at(r) }
                    && close_d.at(r) < horizon as i64
            });
            let booking_day = array(
                py,
                Array1::from_shape_fn(n, |r| if closed[r] { close_d.at(r) } else { BIG }),
            );
            let booking_net = array(
                py,
                Array1::from_shape_fn(n, |r| if closed[r] { net.at(r) } else { 0 }),
            );
            let closed = closed.into_pyarray(py);
            o.set_item("closed", &closed)?;
            c.put_i64(py, "_eq_v", c.int(py, "_eq_v")? + 1)?;
            tuple_call(
                c,
                py,
                "pay",
                vec![booking_day, booking_net, str_obj(py, "inflow")],
            )?;
            let init = o.get_item("init")?;
            let init = days(&init, n)?;
            let record = PyTuple::new(
                py,
                [
                    array(py, Array1::from_shape_fn(n, |r| init.at(r))),
                    array(py, Array1::from_shape_fn(n, |r| close_d.at(r))),
                    closed.call_method0("copy")?.unbind(),
                ],
            )?;
            c.get(py, "offerings")?.cast::<PyList>()?.append(record)?;
            tuple_call(
                c,
                py,
                "mark",
                vec![
                    str_obj(py, "raised"),
                    close.clone().unbind(),
                    closed.into_any().unbind(),
                ],
            )?;
            Ok(none(py))
        }
        "offering_outcome" => {
            let occasion = args.get_item(0)?.extract::<String>()?;
            let closes = args.get_item(1)?.extract::<bool>()?;
            c.get(py, "_n1")?.set_item(&occasion, closes)?;
            let offers = c.get(py, "_offers")?.cast_into::<PyList>()?;
            let mut out = Array1::from_elem(n, BIG);
            for o in offers.iter() {
                if o.get_item("occasion")?.extract::<String>()? == occasion {
                    tuple_call(c, py, "_close", vec![o.clone().unbind()])?;
                    let rows = o.get_item("rows")?;
                    let rows = rows.cast::<PyArray1<bool>>()?.readonly();
                    let rows = rows.as_array();
                    let init = o.get_item("init")?;
                    let init = days(&init, n)?;
                    for r in 0..n {
                        if rows[r] {
                            out[r] = init.at(r);
                        }
                    }
                }
            }
            c.call0(py, "_atm_rebook")?;
            Ok(array(py, out))
        }
        "offering_day" => {
            let occasion = args.get_item(0)?.extract::<String>()?;
            let offers = c.get(py, "_offers")?.cast_into::<PyList>()?;
            let mut out = Array1::from_elem(n, BIG);
            for o in offers.iter() {
                if o.get_item("occasion")?.extract::<String>()? == occasion {
                    let rows = o.get_item("rows")?;
                    let rows = rows.cast::<PyArray1<bool>>()?.readonly();
                    let rows = rows.as_array();
                    let init = o.get_item("init")?;
                    let init = days(&init, n)?;
                    for r in 0..n {
                        if rows[r] {
                            out[r] = init.at(r);
                        }
                    }
                }
            }
            Ok(array(py, out))
        }
        "listing_dates" => {
            let fin = c.get(py, "fin")?;
            let deadline = date_ordinal(&fin.getattr("listing_deadline")?)?;
            let mut effective = deadline;
            let mut k = 1;
            while k < 10 {
                effective -= 1;
                if weekday(effective) {
                    k += 1;
                }
            }
            let determination = deadline + 1;
            let panel = determination + c.p_i64(py, "panel_decision_days")?;
            let suspend = determination + c.p_i64(py, "suspension_after_determination_days")?;
            let f25 = if c.sensitivity(py, "form25_after_panel_days")? {
                c.p_i64(py, "form25_after_panel_days")?
            } else {
                0
            };
            let review = review(c, py)?;
            let out = PyDict::new(py);
            for (key, v) in [
                ("deadline", deadline),
                ("suspension", suspend),
                ("vote_call", effective - 20),
                ("effective_by", effective),
                ("determination", determination),
                ("hearing_request", determination + 7),
                ("panel_decision", panel),
                (
                    "delisted_suspension",
                    suspend + if f25 != 0 { 10 } else { 0 },
                ),
                ("delisted_panel", panel + f25),
            ] {
                out.set_item(key, v - review - 1)?;
            }
            Ok(out.into_any().unbind())
        }
        "repurchase_day" => {
            let input = args.get_item(0)?;
            let fin = c.get(py, "fin")?;
            let notice = fin.getattr("repurchase_notice_business_days")?;
            let window = fin.getattr("repurchase_business_days")?;
            if notice.is_none() || window.is_none() {
                return Err(PyValueError::new_err(format!("{}: the repurchase terms are unknown; its quoted terms must give the notice and the repurchase window",fin.getattr("instrument_id")?.extract::<String>()?)));
            }
            let lag = if c.p_str(py, "repurchase_date")? == "earliest" {
                window.get_item(0)?.extract::<i64>()?
            } else {
                notice.extract::<i64>()? + window.get_item(1)?.extract::<i64>()?
            };
            let review = review(c, py)?;
            if let Ok(a) = input.cast::<PyArray1<i64>>() {
                let size = a.len()?;
                let ds = days(&input, size)?;
                Ok(array(
                    py,
                    Array1::from_shape_fn(size, |r| {
                        after(review + ds.at(r) + 1, lag, false) - review - 1
                    }),
                ))
            } else {
                let d = input.extract::<i64>()?;
                Ok(int_obj(py, after(review + d + 1, lag, false) - review - 1))
            }
        }
        "holder_route_days" => {
            let p = param(c, py, "holder_petition_route")?;
            let lag = if c
                .parameter(py, "holder_petition_route")?
                .eq(&p.get_item("value")?)?
            {
                p.get_item("request_days")?.extract::<i64>()?
            } else {
                0
            };
            Ok(int_obj(py, lag))
        }
        "_priced_coupon" => {
            let parameters = c
                .get(py, "m")?
                .get_item("parameters")?
                .cast_into::<PyDict>()?;
            let p = parameters.get_item("coupon_cash_share")?;
            let priced = if let Some(p) = p {
                let p = p.cast::<PyDict>()?;
                let mode = c
                    .get(py, "sens")?
                    .cast::<PyDict>()?
                    .get_item("coupon_cash_share")?;
                let mode = if let Some(mode) = mode {
                    if mode.is_truthy()? {
                        Some(mode)
                    } else {
                        p.get_item("value")?
                    }
                } else {
                    p.get_item("value")?
                };
                dict_matches(p, "share_price", "model")?
                    && mode
                        .map(|v| v.eq("shares_to_capacity"))
                        .transpose()?
                        .unwrap_or(false)
                    && !c.get(py, "fin")?.is_none()
            } else {
                false
            };
            Ok(priced.into_pyobject(py)?.to_owned().into_any().unbind())
        }
        "coupon_cash_cents" => {
            let fin = c.get(py, "fin")?;
            let coupon = attr_i(&fin, "coupon_cents")?;
            let p = param(c, py, "coupon_cash_share")?.cast_into::<PyDict>()?;
            let sens = c.get(py, "sens")?.cast_into::<PyDict>()?;
            let mode = if let Some(v) = sens.get_item("coupon_cash_share")? {
                if v.is_truthy()? {
                    v
                } else {
                    p.get_item("value")?
                        .ok_or_else(|| PyValueError::new_err("coupon mode missing"))?
                }
            } else {
                p.get_item("value")?
                    .ok_or_else(|| PyValueError::new_err("coupon mode missing"))?
            };
            if mode.extract::<bool>().ok() == Some(true)
                || mode.extract::<String>().ok().as_deref() == Some("all_cash")
            {
                return Ok(int_obj(py, coupon));
            }
            if mode.extract::<String>().ok().as_deref() == Some("all_shares") {
                return Ok(int_obj(py, 0));
            }
            let ratio = p
                .get_item("split_ratio")?
                .filter(|v| !v.is_none())
                .map(|v| v.extract::<i64>())
                .transpose()?
                .unwrap_or(1);
            let ratio = if ratio == 0 { 1 } else { ratio };
            let capacity = p
                .get_item("share_capacity")?
                .ok_or_else(|| PyValueError::new_err("share capacity missing"))?
                .extract::<i64>()?
                .min(
                    p.get_item("share_limit")?
                        .ok_or_else(|| PyValueError::new_err("share limit missing"))?
                        .extract::<i64>()?,
                )
                .div_euclid(ratio);
            let bps = p
                .get_item("share_value_bps")?
                .ok_or_else(|| PyValueError::new_err("share value bps missing"))?
                .extract::<i64>()?;
            if dict_matches(&p, "share_price", "model")? {
                let when = if !args.is_empty() && !args.get_item(0)?.is_none() {
                    args.get_item(0)?
                } else {
                    let dates = fin.getattr("interest_dates")?;
                    let mut chosen: Option<Bound<'_, PyAny>> = None;
                    let mut min = i64::MAX;
                    for date in dates.try_iter()? {
                        let date = date?;
                        let d = date_ordinal(&date)?;
                        if d < min {
                            min = d;
                            chosen = Some(date);
                        }
                    }
                    chosen.ok_or_else(|| PyValueError::new_err("coupon has no interest date"))?
                };
                let mut d = date_ordinal(&when)? - 1;
                let review = review(c, py)?;
                let mut window = Vec::with_capacity(10);
                while window.len() < 10 {
                    if trading(d) {
                        window.push(d - review - 1);
                    }
                    d -= 1;
                }
                let ds = Array2::from_shape_fn((n, 10), |(_, k)| window[k]);
                let price = tuple_call(
                    c,
                    py,
                    "share_price_on",
                    vec![ds.into_pyarray(py).into_any().unbind()],
                )?;
                let price = price.bind(py).cast::<PyArray2<f64>>()?.readonly();
                let price = price.as_array();
                let out = Array1::from_shape_fn(n, |r| {
                    let v0 = price[[r, 0]] + price[[r, 1]];
                    let v1 = price[[r, 2]] + price[[r, 3]];
                    let v2 = price[[r, 4]] + price[[r, 5]];
                    let v3 = price[[r, 6]] + price[[r, 7]];
                    let mean = (((v0 + v1) + (v2 + v3)) + price[[r, 8]] + price[[r, 9]]) / 10.0;
                    let value = mean * ratio as f64 * bps as f64 / 10000.0;
                    let raw_shares = (coupon as f64 / value).floor();
                    let shares = if raw_shares.is_nan() || raw_shares < capacity as f64 {
                        raw_shares
                    } else {
                        capacity as f64
                    };
                    let cash = coupon as f64 - (shares * value).round_ties_even();
                    round_int(if cash < 0.0 { 0.0 } else { cash })
                });
                Ok(array(py, out))
            } else {
                let px = p
                    .get_item("share_price_cents")?
                    .ok_or_else(|| PyValueError::new_err("share price missing"))?
                    .extract::<i64>()?;
                let covered = capacity
                    .wrapping_mul(px)
                    .wrapping_mul(ratio)
                    .wrapping_mul(bps)
                    .div_euclid(10000);
                Ok(int_obj(py, coupon - coupon.min(covered)))
            }
        }
        "instrument_cash" => {
            let fin = c.get(py, "fin")?;
            if !fin.is_none() {
                let coupon = fin.getattr("coupon_cents")?;
                if coupon.is_none()
                    && fin.getattr("kind")?.extract::<String>()? == "convertible_notes"
                {
                    return Err(PyValueError::new_err(format!(
                        "{}: the coupon is unknown; its quoted terms must give it",
                        fin.getattr("instrument_id")?.extract::<String>()?
                    )));
                }
                if coupon.is_truthy()? {
                    let priced = c.call0(py, "_priced_coupon")?.bind(py).extract::<bool>()?;
                    let review = review(c, py)?;
                    for date in fin.getattr("interest_dates")?.try_iter()? {
                        let date = date?;
                        let mut payment = date_ordinal(&date)?;
                        while !business(payment) {
                            payment += 1;
                        }
                        let day = payment - review - 1;
                        let cash =
                            tuple_call(c, py, "coupon_cash_cents", vec![date.clone().unbind()])?;
                        let cash_d = days(cash.bind(py), n)?;
                        let negative = array(
                            py,
                            Array1::from_shape_fn(n, |r| cash_d.at(r).wrapping_neg()),
                        );
                        tuple_call(
                            c,
                            py,
                            "pay",
                            vec![
                                array(py, Array1::from_elem(n, day)),
                                negative,
                                str_obj(py, "notes_interest"),
                                int_obj(py, -1000000),
                            ],
                        )?;
                        if day >= 0
                            && day < horizon as i64
                            && (priced || cash.bind(py).is_truthy()?)
                        {
                            let tuple = PyTuple::new(
                                py,
                                [
                                    int_obj(py, day),
                                    cash.clone_ref(py),
                                    boolean(py, Array1::from_elem(n, true)),
                                    date.unbind(),
                                ],
                            )?;
                            c.get(py, "coupons")?.cast::<PyList>()?.append(tuple)?;
                        }
                    }
                }
            }
            if c.flag(py, "ordinary")? {
                tuple_call(
                    c,
                    py,
                    "resolve",
                    vec![
                        array(py, Array1::zeros(n)),
                        boolean(py, Array1::from_elem(n, true)),
                    ],
                )?;
            }
            c.call0(py, "_atm_rebook")?;
            let chips = c.p_i64(py, "chips_credit_cents")?;
            if chips != 0 {
                if horizon == 0 {
                    return Err(pyo3::exceptions::PyZeroDivisionError::new_err(
                        "empty chips-credit horizon",
                    ));
                }
                let per = chips.div_euclid(horizon as i64);
                let remainder = chips.wrapping_sub(per.wrapping_mul(horizon as i64));
                for name in ["cash", "k:inflow"] {
                    let writable = tuple_call(c, py, "_evw", vec![str_obj(py, name)])?;
                    let mut write = writable.bind(py).cast::<PyArray2<i64>>()?.try_readwrite()?;
                    let mut a = write.as_array_mut();
                    for r in 0..n {
                        for d in 0..horizon {
                            a[[r, d]] = a[[r, d]].wrapping_add(per);
                        }
                        a[[r, horizon - 1]] = a[[r, horizon - 1]].wrapping_add(remainder);
                    }
                }
                tuple_call(
                    c,
                    py,
                    "_touch",
                    vec![str_obj(py, "cash"), str_obj(py, "k:inflow")],
                )?;
            }
            Ok(none(py))
        }
        "_coupon_rebook" => {
            let coupons = c.get(py, "coupons")?.cast_into::<PyList>()?;
            if coupons.is_empty() || !c.call0(py, "_priced_coupon")?.bind(py).extract::<bool>()? {
                return Ok(none(py));
            }
            for i in 0..coupons.len() {
                let coupon = coupons.get_item(i)?;
                let day = coupon.get_item(0)?.extract::<i64>()?;
                let cash = coupon.get_item(1)?;
                let cash = days(&cash, n)?;
                let kept = coupon.get_item(2)?;
                let keep = kept.cast::<PyArray1<bool>>()?.readonly();
                let keep = keep.as_array();
                let when = coupon.get_item(3)?;
                let new = tuple_call(c, py, "coupon_cash_cents", vec![when.clone().unbind()])?;
                let new_d = days(new.bind(py), n)?;
                let delta = Array1::from_shape_fn(n, |r| {
                    if keep[r] {
                        new_d.at(r).wrapping_sub(cash.at(r))
                    } else {
                        0
                    }
                });
                if delta.iter().any(|&v| v != 0) {
                    tuple_call(
                        c,
                        py,
                        "pay",
                        vec![
                            array(py, Array1::from_elem(n, day)),
                            array(py, delta.mapv(i64::wrapping_neg)),
                            str_obj(py, "notes_interest"),
                            int_obj(py, -1000000),
                        ],
                    )?;
                }
                coupons.set_item(
                    i,
                    PyTuple::new(
                        py,
                        [
                            int_obj(py, day),
                            new.clone_ref(py),
                            kept.clone().unbind(),
                            when.clone().unbind(),
                        ],
                    )?,
                )?;
            }
            Ok(none(py))
        }
        "coupon_when_due" => {
            let due = c.get(py, "marks")?.get_item("notes_due")?;
            let due = days(&due, n)?;
            let coupons = c.get(py, "coupons")?.cast_into::<PyList>()?;
            for coupon in coupons.iter() {
                let day = coupon.get_item(0)?.extract::<i64>()?;
                let cash = coupon.get_item(1)?;
                let cash = days(&cash, n)?;
                let kept = coupon.get_item(2)?;
                let mut kept = kept.cast::<PyArray1<bool>>()?.try_readwrite()?;
                let mut keep = kept.as_array_mut();
                let refund = Array1::from_shape_fn(n, |r| {
                    if keep[r] && due.at(r) <= day {
                        cash.at(r)
                    } else {
                        0
                    }
                });
                tuple_call(
                    c,
                    py,
                    "pay",
                    vec![
                        array(py, Array1::from_elem(n, day)),
                        array(py, refund),
                        str_obj(py, "notes_interest"),
                    ],
                )?;
                for r in 0..n {
                    if due.at(r) <= day {
                        keep[r] = false;
                    }
                }
            }
            Ok(none(py))
        }
        _ => Err(PyValueError::new_err(format!(
            "equity method {name} awaiting implementation"
        ))),
    }
}

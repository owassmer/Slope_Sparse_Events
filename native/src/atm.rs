//! At-the-market sales and settlement booking with integer-cent arithmetic.
use crate::price::{checked_shape, filled_array, round_int};
use numpy::{IntoPyArray, PyArray2, PyReadonlyArray1, PyReadonlyArray2};
use pyo3::prelude::*;

type Booking<'py> = (
    Bound<'py, PyArray2<bool>>,
    Bound<'py, PyArray2<i64>>,
    Bound<'py, PyArray2<i64>>,
    Bound<'py, PyArray2<i64>>,
    Bound<'py, PyArray2<i64>>,
);

#[pyfunction]
#[allow(clippy::too_many_arguments)]
pub(crate) fn atm_book<'py>(
    py: Python<'py>,
    sale: PyReadonlyArray1<'py, i64>,
    stop: PyReadonlyArray1<'py, i64>,
    q: i64,
    led: i64,
    init: PyReadonlyArray2<'py, i64>,
    close: PyReadonlyArray2<'py, i64>,
    closed: PyReadonlyArray2<'py, bool>,
    shares: PyReadonlyArray2<'py, i64>,
    lock_on: bool,
    big: i64,
    pricing_days: i64,
    lock_value: i64,
    j: PyReadonlyArray1<'py, i64>,
    days: PyReadonlyArray1<'py, i64>,
    start: PyReadonlyArray1<'py, i64>,
    sp: PyReadonlyArray2<'py, f64>,
    use_close: bool,
    close_price: f64,
    n_days: usize,
    comm: i64,
    old: PyReadonlyArray2<'py, i64>,
    have_old: bool,
) -> PyResult<Booking<'py>> {
    let sale = sale.as_array();
    let stop = stop.as_array();
    let init = init.as_array();
    let close = close.as_array();
    let closed = closed.as_array();
    let shares = shares.as_array();
    let j = j.as_array();
    let days = days.as_array();
    let start = start.as_array();
    let sp = sp.as_array();
    let old = old.as_array();
    let n = stop.len();
    let n_sales = sale.len();
    let n_off = init.nrows();
    let n_settles = days.len();
    let invalid = init.ncols() != n
        || close.dim() != init.dim()
        || closed.dim() != init.dim()
        || shares.dim() != init.dim()
        || start.len() != n_settles
        || (have_old && old.dim() != (n, n_days))
        || (!use_close && sp.dim() != (n, n_days))
        || j.iter().any(|&x| x < 0 || x as usize >= n_sales)
        || days.iter().any(|&x| x < 0 || x as usize >= n_days)
        || start.iter().any(|&x| x < 0 || x as usize > j.len())
        || start.iter().zip(start.iter().skip(1)).any(|(&a, &b)| a > b)
        || (n_days == 0 && !use_close && !j.is_empty());
    if invalid {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "ATM inputs have incompatible dimensions or indices",
        ));
    }
    checked_shape::<i64>(n, n_sales)?;
    checked_shape::<i64>(n, n_days)?;
    let (sold, csold, new, cum, delta) = py.detach(|| -> PyResult<_> {
        let mut sold = filled_array(n, n_sales, false)?;
        let mut csold = filled_array(n, n_sales, 0i64)?;
        let mut new = filled_array(n, n_days, 0i64)?;
        let mut cum = filled_array(n, n_days, 0i64)?;
        let mut delta = filled_array(n, n_days, 0i64)?;
        let keep = 10000i64.wrapping_sub(comm);
        for r in 0..n {
            let mut k: i64 = 0;
            let mut failed = false;
            let mut cs: i64 = 0;
            for s in 0..n_sales {
                let d = sale[s];
                let mut other: i64 = 0;
                for o in 0..n_off {
                    if d >= init[[o, r]] && (d < close[[o, r]] || closed[[o, r]]) {
                        other = other.wrapping_add(shares[[o, r]]);
                    }
                }
                let mut locked = false;
                if lock_on {
                    for o in 0..n_off {
                        if init[[o, r]] < big
                            && d >= init[[o, r]].wrapping_add(pricing_days)
                            && (d < close[[o, r]]
                                || (closed[[o, r]] && d <= close[[o, r]].wrapping_add(lock_value)))
                        {
                            locked = true;
                            break;
                        }
                    }
                }
                let on = d < stop[r] && !locked;
                if on {
                    k = k.wrapping_add(1);
                    if k.wrapping_mul(q).wrapping_add(other) > led {
                        failed = true;
                    }
                }
                let ok = on && !failed;
                sold[[r, s]] = ok;
                if ok {
                    cs = cs.wrapping_add(1);
                }
                csold[[r, s]] = cs;
            }
            for di in 0..n_settles {
                let b = if di + 1 < n_settles {
                    start[di + 1] as usize
                } else {
                    j.len()
                };
                let mut acc: i64 = 0;
                for c in start[di] as usize..b {
                    let col = j[c] as usize;
                    if sold[[r, col]] {
                        let p = if use_close {
                            close_price
                        } else {
                            sp[[r, sale[col].clamp(0, n_days as i64 - 1) as usize]]
                        };
                        let gross = round_int(q as f64 * p);
                        let net = gross.wrapping_mul(keep).div_euclid(10000);
                        acc = acc.wrapping_add(net);
                    }
                }
                new[[r, days[di] as usize]] = acc;
            }
            let mut run: i64 = 0;
            for t in 0..n_days {
                run = run.wrapping_add(new[[r, t]]);
                cum[[r, t]] = run;
                delta[[r, t]] = if have_old {
                    new[[r, t]].wrapping_sub(old[[r, t]])
                } else {
                    new[[r, t]]
                };
            }
        }
        Ok((sold, csold, new, cum, delta))
    })?;
    Ok((
        sold.into_pyarray(py),
        csold.into_pyarray(py),
        new.into_pyarray(py),
        cum.into_pyarray(py),
        delta.into_pyarray(py),
    ))
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(atm_book, m)?)?;
    Ok(())
}

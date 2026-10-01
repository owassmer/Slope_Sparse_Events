//! Exact-operation structural share-price arithmetic. No fast math or fused operations.
use std::collections::HashMap;

use ndarray::{Array2, ArrayView2};
use numpy::{IntoPyArray, PyArray1, PyArray2, PyReadonlyArray1, PyReadonlyArray2};
use pyo3::prelude::*;

/// Validate the axes as well as the element count. An empty axis must not hide
/// overflowing strides or an unrepresentable nonempty axis.
pub(crate) fn checked_shape<T>(rows: usize, cols: usize) -> PyResult<usize> {
    let invalid = || {
        pyo3::exceptions::PyValueError::new_err("native numerical shape exceeds addressable memory")
    };
    if rows > isize::MAX as usize || cols > isize::MAX as usize {
        return Err(invalid());
    }
    let metadata_cells = rows.max(1).checked_mul(cols.max(1)).ok_or_else(invalid)?;
    let bytes = metadata_cells
        .checked_mul(std::mem::size_of::<T>())
        .ok_or_else(invalid)?;
    if bytes > isize::MAX as usize {
        return Err(invalid());
    }
    rows.checked_mul(cols).ok_or_else(invalid)
}

pub(crate) fn filled_vec<T: Clone>(len: usize, value: T) -> PyResult<Vec<T>> {
    checked_shape::<T>(len, 1)?;
    let mut out = Vec::new();
    out.try_reserve_exact(len).map_err(|err| {
        pyo3::exceptions::PyValueError::new_err(format!(
            "unable to allocate native numerical buffer: {err}"
        ))
    })?;
    out.resize(len, value);
    Ok(out)
}

pub(crate) fn filled_array<T: Clone>(rows: usize, cols: usize, value: T) -> PyResult<Array2<T>> {
    let len = checked_shape::<T>(rows, cols)?;
    Array2::from_shape_vec((rows, cols), filled_vec(len, value)?).map_err(|err| {
        pyo3::exceptions::PyValueError::new_err(format!("invalid native numerical shape: {err}"))
    })
}

// CPython's math module and Numba use the host's libm for these operations.
// Using that same implementation matters to the existing byte-level contract.
#[link(name = "m")]
unsafe extern "C" {
    fn erf(x: f64) -> f64;
    fn log(x: f64) -> f64;
    fn exp(x: f64) -> f64;
    fn sqrt(x: f64) -> f64;
    fn pow(x: f64, y: f64) -> f64;
}

fn normal(x: f64) -> f64 {
    0.5 * (1.0 + unsafe { erf(x / sqrt(2.0)) })
}

pub(crate) fn call_value(v: f64, k: f64, s: f64, rate: f64, t: f64) -> (f64, f64) {
    let sq = s * unsafe { sqrt(t) };
    let d1 = (unsafe { log(v / k) } + (rate + s * s / 2.0) * t) / sq;
    let n1 = normal(d1);
    let n2 = normal(d1 - sq);
    (v * n1 - k * unsafe { exp(-rate * t) } * n2, n1)
}

pub(crate) fn price_one(
    owed: i64,
    v: f64,
    notes: i64,
    s: f64,
    rate: f64,
    t: f64,
    shares: i64,
) -> f64 {
    let k = notes.wrapping_add(owed.max(0)) as f64;
    let sq = s * unsafe { sqrt(t) };
    let ratio = v / k;
    // Numba lowers log through LLVM, whose negative-domain NaN is positive.
    // Host libm returns a negative NaN on this platform. The sign is part of
    // the retained array-byte contract even for wrapped int64 strikes.
    let logarithm = if ratio < 0.0 {
        f64::NAN
    } else {
        unsafe { log(ratio) }
    };
    let d1 = (logarithm + (rate + s * s / 2.0) * t) / sq;
    let n1 = normal(d1);
    let n2 = normal(d1 - sq);
    (v * n1 - k * unsafe { exp(-rate * t) } * n2) / shares as f64
}

// CPython 3.12's built-in float sum uses compensated summation. A plain
// left-to-right fold changes the calibrated volatility on the real price table.
fn python_float_sum(values: impl Iterator<Item = f64>) -> f64 {
    let mut total = 0.0f64;
    let mut correction = 0.0;
    for x in values {
        let next = total + x;
        correction += if total.abs() >= x.abs() {
            (total - next) + x
        } else {
            (x - next) + total
        };
        total = next;
    }
    if correction != 0.0 && correction.is_finite() {
        total += correction;
    }
    total
}

#[pyfunction]
fn prices<'py>(
    py: Python<'py>,
    owed: PyReadonlyArray1<'py, i64>,
    v: f64,
    notes: i64,
    s: f64,
    rate: f64,
    t: f64,
    shares: i64,
) -> PyResult<Bound<'py, PyArray1<f64>>> {
    let a = owed.as_array();
    if !a.is_empty()
        && (shares == 0
            || s * unsafe { sqrt(t) } == 0.0
            || a.iter().any(|&x| notes.wrapping_add(x.max(0)) == 0))
    {
        return Err(pyo3::exceptions::PyZeroDivisionError::new_err(
            "division by zero in share price",
        ));
    }
    let out = py.detach(|| -> PyResult<Vec<f64>> {
        let mut out = filled_vec(a.len(), 0.0)?;
        for (dest, &amount) in out.iter_mut().zip(a.iter()) {
            *dest = price_one(amount, v, notes, s, rate, t, shares);
        }
        Ok(out)
    })?;
    Ok(out.into_pyarray(py))
}

#[pyfunction]
fn merton_grid<'py>(
    py: Python<'py>,
    a: PyReadonlyArray2<'py, i64>,
    v: f64,
    notes: i64,
    s: f64,
    rate: f64,
    t: f64,
    shares: i64,
) -> PyResult<Bound<'py, PyArray2<f64>>> {
    let a = a.as_array();
    if !a.is_empty()
        && (shares == 0
            || s * unsafe { sqrt(t) } == 0.0
            || a.iter().any(|&x| notes.wrapping_add(x.max(0)) == 0))
    {
        return Err(pyo3::exceptions::PyZeroDivisionError::new_err(
            "division by zero in share price",
        ));
    }
    let out = py.detach(|| grid_prices(a, v, notes, s, rate, t, shares))?;
    Ok(out.into_pyarray(py))
}

pub(crate) fn grid_prices(
    a: ArrayView2<'_, i64>,
    v: f64,
    notes: i64,
    s: f64,
    rate: f64,
    t: f64,
    shares: i64,
) -> PyResult<Array2<f64>> {
    let (n, m) = a.dim();
    let mut out = filled_array(n, m, 0.0)?;
    let mut seen: HashMap<u64, usize> = HashMap::new();
    for i in 0..n {
        let mut h = 14695981039346656037u64;
        for j in 0..m {
            h = (h ^ a[[i, j]] as u64).wrapping_mul(1099511628211);
        }
        if let Some(&r) = seen.get(&h) {
            if (0..m).all(|j| a[[i, j]] == a[[r, j]]) {
                for j in 0..m {
                    out[[i, j]] = out[[r, j]];
                }
                continue;
            }
        } else {
            seen.try_reserve(1).map_err(|err| {
                pyo3::exceptions::PyValueError::new_err(format!(
                    "unable to allocate native pricing index: {err}"
                ))
            })?;
            seen.insert(h, i);
        }
        let mut value = 0.0;
        for j in 0..m {
            if j == 0 || a[[i, j]] != a[[i, j - 1]] {
                value = price_one(a[[i, j]], v, notes, s, rate, t, shares);
            }
            out[[i, j]] = value;
        }
    }
    Ok(out)
}

#[pyfunction]
fn merton_call(v: f64, k: f64, s: f64, rate: f64, t: f64) -> PyResult<(f64, f64)> {
    validate_call(v, k, s, t)?;
    Ok(call_value(v, k, s, rate, t))
}

fn validate_call(v: f64, k: f64, s: f64, t: f64) -> PyResult<()> {
    if t < 0.0 {
        return Err(pyo3::exceptions::PyValueError::new_err("math domain error"));
    }
    if k == 0.0 {
        return Err(pyo3::exceptions::PyZeroDivisionError::new_err(
            "float division by zero",
        ));
    }
    if v / k <= 0.0 {
        return Err(pyo3::exceptions::PyValueError::new_err("math domain error"));
    }
    if s * unsafe { sqrt(t) } == 0.0 {
        return Err(pyo3::exceptions::PyZeroDivisionError::new_err(
            "float division by zero",
        ));
    }
    Ok(())
}

#[pyfunction]
fn merton_calibrate(
    py: Python<'_>,
    shares: i64,
    close: i64,
    notes: i64,
    rate: f64,
    t: f64,
    se: f64,
) -> PyResult<(f64, f64)> {
    py.detach(|| {
        let e_integer = shares as i128 * close as i128;
        let e = e_integer as f64;
        let mut v = (e_integer + notes as i128) as f64;
        if v == 0.0 {
            return Err(pyo3::exceptions::PyZeroDivisionError::new_err(
                "float division by zero",
            ));
        }
        let mut s = se * e / v;
        for _ in 0..10000 {
            validate_call(v, notes as f64, s, t)?;
            let (ev, nd1) = call_value(v, notes as f64, s, rate, t);
            let nd1_floor = if 1e-9 > nd1 { 1e-9 } else { nd1 };
            let v2 = v + (e - ev) / nd1_floor;
            if nd1 * v2 == 0.0 {
                return Err(pyo3::exceptions::PyZeroDivisionError::new_err(
                    "float division by zero",
                ));
            }
            let s2 = se * e / (nd1 * v2);
            let done = (v2 - v).abs() < 1e-9 * v && (s2 - s).abs() < 1e-15;
            v = v2;
            s = s2;
            if done {
                break;
            }
        }
        Ok((v, s))
    })
}

#[pyfunction]
fn equity_vol(py: Python<'_>, px: Vec<f64>) -> PyResult<f64> {
    if px.len() < 3 {
        return Err(pyo3::exceptions::PyZeroDivisionError::new_err(
            "volatility needs at least three closes",
        ));
    }
    for p in px.windows(2) {
        if p[0] == 0.0 {
            return Err(pyo3::exceptions::PyZeroDivisionError::new_err(
                "float division by zero",
            ));
        }
        if p[1] / p[0] <= 0.0 {
            return Err(pyo3::exceptions::PyValueError::new_err("math domain error"));
        }
    }
    Ok(py.detach(|| {
        let returns = px
            .windows(2)
            .map(|p| unsafe { log(p[1] / p[0]) })
            .collect::<Vec<_>>();
        let mean = python_float_sum(returns.iter().copied()) / returns.len() as f64;
        let ss = python_float_sum(returns.iter().map(|x| unsafe { pow(x - mean, 2.0) }));
        unsafe { sqrt(ss / (returns.len() - 1) as f64 * 252.0) }
    }))
}

/// Match NumPy/Numba's signed-int conversion, including its invalid-value sentinel.
pub(crate) fn round_int(x: f64) -> i64 {
    let rounded = x.round_ties_even();
    if !rounded.is_finite() || !(-9223372036854775808.0..9223372036854775808.0).contains(&rounded) {
        i64::MIN
    } else {
        rounded as i64
    }
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(prices, m)?)?;
    m.add_function(wrap_pyfunction!(merton_grid, m)?)?;
    m.add_function(wrap_pyfunction!(merton_call, m)?)?;
    m.add_function(wrap_pyfunction!(merton_calibrate, m)?)?;
    m.add_function(wrap_pyfunction!(equity_vol, m)?)?;
    Ok(())
}

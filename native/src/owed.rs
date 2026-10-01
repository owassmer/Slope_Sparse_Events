//! Integer-cent judgment and settlement recurrence, preserving each reference operation.
use crate::price::{checked_shape, filled_array, filled_vec, round_int};
use ndarray::ArrayView1;
use numpy::{IntoPyArray, PyArray2, PyReadonlyArray1, PyReadonlyArray2};
use pyo3::prelude::*;

fn interest(c1: f64, se: f64, c2: f64, sa: f64) -> i64 {
    round_int(c1 * se / 365.0 + c2 * sa / 365.0)
}

struct Gross<'a> {
    has_j: bool,
    entry_pd: bool,
    entry: ArrayView1<'a, i64>,
    entry_s: i64,
    entered: i64,
    ce: f64,
    cz: f64,
    has_cls: bool,
    cls: i64,
    base: i64,
    cb: f64,
    cinc: f64,
    fees: i64,
    f: ArrayView1<'a, i64>,
    fee_day: ArrayView1<'a, i64>,
    enf: bool,
    ei: ArrayView1<'a, i64>,
    pending: bool,
    e_ix: ArrayView1<'a, i64>,
}

impl Gross<'_> {
    fn at(&self, d: i64, j: usize) -> i64 {
        let se = if self.has_j {
            d.wrapping_sub(if self.entry_pd {
                self.entry[j]
            } else {
                self.entry_s
            })
            .max(0) as f64
        } else {
            0.0
        };
        let mut out = self
            .entered
            .wrapping_add(interest(self.ce, se, self.cz, 0.0));
        if self.has_cls {
            let sa = d.wrapping_sub(self.f[j]).max(0) as f64;
            let mut after = self.cls.wrapping_add(interest(self.cb, se, self.cinc, sa));
            if d < self.fee_day[j] {
                after = after.wrapping_sub(self.fees);
            }
            if self.enf && d < self.ei[j] {
                let b2 = self.base.wrapping_add(interest(self.cb, se, self.cz, 0.0));
                after = after.min(b2);
            }
            if d >= self.f[j] {
                out = after;
            }
        }
        if self.pending && d < self.e_ix[j] {
            out = 0;
        }
        out
    }
}

fn shape_error(message: &str) -> PyErr {
    pyo3::exceptions::PyValueError::new_err(message.to_owned())
}

#[pyfunction]
#[allow(clippy::too_many_arguments)]
fn owed_kernel<'py>(
    py: Python<'py>,
    day2: PyReadonlyArray2<'py, i64>,
    has_j: bool,
    entry_pd: bool,
    entry_arr: PyReadonlyArray1<'py, i64>,
    entry_s: i64,
    entered: i64,
    ce: f64,
    cz: f64,
    has_cls: bool,
    cls: i64,
    base: i64,
    cb: f64,
    cinc: f64,
    fees: i64,
    f: PyReadonlyArray1<'py, i64>,
    fee_day: PyReadonlyArray1<'py, i64>,
    enf: bool,
    ei: PyReadonlyArray1<'py, i64>,
    pending: bool,
    e_ix: PyReadonlyArray1<'py, i64>,
    resolved: PyReadonlyArray1<'py, i64>,
    taken: PyReadonlyArray1<'py, i64>,
    takes_t: PyReadonlyArray2<'py, i64>,
    takes_a: PyReadonlyArray2<'py, i64>,
) -> PyResult<Bound<'py, PyArray2<i64>>> {
    let days = day2.as_array();
    let (r, n) = days.dim();
    let g = Gross {
        has_j,
        entry_pd,
        entry: entry_arr.as_array(),
        entry_s,
        entered,
        ce,
        cz,
        has_cls,
        cls,
        base,
        cb,
        cinc,
        fees,
        f: f.as_array(),
        fee_day: fee_day.as_array(),
        enf,
        ei: ei.as_array(),
        pending,
        e_ix: e_ix.as_array(),
    };
    let resolved = resolved.as_array();
    let taken = taken.as_array();
    let tt = takes_t.as_array();
    let ta = takes_a.as_array();
    if [
        g.entry.len(),
        g.f.len(),
        g.fee_day.len(),
        g.ei.len(),
        g.e_ix.len(),
        resolved.len(),
        taken.len(),
    ]
    .iter()
    .any(|&len| len != n)
        || tt.dim() != ta.dim()
        || tt.ncols() != n
    {
        return Err(shape_error("owed inputs must match the number of draws"));
    }
    checked_shape::<i64>(r, n)?;
    let out = py.detach(|| -> PyResult<_> {
        let mut out = filled_array(r, n, 0i64)?;
        for row in 0..r {
            for j in 0..n {
                let d = days[[row, j]];
                if d >= resolved[j] {
                    continue;
                }
                let gross = g.at(d, j);
                let tk = if pending {
                    let mut tk: i64 = 0;
                    for k in 0..tt.nrows() {
                        if tt[[k, j]] < d {
                            tk = tk.wrapping_add(ta[[k, j]]);
                        }
                    }
                    tk
                } else {
                    taken[j]
                };
                out[[row, j]] = gross.wrapping_sub(tk).max(0);
            }
        }
        Ok(out)
    })?;
    Ok(out.into_pyarray(py))
}

#[pyfunction]
#[allow(clippy::too_many_arguments)]
fn grid_kernel<'py>(
    py: Python<'py>,
    n_days: usize,
    has_j: bool,
    e_ix: PyReadonlyArray1<'py, i64>,
    entered: i64,
    ce: f64,
    cz: f64,
    has_cls: bool,
    cls: i64,
    base: i64,
    cb: f64,
    cinc: f64,
    fees: i64,
    f: PyReadonlyArray1<'py, i64>,
    fee_day: PyReadonlyArray1<'py, i64>,
    resolved: PyReadonlyArray1<'py, i64>,
    verdict: PyReadonlyArray1<'py, i64>,
    takes_t: PyReadonlyArray2<'py, i64>,
    takes_a: PyReadonlyArray2<'py, i64>,
    has_settle: bool,
    settlement_t: PyReadonlyArray2<'py, i64>,
    settlement_a: PyReadonlyArray2<'py, i64>,
    settled: PyReadonlyArray1<'py, i64>,
) -> PyResult<Bound<'py, PyArray2<i64>>> {
    let resolved = resolved.as_array();
    let n = resolved.len();
    let e = e_ix.as_array();
    let f = f.as_array();
    let g = Gross {
        has_j,
        entry_pd: true,
        entry: e,
        entry_s: 0,
        entered,
        ce,
        cz,
        has_cls,
        cls,
        base,
        cb,
        cinc,
        fees,
        f,
        fee_day: fee_day.as_array(),
        enf: false,
        ei: f,
        pending: true,
        e_ix: e,
    };
    let v = verdict.as_array();
    let tt = takes_t.as_array();
    let ta = takes_a.as_array();
    let st = settlement_t.as_array();
    let sa = settlement_a.as_array();
    let settled = settled.as_array();
    if [e.len(), f.len(), g.fee_day.len(), v.len(), settled.len()]
        .iter()
        .any(|&len| len != n)
        || tt.dim() != ta.dim()
        || tt.ncols() != n
        || st.dim() != sa.dim()
        || st.ncols() != n
    {
        return Err(shape_error(
            "owed-grid inputs must match the number of draws",
        ));
    }
    checked_shape::<i64>(n, n_days)?;
    let scratch_days = n_days
        .checked_add(1)
        .ok_or_else(|| shape_error("owed-grid horizon overflows"))?;
    checked_shape::<i64>(scratch_days, 1)?;
    let out = py.detach(|| -> PyResult<_> {
        let mut out = filled_array(n, n_days, 0i64)?;
        let mut add_t = filled_vec(scratch_days, 0i64)?;
        let mut add_s = filled_vec(scratch_days, 0i64)?;
        for j in 0..n {
            add_t.fill(0);
            add_s.fill(0);
            for k in 0..tt.nrows() {
                let t = tt[[k, j]].clamp(0, n_days as i64) as usize;
                add_t[t] = add_t[t].wrapping_add(ta[[k, j]]);
            }
            let mut total: i64 = 0;
            for k in 0..st.nrows() {
                total = total.wrapping_add(sa[[k, j]]);
                let t = st[[k, j]].clamp(0, n_days as i64) as usize;
                add_s[t] = add_s[t].wrapping_add(sa[[k, j]]);
            }
            let mut tk: i64 = 0;
            let mut ts: i64 = 0;
            for day in 0..n_days {
                let d = day as i64;
                tk = tk.wrapping_add(add_t[day]);
                ts = ts.wrapping_add(add_s[day]);
                let mut amt = if has_cls && d >= f[j] { cls } else { entered };
                amt = if d >= resolved[j] {
                    0
                } else {
                    amt.wrapping_sub(tk).max(0)
                };
                if d >= e[j] {
                    amt = if d >= resolved[j] {
                        0
                    } else {
                        g.at(d, j).wrapping_sub(tk).max(0)
                    };
                }
                if has_settle && d >= settled[j] {
                    amt = total.wrapping_sub(ts);
                }
                out[[j, day]] = if d >= v[j] { amt } else { 0 };
            }
        }
        Ok(out)
    })?;
    Ok(out.into_pyarray(py))
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(owed_kernel, m)?)?;
    m.add_function(wrap_pyfunction!(grid_kernel, m)?)?;
    Ok(())
}

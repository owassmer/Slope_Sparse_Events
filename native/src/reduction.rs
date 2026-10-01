//! Ordered probability products and exact sparse histogram/statistical operations.
use numpy::{
    IntoPyArray, PyArray1, PyArrayMethods, PyReadonlyArray1, PyReadonlyArray2, PyReadonlyArrayDyn,
    PyUntypedArray,
};
use pyo3::basic::CompareOp;
use pyo3::exceptions::{PyTypeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyFloat, PySequence, PyString};

fn cells(rows: usize, columns: usize) -> PyResult<usize> {
    crate::price::checked_shape::<f64>(rows, columns)
}

fn reserved<T>(length: usize) -> PyResult<Vec<T>> {
    if length
        .checked_mul(std::mem::size_of::<T>())
        .is_none_or(|bytes| bytes > isize::MAX as usize)
    {
        return Err(PyValueError::new_err(
            "array capacity exceeds addressable memory",
        ));
    }
    let mut out = Vec::new();
    out.try_reserve_exact(length)
        .map_err(|e| PyValueError::new_err(format!("array allocation failed: {e}")))?;
    Ok(out)
}

fn filled<T: Clone>(length: usize, value: T) -> PyResult<Vec<T>> {
    let mut out = reserved(length)?;
    out.resize(length, value);
    Ok(out)
}

enum Threshold<'py> {
    Float(f64),
    Extended(Bound<'py, PyAny>),
}

fn quantiles<'py>(py: Python<'py>, values: &Bound<'py, PyAny>) -> PyResult<Vec<Threshold<'py>>> {
    if values.is_instance_of::<PyString>() {
        return Err(PyTypeError::new_err("Can't extract `str` to `Vec`"));
    }
    // ndarray implements Python's sequence protocol but is not registered
    // with the Sequence ABC used by PyO3's safe cast. Keep its accepted input
    // behavior without falling back to PyO3's infallible Vec conversion.
    let length = if values.is_instance_of::<PyUntypedArray>() {
        values.len()?
    } else {
        values.cast::<PySequence>()?.len()?
    };
    let mut out = reserved(length)?;
    let numpy = py.import("numpy")?;
    let float16 = numpy.getattr("float16")?;
    let float32 = numpy.getattr("float32")?;
    let float64 = numpy.getattr("float64")?;
    for value in values.try_iter()? {
        out.try_reserve(1)
            .map_err(|e| PyValueError::new_err(format!("array allocation failed: {e}")))?;
        let value = value?;
        if value.is_exact_instance_of::<PyFloat>() {
            out.push(Threshold::Float(value.extract::<f64>()? - 1e-12));
        } else {
            // The subtraction must happen before any promotion: NumPy's
            // float32 q minus a weak Python epsilon remains float32.
            let value = value.sub(1e-12)?;
            if value.is_exact_instance_of::<PyFloat>()
                || value.is_instance(&float16)?
                || value.is_instance(&float32)?
                || value.is_instance(&float64)?
            {
                out.push(Threshold::Float(value.extract()?));
            } else {
                out.push(Threshold::Extended(value));
            }
        }
    }
    Ok(out)
}

enum Cumulative<'py> {
    Rust(Vec<f64>),
    Host(Bound<'py, PyArray1<f64>>),
}
impl<'py> Cumulative<'py> {
    fn new(py: Python<'py>, values: Vec<f64>, thresholds: &[Threshold<'py>]) -> Self {
        if thresholds
            .iter()
            .any(|q| matches!(q, Threshold::Extended(_)))
        {
            // NumPy owns the already native buffer; there is no copy.
            Self::Host(values.into_pyarray(py))
        } else {
            Self::Rust(values)
        }
    }
    fn first(&self, q: &Threshold<'py>) -> PyResult<usize> {
        match (self, q) {
            (Self::Rust(values), Threshold::Float(q)) => {
                Ok(values.iter().position(|&v| v >= *q).unwrap_or(0))
            }
            (Self::Host(values), Threshold::Float(q)) => Ok(values
                .readonly()
                .as_array()
                .iter()
                .position(|&v| v >= *q)
                .unwrap_or(0)),
            (Self::Host(values), Threshold::Extended(q)) => {
                // Extended scalars promote the comparison itself. The host
                // operation is NumPy's compiled comparison; first-hit control
                // and output construction remain Rust.
                let comparisons = values.as_any().rich_compare(q, CompareOp::Ge)?;
                let comparisons = comparisons.cast::<PyArray1<bool>>()?.readonly();
                Ok(comparisons.as_array().iter().position(|&v| v).unwrap_or(0))
            }
            (Self::Rust(_), Threshold::Extended(_)) => Err(PyValueError::new_err(
                "extended threshold requires a comparison buffer",
            )),
        }
    }
}

#[pyfunction]
fn edge_products<'py>(
    py: Python<'py>,
    refs: PyReadonlyArray2<'py, i64>,
    values: PyReadonlyArray1<'py, f64>,
) -> PyResult<Bound<'py, numpy::PyArray1<f64>>> {
    let r = refs.as_array();
    let v = values.as_array();
    let mut out = filled(r.nrows(), 1.0)?;
    // Edge order is part of the floating-point contract; never reassociate.
    for j in 0..r.ncols() {
        for i in 0..r.nrows() {
            let k = r[[i, j]];
            if k < 0 || k as usize >= v.len() {
                return Err(PyValueError::new_err("edge index out of bounds"));
            }
            out[i] *= v[k as usize];
        }
    }
    Ok(out.into_pyarray(py))
}

#[pyfunction]
fn bins_flat<'py>(
    py: Python<'py>,
    values: PyReadonlyArrayDyn<'py, f64>,
    lo: PyReadonlyArray1<'py, f64>,
    width: PyReadonlyArray1<'py, f64>,
    bins: i64,
    rows: Option<PyReadonlyArray1<'py, i64>>,
) -> PyResult<Bound<'py, numpy::PyArray1<i64>>> {
    if bins <= 0 {
        return Err(PyValueError::new_err("bins must be positive"));
    }
    let x = values.as_array();
    let a = lo.as_array();
    let w = width.as_array();
    let shape = x.shape();
    if shape.len() != 1 && shape.len() != 2 {
        return Err(PyValueError::new_err(
            "values must have one or two dimensions",
        ));
    }
    let ncol = if shape.len() == 2 { shape[1] } else { 1 };
    let rr = rows.as_ref().map(|r| r.as_array());
    if let Some(ref r) = rr {
        if r.len() != x.len() && !(shape.len() == 2 && r.len() == ncol) {
            return Err(PyValueError::new_err("bin rows have incompatible shape"));
        }
    }
    if shape.len() == 1 && rr.is_none() {
        return Err(PyValueError::new_err("one-dimensional values require rows"));
    }
    let mut out = reserved(x.len())?;
    for (i, &v) in x.iter().enumerate() {
        let row = match rr {
            Some(ref r) => r[if r.len() == x.len() { i } else { i % ncol }],
            None => (i % ncol) as i64,
        };
        if row < 0 || row as usize >= a.len() || row as usize >= w.len() {
            return Err(PyValueError::new_err("bin row out of bounds"));
        }
        let k = row as usize;
        let z = (v - a[k]) / w[k];
        // NumPy float-to-int64 uses INT64_MIN for NaN and out-of-range values.
        let j = if !z.is_finite() || !(-9223372036854775808.0..9223372036854775808.0).contains(&z) {
            i64::MIN
        } else {
            z as i64
        };
        let offset = row
            .checked_mul(bins)
            .and_then(|offset| offset.checked_add(j.clamp(0, bins - 1)))
            .ok_or_else(|| PyValueError::new_err("bin index exceeds int64"))?;
        out.push(offset);
    }
    Ok(out.into_pyarray(py))
}

type SparseCounts<'py> = (
    Bound<'py, numpy::PyArray1<i32>>,
    Bound<'py, numpy::PyArray1<u32>>,
);

#[pyfunction]
fn sparse_counts<'py>(
    py: Python<'py>,
    flat: PyReadonlyArray1<'py, i64>,
    size: usize,
) -> PyResult<SparseCounts<'py>> {
    let mut c = filled(size, 0u64)?;
    for &j in flat.as_array().iter() {
        if j < 0 || j as usize >= size {
            return Err(PyValueError::new_err("histogram index out of bounds"));
        }
        c[j as usize] += 1;
    }
    let mut idx = Vec::new();
    let mut cnt = Vec::new();
    for (j, n) in c.into_iter().enumerate() {
        if n > 0 {
            idx.try_reserve(1)
                .map_err(|e| PyValueError::new_err(format!("array allocation failed: {e}")))?;
            cnt.try_reserve(1)
                .map_err(|e| PyValueError::new_err(format!("array allocation failed: {e}")))?;
            idx.push(j as i32);
            cnt.push(n as u32);
        }
    }
    Ok((idx.into_pyarray(py), cnt.into_pyarray(py)))
}

// NumPy's contiguous floating-point reduction uses eight partial sums and a
// 128-value block. Matching that tree preserves bitwise histogram normalisation.
fn pairwise_sum(x: &[f64]) -> f64 {
    if x.len() < 8 {
        let mut out = -0.0;
        for &v in x {
            out += v;
        }
        return out;
    }
    if x.len() <= 128 {
        let mut a = [0.0; 8];
        a.copy_from_slice(&x[..8]);
        let end = x.len() - x.len() % 8;
        let mut i = 8;
        while i < end {
            for k in 0..8 {
                a[k] += x[i + k];
            }
            i += 8;
        }
        let mut out = ((a[0] + a[1]) + (a[2] + a[3])) + ((a[4] + a[5]) + (a[6] + a[7]));
        for &v in &x[end..] {
            out += v;
        }
        return out;
    }
    let split = (x.len() / 2) / 8 * 8;
    pairwise_sum(&x[..split]) + pairwise_sum(&x[split..])
}

#[pyfunction]
fn weighted_counts<'py>(
    py: Python<'py>,
    idx: PyReadonlyArray1<'py, i32>,
    cnt: PyReadonlyArray1<'py, u32>,
    lens: PyReadonlyArray1<'py, i64>,
    probs: PyReadonlyArray1<'py, f64>,
    rows: usize,
    bins: usize,
    normalise: bool,
) -> PyResult<Bound<'py, numpy::PyArray2<f64>>> {
    let ix = idx.as_array();
    let c = cnt.as_array();
    let ls = lens.as_array();
    let ps = probs.as_array();
    if ix.len() != c.len() || ls.len() != ps.len() || bins == 0 {
        return Err(PyValueError::new_err("histogram shapes disagree"));
    }
    if rows > isize::MAX as usize || bins > isize::MAX as usize {
        return Err(PyValueError::new_err(
            "histogram axis exceeds addressable memory",
        ));
    }
    let mut out = filled(cells(rows, bins)?, 0.0)?;
    let mut off = 0usize;
    for (n, &p) in ls.iter().zip(ps.iter()) {
        let n =
            usize::try_from(*n).map_err(|_| PyValueError::new_err("invalid histogram lengths"))?;
        let end = off
            .checked_add(n)
            .filter(|&end| end <= ix.len())
            .ok_or_else(|| PyValueError::new_err("invalid histogram lengths"))?;
        for j in off..end {
            let k = ix[j];
            if k < 0 || k as usize >= out.len() {
                return Err(PyValueError::new_err("histogram index out of bounds"));
            }
            out[k as usize] += p * c[j] as f64;
        }
        off = end;
    }
    if off != ix.len() {
        return Err(PyValueError::new_err(
            "histogram lengths do not cover counts",
        ));
    }
    if normalise {
        for row in out.chunks_mut(bins) {
            let sum = pairwise_sum(row);
            let total = if sum < 1e-300 { 1e-300 } else { sum };
            for v in row {
                *v /= total;
            }
        }
    }
    Ok(ndarray::Array2::from_shape_vec((rows, bins), out)
        .map_err(|e| PyValueError::new_err(e.to_string()))?
        .into_pyarray(py))
}

#[pyfunction]
fn histogram_quantiles<'py>(
    py: Python<'py>,
    hist: PyReadonlyArray2<'py, f64>,
    lo: PyReadonlyArray1<'py, f64>,
    width: PyReadonlyArray1<'py, f64>,
    qs: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, numpy::PyArray2<f64>>> {
    let qs = quantiles(py, qs)?;
    let h = hist.as_array();
    let a = lo.as_array();
    let w = width.as_array();
    if a.len() < h.nrows() || w.len() < h.nrows() || h.ncols() == 0 {
        return Err(PyValueError::new_err("histogram shape mismatch"));
    }
    let mut out = filled(cells(qs.len(), h.nrows())?, 0.0)?;
    for r in 0..h.nrows() {
        let mut cum = reserved(h.ncols())?;
        let mut s = 0.0;
        for &v in h.row(r) {
            s += v;
            cum.push(s);
        }
        let cum = Cumulative::new(py, cum, &qs);
        for (i, q) in qs.iter().enumerate() {
            let k = cum.first(q)?;
            out[i * h.nrows() + r] = a[r] + (k as f64 + 0.5) * w[r];
        }
    }
    Ok(ndarray::Array2::from_shape_vec((qs.len(), h.nrows()), out)
        .map_err(|e| PyValueError::new_err(e.to_string()))?
        .into_pyarray(py))
}

#[pyfunction]
fn weighted_quantiles<'py>(
    py: Python<'py>,
    values: PyReadonlyArrayDyn<'py, f64>,
    weights: PyReadonlyArray1<'py, f64>,
    qs: &Bound<'py, PyAny>,
) -> PyResult<Py<PyAny>> {
    let qs = quantiles(py, qs)?;
    let x = values.as_array();
    let w = weights.as_array();
    let dims = x.ndim();
    if dims != 1 && dims != 2 {
        return Err(PyValueError::new_err(
            "values must be one or two dimensional",
        ));
    }
    let rows = x.shape()[0];
    let cols = if dims == 2 { x.shape()[1] } else { 1 };
    if rows == 0 || w.len() != rows {
        return Err(PyValueError::new_err("weights and values disagree"));
    }
    let mut out = filled(cells(qs.len(), cols)?, 0.0)?;
    for c in 0..cols {
        let get = |r: usize| {
            if dims == 2 {
                x[ndarray::IxDyn(&[r, c])]
            } else {
                x[ndarray::IxDyn(&[r])]
            }
        };
        let mut order = reserved(rows)?;
        order.extend(0..rows);
        // Explicit original-index ties preserve the previous stable ordering,
        // including NaNs and signed zeros, without sort's extra allocation.
        order.sort_unstable_by(|&a, &b| {
            let aa = get(a);
            let bb = get(b);
            let ordering = if aa.is_nan() {
                if bb.is_nan() {
                    std::cmp::Ordering::Equal
                } else {
                    std::cmp::Ordering::Greater
                }
            } else if bb.is_nan() {
                std::cmp::Ordering::Less
            } else {
                aa.partial_cmp(&bb).unwrap_or(std::cmp::Ordering::Equal)
            };
            ordering.then_with(|| a.cmp(&b))
        });
        let mut cum = reserved(rows)?;
        let mut s = 0.0;
        for &r in &order {
            s += w[r];
            cum.push(s);
        }
        let cum = Cumulative::new(py, cum, &qs);
        for (i, q) in qs.iter().enumerate() {
            let k = cum.first(q)?;
            out[i * cols + c] = get(order[k]);
        }
    }
    if dims == 1 {
        Ok(out.into_pyarray(py).into_any().unbind())
    } else {
        Ok(ndarray::Array2::from_shape_vec((qs.len(), cols), out)
            .map_err(|e| PyValueError::new_err(e.to_string()))?
            .into_pyarray(py)
            .into_any()
            .unbind())
    }
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(edge_products, m)?)?;
    m.add_function(wrap_pyfunction!(bins_flat, m)?)?;
    m.add_function(wrap_pyfunction!(sparse_counts, m)?)?;
    m.add_function(wrap_pyfunction!(weighted_counts, m)?)?;
    m.add_function(wrap_pyfunction!(histogram_quantiles, m)?)?;
    m.add_function(wrap_pyfunction!(weighted_quantiles, m)?)?;
    Ok(())
}

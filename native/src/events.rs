//! Dated event state. The buffers live in Rust and are shared copy-on-write.
//!
//! NumPy snapshots are immutable views whose base object owns an Arc. A snapshot
//! therefore remains valid after a sibling writes, slices, or drops its state.
//! Dated bookings retain their exact int64 arithmetic and occurrence order.

use ndarray::{Array1, Array2};
use numpy::{IntoPyArray, PyArray1, PyArray2, PyArrayMethods, PyReadonlyArray1, PyReadonlyArray2};
use pyo3::exceptions::{PyIndexError, PyMemoryError, PyValueError};
use pyo3::gc::PyVisit;
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyDict, PyTuple};
use pyo3::PyTraverseError;
use std::collections::BTreeMap;
use std::sync::Arc;

pub const BIG: i64 = 1_000_000;
const KINDS: [&str; 6] = [
    "inflow",
    "levy",
    "reduction",
    "settlement",
    "notes_interest",
    "judgment",
];
const OBLIGATIONS: [&str; 3] = ["settlement", "notes_interest", "judgment"];

pub(crate) fn validate_draws(draws: usize) -> PyResult<()> {
    if draws > isize::MAX as usize / std::mem::size_of::<i64>() {
        return Err(PyValueError::new_err(
            "Draw count exceeds addressable memory",
        ));
    }
    Ok(())
}

pub(crate) fn validate_event_shape(draws: usize, days: usize) -> PyResult<usize> {
    validate_draws(draws)?;
    if days > isize::MAX as usize {
        return Err(PyValueError::new_err(
            "Day count exceeds addressable memory",
        ));
    }
    let cells = draws
        .checked_mul(days)
        .ok_or_else(|| PyValueError::new_err("Event shape overflows"))?;
    if cells > isize::MAX as usize / std::mem::size_of::<i64>() {
        return Err(PyValueError::new_err(
            "Event shape exceeds addressable memory",
        ));
    }
    Ok(cells)
}

fn event_vector(draws: usize, value: i64) -> PyResult<Array1<i64>> {
    validate_draws(draws)?;
    let mut values = Vec::new();
    values
        .try_reserve_exact(draws)
        .map_err(|_| PyMemoryError::new_err("Cannot allocate event vector"))?;
    values.resize(draws, value);
    Ok(Array1::from_vec(values))
}

fn event_matrix(draws: usize, days: usize) -> PyResult<Array2<i64>> {
    let cells = validate_event_shape(draws, days)?;
    let mut values = Vec::new();
    values
        .try_reserve_exact(cells)
        .map_err(|_| PyMemoryError::new_err("Cannot allocate event matrix"))?;
    values.resize(cells, 0);
    Array2::from_shape_vec((draws, days), values)
        .map_err(|_| PyValueError::new_err("Invalid event shape"))
}

#[pyclass(frozen, module = "app._native")]
struct EventArraySnapshot2 {
    array: Arc<Array2<i64>>,
}

#[pyclass(frozen, module = "app._native")]
struct EventArraySnapshot1 {
    array: Arc<Array1<i64>>,
}

fn view2<'py>(py: Python<'py>, array: Arc<Array2<i64>>) -> PyResult<Bound<'py, PyAny>> {
    let owner = Bound::new(py, EventArraySnapshot2 { array })?;
    // SAFETY: the array allocation is pinned by the snapshot's Arc. The ledger
    // uses Arc::make_mut for writes, so it cannot modify this allocation while
    // this snapshot owns a reference. No mutable view is exposed to Python.
    let view =
        unsafe { PyArray2::borrow_from_array(&*owner.borrow().array, owner.clone().into_any()) };
    view.readwrite().make_nonwriteable();
    Ok(view.into_any())
}

fn view1<'py>(py: Python<'py>, array: Arc<Array1<i64>>) -> PyResult<Bound<'py, PyAny>> {
    let owner = Bound::new(py, EventArraySnapshot1 { array })?;
    // SAFETY: identical ownership argument to view2.
    let view =
        unsafe { PyArray1::borrow_from_array(&*owner.borrow().array, owner.clone().into_any()) };
    view.readwrite().make_nonwriteable();
    Ok(view.into_any())
}

fn vec1(a: PyReadonlyArray1<'_, i64>, n: usize, label: &str) -> PyResult<Vec<i64>> {
    let a = a.as_array();
    if a.len() != n {
        return Err(PyValueError::new_err(format!(
            "{label}: expected {n} draws, got {}",
            a.len()
        )));
    }
    Ok(a.iter().copied().collect())
}

fn bool1(a: PyReadonlyArray1<'_, bool>, n: usize, label: &str) -> PyResult<Vec<bool>> {
    let a = a.as_array();
    if a.len() != n {
        return Err(PyValueError::new_err(format!(
            "{label}: expected {n} draws, got {}",
            a.len()
        )));
    }
    Ok(a.iter().copied().collect())
}

/// Native state of all classified dated cash and its per-array versions.
/// Cloning is O(number of buffers), with no copying of draw/day arrays.
#[pyclass(module = "app._native")]
#[derive(Clone)]
pub struct EventLedger {
    n: usize,
    days: usize,
    pub(crate) columns: BTreeMap<String, Arc<Array2<i64>>>,
    pub(crate) vectors: BTreeMap<String, Arc<Array1<i64>>>,
    version: u64,
    versions: BTreeMap<String, u64>,
}

impl EventLedger {
    fn changed(&mut self, names: &[String]) {
        if names.is_empty() {
            return;
        }
        self.version += 1;
        for name in names {
            self.versions.insert(name.clone(), self.version);
        }
    }

    pub(crate) fn book_values(&mut self, name: &str, day: &[i64], cents: &[i64]) -> PyResult<bool> {
        if !self.columns.contains_key(name) {
            return Err(PyValueError::new_err(format!(
                "Unknown event column {name:?}"
            )));
        }
        let any = (0..self.n).any(|r| day[r] >= 0 && day[r] < self.days as i64 && cents[r] != 0);
        if !any {
            return Ok(false);
        }
        let column = self
            .columns
            .get_mut(name)
            .ok_or_else(|| PyValueError::new_err("Unknown event column"))?;
        let a = Arc::make_mut(column);
        for r in 0..self.n {
            let d = day[r];
            if d >= 0 && d < self.days as i64 && cents[r] != 0 {
                a[[r, d as usize]] = a[[r, d as usize]].wrapping_add(cents[r]);
            }
        }
        self.changed(&[name.to_string()]);
        Ok(true)
    }

    fn rows(&self, idx: &[usize]) -> Self {
        let mut out = self.clone();
        out.n = idx.len();
        out.columns = self
            .columns
            .iter()
            .map(|(k, a)| {
                let arr = Array2::from_shape_fn((idx.len(), self.days), |(r, d)| a[[idx[r], d]]);
                (k.clone(), Arc::new(arr))
            })
            .collect();
        out.vectors = self
            .vectors
            .iter()
            .map(|(k, a)| {
                (
                    k.clone(),
                    Arc::new(Array1::from_iter(idx.iter().map(|r| a[*r]))),
                )
            })
            .collect();
        out
    }
}

#[pymethods]
impl EventLedger {
    #[new]
    fn new(draws: usize, days: usize) -> PyResult<Self> {
        validate_event_shape(draws, days)?;
        let mut columns = BTreeMap::new();
        for name in ["cash", "lock", "capacity"]
            .iter()
            .map(|s| s.to_string())
            .chain(KINDS.iter().map(|s| format!("k:{s}")))
        {
            columns.insert(name, Arc::new(event_matrix(draws, days)?));
        }
        let mut vectors = BTreeMap::new();
        vectors.insert("petition".to_string(), Arc::new(event_vector(draws, -1)?));
        for name in OBLIGATIONS {
            vectors.insert(format!("i:{name}"), Arc::new(event_vector(draws, BIG)?));
        }
        Ok(Self {
            n: draws,
            days,
            columns,
            vectors,
            version: 0,
            versions: BTreeMap::new(),
        })
    }

    #[getter]
    fn draws(&self) -> usize {
        self.n
    }

    #[getter]
    fn horizon(&self) -> usize {
        self.days
    }

    #[getter]
    fn version(&self) -> u64 {
        self.version
    }

    fn array<'py>(&self, py: Python<'py>, name: &str) -> PyResult<Bound<'py, PyAny>> {
        if let Some(a) = self.columns.get(name) {
            return view2(py, a.clone());
        }
        if let Some(a) = self.vectors.get(name) {
            return view1(py, a.clone());
        }
        Err(PyValueError::new_err(format!(
            "Unknown event array {name:?}"
        )))
    }

    fn snapshot<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let out = PyDict::new(py);
        for (name, a) in &self.columns {
            out.set_item(name, view2(py, a.clone())?)?;
        }
        for (name, a) in &self.vectors {
            out.set_item(name, view1(py, a.clone())?)?;
        }
        Ok(out)
    }

    fn array_versions(&self) -> BTreeMap<String, u64> {
        self.versions.clone()
    }

    fn fork(&self) -> Self {
        self.clone()
    }

    fn sliced(&self, idx: PyReadonlyArray1<'_, i64>) -> PyResult<Self> {
        let count = idx.len()?;
        validate_event_shape(count, self.days)?;
        let mut rows = Vec::new();
        rows.try_reserve_exact(count)
            .map_err(|_| PyMemoryError::new_err("Cannot allocate event row indices"))?;
        for i in idx.as_array() {
            if *i < 0 || *i >= self.n as i64 {
                return Err(PyIndexError::new_err("Event row out of range"));
            }
            rows.push(*i as usize);
        }
        Ok(self.rows(&rows))
    }

    fn replace_column(&mut self, name: &str, value: PyReadonlyArray2<'_, i64>) -> PyResult<()> {
        let a = value.as_array();
        if a.shape() != [self.n, self.days] {
            return Err(PyValueError::new_err("Event column shape mismatch"));
        }
        if !self.columns.contains_key(name) {
            return Err(PyValueError::new_err("Unknown event column"));
        }
        self.columns
            .insert(name.to_string(), Arc::new(a.to_owned()));
        Ok(())
    }

    fn replace_vector(&mut self, name: &str, value: PyReadonlyArray1<'_, i64>) -> PyResult<()> {
        let v = vec1(value, self.n, name)?;
        if !self.vectors.contains_key(name) {
            return Err(PyValueError::new_err("Unknown event vector"));
        }
        self.vectors
            .insert(name.to_string(), Arc::new(Array1::from(v)));
        Ok(())
    }

    fn book(
        &mut self,
        name: &str,
        day: PyReadonlyArray1<'_, i64>,
        cents: PyReadonlyArray1<'_, i64>,
    ) -> PyResult<bool> {
        let d = vec1(day, self.n, "day")?;
        let c = vec1(cents, self.n, "cents")?;
        self.book_values(name, &d, &c)
    }

    #[pyo3(signature = (kind, day, cents, incurred=None))]
    fn pay(
        &mut self,
        kind: &str,
        day: PyReadonlyArray1<'_, i64>,
        cents: PyReadonlyArray1<'_, i64>,
        incurred: Option<PyReadonlyArray1<'_, i64>>,
    ) -> PyResult<Vec<String>> {
        if !KINDS.contains(&kind) {
            return Err(PyValueError::new_err("Unknown cash kind"));
        }
        let d = vec1(day, self.n, "day")?;
        let c = vec1(cents, self.n, "cents")?;
        // Validate everything before any mutation: malformed input is atomic.
        let inc = incurred.map(|a| vec1(a, self.n, "incurred")).transpose()?;
        if inc.is_some() && !OBLIGATIONS.contains(&kind) {
            return Err(PyValueError::new_err("Only obligations have incurred days"));
        }
        let mut names = Vec::new();
        if self.book_values("cash", &d, &c)? {
            names.push("cash".to_string());
        }
        let key = format!("k:{kind}");
        if self.book_values(&key, &d, &c)? {
            names.push(key);
        }
        if let Some(inc) = inc {
            let key = format!("i:{kind}");
            let old = self
                .vectors
                .get(&key)
                .ok_or_else(|| PyValueError::new_err("Unknown incurred day vector"))?;
            let changed = (0..self.n)
                .any(|r| d[r] >= 0 && d[r] < self.days as i64 && c[r] != 0 && inc[r] < old[r]);
            if changed {
                let vector = self
                    .vectors
                    .get_mut(&key)
                    .ok_or_else(|| PyValueError::new_err("Unknown incurred day vector"))?;
                let a = Arc::make_mut(vector);
                for r in 0..self.n {
                    if d[r] >= 0 && d[r] < self.days as i64 && c[r] != 0 {
                        a[r] = a[r].min(inc[r]);
                    }
                }
                self.changed(std::slice::from_ref(&key));
                names.push(key);
            }
        }
        Ok(names)
    }

    fn add_delta(
        &mut self,
        names: Vec<String>,
        delta: PyReadonlyArray2<'_, i64>,
    ) -> PyResult<bool> {
        let delta = delta.as_array();
        if delta.shape() != [self.n, self.days] {
            return Err(PyValueError::new_err("Event delta shape mismatch"));
        }
        for name in &names {
            if !self.columns.contains_key(name) {
                return Err(PyValueError::new_err("Unknown event column"));
            }
        }
        if !delta.iter().any(|v| *v != 0) {
            return Ok(false);
        }
        for name in &names {
            let column = self
                .columns
                .get_mut(name)
                .ok_or_else(|| PyValueError::new_err("Unknown event column"))?;
            let a = Arc::make_mut(column);
            for ((r, d), v) in delta.indexed_iter() {
                a[[r, d]] = a[[r, d]].wrapping_add(*v);
            }
        }
        self.changed(&names);
        Ok(true)
    }

    fn petition<'py>(
        &mut self,
        py: Python<'py>,
        day: PyReadonlyArray1<'py, i64>,
        where_: PyReadonlyArray1<'py, bool>,
        lag: i64,
    ) -> PyResult<Bound<'py, PyArray1<bool>>> {
        let day = vec1(day, self.n, "petition day")?;
        let wh = bool1(where_, self.n, "petition mask")?;
        let old = self
            .vectors
            .get("petition")
            .ok_or_else(|| PyValueError::new_err("Missing petition vector"))?;
        let win = (0..self.n)
            .map(|r| {
                let d = day[r].wrapping_add(lag);
                wh[r] && d >= 0 && d < self.days as i64 && (old[r] < 0 || d < old[r])
            })
            .collect::<Vec<_>>();
        if win.iter().any(|b| *b) {
            let vector = self
                .vectors
                .get_mut("petition")
                .ok_or_else(|| PyValueError::new_err("Missing petition vector"))?;
            let a = Arc::make_mut(vector);
            for r in 0..self.n {
                if win[r] {
                    a[r] = day[r].wrapping_add(lag);
                }
            }
            self.changed(&["petition".to_string()]);
        }
        Ok(Array1::from(win).into_pyarray(py))
    }

    fn truncate_petition(&mut self) -> PyResult<Vec<String>> {
        let pet = self
            .vectors
            .get("petition")
            .ok_or_else(|| PyValueError::new_err("Missing petition vector"))?;
        let mut names = Vec::new();
        for name in ["cash".to_string()]
            .into_iter()
            .chain(KINDS.iter().map(|s| format!("k:{s}")))
        {
            let old = self
                .columns
                .get(&name)
                .ok_or_else(|| PyValueError::new_err("Unknown event column"))?;
            let any = (0..self.n)
                .any(|r| pet[r] >= 0 && (pet[r] as usize..self.days).any(|d| old[[r, d]] != 0));
            if any {
                let column = self
                    .columns
                    .get_mut(&name)
                    .ok_or_else(|| PyValueError::new_err("Unknown event column"))?;
                let a = Arc::make_mut(column);
                for r in 0..self.n {
                    if pet[r] >= 0 {
                        for d in pet[r] as usize..self.days {
                            a[[r, d]] = 0;
                        }
                    }
                }
                names.push(name);
            }
        }
        self.changed(&names);
        Ok(names)
    }

    /// The ledger's first differing dated effect on each original trajectory.
    fn divergence<'py>(
        &self,
        py: Python<'py>,
        other: &Self,
    ) -> PyResult<Bound<'py, PyArray1<i64>>> {
        if (self.n, self.days) != (other.n, other.days) {
            return Err(PyValueError::new_err("Event state shape mismatch"));
        }
        let mut out = vec![BIG; self.n];
        for (key, a) in &self.columns {
            let b = &other.columns[key];
            if Arc::ptr_eq(a, b) {
                continue;
            }
            for r in 0..self.n {
                for d in 0..self.days.min(out[r] as usize) {
                    if a[[r, d]] != b[[r, d]] {
                        out[r] = d as i64;
                        break;
                    }
                }
            }
        }
        for (key, a) in &self.vectors {
            let b = &other.vectors[key];
            if Arc::ptr_eq(a, b) {
                continue;
            }
            for r in 0..self.n {
                if a[r] != b[r] {
                    let da = if a[r] < 0 { BIG } else { a[r] };
                    let db = if b[r] < 0 { BIG } else { b[r] };
                    out[r] = out[r].min(da.min(db));
                }
            }
        }
        for d in &mut out {
            if *d >= self.days as i64 {
                *d = BIG;
            }
        }
        Ok(Array1::from(out).into_pyarray(py))
    }
}

/// A native transition dispatcher over boundary state. Configuration, the keyed
/// draw source, and complex question records keep their Python representations;
/// transitions themselves are implemented in Rust and cannot call the oracle.
#[pyclass(module = "app._native")]
pub struct NativeChain {
    pub(crate) state: Py<PyDict>,
}

impl NativeChain {
    pub(crate) fn get<'py>(&self, py: Python<'py>, name: &str) -> PyResult<Bound<'py, PyAny>> {
        self.state
            .bind(py)
            .get_item(name)?
            .ok_or_else(|| PyValueError::new_err(format!("NativeChain missing state {name:?}")))
    }

    pub(crate) fn opt<'py>(
        &self,
        py: Python<'py>,
        name: &str,
    ) -> PyResult<Option<Bound<'py, PyAny>>> {
        self.state.bind(py).get_item(name)
    }

    pub(crate) fn put(&self, py: Python<'_>, name: &str, value: &Bound<'_, PyAny>) -> PyResult<()> {
        self.state.bind(py).set_item(name, value)
    }

    pub(crate) fn put_i64(&self, py: Python<'_>, name: &str, value: i64) -> PyResult<()> {
        self.state.bind(py).set_item(name, value)
    }

    pub(crate) fn put_bool(&self, py: Python<'_>, name: &str, value: bool) -> PyResult<()> {
        self.state.bind(py).set_item(name, value)
    }

    pub(crate) fn put1(&self, py: Python<'_>, name: &str, value: Array1<i64>) -> PyResult<()> {
        self.state.bind(py).set_item(name, value.into_pyarray(py))
    }

    pub(crate) fn int(&self, py: Python<'_>, name: &str) -> PyResult<i64> {
        self.get(py, name)?.extract()
    }
    pub(crate) fn flag(&self, py: Python<'_>, name: &str) -> PyResult<bool> {
        self.get(py, name)?.extract()
    }
    pub(crate) fn n(&self, py: Python<'_>) -> PyResult<usize> {
        let draws = self.get(py, "n")?.extract()?;
        validate_draws(draws)?;
        Ok(draws)
    }
    pub(crate) fn days(&self, py: Python<'_>) -> PyResult<usize> {
        let days = self.get(py, "N")?.extract()?;
        validate_event_shape(self.n(py)?, days)?;
        Ok(days)
    }

    pub(crate) fn a1(&self, py: Python<'_>, name: &str) -> PyResult<Array1<i64>> {
        self.per_draw(py, &self.get(py, name)?)
    }

    pub(crate) fn per_draw(
        &self,
        py: Python<'_>,
        value: &Bound<'_, PyAny>,
    ) -> PyResult<Array1<i64>> {
        let n = self.n(py)?;
        if let Ok(v) = value.extract::<i64>() {
            return event_vector(n, v);
        }
        if let Ok(a) = value.cast::<PyArray1<i64>>() {
            let read = a.readonly();
            let v = read.as_array();
            if v.len() == n {
                return Ok(v.to_owned());
            }
            if v.len() == 1 {
                return event_vector(n, v[0]);
            }
        }
        Err(PyValueError::new_err(
            "NativeChain expected int64 scalar or per-draw vector",
        ))
    }

    pub(crate) fn parameter<'py>(&self, py: Python<'py>, key: &str) -> PyResult<Bound<'py, PyAny>> {
        let model = self.get(py, "m")?;
        let p = model.get_item("parameters")?.get_item(key)?;
        let sens = self.get(py, "sens")?.cast_into::<PyDict>()?;
        let pick = sens.get_item(key)?;
        match pick {
            None => p.get_item("value"),
            Some(v) if !v.is_instance_of::<PyBool>() => Ok(v),
            Some(v) if v.is_truthy()? && p.contains("sensitivity")? => p.get_item("sensitivity"),
            Some(_) => p.get_item("value"),
        }
    }

    pub(crate) fn p_i64(&self, py: Python<'_>, key: &str) -> PyResult<i64> {
        self.parameter(py, key)?.extract()
    }
    pub(crate) fn p_str(&self, py: Python<'_>, key: &str) -> PyResult<String> {
        self.parameter(py, key)?.extract()
    }
    pub(crate) fn sensitivity(&self, py: Python<'_>, key: &str) -> PyResult<bool> {
        let sens = self.get(py, "sens")?.cast_into::<PyDict>()?;
        sens.get_item(key)?
            .map(|v| v.is_truthy())
            .transpose()
            .map(|v| v.unwrap_or(false))
    }

    pub(crate) fn rule(&self, py: Python<'_>, key: &str) -> PyResult<i64> {
        self.get(py, "m")?
            .get_item("rules")?
            .get_item(key)?
            .get_item("value")?
            .extract()
    }

    pub(crate) fn ix(&self, py: Python<'_>, date: &Bound<'_, PyAny>) -> PyResult<i64> {
        let review = self
            .get(py, "s")?
            .getattr("review")?
            .call_method0("toordinal")?
            .extract::<i64>()?;
        Ok(date.call_method0("toordinal")?.extract::<i64>()? - review - 1)
    }

    /// Draw generation is an input boundary; it is the sole permitted call into
    /// the reference application's keyed input source, never a transition.
    pub(crate) fn lag(&self, py: Python<'_>, key: &str) -> PyResult<Array1<i64>> {
        let dr = self.get(py, "dr")?;
        let value = dr.call_method1("lag", (self.get(py, "m")?, self.get(py, "iid")?, key))?;
        self.per_draw(py, &value)
    }

    pub(crate) fn invoke_args(
        &self,
        py: Python<'_>,
        name: &str,
        args: Vec<Py<PyAny>>,
    ) -> PyResult<Py<PyAny>> {
        self.invoke(py, name, &PyTuple::new(py, args)?)
    }

    pub(crate) fn call0(&self, py: Python<'_>, name: &str) -> PyResult<Py<PyAny>> {
        self.invoke(py, name, &PyTuple::empty(py))
    }

    pub(crate) fn invoke(
        &self,
        py: Python<'_>,
        name: &str,
        args: &Bound<'_, PyTuple>,
    ) -> PyResult<Py<PyAny>> {
        if let Some(out) = crate::event_equity::dispatch(self, py, name, args) {
            return out;
        }
        if let Some(out) = crate::event_cash_helpers::dispatch(self, py, name, args) {
            return out;
        }
        if let Some(out) = crate::event_snapshot::dispatch(self, py, name, args) {
            return out;
        }
        if let Some(out) = crate::event_core::dispatch(self, py, name, args) {
            return out;
        }
        if let Some(out) = crate::event_transitions::dispatch(self, py, name, args) {
            return out;
        }
        self.core_dispatch(py, name, args)
    }

    fn core_dispatch(
        &self,
        py: Python<'_>,
        name: &str,
        args: &Bound<'_, PyTuple>,
    ) -> PyResult<Py<PyAny>> {
        match name {
            "per_draw" => {
                let np = py.import("numpy")?;
                let dtype = match args.get_item(1) {
                    Ok(v) => v,
                    Err(_) => np.getattr("int64")?,
                };
                let a = np.call_method1("asarray", (args.get_item(0)?, dtype))?;
                let shape = a.getattr("shape")?.extract::<Vec<usize>>()?;
                if shape == [self.n(py)?] {
                    return Ok(a.unbind());
                }
                if shape == [1] {
                    return Ok(np
                        .call_method1("broadcast_to", (a, (self.n(py)?,)))?
                        .unbind());
                }
                if !shape.is_empty() && shape != [1] {
                    return Err(PyValueError::new_err(
                        "Native per-draw input cannot broadcast to draw count",
                    ));
                }
                let item = a.call_method0("item")?;
                let dtype = a.getattr("dtype")?.getattr("str")?.extract::<String>()?;
                let n = self.n(py)?;
                let result = match dtype.as_str() {
                    "<i8" | "=i8" => Array1::from_elem(n, item.extract::<i64>()?)
                        .into_pyarray(py)
                        .into_any(),
                    "|i1" => Array1::from_elem(n, item.extract::<i8>()?)
                        .into_pyarray(py)
                        .into_any(),
                    "<i4" | "=i4" => Array1::from_elem(n, item.extract::<i32>()?)
                        .into_pyarray(py)
                        .into_any(),
                    "|b1" => Array1::from_elem(n, item.extract::<bool>()?)
                        .into_pyarray(py)
                        .into_any(),
                    "<f8" | "=f8" => Array1::from_elem(n, item.extract::<f64>()?)
                        .into_pyarray(py)
                        .into_any(),
                    _ => {
                        return Err(PyValueError::new_err(format!(
                            "Unsupported native per-draw dtype {dtype:?}"
                        )))
                    }
                };
                Ok(result.unbind())
            }
            "p" => Ok(self
                .parameter(py, &args.get_item(0)?.extract::<String>()?)?
                .unbind()),
            "ix" => Ok(self
                .ix(py, &args.get_item(0)?)?
                .into_pyobject(py)?
                .into_any()
                .unbind()),
            _ => Err(PyValueError::new_err(format!(
                "NativeChain transition {name:?} is not implemented; refusing Python fallback"
            ))),
        }
    }
}

#[pymethods]
impl NativeChain {
    fn __traverse__(&self, visit: PyVisit<'_>) -> Result<(), PyTraverseError> {
        visit.call(&self.state)
    }

    fn __clear__(&self, py: Python<'_>) {
        self.state.bind(py).clear();
    }

    #[new]
    fn new(state: Py<PyDict>) -> Self {
        Self { state }
    }

    #[pyo3(signature = (name, *args))]
    fn call(&self, py: Python<'_>, name: &str, args: &Bound<'_, PyTuple>) -> PyResult<Py<PyAny>> {
        self.invoke(py, name, args)
    }
}

/// Cash at daily processing, before the decision's own booking and obligations.
#[pyfunction]
#[pyo3(signature = (day, cum, operating_inflow, inflow, lock, levy, opening))]
fn decision_balance<'py>(
    py: Python<'py>,
    day: PyReadonlyArray1<'py, i64>,
    cum: PyReadonlyArray2<'py, i64>,
    operating_inflow: PyReadonlyArray2<'py, i64>,
    inflow: PyReadonlyArray2<'py, i64>,
    lock: PyReadonlyArray2<'py, i64>,
    levy: PyReadonlyArray2<'py, i64>,
    opening: i64,
) -> PyResult<Bound<'py, PyArray1<i64>>> {
    let cum = cum.as_array();
    let (n, days) = cum.dim();
    if days == 0 {
        return Err(PyValueError::new_err("Empty decision horizon"));
    }
    let day = vec1(day, n, "decision day")?;
    let op = operating_inflow.as_array();
    let inflow = inflow.as_array();
    let lock = lock.as_array();
    let levy = levy.as_array();
    if [op.dim(), inflow.dim(), lock.dim(), levy.dim()]
        .iter()
        .any(|s| *s != cum.dim())
    {
        return Err(PyValueError::new_err("Decision balance shape mismatch"));
    }
    let out = Array1::from_iter((0..n).map(|r| {
        let t = day[r].clamp(0, days as i64 - 1) as usize;
        let prev = if t > 0 { cum[[r, t - 1]] } else { opening };
        prev.wrapping_add(op[[r, t]])
            .wrapping_add(inflow[[r, t]])
            .wrapping_sub(lock[[r, t]])
            .wrapping_add(levy[[r, t]])
    }));
    Ok(out.into_pyarray(py))
}

#[pyfunction]
fn first_below<'py>(
    py: Python<'py>,
    cash: PyReadonlyArray2<'py, i64>,
    floor: PyReadonlyArray2<'py, i64>,
    zero: bool,
) -> PyResult<Bound<'py, PyArray1<i64>>> {
    let cash = cash.as_array();
    let floor = floor.as_array();
    if cash.dim() != floor.dim() {
        return Err(PyValueError::new_err("Cash floor shape mismatch"));
    }
    let (n, days) = cash.dim();
    let out = Array1::from_iter((0..n).map(|r| {
        (0..days)
            .find(|d| cash[[r, *d]] < if zero { 0 } else { floor[[r, *d]] })
            .map_or(BIG, |d| d as i64)
    }));
    Ok(out.into_pyarray(py))
}

#[pyfunction]
fn resolve_delta<'py>(
    py: Python<'py>,
    old: PyReadonlyArray1<'py, i64>,
    new: PyReadonlyArray1<'py, i64>,
    legal: PyReadonlyArray2<'py, i64>,
) -> PyResult<Bound<'py, PyArray2<i64>>> {
    let legal = legal.as_array();
    let (n, days) = legal.dim();
    let old = vec1(old, n, "old resolution")?;
    let new = vec1(new, n, "new resolution")?;
    let out = Array2::from_shape_fn((n, days), |(r, d)| {
        if d as i64 >= new[r] && (d as i64) < old[r] {
            legal[[r, d]].wrapping_neg()
        } else {
            0
        }
    });
    Ok(out.into_pyarray(py))
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<EventLedger>()?;
    m.add_class::<NativeChain>()?;
    m.add_function(wrap_pyfunction!(decision_balance, m)?)?;
    m.add_function(wrap_pyfunction!(first_below, m)?)?;
    m.add_function(wrap_pyfunction!(resolve_delta, m)?)?;
    m.add_function(wrap_pyfunction!(event_cash_zeros, m)?)?;
    m.add_function(wrap_pyfunction!(event_cash_split, m)?)?;
    m.add_function(wrap_pyfunction!(event_cash_add, m)?)?;
    Ok(())
}

/// Plain dataclass boundary for freshly allocated, independent event arrays.
#[pyfunction]
fn event_cash_zeros(
    py: Python<'_>,
    draws: usize,
    days: usize,
    classified: bool,
) -> PyResult<Py<PyTuple>> {
    validate_event_shape(draws, days)?;
    let mut fields = vec![
        event_matrix(draws, days)?
            .into_pyarray(py)
            .into_any()
            .unbind(),
        event_matrix(draws, days)?
            .into_pyarray(py)
            .into_any()
            .unbind(),
        event_matrix(draws, days)?
            .into_pyarray(py)
            .into_any()
            .unbind(),
        event_vector(draws, -1)?
            .into_pyarray(py)
            .into_any()
            .unbind(),
    ];
    if classified {
        let kinds = PyDict::new(py);
        let incurred = PyDict::new(py);
        for k in KINDS {
            kinds.set_item(k, event_matrix(draws, days)?.into_pyarray(py))?;
        }
        for k in OBLIGATIONS {
            incurred.set_item(k, event_vector(draws, BIG)?.into_pyarray(py))?;
        }
        fields.push(kinds.into_any().unbind());
        fields.push(incurred.into_any().unbind());
    } else {
        fields.push(py.None());
        fields.push(py.None());
    }
    fields.push(py.None());
    Ok(PyTuple::new(py, fields)?.unbind())
}

#[pyfunction]
fn event_cash_split(py: Python<'_>, events: &Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
    let kinds = events.getattr("kinds")?;
    if !kinds.is_none() {
        return Ok(PyTuple::new(py, [kinds, events.getattr("incurred")?])?
            .into_any()
            .unbind());
    }
    let cash = events.getattr("cash")?;
    let read = cash.cast::<PyArray2<i64>>()?.try_readonly()?;
    let arr = read.as_array();
    if arr.iter().any(|v| *v != 0) {
        return Ok(py.None());
    }
    let (draws, days) = arr.dim();
    let kinds = PyDict::new(py);
    let incurred = PyDict::new(py);
    for k in KINDS {
        kinds.set_item(k, Array2::<i64>::zeros((draws, days)).into_pyarray(py))?;
    }
    for k in OBLIGATIONS {
        incurred.set_item(k, Array1::from_elem(draws, BIG).into_pyarray(py))?;
    }
    Ok(PyTuple::new(py, [kinds, incurred])?.into_any().unbind())
}

/// Classified composition books each cash contribution once. The petition is
/// the earliest on each trajectory and obligations retain their earliest basis.
#[pyfunction]
fn event_cash_add(
    py: Python<'_>,
    left: &Bound<'_, PyAny>,
    right: &Bound<'_, PyAny>,
) -> PyResult<Py<PyTuple>> {
    let ac = left.getattr("cash")?;
    let bc = right.getattr("cash")?;
    let ar = ac.cast::<PyArray2<i64>>()?.try_readonly()?;
    let br = bc.cast::<PyArray2<i64>>()?.try_readonly()?;
    let a = ar.as_array();
    let b = br.as_array();
    if a.dim() != b.dim() {
        return Err(PyValueError::new_err("Event cash shape mismatch"));
    }
    let (n, days) = a.dim();
    let mut fields = Vec::with_capacity(7);
    fields.push(
        Array2::from_shape_fn((n, days), |(r, d)| a[[r, d]].wrapping_add(b[[r, d]]))
            .into_pyarray(py)
            .into_any()
            .unbind(),
    );
    for name in ["lock", "capacity"] {
        let av = left.getattr(name)?;
        let bv = right.getattr(name)?;
        let av = av.cast::<PyArray2<i64>>()?.try_readonly()?;
        let bv = bv.cast::<PyArray2<i64>>()?.try_readonly()?;
        let av = av.as_array();
        let bv = bv.as_array();
        if av.dim() != (n, days) || bv.dim() != (n, days) {
            return Err(PyValueError::new_err("Event column shape mismatch"));
        }
        fields.push(
            Array2::from_shape_fn((n, days), |(r, d)| av[[r, d]].wrapping_add(bv[[r, d]]))
                .into_pyarray(py)
                .into_any()
                .unbind(),
        );
    }
    let ap = left.getattr("petition")?;
    let bp = right.getattr("petition")?;
    let ap = ap.cast::<PyArray1<i64>>()?.try_readonly()?;
    let bp = bp.cast::<PyArray1<i64>>()?.try_readonly()?;
    let ap = ap.as_array();
    let bp = bp.as_array();
    if ap.len() != n || bp.len() != n {
        return Err(PyValueError::new_err("Petition shape mismatch"));
    }
    fields.push(
        Array1::from_iter((0..n).map(|r| {
            if ap[r] < 0 {
                bp[r]
            } else if bp[r] < 0 {
                ap[r]
            } else {
                ap[r].min(bp[r])
            }
        }))
        .into_pyarray(py)
        .into_any()
        .unbind(),
    );
    let ak = left.getattr("kinds")?;
    let bk = right.getattr("kinds")?;
    if (!ak.is_none() || !bk.is_none())
        && (!ak.is_none() || !a.iter().any(|v| *v != 0))
        && (!bk.is_none() || !b.iter().any(|v| *v != 0))
    {
        let kinds = PyDict::new(py);
        let incurred = PyDict::new(py);
        for key in KINDS {
            let av = if ak.is_none() {
                None
            } else {
                Some(ak.get_item(key)?.cast_into::<PyArray2<i64>>()?)
            };
            let bv = if bk.is_none() {
                None
            } else {
                Some(bk.get_item(key)?.cast_into::<PyArray2<i64>>()?)
            };
            let av = av.as_ref().map(|v| v.try_readonly()).transpose()?;
            let bv = bv.as_ref().map(|v| v.try_readonly()).transpose()?;
            if av.as_ref().is_some_and(|v| v.as_array().dim() != (n, days))
                || bv.as_ref().is_some_and(|v| v.as_array().dim() != (n, days))
            {
                return Err(PyValueError::new_err("Cash kind shape mismatch"));
            }
            let av = av.as_ref().map(|v| v.as_array());
            let bv = bv.as_ref().map(|v| v.as_array());
            let out = Array2::from_shape_fn((n, days), |(r, d)| {
                av.as_ref()
                    .map_or(0, |v| v[[r, d]])
                    .wrapping_add(bv.as_ref().map_or(0, |v| v[[r, d]]))
            });
            kinds.set_item(key, out.into_pyarray(py))?;
        }
        let ai = left.getattr("incurred")?;
        let bi = right.getattr("incurred")?;
        for key in OBLIGATIONS {
            let av = if ak.is_none() {
                None
            } else {
                Some(ai.get_item(key)?.cast_into::<PyArray1<i64>>()?)
            };
            let bv = if bk.is_none() {
                None
            } else {
                Some(bi.get_item(key)?.cast_into::<PyArray1<i64>>()?)
            };
            let av = av.as_ref().map(|v| v.try_readonly()).transpose()?;
            let bv = bv.as_ref().map(|v| v.try_readonly()).transpose()?;
            if av.as_ref().is_some_and(|v| v.as_array().len() != n)
                || bv.as_ref().is_some_and(|v| v.as_array().len() != n)
            {
                return Err(PyValueError::new_err("Incurred day shape mismatch"));
            }
            let av = av.as_ref().map(|v| v.as_array());
            let bv = bv.as_ref().map(|v| v.as_array());
            incurred.set_item(
                key,
                Array1::from_iter((0..n).map(|r| {
                    av.as_ref()
                        .map_or(BIG, |v| v[r])
                        .min(bv.as_ref().map_or(BIG, |v| v[r]))
                }))
                .into_pyarray(py),
            )?;
        }
        fields.push(kinds.into_any().unbind());
        fields.push(incurred.into_any().unbind());
    } else {
        fields.push(py.None());
        fields.push(py.None());
    }
    let ap = left.getattr("proceeds")?;
    let bp = right.getattr("proceeds")?;
    if !ap.is_none() || !bp.is_none() {
        let proceeds = PyDict::new(py);
        for key in ["atm_proceeds", "offering_proceeds"] {
            let av = if ap.is_none() {
                None
            } else {
                ap.cast::<PyDict>()?.get_item(key)?
            };
            let bv = if bp.is_none() {
                None
            } else {
                bp.cast::<PyDict>()?.get_item(key)?
            };
            let av = av
                .as_ref()
                .map(|v| -> PyResult<_> { Ok(v.cast::<PyArray1<i64>>()?.try_readonly()?) })
                .transpose()?;
            let bv = bv
                .as_ref()
                .map(|v| -> PyResult<_> { Ok(v.cast::<PyArray1<i64>>()?.try_readonly()?) })
                .transpose()?;
            if av.as_ref().is_some_and(|v| v.as_array().len() != n)
                || bv.as_ref().is_some_and(|v| v.as_array().len() != n)
            {
                return Err(PyValueError::new_err("Proceeds shape mismatch"));
            }
            let av = av.as_ref().map(|v| v.as_array());
            let bv = bv.as_ref().map(|v| v.as_array());
            proceeds.set_item(
                key,
                Array1::from_iter((0..n).map(|r| {
                    av.as_ref()
                        .map_or(0, |v| v[r])
                        .wrapping_add(bv.as_ref().map_or(0, |v| v[r]))
                }))
                .into_pyarray(py),
            )?;
        }
        fields.push(proceeds.into_any().unbind());
    } else {
        fields.push(py.None());
    }
    Ok(PyTuple::new(py, fields)?.unbind())
}

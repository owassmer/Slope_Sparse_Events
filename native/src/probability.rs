//! Ordered path probabilities and composite derivatives.
//!
//! Ordinary Python floats use Rust IEEE-754 arithmetic. Other scalar types
//! retain Python's numeric protocol at the representation boundary: NumPy's
//! float32/longdouble promotion and rounding must not become f64 arithmetic.
//! Cached immutable conjunction tuples and distribution lookup are host
//! representation boundaries; no forecast or reducer arithmetic is called.
use numpy::IntoPyArray;
use pyo3::exceptions::{PyKeyError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyDict, PyFloat, PyInt, PyList, PySet, PyString, PyTuple};

type Edge<'py> = (Bound<'py, PyString>, Bound<'py, PyString>);
type Conjunctions<'py> = Bound<'py, PyTuple>;

enum Number<'py> {
    Float(f64),
    Host(Bound<'py, PyAny>),
}

impl<'py> Number<'py> {
    fn from(value: Bound<'py, PyAny>) -> PyResult<Self> {
        if value.is_exact_instance_of::<PyFloat>() {
            Ok(Self::Float(value.extract()?))
        } else {
            Ok(Self::Host(value))
        }
    }
    fn object(&self, py: Python<'py>) -> Bound<'py, PyAny> {
        match self {
            Self::Float(v) => PyFloat::new(py, *v).into_any(),
            Self::Host(v) => v.clone(),
        }
    }
    fn copy(&self) -> Self {
        match self {
            Self::Float(v) => Self::Float(*v),
            Self::Host(v) => Self::Host(v.clone()),
        }
    }
    fn mul(&self, py: Python<'py>, other: &Self) -> PyResult<Self> {
        match (self, other) {
            (Self::Float(a), Self::Float(b)) => Ok(Self::Float(a * b)),
            _ => Self::from(self.object(py).mul(other.object(py))?),
        }
    }
    fn add(&self, py: Python<'py>, other: &Self) -> PyResult<Self> {
        match (self, other) {
            (Self::Float(a), Self::Float(b)) => Ok(Self::Float(a + b)),
            _ => Self::from(self.object(py).add(other.object(py))?),
        }
    }
    fn zero(&self, py: Python<'py>) -> PyResult<bool> {
        match self {
            Self::Float(v) => Ok(*v == 0.0),
            Self::Host(v) => v.eq(PyFloat::new(py, 0.0)),
        }
    }
    fn float(&self) -> PyResult<f64> {
        match self {
            Self::Float(v) => Ok(*v),
            Self::Host(v) => v.extract(),
        }
    }
}

/// CPython 3.12 sum: exact integers first, then compensated exact floats.
/// Encountering another scalar switches permanently to its numeric protocol.
enum Sum<'py> {
    Integer(i64),
    Float { total: f64, correction: f64 },
    Host(Number<'py>),
}
impl<'py> Sum<'py> {
    fn finish_float(total: f64, correction: f64) -> f64 {
        if correction != 0.0 && correction.is_finite() {
            total + correction
        } else {
            total
        }
    }
    fn add_float(total: f64, correction: f64, value: f64) -> Self {
        let next = total + value;
        let correction = correction
            + if total.abs() >= value.abs() {
                (total - next) + value
            } else {
                (value - next) + total
            };
        Self::Float {
            total: next,
            correction,
        }
    }
    fn add(self, py: Python<'py>, value: Number<'py>) -> PyResult<Self> {
        match self {
            Self::Integer(total) => match value {
                // The first float is added to the integer result before the
                // compensated loop begins. Its initial rounding is retained.
                Number::Float(v) => Ok(Self::Float {
                    total: total as f64 + v,
                    correction: 0.0,
                }),
                Number::Host(v)
                    if v.is_exact_instance_of::<PyInt>() || v.is_exact_instance_of::<PyBool>() =>
                {
                    if let Ok(value) = v.extract::<i64>() {
                        if let Some(total) = total.checked_add(value) {
                            return Ok(Self::Integer(total));
                        }
                    }
                    // CPython's machine-integer overflow leaves the fast sum
                    // loop permanently, even if subsequent integers cancel.
                    Ok(Self::Host(Number::from(PyInt::new(py, total).add(v)?)?))
                }
                value => Ok(Self::Host(
                    Number::Host(PyInt::new(py, total).into_any()).add(py, &value)?,
                )),
            },
            Self::Float { total, correction } => match value {
                Number::Float(v) => Ok(Self::add_float(total, correction, v)),
                Number::Host(v) if v.is_instance_of::<PyInt>() => {
                    Ok(Self::add_float(total, correction, v.extract()?))
                }
                value => Ok(Self::Host(
                    Number::Float(Self::finish_float(total, correction)).add(py, &value)?,
                )),
            },
            Self::Host(total) => Ok(Self::Host(total.add(py, &value)?)),
        }
    }
    fn finish(self, py: Python<'py>) -> PyResult<Number<'py>> {
        match self {
            Self::Integer(v) => Ok(Number::Host(PyInt::new(py, v).into_any())),
            Self::Float { total, correction } => {
                Ok(Number::Float(Self::finish_float(total, correction)))
            }
            Self::Host(v) => Ok(v),
        }
    }
}

// Reuse the oracle's immutable parsing cache as shared program metadata. This
// keeps JSONDecodeError and parse-once behavior, without another global cache
// or copying every conjunction/atom string for each probability setting.
fn composite(key: &Bound<'_, PyString>) -> PyResult<bool> {
    // Unicode's builtin prefix check reads only the prefix. Converting a very
    // large composite key to UTF-8 here would validate its entire contents.
    key.call_method1("startswith", ("=",))?.extract()
}

fn conjunctions<'py>(py: Python<'py>, key: &Bound<'py, PyString>) -> PyResult<Conjunctions<'py>> {
    if !composite(key)? {
        return Err(PyKeyError::new_err(key.clone().unbind()));
    }
    py.import("app.disputes.forecast")?
        .getattr("_conjunctions")?
        .call1((key,))?
        .cast_into::<PyTuple>()
        .map_err(Into::into)
}

fn atom<'py>(edge: &Bound<'py, PyAny>) -> PyResult<Edge<'py>> {
    let (length, key, branch) = if let Ok(tuple) = edge.cast::<PyTuple>() {
        (tuple.len(), tuple.get_item(0), tuple.get_item(1))
    } else if let Ok(list) = edge.cast::<PyList>() {
        (list.len(), list.get_item(0), list.get_item(1))
    } else {
        return Err(PyValueError::new_err(
            "a probability edge must contain two strings",
        ));
    };
    match length {
        2 => Ok((
            key?.cast_into::<PyString>()?,
            branch?.cast_into::<PyString>()?,
        )),
        0 | 1 => Err(PyValueError::new_err(format!(
            "not enough values to unpack (expected 2, got {length})"
        ))),
        _ => Err(PyValueError::new_err(
            "too many values to unpack (expected 2)",
        )),
    }
}

fn each_edge<'py>(
    edges: &Bound<'py, PyAny>,
    mut visit: impl FnMut(Edge<'py>) -> PyResult<()>,
) -> PyResult<()> {
    if let Ok(tuple) = edges.cast::<PyTuple>() {
        for item in tuple.iter() {
            visit(atom(&item)?)?;
        }
    } else if let Ok(list) = edges.cast::<PyList>() {
        for item in list.iter() {
            visit(atom(&item)?)?;
        }
    } else {
        for item in edges.try_iter()? {
            visit(atom(&item?)?)?;
        }
    }
    Ok(())
}

struct Distribution<'py> {
    value: Bound<'py, PyAny>,
    composite: bool,
}
impl<'py> Distribution<'py> {
    fn new(py: Python<'py>, value: Bound<'py, PyAny>) -> PyResult<Self> {
        let composite = value.is_instance(&py.import("app.disputes.forecast")?.getattr("Dist")?)?;
        Ok(Self { value, composite })
    }
    fn branch(
        &self,
        py: Python<'py>,
        key: &Bound<'py, PyString>,
        branch: &Bound<'py, PyString>,
    ) -> PyResult<Number<'py>> {
        let distribution = if self.composite {
            let dict = self.value.cast::<PyDict>()?;
            match dict.get_item(key)? {
                Some(value) => value,
                None => missing(py, dict, key)?.into_any(),
            }
        } else {
            self.value.get_item(key)?
        };
        Number::from(distribution.get_item(branch)?)
    }
}

fn missing<'py>(
    py: Python<'py>,
    dist: &Bound<'py, PyDict>,
    key: &Bound<'py, PyString>,
) -> PyResult<Bound<'py, PyDict>> {
    let parts = conjunctions(py, key)?;
    let d = Distribution {
        value: dist.clone().into_any(),
        composite: true,
    };
    let mut sum = Sum::Integer(0);
    for part in parts.iter() {
        // math.prod starts with integer 1, which matters for integer and NumPy
        // scalar conjunctions (including an empty conjunction).
        let mut product = Number::Host(PyInt::new(py, 1).into_any());
        for edge in part.cast::<PyTuple>()?.iter() {
            let (key, branch) = atom(&edge)?;
            product = product.mul(py, &d.branch(py, &key, &branch)?)?;
        }
        sum = sum.add(py, product)?;
    }
    let mut yes = sum.finish(py)?.object(py);
    // Python min/max keep their first operand on equality and NaN. This also
    // retains the integer type of the empty/disjoint-empty cases.
    if PyFloat::new(py, 0.0).gt(&yes)? {
        yes = PyFloat::new(py, 0.0).into_any();
    }
    if PyFloat::new(py, 1.0).lt(&yes)? {
        yes = PyFloat::new(py, 1.0).into_any();
    }
    let no = PyFloat::new(py, 1.0).sub(&yes)?;
    let out = PyDict::new(py);
    out.set_item("yes", yes)?;
    out.set_item("no", no)?;
    dist.set_item(key, &out)?;
    Ok(out)
}

fn path<'py>(
    py: Python<'py>,
    edges: &Bound<'py, PyAny>,
    d: &Distribution<'py>,
) -> PyResult<Number<'py>> {
    let mut p = Number::Float(1.0);
    each_edge(edges, |(key, branch)| {
        p = p.mul(py, &d.branch(py, &key, &branch)?)?;
        Ok(())
    })?;
    Ok(p)
}

#[pyfunction]
fn probability_dist_missing<'py>(
    py: Python<'py>,
    dist: &Bound<'py, PyDict>,
    key: &Bound<'py, PyString>,
) -> PyResult<Bound<'py, PyDict>> {
    missing(py, dist, key)
}

#[pyfunction]
fn probability_edge<'py>(
    py: Python<'py>,
    key: &Bound<'py, PyString>,
    branch: &Bound<'py, PyString>,
    dist: Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    Ok(Distribution::new(py, dist)?
        .branch(py, key, branch)?
        .object(py))
}

#[pyfunction]
fn probability_path<'py>(
    py: Python<'py>,
    edges: &Bound<'py, PyAny>,
    dist: Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    Ok(path(py, edges, &Distribution::new(py, dist)?)?.object(py))
}

#[pyfunction]
#[pyo3(signature = (edges, dists, infer_scalar_types=false))]
fn probability_groups<'py>(
    py: Python<'py>,
    edges: &Bound<'py, PyAny>,
    dists: Vec<Bound<'py, PyAny>>,
    infer_scalar_types: bool,
) -> PyResult<Bound<'py, PyAny>> {
    if infer_scalar_types {
        let out = PyList::empty(py);
        for dist in dists {
            out.append(path(py, edges, &Distribution::new(py, dist)?)?.object(py))?;
        }
        // Sharded path groups used np.array on scalar products, which infers
        // float32/longdouble from NumPy scalars. This is dtype packaging only.
        return py.import("numpy")?.getattr("array")?.call1((out,));
    }
    let mut out = crate::price::filled_vec(dists.len(), 1.0)?;
    for (i, dist) in dists.into_iter().enumerate() {
        out[i] = path(py, edges, &Distribution::new(py, dist)?)?.float()?;
    }
    Ok(out.into_pyarray(py).into_any())
}

#[pyfunction]
fn probability_derivative<'py>(
    py: Python<'py>,
    edges: &Bound<'py, PyAny>,
    dist: Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyList>> {
    let seen = PySet::empty(py)?;
    let mut items = Vec::new();
    let mut parsed = Vec::new();
    each_edge(edges, |edge| {
        let key = &edge.0;
        let parts = if composite(key)? {
            Some(conjunctions(py, key)?)
        } else {
            None
        };
        let keys = PySet::empty(py)?;
        match &parts {
            Some(parts) => {
                for part in parts.iter() {
                    for edge in part.cast::<PyTuple>()?.iter() {
                        let (key, _) = atom(&edge)?;
                        keys.add(key)?;
                    }
                }
            }
            None => {
                keys.add(key)?;
            }
        }
        let mut duplicate: Option<Bound<'py, PyString>> = None;
        for key in keys.iter() {
            let key = key.cast_into::<PyString>()?;
            if seen.contains(&key)? {
                let earlier = match &duplicate {
                    Some(previous) => key.as_any().lt(previous)?,
                    None => true,
                };
                if earlier {
                    duplicate = Some(key);
                }
            }
        }
        if let Some(key) = duplicate {
            let key = key.to_str()?;
            return Err(PyValueError::new_err(format!(
                "a path reads {key} twice: its probability is not linear in it"
            )));
        }
        for key in keys.iter() {
            seen.add(key)?;
        }
        parsed.push(parts);
        items.push(edge);
        Ok(())
    })?;
    let edges = items;
    let d = Distribution::new(py, dist)?;
    let mut values = Vec::with_capacity(edges.len());
    for (key, branch) in &edges {
        values.push(d.branch(py, key, branch)?);
    }
    let mut before = Vec::with_capacity(edges.len() + 1);
    before.push(Number::Float(1.0));
    let mut acc = Number::Float(1.0);
    for value in &values {
        acc = acc.mul(py, value)?;
        before.push(acc.copy());
    }
    let mut after: Vec<Number<'py>> = (0..=edges.len()).map(|_| Number::Float(1.0)).collect();
    acc = Number::Float(1.0);
    for i in (0..values.len()).rev() {
        acc = acc.mul(py, &values[i])?;
        after[i] = acc.copy();
    }
    // Index map provides lookup; the vector preserves defaultdict's insertion
    // order even when a later term cancels a previously inserted derivative.
    let indices = PyDict::new(py);
    let mut terms: Vec<(Edge<'py>, Number<'py>)> = Vec::new();
    let mut add = |edge: &Edge<'py>, term: Number<'py>| -> PyResult<()> {
        let key = PyTuple::new(py, [&edge.0, &edge.1])?;
        if let Some(index) = indices.get_item(&key)? {
            let index = index.extract::<usize>()?;
            let (.., value) = &mut terms[index];
            *value = value.add(py, &term)?;
        } else {
            indices.set_item(key, terms.len())?;
            terms.push((edge.clone(), Number::Float(0.0).add(py, &term)?));
        }
        Ok(())
    };
    for (i, edge) in edges.iter().enumerate() {
        let rest = before[i].mul(py, &after[i + 1])?;
        if rest.zero(py)? {
            continue;
        }
        match &parsed[i] {
            None => add(edge, rest)?,
            Some(parts) => {
                let sign = Number::Float(if edge.1.as_any().eq("yes")? {
                    1.0
                } else {
                    -1.0
                });
                for part in parts.iter() {
                    let part = part.cast::<PyTuple>()?;
                    for (m, edge) in part.iter().enumerate() {
                        let (key, branch) = atom(&edge)?;
                        let atom_key = (key, branch);
                        let mut other = Number::Float(1.0);
                        for (n, edge) in part.iter().enumerate() {
                            if n != m {
                                let (key, branch) = atom(&edge)?;
                                other = other.mul(py, &d.branch(py, &key, &branch)?)?;
                            }
                        }
                        add(&atom_key, sign.mul(py, &rest)?.mul(py, &other)?)?;
                    }
                }
            }
        }
    }
    let out = PyList::empty(py);
    for ((key, branch), value) in terms {
        if !value.zero(py)? {
            let tuple = PyTuple::new(py, [key.into_any(), branch.into_any(), value.object(py)])?;
            out.append(tuple)?;
        }
    }
    Ok(out)
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(probability_dist_missing, m)?)?;
    m.add_function(wrap_pyfunction!(probability_edge, m)?)?;
    m.add_function(wrap_pyfunction!(probability_path, m)?)?;
    m.add_function(wrap_pyfunction!(probability_groups, m)?)?;
    m.add_function(wrap_pyfunction!(probability_derivative, m)?)?;
    Ok(())
}

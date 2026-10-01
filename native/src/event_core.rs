//! Native event-booking, timeline, and financial state queries.
use crate::events::{NativeChain, BIG};
use crate::price::{checked_shape, filled_vec, round_int};
use ndarray::{Array1, Array2, ArrayD, ArrayView1};
use numpy::{
    IntoPyArray, PyArray1, PyArray2, PyArrayDyn, PyArrayMethods, PyReadonlyArray1,
    PyUntypedArrayMethods,
};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict, PyInt, PyList, PySet, PyString, PyTuple};

type Obj = Py<PyAny>;
type ResultObj = PyResult<Obj>;
const KINDS: [&str; 6] = [
    "inflow",
    "levy",
    "reduction",
    "settlement",
    "notes_interest",
    "judgment",
];
const OBLIGATIONS: [&str; 3] = ["settlement", "notes_interest", "judgment"];
pub(crate) fn out1(py: Python<'_>, a: Array1<i64>) -> Obj {
    a.into_pyarray(py).into_any().unbind()
}
pub(crate) fn bool1(py: Python<'_>, a: Array1<bool>) -> Obj {
    a.into_pyarray(py).into_any().unbind()
}
pub(crate) fn text_obj(py: Python<'_>, s: &str) -> Obj {
    PyString::new(py, s).into_any().unbind()
}
pub(crate) fn int_obj(py: Python<'_>, v: i64) -> Obj {
    PyInt::new(py, v).into_any().unbind()
}
pub(crate) fn array1(value: &Obj, py: Python<'_>) -> PyResult<Array1<i64>> {
    Ok(value
        .bind(py)
        .cast::<PyArray1<i64>>()?
        .readonly()
        .as_array()
        .to_owned())
}
pub(crate) fn asbool(value: &Bound<'_, PyAny>) -> PyResult<Array1<bool>> {
    Ok(value
        .cast::<PyArray1<bool>>()?
        .readonly()
        .as_array()
        .to_owned())
}
pub(crate) fn call(c: &NativeChain, py: Python<'_>, name: &str, args: Vec<Obj>) -> ResultObj {
    c.invoke_args(py, name, args)
}
pub(crate) fn ev<'py>(c: &NativeChain, py: Python<'py>, name: &str) -> PyResult<Bound<'py, PyAny>> {
    let ev = c.get(py, "ev")?;
    if let Some(k) = name.strip_prefix("k:") {
        ev.getattr("kinds")?.get_item(k)
    } else if let Some(k) = name.strip_prefix("i:") {
        ev.getattr("incurred")?.get_item(k)
    } else {
        ev.getattr(name)
    }
}
fn set_ev(c: &NativeChain, py: Python<'_>, name: &str, value: &Bound<'_, PyAny>) -> PyResult<()> {
    let ev = c.get(py, "ev")?;
    if let Some(k) = name.strip_prefix("k:") {
        ev.getattr("kinds")?.set_item(k, value)
    } else if let Some(k) = name.strip_prefix("i:") {
        ev.getattr("incurred")?.set_item(k, value)
    } else {
        ev.setattr(name, value)
    }
}
fn names() -> Vec<String> {
    ["cash", "lock", "capacity", "petition"]
        .iter()
        .map(|s| s.to_string())
        .chain(KINDS.iter().map(|s| format!("k:{s}")))
        .chain(OBLIGATIONS.iter().map(|s| format!("i:{s}")))
        .collect()
}
fn touch(c: &NativeChain, py: Python<'_>, ns: &[String]) -> PyResult<()> {
    let v = c.int(py, "_cv")? + 1;
    c.put_i64(py, "_cv", v)?;
    let av = c.get(py, "_av")?.cast_into::<PyDict>()?;
    for name in if ns.is_empty() { names() } else { ns.to_vec() } {
        av.set_item(name, v)?;
    }
    Ok(())
}
fn writable<'py>(c: &NativeChain, py: Python<'py>, name: &str) -> PyResult<Bound<'py, PyAny>> {
    let a = ev(c, py, name)?;
    if let Some(own) = c.opt(py, "_ev_own")? {
        if !own.contains(name)? {
            let new = a.call_method0("copy")?;
            set_ev(c, py, name, &new)?;
            own.cast::<PySet>()?.add(name)?;
            return Ok(new);
        }
    }
    Ok(a)
}
fn array_name(c: &NativeChain, py: Python<'_>, a: &Bound<'_, PyAny>) -> PyResult<Option<String>> {
    for name in names() {
        if ev(c, py, &name)?.is(a) {
            return Ok(Some(name));
        }
    }
    Ok(None)
}
fn book(
    c: &NativeChain,
    py: Python<'_>,
    a: &Bound<'_, PyAny>,
    day: &Array1<i64>,
    amt: &Array1<i64>,
) -> PyResult<()> {
    let n = c.n(py)?;
    let days = c.days(py)?;
    if day.len() != n || amt.len() != n {
        return Err(PyValueError::new_err("Booking shape mismatch"));
    }
    if !(0..n).any(|r| day[r] >= 0 && day[r] < days as i64 && amt[r] != 0) {
        return Ok(());
    }
    let name = array_name(c, py, a)?;
    let arr = if let Some(ref name) = name {
        writable(c, py, name)?
    } else {
        a.clone()
    };
    let arr = arr.cast::<PyArray2<i64>>()?;
    let mut rw = arr
        .try_readwrite()
        .map_err(|error| PyValueError::new_err(error.to_string()))?;
    let mut ar = rw.as_array_mut();
    if ar.dim() != (n, days) {
        return Err(PyValueError::new_err("Booking array shape mismatch"));
    }
    for r in 0..n {
        if day[r] >= 0 && day[r] < days as i64 && amt[r] != 0 {
            let d = day[r] as usize;
            ar[[r, d]] = ar[[r, d]].wrapping_add(amt[r]);
        }
    }
    drop(rw);
    touch(c, py, &name.into_iter().collect::<Vec<_>>())
}
fn pay(
    c: &NativeChain,
    py: Python<'_>,
    day: &Array1<i64>,
    amt: &Array1<i64>,
    kind: &str,
    inc: Option<Array1<i64>>,
) -> PyResult<()> {
    if !KINDS.contains(&kind) {
        return Err(PyValueError::new_err("Unknown event cash kind"));
    }
    if inc.is_some() && !OBLIGATIONS.contains(&kind) {
        return Err(PyValueError::new_err(
            "Only event obligations have incurred dates",
        ));
    }
    book(c, py, &ev(c, py, "cash")?, day, amt)?;
    book(c, py, &ev(c, py, &format!("k:{kind}"))?, day, amt)?;
    if let Some(inc) = inc {
        let name = format!("i:{kind}");
        let old = ev(c, py, &name)?;
        let read = old.cast::<PyArray1<i64>>()?.readonly();
        let old = read.as_array();
        let mut new = old.to_owned();
        let days = c.days(py)? as i64;
        for r in 0..day.len() {
            if day[r] >= 0 && day[r] < days && amt[r] != 0 {
                new[r] = old[r].min(inc[r]);
            }
        }
        if new.view() != old {
            drop(read);
            set_ev(c, py, &name, &new.into_pyarray(py).into_any())?;
            touch(c, py, &[name])?;
        }
    }
    Ok(())
}
fn has_judgment(c: &NativeChain, py: Python<'_>) -> PyResult<bool> {
    let d = c.get(py, "d")?;
    Ok(!d.is_none()
        && (!d.getattr("judgment_date")?.is_none()
            || (c.flag(py, "pending")? && c.int(py, "entered")? > 0)))
}
enum ReadDraws<'py> {
    Scalar(i64),
    Array(PyReadonlyArray1<'py, i64>),
}
impl<'py> ReadDraws<'py> {
    fn of(c: &NativeChain, py: Python<'py>, value: &Bound<'py, PyAny>) -> PyResult<Self> {
        if let Ok(array) = value.cast::<PyArray1<i64>>() {
            let array = array.try_readonly()?;
            if array.as_array().len() != c.n(py)? {
                return Err(PyValueError::new_err("Financial state draw shape mismatch"));
            }
            return Ok(Self::Array(array));
        }
        Ok(Self::Scalar(value.extract()?))
    }
    fn field(c: &NativeChain, py: Python<'py>, key: &str) -> PyResult<Self> {
        Self::of(c, py, &c.get(py, key)?)
    }
    fn view(&self) -> ReadDrawsView<'_> {
        match self {
            Self::Scalar(value) => ReadDrawsView::Scalar(*value),
            Self::Array(array) => ReadDrawsView::Array(array.as_array()),
        }
    }
}
enum ReadDrawsView<'a> {
    Scalar(i64),
    Array(ArrayView1<'a, i64>),
}
impl ReadDrawsView<'_> {
    fn at(&self, row: usize) -> i64 {
        match self {
            Self::Scalar(value) => *value,
            Self::Array(array) => array[row],
        }
    }
}
/// One read-only query borrows its dated state until the calculation ends.
/// No state vector is copied merely to construct the amount-owed view.
pub(crate) struct Owed<'py> {
    n: usize,
    pending: bool,
    has: bool,
    entered: i64,
    c_entered: f64,
    c_base: f64,
    c_increase: f64,
    cls: Option<i64>,
    fees: i64,
    entry: ReadDraws<'py>,
    f: ReadDraws<'py>,
    fee: ReadDraws<'py>,
    ei: ReadDraws<'py>,
    resolved: ReadDraws<'py>,
    taken: ReadDraws<'py>,
    takes: Vec<(ReadDraws<'py>, ReadDraws<'py>)>,
}
struct OwedView<'a, 'py> {
    owner: &'a Owed<'py>,
    entry: ReadDrawsView<'a>,
    f: ReadDrawsView<'a>,
    fee: ReadDrawsView<'a>,
    ei: ReadDrawsView<'a>,
    resolved: ReadDrawsView<'a>,
    taken: ReadDrawsView<'a>,
    takes: Vec<(ReadDrawsView<'a>, ReadDrawsView<'a>)>,
}
impl<'py> std::ops::Deref for OwedView<'_, 'py> {
    type Target = Owed<'py>;
    fn deref(&self) -> &Self::Target {
        self.owner
    }
}
/// Correctly rounded Python integer true division for the fixed interest-rate
/// denominator. Converting the integer product to f64 before dividing can round
/// twice and change a cent; normalize and round the exact rational once instead.
pub(crate) fn rate_coefficient(principal: i64, bps: i64) -> f64 {
    let signed = principal as i128 * bps as i128;
    let numerator = signed.unsigned_abs();
    if numerator == 0 {
        return 0.0;
    }
    let denominator = 10_000u128;
    let mut exponent =
        (128 - numerator.leading_zeros()) as i32 - (128 - denominator.leading_zeros()) as i32;
    let below = if exponent >= 0 {
        numerator < (denominator << exponent)
    } else {
        (numerator << (-exponent)) < denominator
    };
    if below {
        exponent -= 1;
    }
    let shift = 52 - exponent;
    let (num, den) = if shift >= 0 {
        (numerator << shift, denominator)
    } else {
        (numerator, denominator << (-shift))
    };
    let mut mantissa = num / den;
    let remainder = num % den;
    if remainder * 2 > den || (remainder * 2 == den && mantissa & 1 != 0) {
        mantissa += 1;
    }
    if mantissa == 1u128 << 53 {
        mantissa >>= 1;
        exponent += 1;
    }
    let sign = if signed < 0 { 1u64 << 63 } else { 0 };
    f64::from_bits(
        sign | (((exponent + 1023) as u64) << 52) | (mantissa as u64 & ((1u64 << 52) - 1)),
    )
}

#[cfg(test)]
mod coefficient_tests {
    use super::rate_coefficient;
    #[test]
    fn integer_true_division_rounds_once() {
        // CPython 3.12: (427155806170096947 * 6151) / 10000. A
        // prior f64 cast returns the next representable number instead.
        assert_eq!(
            rate_coefficient(427155806170096947, 6151).to_bits(),
            0x438d2b9ed5856708
        );
        assert_eq!(
            rate_coefficient(-427155806170096947, 6151).to_bits(),
            0xc38d2b9ed5856708
        );
        assert_eq!(rate_coefficient(0, 6151).to_bits(), 0);
        assert_eq!(
            rate_coefficient(1, 1).to_bits(),
            (1.0f64 / 10000.0).to_bits()
        );
    }
}
impl<'py> Owed<'py> {
    pub(crate) fn of(c: &NativeChain, py: Python<'py>) -> PyResult<Self> {
        let n = c.n(py)?;
        let has = has_judgment(c, py)?;
        let pending = c.flag(py, "pending")?;
        let e = if has {
            if pending {
                ReadDraws::field(c, py, "E_ix")?
            } else {
                ReadDraws::Scalar(c.ix(py, &c.get(py, "d")?.getattr("judgment_date")?)?)
            }
        } else {
            ReadDraws::Scalar(0)
        };
        let cls = c.get(py, "cls_amount")?.extract::<Option<i64>>()?;
        let entered = c.int(py, "entered")?;
        let bps = c.int(py, "bps")?;
        let base = cls.map_or(0, |cls| cls.min(entered));
        let mut takes = Vec::new();
        for item in c.get(py, "takes")?.try_iter()? {
            let item = item?;
            takes.push((
                ReadDraws::of(c, py, &item.get_item(0)?)?,
                ReadDraws::of(c, py, &item.get_item(1)?)?,
            ));
        }
        Ok(Self {
            n,
            pending,
            has,
            entered,
            c_entered: rate_coefficient(entered, bps),
            c_base: rate_coefficient(base, bps),
            c_increase: rate_coefficient(cls.map_or(0, |cls| cls - base), bps),
            cls,
            fees: c.int(py, "cls_fees")?,
            entry: e,
            f: ReadDraws::field(c, py, "F")?,
            fee: ReadDraws::field(c, py, "fee_day")?,
            ei: ReadDraws::field(c, py, "EI")?,
            resolved: ReadDraws::field(c, py, "resolved")?,
            taken: ReadDraws::field(c, py, "taken")?,
            takes,
        })
    }
    fn view(&self) -> OwedView<'_, 'py> {
        OwedView {
            owner: self,
            entry: self.entry.view(),
            f: self.f.view(),
            fee: self.fee.view(),
            ei: self.ei.view(),
            resolved: self.resolved.view(),
            taken: self.taken.view(),
            takes: self
                .takes
                .iter()
                .map(|(t, a)| (t.view(), a.view()))
                .collect(),
        }
    }
}
impl OwedView<'_, '_> {
    fn interest(&self, c1: f64, c2: f64, se: i64, sa: i64) -> i64 {
        round_int(c1 * se.max(0) as f64 / 365.0 + c2 * sa.max(0) as f64 / 365.0)
    }
    pub(crate) fn gross(&self, r: usize, d: i64, enforce: bool) -> i64 {
        let se = if self.has { d - self.entry.at(r) } else { 0 };
        let before = self
            .entered
            .wrapping_add(self.interest(self.c_entered, 0.0, se, 0));
        let mut out = before;
        if let Some(cls) = self.cls {
            let base = cls.min(self.entered);
            let mut after =
                cls.wrapping_add(self.interest(self.c_base, self.c_increase, se, d - self.f.at(r)));
            if d < self.fee.at(r) {
                after = after.wrapping_sub(self.fees);
            }
            if enforce && d < self.ei.at(r) {
                after = after.min(base.wrapping_add(self.interest(self.c_base, 0.0, se, 0)));
            }
            if d >= self.f.at(r) {
                out = after;
            }
        }
        if self.pending && d < self.entry.at(r) {
            0
        } else {
            out
        }
    }
    pub(crate) fn owed(&self, r: usize, d: i64, enforce: bool) -> i64 {
        if d >= self.resolved.at(r) {
            return 0;
        }
        let taken = if self.pending {
            self.takes
                .iter()
                .filter(|(t, _)| t.at(r) < d)
                .fold(0i64, |v, (_, a)| v.wrapping_add(a.at(r)))
        } else {
            self.taken.at(r)
        };
        self.gross(r, d, enforce).wrapping_sub(taken).max(0)
    }
}
fn owed(
    c: &NativeChain,
    py: Python<'_>,
    arg: &Bound<'_, PyAny>,
    enforce: bool,
    gross: bool,
) -> ResultObj {
    let n = c.n(py)?;
    let owned;
    let borrowed;
    let day = match arg.cast::<PyArrayDyn<i64>>() {
        Ok(arr) if !arr.shape().is_empty() => {
            checked_owed_shape(arr.shape(), n)?;
            borrowed = arr.try_readonly()?;
            borrowed.as_array()
        }
        _ => {
            owned = c.per_draw(py, arg)?.into_dyn();
            owned.view()
        }
    };
    checked_owed_shape(day.shape(), n)?;
    let mut out = zeros_for_days(day.raw_dim(), day.len())?;
    let owner = Owed::of(c, py)?;
    let state = owner.view();
    let zero = c.get(py, "d")?.is_none();
    for (i, (d, v)) in day.iter().zip(out.iter_mut()).enumerate() {
        *v = if zero {
            0
        } else if gross {
            state.gross(i % n, *d, enforce)
        } else {
            state.owed(i % n, *d, enforce)
        };
    }
    Ok(out.into_pyarray(py).into_any().unbind())
}

fn checked_days_shape(shape: &[usize]) -> PyResult<()> {
    let mut metadata = 1usize;
    for axis in shape {
        metadata = checked_shape::<i64>(metadata, (*axis).max(1))?;
    }
    Ok(())
}

fn checked_owed_shape(shape: &[usize], draws: usize) -> PyResult<()> {
    if shape.is_empty() || shape[shape.len() - 1] != draws {
        return Err(PyValueError::new_err(
            "Owed days must end in draw dimension",
        ));
    }
    checked_days_shape(shape)
}

fn checked_price_shape(shape: &[usize], draws: usize) -> PyResult<()> {
    if shape.len() != 1 && shape.len() != 2 {
        return Err(PyValueError::new_err("Price owed day rank"));
    }
    if shape[0] != draws {
        return Err(PyValueError::new_err("Price owed draw shape"));
    }
    checked_days_shape(shape)
}

fn zeros_for_days(shape: ndarray::IxDyn, len: usize) -> PyResult<ArrayD<i64>> {
    ArrayD::from_shape_vec(shape, filled_vec(len, 0)?)
        .map_err(|error| PyValueError::new_err(format!("Invalid financial day shape: {error}")))
}
fn mark(
    c: &NativeChain,
    py: Python<'_>,
    name: &str,
    day: &Array1<i64>,
    wh: Option<Array1<bool>>,
) -> PyResult<()> {
    let marks = c.get(py, "marks")?.cast_into::<PyDict>()?;
    let old = marks
        .get_item(name)?
        .ok_or_else(|| PyValueError::new_err("Unknown mark"))?;
    let read = old.cast::<PyArray1<i64>>()?.readonly();
    let old = read.as_array();
    let days = c.days(py)? as i64;
    let mut new = old.to_owned();
    let mask = Array1::from_iter(
        (0..day.len()).map(|r| day[r] >= 0 && day[r] < days && wh.as_ref().is_none_or(|a| a[r])),
    );
    if name == "notes_due" {
        let how = c.get(py, "_how")?;
        let arr = c.get(py, "notes_due_how")?;
        let newhow = arr.call_method0("copy")?;
        for r in 0..day.len() {
            if mask[r] && day[r] < old[r] {
                newhow.set_item(r, &how)?;
            }
        }
        c.put(py, "notes_due_how", &newhow)?;
    }
    for r in 0..day.len() {
        if mask[r] {
            new[r] = old[r].min(day[r]);
        }
    }
    drop(read);
    marks.set_item(name, new.into_pyarray(py))?;
    Ok(())
}
fn live(c: &NativeChain, py: Python<'_>, d: &Array1<i64>) -> PyResult<Array1<bool>> {
    let pet = ev(c, py, "petition")?;
    let read = pet.cast::<PyArray1<i64>>()?.readonly();
    let pet = read.as_array();
    let resolved = c.a1(py, "resolved")?;
    Ok(Array1::from_iter((0..d.len()).map(|r| {
        d[r] < if pet[r] < 0 { BIG } else { pet[r] } && d[r] < resolved[r]
    })))
}
fn digest(c: &NativeChain, py: Python<'_>, name: &str, a: &Bound<'_, PyAny>) -> ResultObj {
    let av = c.get(py, "_av")?.cast_into::<PyDict>()?;
    let v = av
        .get_item(name)?
        .map(|x| x.extract::<i64>())
        .transpose()?
        .unwrap_or(0);
    let hd = c.get(py, "_hd")?.cast_into::<PyDict>()?;
    if let Some(hit) = hd.get_item(name)? {
        if hit.get_item(0)?.extract::<i64>()? == v {
            return Ok(hit.get_item(1)?.unbind());
        }
    }
    // xxhash is a C implementation at the hashing boundary; no financial/state
    // reference functions are called here. Array payload is included unchanged.
    let h = py.import("xxhash")?.getattr("xxh3_128")?.call0()?;
    let dtype = a.getattr("dtype")?.getattr("str")?.extract::<String>()?;
    let shape = a.getattr("shape")?.str()?.to_str()?.to_string();
    h.call_method1("update", (format!("{dtype}{shape}").as_bytes(),))?;
    h.call_method1("update", (a.call_method0("tobytes")?,))?;
    let d = h.call_method0("digest")?;
    hd.set_item(name, (v, &d))?;
    Ok(d.unbind())
}
fn run_key(
    c: &NativeChain,
    py: Python<'_>,
    which: &str,
    ns: &Bound<'_, PyAny>,
    head: Option<&Bound<'_, PyAny>>,
) -> ResultObj {
    let keys = c.get(py, "_keys")?.cast_into::<PyDict>()?;
    let v = c.int(py, "_cv")?;
    if let Some(hit) = keys.get_item(which)? {
        if hit.get_item(0)?.extract::<i64>()? == v {
            return Ok(hit.get_item(1)?.unbind());
        }
    }
    let mut prefix = which.as_bytes().to_vec();
    if let Some(head) = head {
        prefix.extend(head.extract::<Vec<u8>>()?);
    }
    let h = py
        .import("xxhash")?
        .getattr("xxh3_128")?
        .call1((PyBytes::new(py, &prefix),))?;
    for name in ns.try_iter()? {
        let name = name?.extract::<String>()?;
        h.call_method1(
            "update",
            (digest(c, py, &name, &ev(c, py, &name)?)?.bind(py),),
        )?;
    }
    let d = h.call_method0("digest")?;
    keys.set_item(which, (v, &d))?;
    Ok(d.unbind())
}
fn timeline_pending(c: &NativeChain, py: Python<'_>) -> PyResult<()> {
    let w = c
        .get(py, "m")?
        .get_item("parameters")?
        .get_item("verdict_window")?;
    let dates = if c.sensitivity(py, "verdict_window")? {
        vec![w.get_item("sensitivity")?.extract::<String>()?]
    } else {
        w.get_item("days")?.extract::<Vec<String>>()?
    };
    let dt = py.import("datetime")?.getattr("date")?;
    let idx = dates
        .iter()
        .map(|s| c.ix(py, &dt.call_method1("fromisoformat", (s,))?))
        .collect::<PyResult<Vec<_>>>()?;
    if idx.is_empty() {
        return Err(PyValueError::new_err("Empty verdict window"));
    }
    let kw = PyDict::new(py);
    kw.set_item("adverse_high", false)?;
    let u = c
        .get(py, "dr")?
        .call_method("u", (c.get(py, "iid")?, "verdict", "date"), Some(&kw))?;
    let u = u.cast::<PyArray1<f64>>()?.readonly();
    let n = c.n(py)?;
    let v = Array1::from_iter(
        u.as_array()
            .iter()
            .map(|u| idx[((*u * idx.len() as f64) as usize).min(idx.len() - 1)]),
    );
    c.put1(py, "V", v)?;
    c.put(py, "ruling", &PyDict::new(py).into_any())?;
    for key in ["F", "A", "AD", "fee_day", "EF", "EI", "E_ix", "E0", "e_ix"] {
        c.put1(py, key, Array1::from_elem(n, BIG))?;
    }
    Ok(())
}
fn timeline(c: &NativeChain, py: Python<'_>) -> PyResult<()> {
    let d = c.get(py, "d")?;
    let model = c.get(py, "m")?;
    let lagp = model
        .get_item("parameters")?
        .get_item("ruling_lag_days")?
        .cast_into::<PyDict>()?;
    let common = lagp
        .get_item("mode")?
        .is_some_and(|v| v.extract::<String>().is_ok_and(|v| v == "common"));
    let motions = d.getattr("motions")?;
    let mut close = None;
    for motion in motions.try_iter()? {
        let dt = motion?.getattr("briefing_close")?;
        if !dt.is_none() {
            let v = dt.call_method0("toordinal")?.extract::<i64>()?;
            if close.as_ref().is_none_or(|(x, _)| v > *x) {
                close = Some((v, dt.unbind()));
            }
        }
    }
    let n = c.n(py)?;
    let ruling = PyDict::new(py);
    let mut money = Vec::new();
    let mut tolling = Vec::new();
    let mut fees = Vec::new();
    for motion in motions.try_iter()? {
        let motion = motion?;
        let id = motion.getattr("motion_id")?.extract::<String>()?;
        let kind = motion.getattr("kind")?.extract::<String>()?;
        let dt = motion.getattr("briefing_close")?;
        let day = c.ix(
            py,
            if dt.is_none() {
                close
                    .as_ref()
                    .ok_or_else(|| PyValueError::new_err("Motion briefing dates missing"))?
                    .1
                    .bind(py)
            } else {
                &dt
            },
        )?;
        let a = c
            .lag(py, if common { "common" } else { &id })?
            .mapv(|v| day + v);
        ruling.set_item(&id, a.clone().into_pyarray(py))?;
        if ["rule_50b", "rule_52b", "rule_59a", "rule_59e"].contains(&kind.as_str()) {
            money.push(a.clone());
        }
        if ["rule_50b", "rule_52b", "rule_59a", "rule_59e", "injunction"].contains(&kind.as_str()) {
            tolling.push(a.clone());
        }
        if kind == "rule_54_fees" {
            fees.push(a);
        }
    }
    c.put(py, "ruling", &ruling.into_any())?;
    let post = d.getattr("stage")?.extract::<String>()? == "post_trial" && !money.is_empty();
    let jd = d.getattr("judgment_date")?;
    let finalday = if jd.is_none() { -1 } else { c.ix(py, &jd)? };
    let maxof = |arrs: &[Array1<i64>], fallback: i64| {
        Array1::from_iter((0..n).map(|r| arrs.iter().map(|a| a[r]).max().unwrap_or(fallback)))
    };
    let f = if post {
        maxof(&money, finalday)
    } else {
        Array1::from_elem(n, finalday)
    };
    let a = if post && !tolling.is_empty() {
        maxof(&tolling, finalday)
    } else {
        f.clone()
    };
    let notice = c.rule(py, "frap_4a1a")?;
    c.put1(py, "AD", a.mapv(|v| v + notice))?;
    c.put1(py, "A", a)?;
    c.put1(py, "fee_day", maxof(&fees, BIG))?;
    c.put1(py, "F", f.clone())?;
    c.put1(py, "EF", f.clone())?;
    c.put1(py, "EI", f)?;
    let e = if jd.is_none() {
        -1
    } else {
        (c.ix(py, &jd)? + c.rule(py, "frcp_62a")? + 1).max(-1)
    };
    c.put_i64(py, "e_ix", e)?;
    c.put_i64(py, "E0", e.max(0))?;
    Ok(())
}

pub(crate) fn dispatch(
    c: &NativeChain,
    py: Python<'_>,
    name: &str,
    args: &Bound<'_, PyTuple>,
) -> Option<ResultObj> {
    Some((|| match name {
        "has_judgment" => Ok(has_judgment(c, py)?
            .into_pyobject(py)?
            .to_owned()
            .into_any()
            .unbind()),
        "entry_ix" => {
            if c.flag(py, "pending")? {
                Ok(c.get(py, "E_ix")?.unbind())
            } else {
                Ok(c.ix(py, &c.get(py, "d")?.getattr("judgment_date")?)?
                    .into_pyobject(py)?
                    .into_any()
                    .unbind())
            }
        }
        "_timeline_pending" => {
            timeline_pending(c, py)?;
            Ok(py.None())
        }
        "_timeline" => {
            timeline(c, py)?;
            Ok(py.None())
        }
        "_touch" => {
            touch(c, py, &args.extract::<Vec<String>>()?)?;
            Ok(py.None())
        }
        "_evw" => Ok(writable(c, py, &args.get_item(0)?.extract::<String>()?)?.unbind()),
        "_arrays" => {
            let out = PyList::empty(py);
            for name in names() {
                out.append((&name, ev(c, py, &name)?))?;
            }
            Ok(out.into_any().unbind())
        }
        "_name" => {
            let ns = array_name(c, py, &args.get_item(0)?)?
                .into_iter()
                .collect::<Vec<_>>();
            Ok(PyTuple::new(py, ns)?.into_any().unbind())
        }
        "_digest" => digest(
            c,
            py,
            &args.get_item(0)?.extract::<String>()?,
            &args.get_item(1)?,
        ),
        "_run_key" => run_key(
            c,
            py,
            &args.get_item(0)?.extract::<String>()?,
            &args.get_item(1)?,
            args.get_item(2).ok().as_ref(),
        ),
        "mark" => {
            let n = args.get_item(0)?.extract::<String>()?;
            let d = c.per_draw(py, &args.get_item(1)?)?;
            let wh = args
                .get_item(2)
                .ok()
                .filter(|v| !v.is_none())
                .map(|v| asbool(&v))
                .transpose()?;
            mark(c, py, &n, &d, wh)?;
            Ok(py.None())
        }
        "book" => {
            book(
                c,
                py,
                &args.get_item(0)?,
                &c.per_draw(py, &args.get_item(1)?)?,
                &c.per_draw(py, &args.get_item(2)?)?,
            )?;
            Ok(py.None())
        }
        "pay" => {
            let day = c.per_draw(py, &args.get_item(0)?)?;
            let amt = c.per_draw(py, &args.get_item(1)?)?;
            let kind = args.get_item(2)?.extract::<String>()?;
            let inc = args
                .get_item(3)
                .ok()
                .filter(|v| !v.is_none())
                .map(|v| c.per_draw(py, &v))
                .transpose()?;
            pay(c, py, &day, &amt, &kind, inc)?;
            Ok(py.None())
        }
        "live" => Ok(bool1(
            py,
            live(c, py, &c.per_draw(py, &args.get_item(0)?)?)?,
        )),
        "cash_at" => {
            let d = c.per_draw(py, &args.get_item(0)?)?;
            let cum = c.call0(py, "cum")?;
            let read = cum.bind(py).cast::<PyArray2<i64>>()?.readonly();
            let a = read.as_array();
            let days = c.days(py)? as i64;
            Ok(out1(
                py,
                Array1::from_iter((0..d.len()).map(|r| a[[r, d[r].clamp(0, days - 1) as usize]])),
            ))
        }
        "owed_at" | "owed_at_py" => owed(
            c,
            py,
            &args.get_item(0)?,
            args.get_item(1)
                .ok()
                .is_some_and(|v| v.is_truthy().unwrap_or(false)),
            false,
        ),
        "_owed_gross" => owed(
            c,
            py,
            &args.get_item(0)?,
            args.get_item(1)
                .ok()
                .is_some_and(|v| v.is_truthy().unwrap_or(false)),
            true,
        ),
        "taken_before" => {
            if !c.flag(py, "pending")? {
                return Ok(c.get(py, "taken")?.unbind());
            }
            let n = c.n(py)?;
            let argument = args.get_item(0)?;
            let owned;
            let borrowed;
            let day = match argument.cast::<PyArrayDyn<i64>>() {
                Ok(array) if !array.shape().is_empty() => {
                    checked_owed_shape(array.shape(), n)?;
                    borrowed = array.try_readonly()?;
                    borrowed.as_array()
                }
                _ => {
                    owned = c.per_draw(py, &argument)?.into_dyn();
                    owned.view()
                }
            };
            checked_owed_shape(day.shape(), n)?;
            let mut out = zeros_for_days(day.raw_dim(), day.len())?;
            let owner = Owed::of(c, py)?;
            let state = owner.view();
            for (i, (d, v)) in day.iter().zip(out.iter_mut()).enumerate() {
                let r = i % state.n;
                *v = state
                    .takes
                    .iter()
                    .filter(|(t, _)| t.at(r) < *d)
                    .fold(0i64, |v, (_, a)| v.wrapping_add(a.at(r)));
            }
            Ok(out.into_pyarray(py).into_any().unbind())
        }
        "petition" => {
            let lag = c.p_i64(py, "petition_lag_days")?;
            let day = c.per_draw(py, &args.get_item(0)?)?.mapv(|v| v + lag);
            let wh = args
                .get_item(1)
                .ok()
                .filter(|v| !v.is_none())
                .map(|v| asbool(&v))
                .transpose()?;
            let cause = args
                .get_item(2)
                .ok()
                .map(|v| v.extract::<String>())
                .transpose()?
                .unwrap_or_else(|| "enforcement".into());
            let pet = ev(c, py, "petition")?;
            let read = pet.cast::<PyArray1<i64>>()?.readonly();
            let old = read.as_array();
            let days = c.days(py)? as i64;
            let win = Array1::from_iter((0..day.len()).map(|r| {
                day[r] >= 0
                    && day[r] < days
                    && wh.as_ref().is_none_or(|w| w[r])
                    && (old[r] < 0 || day[r] < old[r])
            }));
            let any = win.iter().any(|v| *v);
            let new =
                Array1::from_iter((0..day.len()).map(|r| if win[r] { day[r] } else { old[r] }));
            drop(read);
            if any {
                set_ev(c, py, "petition", &new.into_pyarray(py).into_any())?;
                touch(c, py, &["petition".into()])?;
                c.put_i64(py, "_eq_v", c.int(py, "_eq_v")? + 1)?;
            }
            let oldcause = c.get(py, "pet_cause")?;
            let a = oldcause.cast::<PyArray1<i8>>()?.readonly();
            let code = ["none", "enforcement", "notes", "cash_floor"]
                .iter()
                .position(|s| *s == cause)
                .ok_or_else(|| PyValueError::new_err("Unknown petition cause"))?
                as i8;
            let out =
                Array1::from_iter(
                    (0..day.len()).map(|r| if win[r] { code } else { a.as_array()[r] }),
                );
            c.put(py, "pet_cause", &out.into_pyarray(py).into_any())?;
            if any {
                c.call0(py, "_atm_rebook")?;
            }
            Ok(py.None())
        }
        "resolve" => {
            let day = c.per_draw(py, &args.get_item(0)?)?;
            let wh = asbool(&args.get_item(1)?)?;
            call(
                c,
                py,
                "release_lock",
                vec![out1(py, day.clone()), bool1(py, wh.clone())],
            )?;
            let old = c.a1(py, "resolved")?;
            let days = c.days(py)?;
            let new = Array1::from_iter((0..day.len()).map(|r| {
                old[r].min(if wh[r] && day[r] < days as i64 {
                    day[r]
                } else {
                    BIG
                })
            }));
            c.put1(py, "resolved", new.clone())?;
            let legal = c.get(py, "basis")?.getattr("legal")?;
            let legal = legal.cast::<PyArray2<i64>>()?.readonly();
            let legal = legal.as_array();
            let mut delta = Array2::zeros((day.len(), days));
            for r in 0..day.len() {
                for d in 0..days {
                    if d as i64 >= new[r] && (d as i64) < old[r] {
                        delta[[r, d]] = legal[[r, d]].wrapping_neg();
                    }
                }
            }
            if delta.iter().any(|v| *v != 0) {
                for name in ["cash", "k:reduction"] {
                    let arr = writable(c, py, name)?;
                    let mut rw = arr.cast::<PyArray2<i64>>()?.try_readwrite()?;
                    let mut a = rw.as_array_mut();
                    for ((r, d), v) in delta.indexed_iter() {
                        a[[r, d]] = a[[r, d]].wrapping_add(*v);
                    }
                }
                touch(c, py, &["cash".into(), "k:reduction".into()])?;
            }
            Ok(py.None())
        }
        "adverse_standing" => {
            let day = c.per_draw(py, &args.get_item(0)?)?;
            let f = c.a1(py, "adverse_from")?;
            let u = c.a1(py, "adverse_until")?;
            let end = c.a1(py, "resolved")?;
            Ok(bool1(
                py,
                Array1::from_iter(
                    (0..day.len()).map(|r| day[r] >= f[r] && day[r] < u[r] && day[r] < end[r]),
                ),
            ))
        }
        "booking" => {
            let node = args.get_item(0)?.extract::<String>()?;
            let branch = args.get_item(1)?.extract::<String>()?;
            let bookings = c.get(py, "bookings")?.cast_into::<PyDict>()?;
            let own = bookings
                .get_item(&node)?
                .map(|v| v.cast_into::<PyDict>())
                .transpose()?
                .unwrap_or_else(|| PyDict::new(py));
            if let Some(v) = own.get_item(&branch)? {
                Ok(v.unbind())
            } else if c.flag(py, "equity")? {
                let s = match branch.as_str() {
                    "initiate_offering" => "offer",
                    "file" => "petition",
                    "neither" | "none" => "none",
                    _ => return Err(PyValueError::new_err("Unknown branch booking")),
                };
                Ok(text_obj(py, s))
            } else {
                Err(PyValueError::new_err("Unknown branch booking"))
            }
        }
        "_owed_key" => {
            let fields = PyList::empty(py);
            for key in ["entered", "cls_amount", "bps"] {
                fields.append(c.get(py, key)?)?;
            }
            let np = py.import("numpy")?;
            let bytes = |v: Bound<'_, PyAny>| -> ResultObj {
                if v.is_none() {
                    Ok(py.None())
                } else {
                    Ok(np
                        .call_method1("asarray", (v,))?
                        .call_method0("tobytes")?
                        .unbind())
                }
            };
            fields.append(bytes(c.get(py, "cls_fees")?)?)?;
            for key in ["F", "resolved", "V", "E_ix", "fee_day", "EI"] {
                fields.append(bytes(
                    c.opt(py, key)?.unwrap_or_else(|| py.None().into_bound(py)),
                )?)?;
            }
            for key in ["takes", "settlement_parts"] {
                let parts = PyList::empty(py);
                for p in c.get(py, key)?.try_iter()? {
                    let p = p?;
                    parts.append((bytes(p.get_item(0)?)?, bytes(p.get_item(1)?)?))?;
                }
                fields.append(PyTuple::new(py, parts.iter())?)?;
            }
            fields.append(
                c.get(py, "marks")?
                    .get_item("settled")?
                    .call_method0("tobytes")?,
            )?;
            Ok(PyTuple::new(py, fields.iter())?.into_any().unbind())
        }
        "_through" => {
            let n = c.n(py)?;
            let days = c.days(py)?;
            let mut out = Array2::<i64>::zeros((n, days + 1));
            for item in args.get_item(0)?.try_iter()? {
                let item = item?;
                let t = c.per_draw(py, &item.get_item(0)?)?;
                let a = c.per_draw(py, &item.get_item(1)?)?;
                for r in 0..n {
                    let d = t[r].clamp(0, days as i64) as usize;
                    out[[r, d]] = out[[r, d]].wrapping_add(a[r]);
                }
            }
            let mut result = Array2::zeros((n, days));
            for r in 0..n {
                let mut total = 0i64;
                for d in 0..days {
                    total = total.wrapping_add(out[[r, d]]);
                    result[[r, d]] = total;
                }
            }
            Ok(result.into_pyarray(py).into_any().unbind())
        }
        "_price_owed_grid" | "_price_owed_grid_py" => {
            let n = c.n(py)?;
            let days = c.days(py)?;
            let mut out = Array2::zeros((n, days));
            if !c.flag(py, "pending")? || c.int(py, "entered")? == 0 {
                return Ok(out.into_pyarray(py).into_any().unbind());
            }
            let owner = Owed::of(c, py)?;
            let state = owner.view();
            let v = c.a1(py, "V")?;
            let settled = c.get(py, "marks")?.get_item("settled")?;
            let settled = c.per_draw(py, &settled)?;
            let mut parts = Vec::new();
            for p in c.get(py, "settlement_parts")?.try_iter()? {
                let p = p?;
                parts.push((
                    c.per_draw(py, &p.get_item(0)?)?,
                    c.per_draw(py, &p.get_item(1)?)?,
                ));
            }
            for r in 0..n {
                for d in 0..days {
                    let day = d as i64;
                    let taken = state
                        .takes
                        .iter()
                        .filter(|(t, _)| t.at(r) <= day)
                        .fold(0i64, |a, (_, v)| a.wrapping_add(v.at(r)));
                    let mut amt = match state.cls {
                        Some(cls) if day >= state.f.at(r) => cls,
                        _ => state.entered,
                    };
                    amt = if day >= state.resolved.at(r) {
                        0
                    } else {
                        amt.wrapping_sub(taken).max(0)
                    };
                    if day >= state.entry.at(r) {
                        amt = if day >= state.resolved.at(r) {
                            0
                        } else {
                            state.gross(r, day, false).wrapping_sub(taken).max(0)
                        };
                    }
                    if !parts.is_empty() && day >= settled[r] {
                        amt = parts
                            .iter()
                            .filter(|(t, _)| t[r] > day)
                            .fold(0i64, |a, (_, v)| a.wrapping_add(v[r]));
                    }
                    out[[r, d]] = if day >= v[r] { amt } else { 0 };
                }
            }
            Ok(out.into_pyarray(py).into_any().unbind())
        }
        "price_owed" => {
            let n = c.n(py)?;
            let argument = args.get_item(0)?;
            let owned;
            let borrowed;
            let day = match argument.cast::<PyArrayDyn<i64>>() {
                Ok(a) if !a.shape().is_empty() => {
                    checked_price_shape(a.shape(), n)?;
                    borrowed = a.try_readonly()?;
                    borrowed.as_array()
                }
                _ => {
                    owned = c.per_draw(py, &argument)?.into_dyn();
                    owned.view()
                }
            };
            checked_price_shape(day.shape(), n)?;
            let mut out = zeros_for_days(day.raw_dim(), day.len())?;
            if !c.flag(py, "pending")? || c.int(py, "entered")? == 0 {
                return Ok(out.into_pyarray(py).into_any().unbind());
            }
            let k = if day.ndim() == 2 { day.shape()[1] } else { 1 };
            let owner = Owed::of(c, py)?;
            let state = owner.view();
            let v = c.a1(py, "V")?;
            let settled = c.per_draw(py, &c.get(py, "marks")?.get_item("settled")?)?;
            let mut parts = Vec::new();
            for p in c.get(py, "settlement_parts")?.try_iter()? {
                let p = p?;
                parts.push((
                    c.per_draw(py, &p.get_item(0)?)?,
                    c.per_draw(py, &p.get_item(1)?)?,
                ));
            }
            for (i, (d, out)) in day.iter().zip(out.iter_mut()).enumerate() {
                let r = i / k;
                let tk = state
                    .takes
                    .iter()
                    .filter(|(t, _)| t.at(r) <= *d)
                    .fold(0i64, |a, (_, v)| a.wrapping_add(v.at(r)));
                let mut amt = match state.cls {
                    Some(cls) if *d >= state.f.at(r) => cls,
                    _ => state.entered,
                };
                amt = if *d >= state.resolved.at(r) {
                    0
                } else {
                    amt.wrapping_sub(tk).max(0)
                };
                if *d >= state.entry.at(r) {
                    amt = if *d >= state.resolved.at(r) {
                        0
                    } else {
                        state.gross(r, *d, false).wrapping_sub(tk).max(0)
                    };
                }
                if !parts.is_empty() && *d >= settled[r] {
                    amt = parts
                        .iter()
                        .filter(|(t, _)| t[r] > *d)
                        .fold(0i64, |a, (_, v)| a.wrapping_add(v[r]));
                }
                *out = if *d >= v[r] { amt } else { 0 };
            }
            Ok(out.into_pyarray(py).into_any().unbind())
        }
        _ => Err(PyValueError::new_err(format!("__not_core__{name}"))),
    })())
    .filter(|r| !matches!(r,Err(e)if e.to_string().contains("__not_core__")))
}

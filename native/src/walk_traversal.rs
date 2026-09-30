//! Native depth-first control flow with typed path state. The boundary façade
//! supplies trace queries, question construction and ordered fact records.
//! It is deliberately separate from the retained Python traversal oracle.
use super::walk::{encode, Conjunctions, Edge, Step};
use numpy::PyReadonlyArray1;
use pyo3::exceptions::{PyNotImplementedError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyModule, PyTuple};
use pyo3::IntoPyObjectExt;
use pyo3::{PyTraverseError, PyVisit};
use std::sync::{Arc, OnceLock};

/// Action branch shares its immutable prefix. Adding one step allocates one node;
/// taking another action at the same state only copies the Arc handle.
#[derive(Clone)]
struct History<T>(Option<Arc<Link<T>>>);
struct Link<T> {
    value: T,
    parent: History<T>,
    len: usize,
    // The Python boundary reuses prefix tuples and their string/edge objects,
    // just as the reference walk reuses the items in its immutable tuples.
    python: OnceLock<Py<PyAny>>,
}
impl<T> Default for History<T> {
    fn default() -> Self {
        Self(None)
    }
}
impl<T: Clone> History<T> {
    fn len(&self) -> usize {
        self.0.as_ref().map_or(0, |n| n.len)
    }
    fn push(&mut self, value: T) {
        let len = self.len() + 1;
        let parent = self.clone();
        self.0 = Some(Arc::new(Link {
            value,
            parent,
            len,
            python: OnceLock::new(),
        }));
    }
    fn extend_from_slice(&mut self, values: &[T]) {
        for value in values {
            self.push(value.clone());
        }
    }
    fn extend<I: IntoIterator<Item = T>>(&mut self, values: I) {
        for value in values {
            self.push(value);
        }
    }
    fn iter(&self) -> std::vec::IntoIter<&T> {
        let mut out = Vec::with_capacity(self.len());
        let mut node = self.0.as_ref();
        while let Some(n) = node {
            out.push(&n.value);
            node = n.parent.0.as_ref();
        }
        out.reverse();
        out.into_iter()
    }
}
impl<'a, T: Clone> IntoIterator for &'a History<T> {
    type Item = &'a T;
    type IntoIter = std::vec::IntoIter<&'a T>;
    fn into_iter(self) -> Self::IntoIter {
        self.iter()
    }
}

#[derive(Clone)]
struct State {
    steps: History<Step>,
    edges: History<Edge>,
    cls: String,
    stayed: bool,
    appealed: bool,
    early: bool,
    a4: String,
    notes_due: bool,
    floor: String,
    late: History<(String, usize)>,
    k: usize,
    out: String,
    np: String,
    failed: bool,
    resp: String,
}
impl Default for State {
    fn default() -> Self {
        Self {
            steps: History::default(),
            edges: History::default(),
            cls: "entered".into(),
            stayed: false,
            appealed: false,
            early: false,
            a4: "open".into(),
            notes_due: false,
            floor: "open".into(),
            late: History::default(),
            k: 1,
            out: "open".into(),
            np: "open".into(),
            failed: false,
            resp: "none".into(),
        }
    }
}
impl State {
    fn add(&self, step: Step, edge: Option<Edge>) -> Self {
        let mut s = self.clone();
        s.steps.push(step);
        if let Some(e) = edge {
            s.edges.push(e);
        }
        s
    }
    fn py(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let m = py.import("app.disputes.forecast")?;
        let kw = PyDict::new(py);
        kw.set_item("steps", steps_py(py, &self.steps)?)?;
        kw.set_item("edges", tuple_py(py, &self.edges)?)?;
        kw.set_item("cls", &self.cls)?;
        kw.set_item("stayed", self.stayed)?;
        kw.set_item("appealed", self.appealed)?;
        kw.set_item("early", self.early)?;
        kw.set_item("a4", &self.a4)?;
        kw.set_item("notes_due", self.notes_due)?;
        kw.set_item("floor", &self.floor)?;
        kw.set_item("late", tuple_py(py, &self.late)?)?;
        kw.set_item("k", self.k)?;
        kw.set_item("out", &self.out)?;
        kw.set_item("np", &self.np)?;
        kw.set_item("failed", self.failed)?;
        kw.set_item("resp", &self.resp)?;
        Ok(m.getattr("_S")?.call((), Some(&kw))?.unbind())
    }
}
trait PySequence {
    type Item;
    fn python_tuple(&self, py: Python<'_>) -> PyResult<Py<PyAny>>;
}
impl<T> PySequence for History<T>
where
    T: Clone,
    for<'a> T: IntoPyObject<'a>,
{
    type Item = T;
    fn python_tuple(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let mut missing = Vec::new();
        let mut cursor = self.0.as_ref();
        let mut prefix = PyTuple::empty(py).into_any().unbind();
        while let Some(node) = cursor {
            if let Some(cached) = node.python.get() {
                prefix = cached.clone_ref(py);
                break;
            }
            missing.push(node);
            cursor = node.parent.0.as_ref();
        }
        for node in missing.into_iter().rev() {
            let parent = prefix.bind(py).cast::<PyTuple>()?;
            let mut items: Vec<_> = parent.iter().map(Bound::unbind).collect();
            items.push(node.value.clone().into_py_any(py)?);
            prefix = PyTuple::new(py, items)?.into_any().unbind();
            // OnceLock also permits independent readers of a shared prefix.
            let _ = node.python.set(prefix.clone_ref(py));
            if let Some(cached) = node.python.get() {
                prefix = cached.clone_ref(py);
            }
        }
        Ok(prefix)
    }
}
impl<T> PySequence for [T]
where
    T: Clone,
    for<'a> T: IntoPyObject<'a>,
{
    type Item = T;
    fn python_tuple(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        Ok(PyTuple::new(
            py,
            self.iter()
                .map(|x| x.clone().into_py_any(py))
                .collect::<PyResult<Vec<_>>>()?,
        )?
        .into_any()
        .unbind())
    }
}
impl<T> PySequence for Vec<T>
where
    T: Clone,
    for<'a> T: IntoPyObject<'a>,
{
    type Item = T;
    fn python_tuple(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        self.as_slice().python_tuple(py)
    }
}
impl<T, const N: usize> PySequence for [T; N]
where
    T: Clone,
    for<'a> T: IntoPyObject<'a>,
{
    type Item = T;
    fn python_tuple(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        self.as_slice().python_tuple(py)
    }
}
fn tuple_py<T: PySequence + ?Sized>(py: Python<'_>, value: &T) -> PyResult<Py<PyAny>> {
    value.python_tuple(py)
}
fn steps_py<T: PySequence<Item = Step> + ?Sized>(py: Python<'_>, value: &T) -> PyResult<Py<PyAny>> {
    value.python_tuple(py)
}
fn st(n: &str, c: &str, b: &str) -> Step {
    (n.into(), c.into(), b.into())
}
fn ed(k: &str, b: &str) -> Option<Edge> {
    Some((k.into(), b.into()))
}
fn comp(c: Conjunctions) -> Option<Edge> {
    Some((encode(&c), "yes".into()))
}
fn yes(k: &str) -> Edge {
    (k.into(), "yes".into())
}
fn no(k: &str) -> Edge {
    (k.into(), "no".into())
}
fn label(c: &str) -> String {
    let p: Vec<_> = c.split(':').collect();
    format!(
        "{}{}{}",
        p[0],
        if c.starts_with("amt:") {
            p.get(1).copied().unwrap_or("")
        } else {
            ""
        },
        if c.ends_with(":retrial") {
            "_retrial"
        } else {
            ""
        }
    )
}

#[derive(Clone)]
enum Action {
    Settle(String, Box<Action>),
    Verdict,
    Entry,
    Motions,
    Q1,
    StayI1,
    J9Stayed,
    J9I1,
    A4I1,
    Response(String, Box<Action>, Box<Action>, bool),
    Offer(String, Box<Action>),
    Notes(String, Box<Action>),
    RipeI1,
    RipeI1Filed(Box<Action>),
    Ruling,
    RulingPending,
    Post,
    Appeal,
    StayPost,
    StayedTail,
    I3,
    A4Post(Box<Action>, bool),
    Enforce(Box<Action>, bool, bool),
    RipePost,
    Tail(String),
    Kept(Vec<(String, i64)>, String),
    DelistingNotes(String, i64, String),
    Listing(String),
    Delisting(String, i64, String),
    Distress(String, Option<Box<Action>>),
    DistressNext(String, Option<Box<Action>>),
    AskDistress(Step, String, Option<Box<Action>>),
    Floor(String, Option<Box<Action>>),
    CashOut(String, Option<Box<Action>>),
    End(String, Option<Box<Action>>),
    Emit(String),
}
impl Action {
    fn name(&self) -> &'static str {
        match self {
            Action::Settle(..) => "settle",
            Action::Verdict => "verdict",
            Action::Entry => "entry",
            Action::Motions => "motions",
            Action::Q1 => "q1",
            Action::StayI1 => "stay_i1",
            Action::J9Stayed => "j9_stayed",
            Action::J9I1 => "j9_i1",
            Action::A4I1 => "a4_i1",
            Action::Response(..) => "a4",
            Action::Offer(..) => "offer",
            Action::Notes(..) => "notes_petition",
            Action::RipeI1 => "ripe_i1",
            Action::Ruling => "ruling",
            Action::RulingPending => "ruling_pending",
            Action::Post => "post",
            Action::Appeal => "appeal",
            Action::StayPost => "stay_post",
            Action::StayedTail => "stayed_tail",
            Action::I3 => "i3",
            Action::A4Post(..) => "a4_post",
            Action::Enforce(..) => "enforce",
            Action::RipePost => "ripe_post",
            Action::Tail(..) => "tail",
            Action::Kept(..) => "kept",
            Action::DelistingNotes(..) => "delisting_notes",
            Action::Listing(..) => "listing",
            Action::Delisting(..) => "delisting",
            Action::Distress(..) => "distress",
            Action::AskDistress(..) => "ask_distress",
            Action::Floor(..) => "floor",
            Action::CashOut(..) => "cash_out",
            Action::End(..) => "_end",
            Action::Emit(..) => "emit",
            Action::DistressNext(..) | Action::RipeI1Filed(..) => "",
        }
    }
}

#[pyclass(module = "app._native")]
pub struct NativeWalk {
    helper: Py<PyAny>,
    fc: Py<PyAny>,
    d: Py<PyAny>,
    pend: bool,
    ordinary: bool,
    n: i64,
    resp: String,
    quiet: String,
    seek: String,
    again: Vec<String>,
    equity: bool,
    raising: bool,
    group_fork: bool,
}
#[pymethods]
impl NativeWalk {
    fn __traverse__(&self, visit: PyVisit<'_>) -> Result<(), PyTraverseError> {
        visit.call(&self.helper)?;
        visit.call(&self.fc)?;
        visit.call(&self.d)?;
        Ok(())
    }
    fn __clear__(&mut self, py: Python<'_>) {
        self.helper = py.None();
        self.fc = py.None();
        self.d = py.None();
    }
    #[new]
    #[pyo3(signature=(fc,d,ordinary=false))]
    fn new(py: Python<'_>, fc: Py<PyAny>, d: Py<PyAny>, ordinary: bool) -> PyResult<Self> {
        let m = py.import("app.disputes.forecast")?;
        let helper = if ordinary {
            m.getattr("_OrdinaryWalk")?.call1((fc.bind(py),))?
        } else {
            m.getattr("_Walk")?.call1((fc.bind(py), d.bind(py)))?
        };
        let pend = helper.getattr("pend")?.extract()?;
        let n = helper.getattr("N")?.extract()?;
        Ok(Self {
            helper: helper.clone().unbind(),
            n,
            pend,
            ordinary,
            resp: if ordinary {
                String::new()
            } else {
                helper.getattr("resp")?.extract()?
            },
            quiet: if ordinary {
                "neither".into()
            } else {
                helper.getattr("quiet")?.extract()?
            },
            seek: if ordinary {
                "none".into()
            } else {
                helper.getattr("seek")?.extract()?
            },
            again: if ordinary {
                vec![]
            } else {
                helper.getattr("again")?.extract()?
            },
            equity: fc.bind(py).getattr("equity")?.extract()?,
            raising: fc.bind(py).getattr("raising")?.extract()?,
            group_fork: helper.getattr("GROUP_FORK")?.extract()?,
            fc,
            d,
        })
    }
    #[getter]
    fn pend(&self) -> bool {
        self.pend
    }
    #[getter]
    fn out(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        Ok(self.helper.bind(py).getattr("out")?.unbind())
    }
    #[getter]
    fn keys(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        Ok(self.helper.bind(py).getattr("keys")?.unbind())
    }
    #[getter]
    fn reference(&self, py: Python<'_>) -> Py<PyAny> {
        self.helper.clone_ref(py)
    }
    fn run(&mut self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        if self.ordinary {
            let mut s = State::default();
            s.cls.clear();
            self.go(py, Action::Listing("operating".into()), s)?;
        } else if self.pend {
            let s = State {
                cls: "claimed".into(),
                ..State::default()
            };
            self.go(
                py,
                Action::Settle("I0".into(), Box::new(Action::Verdict)),
                s,
            )?;
        } else {
            let stage: String = self.d.bind(py).getattr("stage")?.extract()?;
            let motions = self.d.bind(py).getattr("motions")?;
            let mut post_trial = false;
            for mo in motions.try_iter()? {
                let kind: String = mo?.getattr("kind")?.extract()?;
                if ["rule_50b", "rule_52b", "rule_59a", "rule_59e"].contains(&kind.as_str()) {
                    post_trial = true;
                }
            }
            if stage == "post_trial" && post_trial {
                self.go(
                    py,
                    Action::Settle("I1".into(), Box::new(Action::Q1)),
                    State::default(),
                )?;
            } else {
                let mut s = State::default();
                let c: String = self
                    .helper
                    .bind(py)
                    .call_method0("_entered_class")?
                    .extract()?;
                s.cls = label(&c);
                s.stayed = stage == "appeal_pending";
                s.appealed = ["appeal_filed", "appeal_pending"].contains(&stage.as_str());
                self.go(py, Action::Post, s)?;
            }
        }
        Ok(self.helper.bind(py).getattr("out")?.unbind())
    }
}
impl NativeWalk {
    fn hp<'py>(&self, py: Python<'py>) -> Bound<'py, PyAny> {
        self.helper.bind(py).clone()
    }
    fn trace(&self, py: Python<'_>, s: &State, extra: &[Step], full: bool) -> PyResult<Py<PyAny>> {
        let mut steps = s.steps.clone();
        steps.extend_from_slice(extra);
        Ok(self
            .hp(py)
            .call_method1("_trace", (steps_py(py, &steps)?, full))?
            .unbind())
    }
    fn ints<'py>(
        &self,
        py: Python<'py>,
        tr: &Py<PyAny>,
        name: &str,
    ) -> PyResult<PyReadonlyArray1<'py, i64>> {
        let a = tr.bind(py).getattr(name)?;
        let a = if name == "day" { a.get_item(-1)? } else { a };
        Ok(a.extract()?)
    }
    fn inside(&self, py: Python<'_>, s: &State, step: &Step) -> PyResult<bool> {
        let mut v = s.steps.clone();
        v.push(step.clone());
        self.hp(py)
            .call_method1("inside", (steps_py(py, &v)?,))?
            .extract()
    }
    fn arises(&self, py: Python<'_>, s: &State, step: &Step) -> PyResult<bool> {
        self.hp(py)
            .call_method1("arises", (s.py(py)?, step))?
            .extract()
    }
    fn pred(&self, py: Python<'_>, name: &str, s: &State) -> PyResult<bool> {
        self.hp(py).call_method1(name, (s.py(py)?,))?.extract()
    }
    fn take(
        &self,
        py: Python<'_>,
        s: &State,
        step: Step,
        edge: Option<Edge>,
        keys: &[String],
    ) -> PyResult<State> {
        if !keys.is_empty() {
            let mut steps = s.steps.clone();
            steps.push(step.clone());
            let tr = self
                .hp(py)
                .call_method1("_facts", (steps_py(py, &steps)?,))?;
            self.fc
                .bind(py)
                .call_method1("record", (tuple_py(py, keys)?, tr))?;
        }
        Ok(s.add(step, edge))
    }
    fn rec(&self, py: Python<'_>, k: &str, s: &State, extra: &[Step]) -> PyResult<()> {
        let mut v = s.steps.clone();
        v.extend_from_slice(extra);
        self.hp(py).call_method1("rec", (k, steps_py(py, &v)?))?;
        Ok(())
    }
    fn node(
        &self,
        py: Python<'_>,
        name: &str,
        ctx: &[String],
        s: Option<&State>,
        probe: &[Step],
        assumptions: &[String],
        branches: Option<&[String]>,
        groups: Option<Py<PyAny>>,
    ) -> PyResult<String> {
        let mut args = vec![name.into_py_any(py)?];
        for c in ctx {
            args.push(c.into_py_any(py)?);
        }
        let kw = PyDict::new(py);
        if let Some(s) = s {
            kw.set_item("s", s.py(py)?)?;
        }
        if !probe.is_empty() {
            kw.set_item(
                "probe",
                if probe.len() == 1 {
                    probe[0].clone().into_py_any(py)?
                } else {
                    steps_py(py, probe)?
                },
            )?;
        }
        kw.set_item("assumptions", tuple_py(py, assumptions)?)?;
        if let Some(b) = branches {
            kw.set_item("branches", tuple_py(py, b)?)?;
        }
        if let Some(g) = groups {
            kw.set_item("groups", g)?;
        }
        self.hp(py)
            .getattr("node")?
            .call(PyTuple::new(py, args)?, Some(&kw))?
            .extract()
    }
    fn emit(&self, py: Python<'_>, s: &State, outcome: &str) -> PyResult<()> {
        self.hp(py).call_method1("emit", (s.py(py)?, outcome))?;
        Ok(())
    }
    fn court(&self, py: Python<'_>, s: &State, k: &str, ctx: &str) -> PyResult<()> {
        self.hp(py).call_method1("court", (s.py(py)?, k, ctx))?;
        Ok(())
    }
    fn stay_court(&self, py: Python<'_>, mut s: State, k: &str, ctx: &str) -> PyResult<State> {
        let setup = self.fc.bind(py).getattr("setup")?;
        if !setup.is_none() && setup.getattr("cash_processing")?.extract::<String>()? == "daily" {
            s.late.push((k.into(), s.steps.len()));
        } else {
            self.court(py, &s, k, ctx)?;
        }
        Ok(s)
    }
    fn assumed(&self, py: Python<'_>, name: &str) -> PyResult<Vec<String>> {
        py.import("app.disputes.forecast")?
            .getattr("ASSUMED")?
            .get_item(name)?
            .extract()
    }
    fn fcclasses(&self, py: Python<'_>, name: &str) -> PyResult<Vec<(String, Conjunctions)>> {
        let obj = self.fc.bind(py).call_method1(name, (self.d.bind(py),))?;
        obj.call_method0("items")?
            .try_iter()?
            .map(|x| x?.extract())
            .collect()
    }
    fn watch(
        &mut self,
        py: Python<'_>,
        s: &State,
        no: Step,
        marks: Py<PyAny>,
        nodes: &[&str],
        walks: &[&str],
        then: Action,
    ) -> PyResult<bool> {
        let w = py
            .import("app.disputes.forecast")?
            .getattr("_Watch")?
            .call1((marks, nodes.to_vec(), walks.to_vec()))?;
        if self.hp(py).hasattr("_native_watch_begin")? {
            self.hp(py)
                .call_method1("_native_watch_begin", (s.py(py)?, &no, &w))?;
        }
        self.hp(py)
            .getattr("_watch")?
            .call_method1("append", (&w,))?;
        let result = self.go(py, then, s.add(no.clone(), None));
        self.hp(py).getattr("_watch")?.call_method0("pop")?;
        let read = if self.hp(py).hasattr("_native_watch_end")? {
            self.hp(py)
                .call_method1("_native_watch_end", (s.py(py)?, &no, &w))?
                .extract()?
        } else {
            w.getattr("read")?.extract()?
        };
        result?;
        Ok(read)
    }
    fn first(&mut self, py: Python<'_>, s: &State, probe: &Step, then: Action) -> PyResult<bool> {
        if !self.equity && (!self.pend || s.floor == "done") {
            return Ok(false);
        }
        let x = self.trace(py, s, std::slice::from_ref(probe), false)?;
        let dxguard = self.ints(py, &x, "day")?;
        let dx = dxguard.as_array();
        let reads = x.bind(py).getattr("reads")?;
        let reads: Option<PyReadonlyArray1<'_, i64>> = if reads.is_none() {
            None
        } else {
            Some(reads.extract()?)
        };
        if reads
            .as_ref()
            .is_some_and(|r| r.as_array().len() != dx.len())
        {
            return Err(PyValueError::new_err("trace read day shape differs"));
        }
        let candidates = if self.equity {
            self.candidates(s)
        } else {
            vec![if s.floor == "open" {
                st(
                    "cash_floor",
                    "",
                    if self.raising { "continue" } else { "no" },
                )
            } else {
                st("cash_out", "", "no")
            }]
        };
        for c in candidates {
            let a = self.trace(py, s, std::slice::from_ref(&c), false)?;
            let tguard = self.ints(py, &a, "day")?;
            let petguard = self.ints(py, &a, "petition")?;
            let t = tguard.as_array();
            let pet = petguard.as_array();
            if t.len() != dx.len() || pet.len() != dx.len() {
                return Err(PyValueError::new_err(
                    "trace date and petition shapes differ",
                ));
            }
            if t.iter()
                .zip(dx.iter())
                .zip(pet.iter())
                .enumerate()
                .any(|(i, ((&t, &dx), &pet))| {
                    let rx = reads.as_ref().map_or(dx, |r| dx.max(r.as_array()[i]));
                    let pet = if pet < 0 { i64::MAX } else { pet };
                    t < rx && dx < self.n && t < pet && dx < pet
                })
            {
                let action = if self.equity {
                    Action::AskDistress(c, "".into(), Some(Box::new(then)))
                } else if s.floor == "open" {
                    Action::Floor("".into(), Some(Box::new(then)))
                } else {
                    Action::CashOut("".into(), Some(Box::new(then)))
                };
                self.go(py, action, s.clone())?;
                return Ok(true);
            }
        }
        Ok(false)
    }
    fn candidates(&self, s: &State) -> Vec<Step> {
        let mut c = vec![st("cash_floor", &s.k.to_string(), "neither")];
        if s.out == "open" {
            c.push(st("cash_out", "", "neither"));
        }
        if s.np == "open" && s.out == "done" {
            c.push(st("nonpayment", "", "due"));
        }
        c
    }
    fn all_petition(&self, py: Python<'_>, s: &State) -> PyResult<bool> {
        let tr = self.trace(py, s, &[], true)?;
        Ok(self
            .ints(py, &tr, "petition")?
            .as_array()
            .iter()
            .all(|&x| x >= 0))
    }
    fn joined(
        &self,
        py: Python<'_>,
        s: &State,
        node: &str,
        ctx: &str,
        mut classes: Vec<(String, Conjunctions)>,
        sets: &[Vec<String>],
    ) -> PyResult<Vec<(String, Conjunctions)>> {
        let mask = self
            .hp(py)
            .call_method1("mask_of", (steps_py(py, &s.steps)?,))?;
        for names in sets {
            let mut keep: Vec<String> = vec![];
            for c in names {
                if !classes.iter().any(|(k, _)| k == c) {
                    continue;
                }
                let mut join = None;
                for k in &keep {
                    if self
                        .hp(py)
                        .call_method1(
                            "_same_after",
                            (s.py(py)?, st(node, ctx, k), st(node, ctx, c), &mask),
                        )?
                        .extract::<bool>()?
                    {
                        join = Some(k.clone());
                        break;
                    }
                }
                if let Some(k) = join {
                    let i = classes
                        .iter()
                        .position(|(n, _)| n == c)
                        .ok_or_else(|| PyValueError::new_err("missing class to join"))?;
                    let (_, part) = classes.remove(i);
                    classes
                        .iter_mut()
                        .find(|(n, _)| *n == k)
                        .ok_or_else(|| PyValueError::new_err("missing joined class"))?
                        .1
                        .extend(part);
                } else {
                    keep.push(c.clone());
                }
            }
        }
        Ok(classes)
    }
    fn unfiled(
        &self,
        py: Python<'_>,
        s: &State,
        node: &str,
        ctx: &str,
        mut classes: Vec<(String, Conjunctions)>,
        pairs: &[(&str, &str)],
    ) -> PyResult<Vec<(String, Conjunctions)>> {
        for &(filed, none) in pairs {
            if classes.iter().any(|(k, _)| k == filed) && classes.iter().any(|(k, _)| k == none) {
                let a = self.trace(py, s, &[st(node, ctx, filed)], true)?;
                let b = self.trace(py, s, &[st(node, ctx, none)], true)?;
                if a.bind(py)
                    .getattr("digest")?
                    .eq(b.bind(py).getattr("digest")?)?
                {
                    let i = classes
                        .iter()
                        .position(|(k, _)| k == filed)
                        .ok_or_else(|| PyValueError::new_err("missing filed class"))?;
                    let (_, mut part) = classes.remove(i);
                    let old = &mut classes
                        .iter_mut()
                        .find(|(k, _)| k == none)
                        .ok_or_else(|| PyValueError::new_err("missing unfiled class"))?
                        .1;
                    part.append(old);
                    *old = part;
                }
            }
        }
        Ok(classes)
    }
    fn go(&mut self, py: Python<'_>, a: Action, s: State) -> PyResult<()> {
        let name = a.name();
        if !name.is_empty() && self.hp(py).hasattr("_native_enter")? {
            let state = s.py(py)?;
            let token = self
                .hp(py)
                .call_method1("_native_enter", (name, state.bind(py)))?
                .unbind();
            if token.bind(py).is_instance_of::<pyo3::types::PyBool>()
                && !token.bind(py).is_truthy()?
            {
                return Ok(());
            }
            let result = self.execute(py, a, s);
            self.hp(py)
                .call_method1("_native_exit", (name, state.bind(py), token))?;
            result
        } else {
            self.execute(py, a, s)
        }
    }
    fn execute(&mut self, py: Python<'_>, a: Action, s: State) -> PyResult<()> {
        match a {
            Action::Settle(interval, then) => {
                let probe = st("settle", &interval, "no");
                let tr = self.trace(py, &s, std::slice::from_ref(&probe), false)?;
                let day = self.ints(py, &tr, "day")?;
                let offers = self.ints(py, &tr, "settle_offer")?;
                if day.as_array().len() != offers.as_array().len() {
                    return Err(PyValueError::new_err("settlement offer shape differs"));
                }
                if !day
                    .as_array()
                    .iter()
                    .zip(offers.as_array().iter())
                    .any(|(&d, &o)| d < self.n && o > 0)
                {
                    return self.go(py, *then, s);
                }
                if interval == "I3" {
                    self.hp(py).call_method1("_reads", ("unstayed",))?;
                }
                if self.first(
                    py,
                    &s,
                    &probe,
                    Action::Settle(interval.clone(), then.clone()),
                )? {
                    return Ok(());
                }
                let ctx = vec![interval.clone(), s.cls.clone()];
                let a3 = self.node(
                    py,
                    "settlement_offer",
                    &ctx,
                    Some(&s),
                    std::slice::from_ref(&probe),
                    &[],
                    None,
                    None,
                )?;
                let terms = py
                    .import("app.analysis.events")?
                    .getattr("settlement_terms")?
                    .call1((
                        self.fc.bind(py).getattr("m")?,
                        self.fc.bind(py).getattr("sens")?,
                    ))?
                    .extract::<(String, i64)>()?;
                let mut text="the company offers to settle for its available cash above its 30-day operating need".to_string();
                if terms.0 == "installments" {
                    text += &format!(
                        ", paid in {} equal monthly installments from the settlement date",
                        terms.1
                    );
                }
                let q4 = self.node(
                    py,
                    "settlement_accept",
                    &ctx,
                    Some(&s),
                    &[probe],
                    &[text],
                    None,
                    None,
                )?;
                let key = encode(&[vec![yes(&a3), yes(&q4)]]);
                let keys = vec![a3, q4];
                let y = self.take(
                    py,
                    &s,
                    st("settle", &interval, "yes"),
                    ed(&key, "yes"),
                    &keys,
                )?;
                self.go(py, Action::Tail("settled".into()), y)?;
                let y = self.take(py, &s, st("settle", &interval, "no"), ed(&key, "no"), &keys)?;
                self.go(py, *then, y)
            }
            Action::Verdict => {
                let branches = self
                    .fc
                    .bind(py)
                    .getattr("m")?
                    .get_item("templates")?
                    .get_item("pending_money_claim")?
                    .get_item("verdict_branches")?;
                let first: String = branches
                    .call_method0("__iter__")?
                    .call_method0("__next__")?
                    .extract()?;
                if self.first(py, &s, &st("verdict", "I0", &first), Action::Verdict)? {
                    return Ok(());
                }
                for (b, parts) in self.fcclasses(py, "verdict_classes")? {
                    let in_branches = branches.contains(&b)?;
                    let cls = if in_branches {
                        b.clone()
                    } else {
                        format!("award{}", b.split(':').nth(1).unwrap_or(""))
                    };
                    let mut y = s.add(st("verdict", "I0", &b), comp(parts));
                    y.cls = cls;
                    let judgment = !in_branches
                        || branches
                            .get_item(&b)?
                            .get_item("judgment")?
                            .extract::<bool>()?;
                    self.go(
                        py,
                        if judgment {
                            Action::Entry
                        } else {
                            Action::Tail("no_judgment".into())
                        },
                        y,
                    )?;
                }
                Ok(())
            }
            Action::Entry => {
                let p = st(&self.resp, "entry", &self.quiet);
                if !self.arises(py, &s, &p)? {
                    self.go(py, Action::Motions, s)
                } else {
                    self.go(
                        py,
                        Action::Response(
                            "entry".into(),
                            Box::new(Action::Motions),
                            Box::new(Action::Emit("petition".into())),
                            false,
                        ),
                        s,
                    )
                }
            }
            Action::Motions => {
                let p = st("post_trial_motions", "", "no");
                if !self.arises(py, &s, &p)? {
                    return self.go(py, Action::Post, s);
                }
                if self.first(py, &s, &p, Action::Motions)? {
                    return Ok(());
                }
                let k = self.node(
                    py,
                    "post_trial_motions",
                    std::slice::from_ref(&s.cls),
                    Some(&s),
                    std::slice::from_ref(&p),
                    &["a money judgment is entered on the verdict".into()],
                    None,
                    None,
                )?;
                self.go(
                    py,
                    Action::Settle("I1".into(), Box::new(Action::Q1)),
                    self.take(
                        py,
                        &s,
                        st("post_trial_motions", "", "yes"),
                        ed(&k, "yes"),
                        std::slice::from_ref(&k),
                    )?,
                )?;
                self.go(py, Action::Post, self.take(py, &s, p, ed(&k, "no"), &[k])?)
            }
            Action::Q1 => {
                if self.first(py, &s, &st("execute_pre_ruling", "I1", "no"), Action::Q1)? {
                    return Ok(());
                }
                let pno = st("execute_pre_ruling", "I1", "no");
                let pyes = st("execute_pre_ruling", "I1", "yes");
                let ctx = if self.pend {
                    vec!["I1".into(), s.cls.clone()]
                } else {
                    vec!["I1".into()]
                };
                let assumed = vec!["post-trial motions are pending".into()];
                if self.pend
                    && self
                        .hp(py)
                        .call_method1("_q1_opens_nothing", (s.py(py)?, &pyes))?
                        .extract::<bool>()?
                {
                    let i0 = self.hp(py).getattr("out")?.len()?;
                    let mut a = s.steps.clone();
                    a.push(pyes.clone());
                    let y = self
                        .hp(py)
                        .call_method1("_raw", (steps_py(py, &a)?, true))?;
                    a.push(st("stay", "I1", "yes"));
                    let stay = self
                        .hp(py)
                        .call_method1("_raw", (steps_py(py, &a)?, true))?;
                    let marks = PyDict::new(py);
                    marks.set_item("executing", y.getattr("marks")?.get_item("executing")?)?;
                    marks.set_item("stay_moved", stay.getattr("marks")?.get_item("stay_moved")?)?;
                    if !self.watch(
                        py,
                        &s,
                        pno.clone(),
                        marks.into_any().unbind(),
                        &["enforce_after_final"],
                        &["unstayed"],
                        Action::RipeI1,
                    )? {
                        return Ok(());
                    }
                    let k = self.node(
                        py,
                        "execute_pre_ruling",
                        &ctx,
                        None,
                        &[],
                        &assumed,
                        None,
                        None,
                    )?;
                    self.rec(py, &k, &s, std::slice::from_ref(&pyes))?;
                    self.rec(py, &k, &s, &[pno])?;
                    self.hp(py)
                        .call_method1("_edge_after", (i0, s.edges.len(), (k.clone(), "no")))?;
                    return self.go(py, Action::StayI1, s.add(pyes, ed(&k, "yes")));
                }
                let k = self.node(
                    py,
                    "execute_pre_ruling",
                    &ctx,
                    None,
                    &[],
                    &assumed,
                    None,
                    None,
                )?;
                self.go(
                    py,
                    Action::StayI1,
                    self.take(py, &s, pyes, ed(&k, "yes"), std::slice::from_ref(&k))?,
                )?;
                self.go(
                    py,
                    Action::RipeI1,
                    self.take(py, &s, pno, ed(&k, "no"), &[k])?,
                )
            }
            Action::StayI1 => {
                let p = st("stay", "I1", "no");
                if self.first(py, &s, &p, Action::StayI1)? {
                    return Ok(());
                }
                let ctx = vec!["I1".into(), s.cls.clone()];
                let a1 = self.node(
                    py,
                    "stay_motion",
                    &ctx,
                    Some(&s),
                    std::slice::from_ref(&p),
                    &["the creditor executes before the ruling".into()],
                    None,
                    None,
                )?;
                let j8 = self.node(
                    py,
                    "stay_approved",
                    &ctx,
                    Some(&s),
                    std::slice::from_ref(&p),
                    &["the company moves for a stay".into()],
                    None,
                    None,
                )?;
                let s = self.stay_court(py, s, &j8, "stay_I1")?;
                let key = encode(&[vec![yes(&a1), yes(&j8)]]);
                let mut y = self.take(
                    py,
                    &s,
                    st("stay", "I1", "yes"),
                    ed(&key, "yes"),
                    std::slice::from_ref(&a1),
                )?;
                y.stayed = true;
                self.go(py, Action::J9Stayed, y)?;
                self.go(
                    py,
                    Action::J9I1,
                    self.take(py, &s, p, ed(&key, "no"), &[a1])?,
                )
            }
            Action::J9Stayed => {
                let yes = st("registration_early", "I1", "yes");
                let no = st("registration_early", "I1", "no");
                let moves = self
                    .fc
                    .bind(py)
                    .call_method1(
                        "moves_cash",
                        (self.d.bind(py), steps_py(py, &s.steps)?, yes, no),
                    )?
                    .extract::<bool>()?;
                self.go(py, if moves { Action::J9I1 } else { Action::RipeI1 }, s)
            }
            Action::J9I1 => {
                let p = st("registration_early", "I1", "no");
                if self.first(py, &s, &p, Action::J9I1)? {
                    return Ok(());
                }
                let ctx = if self.pend {
                    vec!["I1".into(), s.cls.clone()]
                } else {
                    vec!["I1".into()]
                };
                let k = self.node(
                    py,
                    "registration_early",
                    &ctx,
                    Some(&s),
                    std::slice::from_ref(&p),
                    &["the creditor executes before finality".into()],
                    None,
                    None,
                )?;
                self.court(py, &s, &k, "registration_I1")?;
                let mut y = self.take(
                    py,
                    &s,
                    st("registration_early", "I1", "yes"),
                    ed(&k, "yes"),
                    &[],
                )?;
                y.early = true;
                self.go(py, Action::A4I1, y)?;
                self.go(py, Action::RipeI1, self.take(py, &s, p, ed(&k, "no"), &[])?)
            }
            Action::A4I1 => {
                if !self.arises(py, &s, &st(&self.resp, "I1", &self.quiet))? {
                    self.go(py, Action::RipeI1, s)
                } else {
                    self.go(
                        py,
                        Action::Response(
                            "I1".into(),
                            Box::new(Action::RipeI1),
                            Box::new(Action::Emit("petition".into())),
                            false,
                        ),
                        s,
                    )
                }
            }
            Action::Response(phase, then, on_file, i3) => {
                self.response(py, s, phase, *then, *on_file, i3)
            }
            Action::Offer(occasion, then) => {
                if self
                    .hp(py)
                    .call_method1("_closes_after", (s.py(py)?, &occasion))?
                    .extract::<bool>()?
                {
                    return self.go(py, *then, s.add(st("offering", &occasion, "no"), None));
                }
                let mut ctx = vec![occasion.clone()];
                if s.failed {
                    ctx.push("after_failed".into());
                }
                let br = vec!["yes".into(), "no".into()];
                let k = self.node(py, "offering_closes", &ctx, None, &[], &[], Some(&br), None)?;
                for b in ["yes", "no"] {
                    let mut y = s.add(st("offering", &occasion, b), ed(&k, b));
                    y.late.push((k.clone(), s.steps.len()));
                    y.failed = s.failed || b == "no";
                    self.go(py, *then.clone(), y)?;
                }
                Ok(())
            }
            Action::Notes(phase, then) => self.notes(py, s, phase, *then),
            Action::RipeI1 => {
                let after = Action::Notes("I1".into(), Box::new(Action::Ruling));
                let reading: String = self.hp(py).call_method0("reading")?.extract()?;
                if self.pend
                    && reading == "entered"
                    && s.a4 == "seek"
                    && self.arises(py, &s, &st(&self.resp, "ripe", &self.quiet))?
                {
                    // The explicit filing branch emits only where every draw files.
                    return self.response(
                        py,
                        s,
                        "ripe".into(),
                        after.clone(),
                        Action::RipeI1Filed(Box::new(after)),
                        false,
                    );
                }
                self.go(py, after, s)
            }
            Action::RipeI1Filed(after) => {
                if self.all_petition(py, &s)? {
                    self.emit(py, &s, "petition")
                } else {
                    self.go(py, *after, s)
                }
            }
            Action::Ruling => {
                if self.pend {
                    return self.go(py, Action::RulingPending, s);
                }
                for (c, parts) in self.fcclasses(py, "ruling_classes")? {
                    let mut y = s.add(st("ruling", "", &c), comp(parts));
                    y.cls = label(&c);
                    self.go(
                        py,
                        if c == "none" {
                            Action::Tail("vacated".into())
                        } else if c == "retrial" {
                            Action::Tail("new_trial".into())
                        } else {
                            Action::Post
                        },
                        y,
                    )?;
                }
                Ok(())
            }
            Action::RulingPending => self.ruling_pending(py, s),
            Action::Post => self.go(py, Action::Settle("I2".into(), Box::new(Action::Appeal)), s),
            Action::Appeal => {
                let pno = st("appeal", "", "no");
                let pyes = st("appeal", "", "yes");
                if s.appealed || !self.arises(py, &s, &pno)? {
                    return self.go(py, Action::StayPost, s);
                }
                if self.first(py, &s, &pno, Action::Appeal)? {
                    return Ok(());
                }
                let i0 = self.hp(py).getattr("out")?.len()?;
                if self.pend {
                    let mut steps = s.steps.clone();
                    steps.push(pyes.clone());
                    let tr = self
                        .hp(py)
                        .call_method1("_raw", (steps_py(py, &steps)?, true))?;
                    let marks = PyDict::new(py);
                    marks.set_item("appealed", tr.getattr("marks")?.get_item("appealed")?)?;
                    if !self.watch(
                        py,
                        &s,
                        pno.clone(),
                        marks.into_any().unbind(),
                        &["enforce_after_final"],
                        &[],
                        Action::StayPost,
                    )? {
                        return Ok(());
                    }
                }
                let k = self.node(
                    py,
                    "appeal",
                    std::slice::from_ref(&s.cls),
                    Some(&s),
                    std::slice::from_ref(&pno),
                    &["a money award survives the ruling".into()],
                    None,
                    None,
                )?;
                if self.pend {
                    self.rec(py, &k, &s, std::slice::from_ref(&pyes))?;
                    self.rec(py, &k, &s, &[pno])?;
                    self.hp(py)
                        .call_method1("_edge_after", (i0, s.edges.len(), (k.clone(), "no")))?;
                    let mut y = s.add(pyes, ed(&k, "yes"));
                    y.appealed = true;
                    self.go(py, Action::StayPost, y)
                } else {
                    let mut y = self.take(py, &s, pyes, ed(&k, "yes"), std::slice::from_ref(&k))?;
                    y.appealed = true;
                    self.go(py, Action::StayPost, y)?;
                    self.go(
                        py,
                        Action::StayPost,
                        self.take(py, &s, pno, ed(&k, "no"), &[k])?,
                    )
                }
            }
            Action::StayPost => {
                if s.stayed {
                    return self.go(py, Action::StayedTail, s);
                }
                let p = st("stay", "post", "no");
                if !self.arises(py, &s, &p)? {
                    return self.go(py, Action::I3, s);
                }
                self.hp(py).call_method1("_reads", ("unstayed",))?;
                if self.first(py, &s, &p, Action::StayPost)? {
                    return Ok(());
                }
                let ctx = vec!["post".into(), s.cls.clone()];
                let a1 = self.node(
                    py,
                    "stay_motion",
                    &ctx,
                    Some(&s),
                    std::slice::from_ref(&p),
                    &["the final judgment is entered".into()],
                    None,
                    None,
                )?;
                let j8 = self.node(
                    py,
                    "stay_approved",
                    &ctx,
                    Some(&s),
                    std::slice::from_ref(&p),
                    &["the company moves for a stay".into()],
                    None,
                    None,
                )?;
                let s = self.stay_court(py, s, &j8, "stay_post")?;
                let key = encode(&[vec![yes(&a1), yes(&j8)]]);
                let mut y = self.take(
                    py,
                    &s,
                    st("stay", "post", "yes"),
                    ed(&key, "yes"),
                    std::slice::from_ref(&a1),
                )?;
                y.stayed = true;
                self.go(
                    py,
                    Action::Enforce(Box::new(Action::StayedTail), true, false),
                    y,
                )?;
                self.go(py, Action::I3, self.take(py, &s, p, ed(&key, "no"), &[a1])?)
            }
            Action::StayedTail => self.go(
                py,
                Action::Settle(
                    "I4".into(),
                    Box::new(Action::Notes(
                        "post".into(),
                        Box::new(Action::Tail("stayed".into())),
                    )),
                ),
                s,
            ),
            Action::I3 => {
                if self.pend && self.pred(py, "levy_first", &s)? {
                    self.go(
                        py,
                        Action::Enforce(
                            Box::new(Action::Settle("I3".into(), Box::new(Action::RipePost))),
                            false,
                            true,
                        ),
                        s,
                    )
                } else {
                    self.go(
                        py,
                        Action::Settle(
                            "I3".into(),
                            Box::new(Action::Enforce(Box::new(Action::RipePost), false, false)),
                        ),
                        s,
                    )
                }
            }
            Action::A4Post(then, i3) => {
                if s.a4 == "closed" || !self.arises(py, &s, &st(&self.resp, "post", &self.quiet))? {
                    return self.go(py, *then, s);
                }
                self.hp(py).call_method1("_reads", ("unstayed",))?;
                self.go(
                    py,
                    Action::Response(
                        "post".into(),
                        then,
                        Box::new(Action::Tail("petition".into())),
                        i3,
                    ),
                    s,
                )
            }
            Action::Enforce(then, pending, i3) => self.enforce(py, s, *then, pending, i3),
            Action::RipePost => {
                let after =
                    Action::Notes("post".into(), Box::new(Action::Tail("unresolved".into())));
                let reading: String = self.hp(py).call_method0("reading")?.extract()?;
                if s.a4 == "seek"
                    && !(self.pend && reading == "entered")
                    && self.arises(py, &s, &st(&self.resp, "ripe", &self.quiet))?
                {
                    self.hp(py).call_method1("_reads", ("unstayed",))?;
                    self.go(
                        py,
                        Action::Response(
                            "ripe".into(),
                            Box::new(after),
                            Box::new(Action::Tail("petition".into())),
                            false,
                        ),
                        s,
                    )
                } else {
                    self.go(py, after, s)
                }
            }
            Action::Tail(outcome) => self.tail(py, s, outcome),
            Action::Kept(dates, outcome) => self.kept(py, s, dates, outcome),
            Action::DelistingNotes(dc, delist, outcome) => {
                self.delisting_notes(py, s, dc, delist, outcome)
            }
            Action::Listing(outcome) => self.listing(py, s, outcome),
            Action::Delisting(dc, delist, outcome) => self.delisting(py, s, dc, delist, outcome),
            Action::Distress(outcome, then) => {
                for c in self.candidates(&s) {
                    if self.inside(py, &s, &c)? {
                        return self.go(py, Action::AskDistress(c, outcome, then), s);
                    }
                }
                self.go(py, Action::End(outcome, then), s)
            }
            Action::DistressNext(outcome, then) => {
                if let Some(t) = then {
                    self.go(py, *t, s)
                } else {
                    self.go(py, Action::Distress(outcome, None), s)
                }
            }
            Action::AskDistress(c, outcome, then) => self.ask_distress(py, s, c, outcome, then),
            Action::Floor(outcome, then) => {
                if self.equity {
                    return self.go(py, Action::Distress(outcome, then), s);
                }
                if s.floor == "cash_out" {
                    return self.go(py, Action::CashOut(outcome, then), s);
                }
                if s.floor == "done" {
                    return self.go(py, Action::End(outcome, then), s);
                }
                let probe = st("cash_floor", "", "no");
                if !self.inside(py, &s, &probe)? {
                    return self.go(py, Action::End(outcome, then), s);
                }
                let k = self.node(
                    py,
                    "petition_cash_floor",
                    &[],
                    Some(&s),
                    std::slice::from_ref(&probe),
                    &[],
                    None,
                    None,
                )?;
                let mut s = s;
                self.late(py, &mut s, &k, &probe)?;
                let mut y = s.add(st("cash_floor", "", "yes"), ed(&k, "yes"));
                y.floor = "done".into();
                self.go(py, Action::End("petition".into(), then.clone()), y)?;
                let mut y = s.add(probe, ed(&k, "no"));
                y.floor = "cash_out".into();
                if let Some(t) = then {
                    self.go(py, *t, y)
                } else {
                    self.go(py, Action::CashOut(outcome, None), y)
                }
            }
            Action::CashOut(outcome, then) => {
                let p = st("cash_out", "", "no");
                if !self.inside(py, &s, &p)? {
                    return self.go(py, Action::End(outcome, then), s);
                }
                let k = self.node(
                    py,
                    "petition_cash_out",
                    &["cash_exhausted".into()],
                    Some(&s),
                    std::slice::from_ref(&p),
                    &[],
                    None,
                    None,
                )?;
                let mut s = s;
                self.late(py, &mut s, &k, &p)?;
                let mut y = s.add(st("cash_out", "", "yes"), ed(&k, "yes"));
                y.floor = "done".into();
                self.go(py, Action::End("petition".into(), then.clone()), y)?;
                let mut y = s.add(p, ed(&k, "no"));
                y.floor = "done".into();
                self.go(py, Action::End(outcome, then), y)
            }
            Action::End(outcome, then) => {
                if let Some(t) = then {
                    self.go(py, *t, s)
                } else {
                    self.emit(py, &s, &outcome)
                }
            }
            Action::Emit(outcome) => self.emit(py, &s, &outcome),
        }
    }
    fn late(&self, py: Python<'_>, s: &mut State, k: &str, p: &Step) -> PyResult<()> {
        if self.pend {
            s.late.push((k.into(), s.steps.len()));
        } else {
            self.rec(py, k, s, std::slice::from_ref(p))?;
        }
        Ok(())
    }
    fn response(
        &mut self,
        py: Python<'_>,
        s: State,
        phase: String,
        then: Action,
        on_file: Action,
        i3: bool,
    ) -> PyResult<()> {
        let probe = st(&self.resp, &phase, &self.quiet);
        if self.first(
            py,
            &s,
            &probe,
            Action::Response(
                phase.clone(),
                Box::new(then.clone()),
                Box::new(on_file.clone()),
                i3,
            ),
        )? {
            return Ok(());
        }
        let pending = s.stayed && (phase == "I1" || phase == "post");
        let mut assumed = if phase == "entry" {
            vec![]
        } else {
            vec!["the judgment is enforceable, unstayed and unpaid".into()]
        };
        match phase.as_str(){"post"=>assumed.push("the creditor levies on the company's cash that day".into()),"ripe"=>assumed.push("the judgment default under the notes has ripened that day".into()),"entry"=>assumed.push("the money judgment was entered that day, unpaid; execution is stayed automatically for its first 30 days (Fed. R. Civ. P. 62(a))".into()),_=>{}}
        if pending {
            assumed.push("the company has moved for a stay, not yet approved".into());
        }
        if self.pend {
            let mut steps = s.steps.clone();
            steps.push(probe.clone());
            let codes: Vec<i64> = self
                .hp(py)
                .call_method1("option_groups", (steps_py(py, &steps)?,))?
                .extract()?;
            if !codes.iter().any(|&c| c >= 0) {
                return self.go(py, then, s);
            }
            let after = if s.a4 == "seek" {
                format!("after_{}", s.resp)
            } else {
                "first".into()
            };
            let late = phase == "post" || phase == "ripe";
            if !self.group_fork {
                let g = self
                    .hp(py)
                    .call_method1("group_codes", (steps_py(py, &steps)?,))?;
                let vals: Vec<i64> = g.call_method0("tolist")?.extract()?;
                let mask = self
                    .hp(py)
                    .call_method1("mask_of", (steps_py(py, &s.steps)?,))?;
                let mask: Option<Vec<bool>> = if mask.is_none() {
                    None
                } else {
                    Some(mask.call_method0("tolist")?.extract()?)
                };
                if vals
                    .iter()
                    .enumerate()
                    .any(|(i, &g)| g < 0 && mask.as_ref().is_none_or(|m| m[i]))
                {
                    self.go(
                        py,
                        then.clone(),
                        s.add(st(&self.resp, &phase, &format!("@-1={}", self.quiet)), None),
                    )?;
                }
                let live: Vec<i64> = vals
                    .iter()
                    .copied()
                    .filter(|&c| c >= 0)
                    .collect::<std::collections::BTreeSet<_>>()
                    .into_iter()
                    .collect();
                let full: Vec<String> = ["pay", "initiate_offering", "file", "none"]
                    .iter()
                    .filter(|&&b| live.iter().any(|&c| offered(true, c, b)))
                    .map(|&x| x.into())
                    .collect();
                let mut ctx = vec![phase.clone(), s.cls.clone(), after];
                if pending {
                    ctx.push("stay_pending".into());
                }
                let k = self.node(
                    py,
                    &self.resp,
                    &ctx,
                    Some(&s),
                    &[probe],
                    &assumed,
                    Some(&full),
                    Some(g.unbind()),
                )?;
                for b in full {
                    let label = format!(
                        "@{}={b}",
                        live.iter()
                            .filter(|&&c| offered(true, c, &b))
                            .map(ToString::to_string)
                            .collect::<Vec<_>>()
                            .join(",")
                    );
                    let mut y = if late {
                        s.add(st(&self.resp, &phase, &label), ed(&k, &b))
                    } else {
                        self.take(
                            py,
                            &s,
                            st(&self.resp, &phase, &label),
                            ed(&k, &b),
                            std::slice::from_ref(&k),
                        )?
                    };
                    if late {
                        y.late.push((k.clone(), s.steps.len()));
                    }
                    self.response_state(&mut y, &s, &b);
                    self.response_go(py, y, &b, &phase, &then, i3)?;
                }
                return Ok(());
            }
            for c in codes {
                if c < 0 {
                    self.go(
                        py,
                        then.clone(),
                        s.add(st(&self.resp, &phase, &format!("@-1={}", self.quiet)), None),
                    )?;
                    continue;
                }
                let br: Vec<String> = ["pay", "initiate_offering", "file", "none"]
                    .iter()
                    .filter(|&&b| offered(true, c, b))
                    .map(|&b| b.into())
                    .collect();
                let mut ctx = vec![
                    phase.clone(),
                    s.cls.clone(),
                    if c & 1 != 0 {
                        "pay".into()
                    } else {
                        "nopay".into()
                    },
                    if c & 2 != 0 {
                        "offer".into()
                    } else {
                        "nooffer".into()
                    },
                    after.clone(),
                ];
                if pending {
                    ctx.push("stay_pending".into());
                }
                let k = self.node(
                    py,
                    &self.resp,
                    &ctx,
                    Some(&s),
                    std::slice::from_ref(&probe),
                    &assumed,
                    Some(&br),
                    None,
                )?;
                self.fc.bind(py).getattr("node_group")?.set_item(&k, c)?;
                for b in br {
                    let step = st(&self.resp, &phase, &format!("@{c}={b}"));
                    let mut y = if late {
                        s.add(step, ed(&k, &b))
                    } else {
                        self.take(py, &s, step, ed(&k, &b), std::slice::from_ref(&k))?
                    };
                    if late {
                        y.late.push((k.clone(), s.steps.len()));
                    }
                    self.response_state(&mut y, &s, &b);
                    self.response_go(py, y, &b, &phase, &then, i3)?;
                }
            }
            return Ok(());
        }
        let pay = self
            .fc
            .bind(py)
            .call_method1(
                "pay_possible",
                (
                    self.d.bind(py),
                    steps_py(py, &s.steps)?,
                    st(&self.resp, &phase, &self.seek),
                ),
            )?
            .extract::<bool>()?;
        let mut br = if pay { vec!["pay".into()] } else { vec![] };
        br.extend(["seek_sale_or_financing", "file", "neither"].map(str::to_string));
        let mut ctx = vec![
            phase.clone(),
            s.cls.clone(),
            if pay { "pay".into() } else { "nopay".into() },
            if s.a4 == "seek" {
                "after_seek".into()
            } else {
                "first".into()
            },
        ];
        if pending {
            ctx.push("stay_pending".into());
        }
        let k = self.node(
            py,
            &self.resp,
            &ctx,
            Some(&s),
            &[probe],
            &assumed,
            Some(&br),
            None,
        )?;
        for b in br {
            let mut y = self.take(
                py,
                &s,
                st(&self.resp, &phase, &b),
                ed(&k, &b),
                std::slice::from_ref(&k),
            )?;
            y.a4 = if self.again.contains(&b) {
                "seek".into()
            } else {
                "closed".into()
            };
            let next = if b == "pay" {
                Action::Tail("paid".into())
            } else if b == "file" {
                on_file.clone()
            } else {
                then.clone()
            };
            self.go(
                py,
                if i3 && (b == "pay" || b == "file") {
                    Action::Settle("I3".into(), Box::new(next))
                } else {
                    next
                },
                y,
            )?;
        }
        Ok(())
    }
    fn response_state(&self, y: &mut State, s: &State, b: &str) {
        y.a4 = if self.again.iter().any(|v| v == b) {
            "seek".into()
        } else {
            "closed".into()
        };
        y.resp = if b == "initiate_offering" {
            "offer".into()
        } else if b == "none" {
            "none".into()
        } else {
            s.resp.clone()
        };
    }
    fn response_go(
        &mut self,
        py: Python<'_>,
        y: State,
        b: &str,
        phase: &str,
        then: &Action,
        i3: bool,
    ) -> PyResult<()> {
        let a = if b == "initiate_offering" {
            Action::Offer(phase.into(), Box::new(then.clone()))
        } else if b == "none" {
            then.clone()
        } else {
            let tail = Action::Tail(if b == "pay" {
                "paid".into()
            } else {
                "petition".into()
            });
            if i3 {
                Action::Settle("I3".into(), Box::new(tail))
            } else {
                tail
            }
        };
        self.go(py, a, y)
    }
    fn notes(&mut self, py: Python<'_>, s: State, phase: String, then: Action) -> PyResult<()> {
        let f = self.hp(py).getattr("fin")?;
        let p = st("judgment_default", &phase, "no");
        if f.is_none()
            || !f.getattr("judgment_default_days")?.is_truthy()?
            || !self.arises(py, &s, &p)?
        {
            return self.go(py, then, s);
        }
        if self.first(
            py,
            &s,
            &p,
            Action::Notes(phase.clone(), Box::new(then.clone())),
        )? {
            return Ok(());
        }
        let acc = st("judgment_default", &phase, "accelerated");
        let issuer = vec![acc.clone(), st("notes_due_date", "issuer", "")];
        let holders = vec![acc, st("notes_due_date", "holders", "")];
        let mut ctx = vec![phase.clone(), s.cls.clone()];
        if phase != "I1"
            && s.steps
                .iter()
                .any(|x| x.0 == "judgment_default" && x.1 == "I1")
        {
            ctx.push("entered_not_acted".into());
        }
        let h1 = self.node(
            py,
            "holders_act_judgment",
            &ctx,
            Some(&s),
            std::slice::from_ref(&p),
            &[],
            None,
            None,
        )?;
        let a5 = self.node(
            py,
            "petition_on_notes",
            &[format!("judgment_{phase}")],
            Some(&s),
            &issuer,
            &["the holders accelerate the notes".into()],
            None,
            None,
        )?;
        let inside = if !self.pend {
            true
        } else {
            let tr = self.trace(py, &s, &holders, false)?;
            self.ints(py, &tr, "day")?
                .as_array()
                .iter()
                .any(|&x| x < self.n)
        };
        let mut classes = if inside {
            let h3 = self.node(
                py,
                "holders_involuntary",
                &[format!("judgment_{phase}")],
                Some(&s),
                &holders,
                &[
                    "the notes are accelerated and unpaid".into(),
                    "the issuer does not file".into(),
                ],
                None,
                None,
            )?;
            self.rec(py, &a5, &s, &issuer)?;
            self.rec(py, &h3, &s, &holders)?;
            vec![
                ("yes".into(), vec![vec![yes(&h1), yes(&a5)]]),
                (
                    "holders_file".into(),
                    vec![vec![yes(&h1), no(&a5), yes(&h3)]],
                ),
                ("accelerated".into(), vec![vec![yes(&h1), no(&a5), no(&h3)]]),
            ]
        } else {
            self.rec(py, &a5, &s, &issuer)?;
            vec![
                ("yes".into(), vec![vec![yes(&h1), yes(&a5)]]),
                ("accelerated".into(), vec![vec![yes(&h1), no(&a5)]]),
            ]
        };
        classes = self.unfiled(
            py,
            &s,
            "judgment_default",
            &phase,
            classes,
            &[("holders_file", "accelerated")],
        )?;
        classes = self.joined(
            py,
            &s,
            "judgment_default",
            &phase,
            classes,
            &[vec!["yes".into(), "holders_file".into()]],
        )?;
        for (branch, parts) in classes {
            let mut y = self.notes_take(
                py,
                &s,
                st("judgment_default", &phase, &branch),
                comp(parts),
                &h1,
            )?;
            y.notes_due = true;
            if branch != "accelerated" && self.all_petition(py, &y)? {
                self.go(py, Action::Floor("petition".into(), None), y)?;
            } else {
                self.go(py, then.clone(), y)?;
            }
        }
        self.go(
            py,
            then,
            self.notes_take(py, &s, p, comp(vec![vec![no(&h1)]]), &h1)?,
        )
    }
    fn notes_take(
        &self,
        py: Python<'_>,
        s: &State,
        p: Step,
        e: Option<Edge>,
        h1: &str,
    ) -> PyResult<State> {
        if self.pend {
            let mut y = s.add(p, e);
            y.late.push((h1.into(), s.steps.len()));
            Ok(y)
        } else {
            self.take(py, s, p, e, &[h1.into()])
        }
    }
    fn ruling_pending(&mut self, py: Python<'_>, s: State) -> PyResult<()> {
        let p = st("post_trial_ruling", "", "unchanged");
        if !self.arises(py, &s, &p)? {
            return self.go(py, Action::Tail("motions_pending".into()), s);
        }
        if self.first(py, &s, &p, Action::RulingPending)? {
            return Ok(());
        }
        let below: Option<(i64, i64, i64)> = self
            .hp(py)
            .call_method1("reduced_band", (s.py(py)?,))?
            .extract()?;
        let mut br = vec!["unchanged".into()];
        if below.is_some() {
            br.push("reduced".into());
        }
        br.push("set_aside".into());
        let k = self.node(
            py,
            "post_trial_ruling",
            std::slice::from_ref(&s.cls),
            Some(&s),
            std::slice::from_ref(&p),
            &["post-trial motions are pending".into()],
            Some(&br),
            None,
        )?;
        self.go(
            py,
            Action::Post,
            self.take(py, &s, p, ed(&k, "unchanged"), std::slice::from_ref(&k))?,
        )?;
        let mut aside = vec![vec![(k.clone(), "set_aside".into())]];
        if let Some((lo, hi, booked)) = below {
            let c3 = self.node(
                py,
                "remittitur_elected",
                &[s.cls.clone(), format!("remit{booked}")],
                None,
                &[],
                &[
                    "the court orders a new trial unless the claimant accepts the reduced amount"
                        .into(),
                ],
                None,
                None,
            )?;
            self.fc
                .bind(py)
                .getattr("remitted")?
                .set_item(&c3, (booked, lo, hi))?;
            let p = st(
                "post_trial_ruling",
                "",
                &format!("reduced:{booked}:{lo}:{hi}"),
            );
            let mut y = self.take(
                py,
                &s,
                p,
                comp(vec![vec![(k.clone(), "reduced".into()), yes(&c3)]]),
                &[k.clone(), c3.clone()],
            )?;
            y.cls = format!("reduced{booked}");
            self.go(
                py,
                Action::Notes("ruling".into(), Box::new(Action::Post)),
                y,
            )?;
            aside.push(vec![(k.clone(), "reduced".into()), no(&c3)]);
        }
        let mut y = self.take(
            py,
            &s,
            st("post_trial_ruling", "", "set_aside"),
            comp(aside),
            &[k],
        )?;
        y.cls = "set_aside".into();
        self.go(py, Action::Tail("set_aside".into()), y)
    }
    fn enforce(
        &mut self,
        py: Python<'_>,
        s: State,
        then: Action,
        pending: bool,
        i3: bool,
    ) -> PyResult<()> {
        let levy = st("enforce", "post", "levy");
        let none = st("enforce", "post", "none");
        let moves = if pending {
            self.fc
                .bind(py)
                .call_method1(
                    "moves_cash",
                    (self.d.bind(py), steps_py(py, &s.steps)?, &levy, &none),
                )?
                .extract()?
        } else {
            true
        };
        if !self.arises(py, &s, &none)? || !moves {
            return self.go(py, then, s);
        }
        self.hp(py).call_method1("_reads", ("unstayed",))?;
        if self.first(
            py,
            &s,
            &none,
            Action::Enforce(Box::new(then.clone()), pending, i3),
        )? {
            return Ok(());
        }
        let mut ctx = vec![
            s.cls.clone(),
            if s.appealed {
                "appealed".into()
            } else {
                "final".into()
            },
        ];
        let mut assumed =
            vec!["the judgment is enforceable, unstayed and unpaid after the ruling".into()];
        if pending {
            ctx.push("stay_pending".into());
            assumed.push("the company has moved for a stay, not yet approved".into());
        }
        let q3 = self.node(
            py,
            "enforce_after_final",
            &ctx,
            Some(&s),
            std::slice::from_ref(&none),
            &assumed,
            None,
            None,
        )?;
        let (lp, np) = if s.appealed && !s.early {
            let mut ctx = vec!["post".into(), s.cls.clone()];
            if pending {
                ctx.push("stay_pending".into());
            }
            let j9 = self.node(
                py,
                "registration_early",
                &ctx,
                Some(&s),
                std::slice::from_ref(&none),
                &["the creditor enforces before finality".into()],
                None,
                None,
            )?;
            self.court(py, &s, &j9, "registration_post")?;
            (
                vec![vec![yes(&q3), yes(&j9)]],
                vec![vec![no(&q3)], vec![yes(&q3), no(&j9)]],
            )
        } else {
            (vec![vec![yes(&q3)]], vec![vec![no(&q3)]])
        };
        self.go(
            py,
            Action::A4Post(Box::new(then.clone()), i3),
            self.take(py, &s, levy, comp(lp), std::slice::from_ref(&q3))?,
        )?;
        self.go(py, then, self.take(py, &s, none, comp(np), &[q3])?)
    }
    fn listing_dates(&self, py: Python<'_>) -> PyResult<Vec<(String, i64)>> {
        self.hp(py)
            .call_method0("_listing_dates")?
            .call_method0("items")?
            .try_iter()?
            .map(|x| x?.extract())
            .collect()
    }
    fn tail(&mut self, py: Python<'_>, s: State, outcome: String) -> PyResult<()> {
        if self.equity {
            return self.go(py, Action::Listing(outcome), s);
        }
        let f = self.hp(py).getattr("fin")?;
        if f.is_none()
            || f.getattr("listing_deadline")?.is_none()
            || !self.inside(py, &s, &st("listing", "", "listed"))?
        {
            return self.go(py, Action::Floor(outcome, None), s);
        }
        let dates = self.listing_dates(py)?;
        if dt(&dates, "delisted_panel")?.min(dt(&dates, "delisted_suspension")?) >= self.n {
            return self.go(py, Action::Floor(outcome, None), s);
        }
        if self.fc.bind(py).getattr("spec")?.contains("listing_kept")?
            && dt(&dates, "panel_decision")? >= self.n
        {
            return self.go(py, Action::Kept(dates, outcome), s);
        }
        if self.first(
            py,
            &s,
            &st("listing_date", "vote_call", ""),
            Action::Tail(outcome.clone()),
        )? {
            return Ok(());
        }
        let ask = |this: &Self, name: &str, ctx: &str, assumed: Vec<String>| -> PyResult<String> {
            let p = st("listing_date", ctx, "");
            let k = this.node(
                py,
                name,
                &[],
                Some(&s),
                std::slice::from_ref(&p),
                &assumed,
                None,
                None,
            )?;
            this.rec(py, &k, &s, &[p])?;
            Ok(k)
        };
        let a7 = ask(self, "reverse_split_board", "vote_call", vec![])?;
        let st1 = ask(
            self,
            "split_approved",
            "effective_by",
            vec!["the board calls the vote in time".into()],
        )?;
        let a8 = ask(
            self,
            "nasdaq_hearing",
            "hearing_request",
            vec!["the stock is not compliant on the deadline".into()],
        )?;
        let n1 = ask(
            self,
            "panel_exception",
            "panel_decision",
            vec!["the issuer requests a hearing".into()],
        )?;
        let not_ok = vec![vec![no(&a7)], vec![yes(&a7), no(&st1)]];
        let mut listed = vec![vec![yes(&a7), yes(&st1)]];
        let mut panel = vec![];
        let mut suspension = vec![];
        for c in not_ok {
            let mut p = c.clone();
            p.extend([yes(&a8), yes(&n1)]);
            listed.push(p);
            let mut p = c.clone();
            p.extend([yes(&a8), no(&n1)]);
            panel.push(p);
            let mut p = c;
            p.push(no(&a8));
            suspension.push(p);
        }
        let mut classes = vec![
            ("listed".to_string(), listed),
            ("delisted_panel".into(), panel),
            ("delisted_suspension".into(), suspension),
        ];
        for c in ["delisted_panel", "delisted_suspension"] {
            if dt(&dates, c)? >= self.n {
                let i = classes
                    .iter()
                    .position(|(k, _)| k == c)
                    .ok_or_else(|| PyValueError::new_err("missing delisting class"))?;
                let (_, part) = classes.remove(i);
                classes[0].1.extend(part);
            }
        }
        for (c, parts) in classes {
            let y = self.take(py, &s, st("listing", "", &c), comp(parts), &[])?;
            let next = if c == "listed" {
                Action::Floor(outcome.clone(), None)
            } else {
                Action::DelistingNotes(c.clone(), dt(&dates, &c)?, outcome.clone())
            };
            self.go(py, next, y)?;
        }
        Ok(())
    }
    fn kept(
        &mut self,
        py: Python<'_>,
        s: State,
        dates: Vec<(String, i64)>,
        outcome: String,
    ) -> PyResult<()> {
        if dt(&dates, "delisted_suspension")? >= self.n {
            return self.go(py, Action::Floor(outcome, None), s);
        }
        let at = st("listing_date", "kept", "");
        if !self.inside(py, &s, &at)? {
            return self.go(py, Action::Floor(outcome, None), s);
        }
        if self.first(py, &s, &at, Action::Kept(dates.clone(), outcome.clone()))? {
            return Ok(());
        }
        let k = self.node(
            py,
            "listing_kept",
            &[],
            Some(&s),
            std::slice::from_ref(&at),
            &self.assumed(py, "listing_kept")?,
            None,
            None,
        )?;
        self.rec(py, &k, &s, &[at])?;
        self.go(
            py,
            Action::Floor(outcome.clone(), None),
            self.take(py, &s, st("listing", "kept", "listed"), ed(&k, "yes"), &[])?,
        )?;
        self.go(
            py,
            Action::DelistingNotes(
                "delisted_suspension".into(),
                dt(&dates, "delisted_suspension")?,
                outcome,
            ),
            self.take(
                py,
                &s,
                st("listing", "kept", "delisted_suspension"),
                ed(&k, "no"),
                &[],
            )?,
        )
    }
    fn delisting_notes(
        &mut self,
        py: Python<'_>,
        s: State,
        dc: String,
        delist: i64,
        outcome: String,
    ) -> PyResult<()> {
        let p = st("delisting_notes", &dc, "none");
        if !self.inside(py, &s, &p)? {
            return self.go(py, Action::Floor(outcome, None), s);
        }
        if self.first(
            py,
            &s,
            &p,
            Action::DelistingNotes(dc.clone(), delist, outcome.clone()),
        )? {
            return Ok(());
        }
        let h2 = self.node(
            py,
            "holders_act_delisting",
            std::slice::from_ref(&dc),
            Some(&s),
            std::slice::from_ref(&p),
            &self.assumed(py, "holders_act_delisting")?,
            None,
            None,
        )?;
        let acc = st("delisting_notes", &dc, "accelerated");
        let issuer = vec![acc.clone(), st("notes_due_date", "issuer", "")];
        let holders = vec![acc, st("notes_due_date", "holders", "")];
        let a5 = self.node(
            py,
            "petition_on_notes",
            &[format!("delisting_{dc}")],
            Some(&s),
            &issuer,
            &self.assumed(py, "petition_on_notes:delisting")?,
            None,
            None,
        )?;
        let h3 = self.node(
            py,
            "holders_involuntary",
            &[format!("delisting_{dc}")],
            Some(&s),
            &holders,
            &self.assumed(py, "holders_involuntary:delisting")?,
            None,
            None,
        )?;
        let mut facts = vec![(a5.clone(), issuer), (h3.clone(), holders)];
        let mut classes = vec![
            (
                "petition_delist".into(),
                vec![vec![(h2.clone(), "accelerate".into()), yes(&a5)]],
            ),
            (
                "petition_delist_holders".into(),
                vec![vec![(h2.clone(), "accelerate".into()), no(&a5), yes(&h3)]],
            ),
            (
                "accelerated".into(),
                vec![vec![(h2.clone(), "accelerate".into()), no(&a5), no(&h3)]],
            ),
        ];
        let mut none = vec![vec![(h2.clone(), "neither".into())]];
        if !self.pend {
            let repday: i64 = self
                .hp(py)
                .call_method1("_repurchase_day", (delist,))?
                .extract()?;
            if repday < self.n {
                let rep = st("delisting_notes", &dc, "repurchase_unpaid");
                let issuer = vec![rep.clone(), st("notes_due_date", "issuer", "")];
                let holders = vec![rep, st("notes_due_date", "holders", "")];
                let a5r = self.node(
                    py,
                    "petition_on_notes",
                    &[format!("repurchase_{dc}")],
                    Some(&s),
                    &issuer,
                    &self.assumed(py, "petition_on_notes:repurchase")?,
                    None,
                    None,
                )?;
                let h3r = self.node(
                    py,
                    "holders_involuntary",
                    &[format!("repurchase_{dc}")],
                    Some(&s),
                    &holders,
                    &self.assumed(py, "holders_involuntary:repurchase")?,
                    None,
                    None,
                )?;
                classes.extend([
                    (
                        "petition_repurchase".into(),
                        vec![vec![(h2.clone(), "repurchase_only".into()), yes(&a5r)]],
                    ),
                    (
                        "petition_repurchase_holders".into(),
                        vec![vec![
                            (h2.clone(), "repurchase_only".into()),
                            no(&a5r),
                            yes(&h3r),
                        ]],
                    ),
                    (
                        "repurchase_unpaid".into(),
                        vec![vec![
                            (h2.clone(), "repurchase_only".into()),
                            no(&a5r),
                            no(&h3r),
                        ]],
                    ),
                ]);
                facts.extend([(a5r, issuer), (h3r, holders)]);
            } else {
                none.push(vec![(h2.clone(), "repurchase_only".into())]);
            }
        }
        classes.push(("none".into(), none));
        for (k, at) in facts {
            self.rec(py, &k, &s, &at)?;
        }
        classes = self.unfiled(
            py,
            &s,
            "delisting_notes",
            &dc,
            classes,
            &[
                ("petition_delist_holders", "accelerated"),
                ("petition_repurchase_holders", "repurchase_unpaid"),
            ],
        )?;
        classes = self.joined(
            py,
            &s,
            "delisting_notes",
            &dc,
            classes,
            &[
                vec!["petition_delist".into(), "petition_delist_holders".into()],
                vec![
                    "petition_repurchase".into(),
                    "petition_repurchase_holders".into(),
                ],
            ],
        )?;
        for (c, parts) in classes {
            let mut y = self.take(
                py,
                &s,
                st("delisting_notes", &dc, &c),
                comp(parts),
                std::slice::from_ref(&h2),
            )?;
            y.notes_due = c != "none";
            self.go(py, Action::Floor(outcome.clone(), None), y)?;
        }
        Ok(())
    }
    fn listing(&mut self, py: Python<'_>, s: State, outcome: String) -> PyResult<()> {
        let at = st("listing_date", "compliance", "");
        let f = self.hp(py).getattr("fin")?;
        if f.is_none() || f.getattr("listing_deadline")?.is_none() || !self.inside(py, &s, &at)? {
            return self.go(py, Action::Distress(outcome, None), s);
        }
        if self.first(py, &s, &at, Action::Listing(outcome.clone()))? {
            return Ok(());
        }
        let d6a = self.node(
            py,
            "bid_compliance",
            &["deadline".into()],
            None,
            &[],
            &[],
            None,
            None,
        )?;
        let d6b = self.node(
            py,
            "hearing_request",
            &["determination".into()],
            None,
            &[],
            &["compliance is not regained by the deadline".into()],
            None,
            None,
        )?;
        self.rec(py, &d6a, &s, &[at])?;
        self.rec(py, &d6b, &s, &[st("listing_date", "hearing_request", "")])?;
        let dates = self.listing_dates(py)?;
        let suspension = dt(&dates, "delisted_suspension")?;
        let classes = vec![
            ("compliant".into(), vec![vec![yes(&d6a)]]),
            ("hearing".into(), vec![vec![no(&d6a), yes(&d6b)]]),
            ("suspended".into(), vec![vec![no(&d6a), no(&d6b)]]),
        ];
        let mut to = vec!["compliant".into(), "hearing".into()];
        if suspension >= self.n {
            to.push("suspended".into());
        }
        for (c, parts) in self.joined(py, &s, "listing", "", classes, &[to])? {
            let y = s.add(st("listing", "", &c), comp(parts));
            self.go(
                py,
                if c == "suspended" && suspension < self.n {
                    Action::Delisting("delisted_suspension".into(), suspension, outcome.clone())
                } else {
                    Action::Distress(outcome.clone(), None)
                },
                y,
            )?;
        }
        Ok(())
    }
    fn delisting(
        &mut self,
        py: Python<'_>,
        s: State,
        dc: String,
        delist: i64,
        outcome: String,
    ) -> PyResult<()> {
        let p = st("delisting_notes", &dc, "none");
        if !self.inside(py, &s, &p)? {
            return self.go(py, Action::Distress(outcome, None), s);
        }
        if self.first(
            py,
            &s,
            &p,
            Action::Delisting(dc.clone(), delist, outcome.clone()),
        )? {
            return Ok(());
        }
        if self
            .hp(py)
            .call_method1("_repurchase_day", (delist,))?
            .extract::<i64>()?
            < self.n
        {
            return Err(PyNotImplementedError::new_err(
                "the repurchase falls due inside the period: D9 on an unpaid repurchase and H3 on it are not built (QUESTIONS §4.4 D9)",
            ));
        }
        if self
            .hp(py)
            .call_method1("_declared_after", (s.py(py)?, &p))?
            .extract::<bool>()?
        {
            return self.go(py, Action::Distress(outcome, None), s.add(p, None));
        }
        let acc = st("delisting_notes", &dc, "accelerated");
        let issuer = vec![acc.clone(), st("notes_due_date", "issuer", "")];
        let holders = vec![acc, st("notes_due_date", "holders", "")];
        let h2 = self.node(
            py,
            "holders_act_delisting",
            std::slice::from_ref(&dc),
            Some(&s),
            std::slice::from_ref(&p),
            &self.assumed(py, "holders_act_delisting")?,
            Some(&["accelerate".into(), "neither".into()]),
            None,
        )?;
        let a5 = self.node(
            py,
            "petition_on_notes",
            &[format!("delisting_{dc}")],
            Some(&s),
            &issuer,
            &self.assumed(py, "petition_on_notes:delisting")?,
            None,
            None,
        )?;
        self.rec(py, &h2, &s, &[p])?;
        self.rec(py, &a5, &s, &issuer)?;
        let mut classes = vec![
            (
                "petition_delist".into(),
                vec![vec![(h2.clone(), "accelerate".into()), yes(&a5)]],
            ),
            (
                "accelerated".into(),
                vec![vec![(h2.clone(), "accelerate".into()), no(&a5)]],
            ),
            ("none".into(), vec![vec![(h2.clone(), "neither".into())]]),
        ];
        let tr = self.trace(py, &s, &holders, false)?;
        if self
            .ints(py, &tr, "day")?
            .as_array()
            .iter()
            .any(|&t| t < self.n)
        {
            let h3 = self.node(
                py,
                "holders_involuntary",
                &[format!("delisting_{dc}")],
                Some(&s),
                &holders,
                &self.assumed(py, "holders_involuntary:delisting")?,
                None,
                None,
            )?;
            self.rec(py, &h3, &s, &holders)?;
            classes = vec![
                (
                    "petition_delist".into(),
                    vec![vec![(h2.clone(), "accelerate".into()), yes(&a5)]],
                ),
                (
                    "petition_delist_holders".into(),
                    vec![vec![(h2.clone(), "accelerate".into()), no(&a5), yes(&h3)]],
                ),
                (
                    "accelerated".into(),
                    vec![vec![(h2.clone(), "accelerate".into()), no(&a5), no(&h3)]],
                ),
                ("none".into(), vec![vec![(h2.clone(), "neither".into())]]),
            ];
        }
        for (c, parts) in self.joined(
            py,
            &s,
            "delisting_notes",
            &dc,
            classes,
            &[vec![
                "petition_delist".into(),
                "petition_delist_holders".into(),
            ]],
        )? {
            let mut y = s.add(st("delisting_notes", &dc, &c), comp(parts));
            y.notes_due = c != "none";
            self.go(
                py,
                Action::Distress(
                    if c.starts_with("petition") {
                        "petition".into()
                    } else {
                        outcome.clone()
                    },
                    None,
                ),
                y,
            )?;
        }
        Ok(())
    }
    fn ask_distress(
        &mut self,
        py: Python<'_>,
        s: State,
        c: Step,
        outcome: String,
        then: Option<Box<Action>>,
    ) -> PyResult<()> {
        let next = Action::DistressNext(outcome.clone(), then.clone());
        if c.0 == "nonpayment" {
            return self.nonpayment(py, s, c, outcome, then);
        }
        let mut steps = s.steps.clone();
        steps.push(c.clone());
        let codes: Vec<i64> = self
            .hp(py)
            .call_method1("option_groups", (steps_py(py, &steps)?,))?
            .extract()?;
        if !codes.iter().any(|&x| x >= 0) {
            return self.go(py, next, s);
        }
        let isfloor = c.0 == "cash_floor";
        let q = if isfloor {
            "financing_at_floor"
        } else {
            "petition_cash_out"
        };
        let qctx = if isfloor {
            format!("floor{}", c.1)
        } else {
            "cash_exhausted".into()
        };
        let occasion = if isfloor {
            format!("floor{}", c.1)
        } else {
            "cash_out".into()
        };
        if !self.group_fork {
            let groups = self
                .hp(py)
                .call_method1("group_codes", (steps_py(py, &steps)?,))?;
            let g: Vec<i64> = groups.call_method0("tolist")?.extract()?;
            let mask = self
                .hp(py)
                .call_method1("mask_of", (steps_py(py, &s.steps)?,))?;
            let m: Option<Vec<bool>> = if mask.is_none() {
                None
            } else {
                Some(mask.call_method0("tolist")?.extract()?)
            };
            if g.iter()
                .enumerate()
                .any(|(i, &g)| g < 0 && m.as_ref().is_none_or(|m| m[i]))
            {
                let mut y = s.add(st(&c.0, &c.1, "@-1=neither"), None);
                distress_state(&mut y, isfloor);
                self.go(py, next.clone(), y)?;
            }
            let live: Vec<i64> = g
                .iter()
                .copied()
                .filter(|&v| v >= 0)
                .collect::<std::collections::BTreeSet<_>>()
                .into_iter()
                .collect();
            let full: Vec<String> = ["initiate_offering", "file", "neither"]
                .iter()
                .filter(|&&b| live.iter().any(|&g| offered(false, g, b)))
                .map(|&x| x.into())
                .collect();
            let k = self.node(
                py,
                q,
                &[qctx],
                Some(&s),
                std::slice::from_ref(&c),
                &[],
                Some(&full),
                Some(groups.unbind()),
            )?;
            for b in full {
                let label = format!(
                    "@{}={b}",
                    live.iter()
                        .filter(|&&g| offered(false, g, &b))
                        .map(ToString::to_string)
                        .collect::<Vec<_>>()
                        .join(",")
                );
                let mut y = s.add(st(&c.0, &c.1, &label), ed(&k, &b));
                y.late.push((k.clone(), s.steps.len()));
                distress_state(&mut y, isfloor);
                self.distress_go(py, y, &b, &occasion, &next, then.clone())?;
            }
            return Ok(());
        }
        for code in codes {
            if code < 0 {
                let mut y = s.add(st(&c.0, &c.1, "@-1=neither"), None);
                distress_state(&mut y, isfloor);
                self.go(py, next.clone(), y)?;
                continue;
            }
            let br: Vec<String> = ["initiate_offering", "file", "neither"]
                .iter()
                .filter(|&&b| offered(false, code, b))
                .map(|&x| x.into())
                .collect();
            let ctx = vec![
                qctx.clone(),
                if code & 2 != 0 {
                    "offer".into()
                } else {
                    "nooffer".into()
                },
            ];
            let k = self.node(
                py,
                q,
                &ctx,
                Some(&s),
                std::slice::from_ref(&c),
                &[],
                Some(&br),
                None,
            )?;
            self.fc.bind(py).getattr("node_group")?.set_item(&k, code)?;
            for b in br {
                let mut y = s.add(st(&c.0, &c.1, &format!("@{code}={b}")), ed(&k, &b));
                y.late.push((k.clone(), s.steps.len()));
                distress_state(&mut y, isfloor);
                self.distress_go(py, y, &b, &occasion, &next, then.clone())?;
            }
        }
        Ok(())
    }
    fn distress_go(
        &mut self,
        py: Python<'_>,
        y: State,
        b: &str,
        occasion: &str,
        next: &Action,
        then: Option<Box<Action>>,
    ) -> PyResult<()> {
        self.go(
            py,
            if b == "initiate_offering" {
                Action::Offer(occasion.into(), Box::new(next.clone()))
            } else if b == "neither" {
                next.clone()
            } else {
                Action::End("petition".into(), then)
            },
            y,
        )
    }
    fn nonpayment(
        &mut self,
        py: Python<'_>,
        s: State,
        c: Step,
        outcome: String,
        then: Option<Box<Action>>,
    ) -> PyResult<()> {
        let assumed =
            "the notes are due and unpaid under Indenture §7.02 on the general nonpayment"
                .to_string();
        let a5 = self.node(
            py,
            "petition_on_notes",
            &["nonpayment".into()],
            Some(&s),
            std::slice::from_ref(&c),
            std::slice::from_ref(&assumed),
            None,
            None,
        )?;
        let h3 = self.node(
            py,
            "holders_involuntary",
            &["nonpayment".into()],
            Some(&s),
            &[c],
            &[assumed, "the issuer does not file".into()],
            None,
            None,
        )?;
        let classes = vec![
            ("petition".into(), vec![vec![yes(&a5)]]),
            ("holders_file".into(), vec![vec![no(&a5), yes(&h3)]]),
            ("due".into(), vec![vec![no(&a5), no(&h3)]]),
        ];
        for (b, parts) in self.joined(
            py,
            &s,
            "nonpayment",
            "",
            classes,
            &[vec!["petition".into(), "holders_file".into()]],
        )? {
            let mut y = s.add(st("nonpayment", "", &b), comp(parts));
            y.late
                .extend([(a5.clone(), s.steps.len()), (h3.clone(), s.steps.len())]);
            y.np = "done".into();
            y.notes_due = true;
            self.go(
                py,
                Action::DistressNext(
                    if b != "due" {
                        "petition".into()
                    } else {
                        outcome.clone()
                    },
                    then.clone(),
                ),
                y,
            )?;
        }
        Ok(())
    }
}
fn offered(response: bool, code: i64, b: &str) -> bool {
    match b {
        "pay" => response && code & 1 != 0,
        "initiate_offering" => code & 2 != 0,
        "file" => true,
        "none" => response,
        "neither" => !response,
        _ => false,
    }
}
fn distress_state(s: &mut State, isfloor: bool) {
    if isfloor {
        s.k += 1;
    } else {
        s.out = "done".into();
    }
}
fn dt(dates: &[(String, i64)], name: &str) -> PyResult<i64> {
    dates
        .iter()
        .find(|(k, _)| k == name)
        .map(|(_, v)| *v)
        .ok_or_else(|| PyValueError::new_err(format!("missing listing date {name}")))
}
pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<NativeWalk>()?;
    m.add_function(wrap_pyfunction!(walk_bank, m)?)?;
    Ok(())
}

#[pyfunction]
pub fn walk_bank(py: Python<'_>, fc: Py<PyAny>) -> PyResult<Py<PyAny>> {
    let params = fc.bind(py).getattr("m")?.get_item("parameters")?;
    let ordinary = params
        .call_method1("get", ("ordinary_view", PyDict::new(py)))?
        .call_method1("get", ("value",))?;
    if ordinary.extract::<Option<String>>()?.as_deref() == Some("same_forecast") {
        let mut w = NativeWalk::new(py, fc, py.None(), true)?;
        return w.run(py);
    }
    let helper = py
        .import("app.disputes.forecast")?
        .getattr("_BankWalk")?
        .call1((fc.bind(py),))?;
    let floor = st("cash_floor", "", "no");
    let out = st("cash_out", "", "no");
    let probes = PyDict::new(py);
    probes.set_item(
        "petition_cash_floor",
        steps_py(py, std::slice::from_ref(&floor))?,
    )?;
    probes.set_item(
        "petition_cash_out",
        steps_py(py, &[floor.clone(), out.clone()])?,
    )?;
    helper.setattr("probe", probes)?;
    if !helper
        .call_method1("inside", (steps_py(py, std::slice::from_ref(&floor))?,))?
        .extract::<bool>()?
    {
        helper.call_method1(
            "emit",
            (
                steps_py(py, &[])?,
                tuple_py(py, &Vec::<Edge>::new())?,
                "operating",
            ),
        )?;
        return Ok(helper.getattr("out")?.unbind());
    }
    let k: String = helper
        .call_method1("node", ("petition_cash_floor",))?
        .extract()?;
    helper.call_method1(
        "emit",
        (
            steps_py(py, &[st("cash_floor", "", "yes")])?,
            tuple_py(py, &[yes(&k)])?,
            "petition",
        ),
    )?;
    if !helper
        .call_method1("inside", (steps_py(py, &[floor.clone(), out.clone()])?,))?
        .extract::<bool>()?
    {
        helper.call_method1(
            "emit",
            (
                steps_py(py, &[floor])?,
                tuple_py(py, &[no(&k)])?,
                "operating",
            ),
        )?;
        return Ok(helper.getattr("out")?.unbind());
    }
    let k2: String = helper
        .call_method1("node", ("petition_cash_out", "cash_exhausted"))?
        .extract()?;
    helper.call_method1(
        "emit",
        (
            steps_py(py, &[floor.clone(), st("cash_out", "", "yes")])?,
            tuple_py(py, &[no(&k), yes(&k2)])?,
            "petition",
        ),
    )?;
    helper.call_method1(
        "emit",
        (
            steps_py(py, &[floor, out])?,
            tuple_py(py, &[no(&k), no(&k2)])?,
            "operating",
        ),
    )?;
    Ok(helper.getattr("out")?.unbind())
}

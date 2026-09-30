//! Exact deterministic path algebra. Inputs are converted once to typed Rust
//! records; masks, class partitioning and probability expressions run natively.
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyList, PyModule, PyString, PyTuple};
use pyo3::IntoPyObjectExt;
use std::collections::{BTreeMap, BTreeSet, HashMap};

pub(super) type Edge = (String, String);
pub(super) type Step = (String, String, String);
type Class = (String, Vec<String>, Option<Vec<u8>>);
type Path = (
    String,
    Vec<Step>,
    String,
    Vec<Edge>,
    String,
    Option<Vec<u8>>,
    Vec<Class>,
);
pub(super) type Conjunctions = Vec<Vec<Edge>>;

/// One conversion owns one intern table. Returned paths share immutable Python
/// strings and prefix items; dropping the table releases every temporary owner.
/// No process-wide interning or retained global memo is involved.
struct PythonPaths<'py> {
    py: Python<'py>,
    strings: HashMap<String, Py<PyString>>,
    steps: HashMap<Step, Py<PyAny>>,
    edges: HashMap<Edge, Py<PyAny>>,
}
impl<'py> PythonPaths<'py> {
    fn new(py: Python<'py>) -> Self {
        Self {
            py,
            strings: HashMap::new(),
            steps: HashMap::new(),
            edges: HashMap::new(),
        }
    }
    fn string(&mut self, value: &str) -> Py<PyAny> {
        if let Some(item) = self.strings.get(value) {
            return item.clone_ref(self.py).into_any();
        }
        let item = PyString::new(self.py, value).unbind();
        self.strings.insert(value.into(), item.clone_ref(self.py));
        item.into_any()
    }
    fn step(&mut self, value: &Step) -> PyResult<Py<PyAny>> {
        if let Some(item) = self.steps.get(value) {
            return Ok(item.clone_ref(self.py));
        }
        let items = [
            self.string(&value.0),
            self.string(&value.1),
            self.string(&value.2),
        ];
        let item = PyTuple::new(self.py, items)?.into_any().unbind();
        self.steps.insert(value.clone(), item.clone_ref(self.py));
        Ok(item)
    }
    fn edge(&mut self, value: &Edge) -> PyResult<Py<PyAny>> {
        if let Some(item) = self.edges.get(value) {
            return Ok(item.clone_ref(self.py));
        }
        let items = [self.string(&value.0), self.string(&value.1)];
        let item = PyTuple::new(self.py, items)?.into_any().unbind();
        self.edges.insert(value.clone(), item.clone_ref(self.py));
        Ok(item)
    }
    fn path(&mut self, value: &Path) -> PyResult<Py<PyAny>> {
        let instance = self.string(&value.0);
        let steps = PyList::new(
            self.py,
            value
                .1
                .iter()
                .map(|s| self.step(s))
                .collect::<PyResult<Vec<_>>>()?,
        )?;
        let outcome = self.string(&value.2);
        let edges = PyList::new(
            self.py,
            value
                .3
                .iter()
                .map(|e| self.edge(e))
                .collect::<PyResult<Vec<_>>>()?,
        )?;
        let class = self.string(&value.4);
        let mask = value.5.clone().into_py_any(self.py)?;
        let mut classes = Vec::with_capacity(value.6.len());
        for (key, tags, codes) in &value.6 {
            let key = self.string(key);
            let tags = PyList::new(
                self.py,
                tags.iter().map(|t| self.string(t)).collect::<Vec<_>>(),
            )?;
            classes.push((key, tags, codes.clone()).into_py_any(self.py)?);
        }
        (
            instance,
            steps,
            outcome,
            edges,
            class,
            mask,
            PyList::new(self.py, classes)?,
        )
            .into_py_any(self.py)
    }
}
fn paths_to_python(py: Python<'_>, paths: Vec<Path>) -> PyResult<Py<PyAny>> {
    let mut intern = PythonPaths::new(py);
    let output = PyList::empty(py);
    for path in paths {
        output.append(intern.path(&path)?)?;
    }
    Ok(output.into_any().unbind())
}

fn ascii_json_string(value: &str) -> String {
    let mut out = String::from("\"");
    for c in value.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\x08' => out.push_str("\\b"),
            '\x0c' => out.push_str("\\f"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (' '..='~').contains(&c) => out.push(c),
            c => {
                for u in c.encode_utf16(&mut [0; 2]) {
                    out.push_str(&format!("\\u{u:04x}"));
                }
            }
        }
    }
    out.push('"');
    out
}
pub(super) fn encode(parts: &[Vec<Edge>]) -> String {
    let cs: Vec<String> = parts
        .iter()
        .map(|c| {
            let es: Vec<String> = c
                .iter()
                .map(|(k, b)| format!("[{},{}]", ascii_json_string(k), ascii_json_string(b)))
                .collect();
            format!("[{}]", es.join(","))
        })
        .collect();
    format!("=[{}]", cs.join(","))
}
fn parse(key: &str) -> PyResult<Conjunctions> {
    serde_json::from_str(&key[1..])
        .map_err(|e| PyValueError::new_err(format!("invalid composite: {e}")))
}
fn complement(
    conj: &[Vec<Edge>],
    branches: &HashMap<String, Vec<String>>,
) -> PyResult<Conjunctions> {
    if conj.is_empty() {
        return Ok(vec![vec![]]);
    }
    if conj.iter().any(Vec::is_empty) {
        return Ok(vec![]);
    }
    let key = &conj[0][0].0;
    let br = branches
        .get(key)
        .ok_or_else(|| PyValueError::new_err(format!("missing branches for {key}")))?;
    let mut out = Vec::new();
    for b in br {
        let mut rest = Vec::new();
        for c in conj {
            // dict(c) takes the latest duplicate key, as the Python oracle does.
            let chosen = c.iter().rev().find(|(k, _)| k == key).map(|(_, v)| v);
            if chosen.is_none() || chosen == Some(b) {
                rest.push(c.iter().filter(|(k, _)| k != key).cloned().collect());
            }
        }
        if rest.is_empty() {
            out.push(vec![(key.clone(), b.clone())]);
        } else {
            for x in complement(&rest, branches)? {
                let mut row = vec![(key.clone(), b.clone())];
                row.extend(x);
                out.push(row);
            }
        }
    }
    Ok(out)
}
fn expand(edges: &[Edge], branches: &HashMap<String, Vec<String>>) -> PyResult<Conjunctions> {
    let mut out = vec![vec![]];
    for (k, b) in edges {
        let options = if k.starts_with('=') {
            let c = parse(k)?;
            if b == "yes" {
                c
            } else {
                complement(&c, branches)?
            }
        } else {
            vec![vec![(k.clone(), b.clone())]]
        };
        let mut next = Vec::new();
        for prefix in &out {
            for option in &options {
                let mut x = prefix.clone();
                x.extend(option.clone());
                next.push(x);
            }
        }
        out = next;
    }
    Ok(out)
}
fn counts(edges: &[Edge]) -> HashMap<Edge, usize> {
    let mut out = HashMap::new();
    for e in edges {
        *out.entry(e.clone()).or_default() += 1;
    }
    out
}
fn merge_group(
    paths: &[Path],
    group: &[usize],
    branches: &HashMap<String, Vec<String>>,
) -> PyResult<Path> {
    let first = &paths[group[0]];
    if group.len() == 1 {
        return Ok(first.clone());
    }
    let mut common = counts(&first.3);
    for &j in &group[1..] {
        let c = counts(&paths[j].3);
        common.retain(|k, v| {
            *v = (*v).min(*c.get(k).unwrap_or(&0));
            *v > 0
        });
    }
    let mut left = common.clone();
    let mut keep = Vec::new();
    for e in &first.3 {
        if let Some(v) = left.get_mut(e) {
            if *v > 0 {
                keep.push(e.clone());
                *v -= 1;
            }
        }
    }
    let mut conjunctions = Vec::new();
    let mut classes = BTreeMap::new();
    for &j in group {
        let mut seen = BTreeSet::new();
        let ct = counts(&paths[j].3);
        let mut rest = Vec::new();
        for e in &paths[j].3 {
            if seen.insert(e.clone()) {
                let n = ct[e] - common.get(e).copied().unwrap_or(0);
                for _ in 0..n {
                    rest.push(e.clone());
                }
            }
        }
        if rest.is_empty() {
            return Err(PyValueError::new_err(
                "a merged path's edges are a subset of another member's: the members are not disjoint",
            ));
        }
        conjunctions.extend(expand(&rest, branches)?);
        for c in &paths[j].6 {
            classes.insert(c.0.clone(), c.clone());
        }
    }
    keep.push((encode(&conjunctions), "yes".into()));
    let mut out = first.clone();
    out.3 = keep;
    out.6 = classes.into_values().collect();
    Ok(out)
}
#[pyfunction]
fn walk_composite(parts: Conjunctions) -> String {
    encode(&parts)
}
#[pyfunction]
fn walk_expand(edges: Vec<Edge>, branches: HashMap<String, Vec<String>>) -> PyResult<Conjunctions> {
    expand(&edges, &branches)
}
#[pyfunction]
fn walk_complement(
    parts: Conjunctions,
    branches: HashMap<String, Vec<String>>,
) -> PyResult<Conjunctions> {
    complement(&parts, &branches)
}
#[pyfunction]
fn walk_merge(
    py: Python<'_>,
    paths: Vec<Path>,
    groups: Vec<Vec<usize>>,
    branches: HashMap<String, Vec<String>>,
) -> PyResult<Py<PyAny>> {
    let mut out = Vec::new();
    for g in groups {
        if g.is_empty() || g.iter().any(|&j| j >= paths.len()) {
            return Err(PyValueError::new_err("invalid path group"));
        }
        let mut compatible: Vec<(HashMap<String, Class>, Vec<usize>)> = Vec::new();
        for j in g {
            let classes: HashMap<_, _> = paths[j]
                .6
                .iter()
                .map(|c| (c.0.clone(), c.clone()))
                .collect();
            if let Some((seen, members)) = compatible.iter_mut().find(|(seen, _)| {
                classes
                    .iter()
                    .all(|(k, c)| seen.get(k).is_none_or(|s| s == c))
            }) {
                seen.extend(classes);
                members.push(j);
            } else {
                compatible.push((classes, vec![j]));
            }
        }
        for (_, members) in compatible {
            out.push(merge_group(&paths, &members, &branches)?);
        }
    }
    paths_to_python(py, out)
}
fn unpack(mask: Option<&[u8]>, n: usize) -> PyResult<Vec<bool>> {
    match mask {
        None => Ok(vec![true; n]),
        Some(m) => {
            if m.len() * 8 < n {
                return Err(PyValueError::new_err(
                    "path mask is shorter than draw count",
                ));
            }
            Ok((0..n).map(|i| m[i / 8] & (1 << (7 - i % 8)) != 0).collect())
        }
    }
}
fn pack(mask: &[bool]) -> Vec<u8> {
    let mut out = vec![0; mask.len().div_ceil(8)];
    for (i, &v) in mask.iter().enumerate() {
        if v {
            out[i / 8] |= 1 << (7 - i % 8);
        }
    }
    out
}
fn rewrite(key: &str, to: &HashMap<String, String>) -> PyResult<String> {
    if !key.starts_with('=') {
        return Ok(to.get(key).cloned().unwrap_or_else(|| key.into()));
    }
    let mut parts = parse(key)?;
    for c in &mut parts {
        for (k, _) in c {
            if let Some(v) = to.get(k) {
                *k = v.clone();
            }
        }
    }
    Ok(encode(&parts))
}
#[pyfunction]
#[pyo3(signature=(paths, known, n, dead, first=None))]
fn walk_expand_classes(
    py: Python<'_>,
    paths: Vec<Path>,
    known: Vec<String>,
    n: usize,
    dead: Vec<String>,
    first: Option<HashMap<String, String>>,
) -> PyResult<Py<PyAny>> {
    let dead: BTreeSet<_> = dead.into_iter().collect();
    let mut known = known;
    known.sort();
    let supplied = first.is_some();
    let mut first: HashMap<String, String> = first.unwrap_or_default();
    if !supplied {
        for k in known {
            if let Some((base, _)) = k.split_once("|#") {
                let old = first.get(base);
                let old_dead = old.is_none_or(|s| dead.contains(s));
                if (!dead.contains(&k) || old_dead) && old_dead {
                    first.insert(base.into(), k);
                }
            }
        }
    }
    let mut out = Vec::new();
    for p in paths {
        if p.6.is_empty() {
            out.push(p);
            continue;
        }
        let mask = unpack(p.5.as_deref(), n)?;
        let on: Vec<_> = mask
            .iter()
            .enumerate()
            .filter_map(|(i, &v)| v.then_some(i))
            .collect();
        let mut fixed = HashMap::new();
        let mut cols: Vec<(String, Vec<String>, Vec<i64>)> = Vec::new();
        for (k, tags, codes) in &p.6 {
            let mut tags = tags.clone();
            let mut codes = codes
                .as_ref()
                .map(|a| a.iter().map(|&x| i64::from(x as i8)).collect::<Vec<_>>());
            if tags.iter().any(|t| dead.contains(&format!("{k}|{t}"))) {
                let mut c = codes.unwrap_or_else(|| vec![0; on.len()]);
                for x in &mut c {
                    if *x >= 0
                        && tags
                            .get(*x as usize)
                            .is_some_and(|t| dead.contains(&format!("{k}|{t}")))
                    {
                        *x = -1;
                    }
                }
                codes = Some(c);
            }
            if tags.is_empty() {
                if let Some(v) = first.get(k) {
                    fixed.insert(k.clone(), v.clone());
                }
            } else if codes.is_none() {
                fixed.insert(k.clone(), format!("{k}|{}", tags[0]));
            } else {
                let mut c = codes.ok_or_else(|| PyValueError::new_err("missing class codes"))?;
                if c.len() != on.len() {
                    return Err(PyValueError::new_err(
                        "class code length differs from path draws",
                    ));
                }
                if c.iter().any(|&x| x < 0) {
                    let first = first.get(k).ok_or_else(|| {
                        PyValueError::new_err(format!("missing first class for {k}"))
                    })?;
                    let t = first.rsplit('|').next().unwrap_or("");
                    let ix = match tags.iter().position(|v| v == t) {
                        Some(i) => i,
                        None => {
                            tags.push(t.into());
                            tags.len() - 1
                        }
                    };
                    for x in &mut c {
                        if *x < 0 {
                            *x = ix as i64;
                        }
                    }
                }
                if c.iter().any(|&x| x < 0 || x as usize >= tags.len()) {
                    return Err(PyValueError::new_err("invalid class index"));
                }
                cols.push((k.clone(), tags, c));
            }
        }
        if cols.is_empty() {
            let mut p = p;
            for (k, _) in &mut p.3 {
                *k = rewrite(k, &fixed)?;
            }
            p.6.clear();
            out.push(p);
            continue;
        }
        let mut groups: BTreeMap<Vec<i64>, Vec<usize>> = BTreeMap::new();
        for (j, &draw) in on.iter().enumerate() {
            groups
                .entry(cols.iter().map(|(_, _, c)| c[j]).collect())
                .or_default()
                .push(draw);
        }
        for (combo, draws) in groups {
            let mut to = fixed.clone();
            for ((k, tags, _), ix) in cols.iter().zip(combo) {
                to.insert(k.clone(), format!("{k}|{}", tags[ix as usize]));
            }
            let mut child = p.clone();
            for (k, _) in &mut child.3 {
                *k = rewrite(k, &to)?;
            }
            let mut m = vec![false; n];
            for i in draws {
                m[i] = true;
            }
            child.5 = Some(pack(&m));
            child.6.clear();
            out.push(child);
        }
    }
    paths_to_python(py, out)
}
#[pyfunction]
fn walk_class_entry(key: String, cls: Vec<String>, mask: Option<Vec<bool>>) -> PyResult<Class> {
    if mask.as_ref().is_some_and(|m| m.len() != cls.len()) {
        return Err(PyValueError::new_err("class mask shape differs"));
    }
    let values: Vec<_> = cls
        .into_iter()
        .enumerate()
        .filter_map(|(i, c)| mask.as_ref().is_none_or(|m| m[i]).then_some(c))
        .collect();
    let tags: Vec<_> = values
        .iter()
        .filter(|s| !s.is_empty())
        .cloned()
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect();
    if tags.len() == 1 && values.iter().all(|s| !s.is_empty()) {
        return Ok((key, tags, None));
    }
    // NumPy casts int indices to int8; preserve its bytes including wrapping.
    let codes = values
        .iter()
        .map(|c| {
            if c.is_empty() {
                255
            } else {
                tags.binary_search(c).map_or(255, |i| i as u8)
            }
        })
        .collect();
    Ok((key, tags, Some(codes)))
}
#[pyfunction]
fn walk_group_classes(cls: Option<Vec<String>>, groups: Vec<i8>) -> PyResult<Vec<String>> {
    if cls.as_ref().is_some_and(|c| c.len() != groups.len()) {
        return Err(PyValueError::new_err("group class shape differs"));
    }
    Ok(groups
        .iter()
        .enumerate()
        .map(|(i, &g)| {
            if g < 0 {
                String::new()
            } else {
                let base = cls
                    .as_ref()
                    .and_then(|c| (!c[i].is_empty()).then_some(c[i].as_str()))
                    .unwrap_or("#-");
                format!("{base}.g{g}")
            }
        })
        .collect())
}
pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(walk_composite, m)?)?;
    m.add_function(wrap_pyfunction!(walk_complement, m)?)?;
    m.add_function(wrap_pyfunction!(walk_expand, m)?)?;
    m.add_function(wrap_pyfunction!(walk_merge, m)?)?;
    m.add_function(wrap_pyfunction!(walk_expand_classes, m)?)?;
    m.add_function(wrap_pyfunction!(walk_class_entry, m)?)?;
    m.add_function(wrap_pyfunction!(walk_group_classes, m)?)?;
    Ok(())
}

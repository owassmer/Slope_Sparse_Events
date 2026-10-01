//! Exact deterministic path algebra. Histories stay shared Python references;
//! only the current group's edge and class controls are represented as native IDs.
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList, PyModule, PySet, PyString, PyTuple};
use pyo3::IntoPyObjectExt;
use std::collections::{BTreeMap, BTreeSet, HashMap};

pub(super) type Edge = (String, String);
pub(super) type Step = (String, String, String);
type Class = (String, Vec<String>, Option<Vec<u8>>);
pub(super) type Conjunctions = Vec<Vec<Edge>>;
type EdgeId = (usize, usize);
type IdConjunctions = Vec<Vec<EdgeId>>;

/// Bucket keys reuse CPython's cached string hash. Exact comparison resolves
/// collisions, and each entry retains an immutable Python string rather than a
/// second owned copy of its potentially very large composite payload.
#[derive(Default)]
struct TextPool<'py> {
    strings: Vec<Bound<'py, PyString>>,
    buckets: HashMap<isize, Vec<usize>>,
}
impl<'py> TextPool<'py> {
    fn intern(&mut self, value: Bound<'py, PyString>) -> PyResult<usize> {
        // String subclasses can override hash/equality; the previous typed
        // extraction compared their text. Keep that contract without invoking
        // an arbitrary subclass equality callback in the native algebra.
        let hash = if value.is_exact_instance_of::<PyString>() {
            value.hash()?
        } else {
            PyString::new(value.py(), value.to_str()?).hash()?
        };
        if let Some(bucket) = self.buckets.get(&hash) {
            for &id in bucket {
                if self.string(id)?.is(&value) || self.text(id)? == value.to_str()? {
                    return Ok(id);
                }
            }
        }
        let id = self.strings.len();
        self.strings.push(value);
        self.buckets.entry(hash).or_default().push(id);
        Ok(id)
    }
    fn new_string(&mut self, py: Python<'py>, value: &str) -> PyResult<usize> {
        self.intern(PyString::new(py, value))
    }
    fn string(&self, id: usize) -> PyResult<&Bound<'py, PyString>> {
        self.strings
            .get(id)
            .ok_or_else(|| PyValueError::new_err("invalid native string ID"))
    }
    fn text(&self, id: usize) -> PyResult<&str> {
        self.string(id)?.to_str()
    }
}

struct WireEdge<'py> {
    row: Bound<'py, PyAny>,
    id: EdgeId,
}
struct WireClass<'py> {
    row: Bound<'py, PyAny>,
    key: usize,
    tags: Vec<usize>,
    codes: Option<Vec<u8>>,
}
#[derive(Clone, PartialEq, Eq)]
struct ClassSignature {
    tags: Vec<usize>,
    codes: Option<Vec<u8>>,
}
struct WirePath<'py> {
    row: Bound<'py, PyTuple>,
    edges: Vec<WireEdge<'py>>,
    classes: Vec<WireClass<'py>>,
}
fn wire_path<'py>(row: Bound<'py, PyAny>, pool: &mut TextPool<'py>) -> PyResult<WirePath<'py>> {
    let row = row.cast_into::<PyTuple>()?;
    if row.len() != 7 {
        return Err(PyValueError::new_err("path must have seven fields"));
    }
    // History fields are never traversed or copied: the original path keeps
    // them alive, and every output retains their original immutable prefixes.
    for ix in [0, 2, 4] {
        row.get_item(ix)?.cast::<PyString>()?;
    }
    let mut edges = Vec::new();
    for item in row.get_item(3)?.try_iter()? {
        let item = item?;
        if item.len()? != 2 {
            return Err(PyValueError::new_err("edge must have two fields"));
        }
        let key = pool.intern(item.get_item(0)?.cast_into::<PyString>()?)?;
        let branch = pool.intern(item.get_item(1)?.cast_into::<PyString>()?)?;
        edges.push(WireEdge {
            row: item,
            id: (key, branch),
        });
    }
    let mut classes = Vec::new();
    for item in row.get_item(6)?.try_iter()? {
        let item = item?;
        if item.len()? != 3 {
            return Err(PyValueError::new_err("class must have three fields"));
        }
        let key = pool.intern(item.get_item(0)?.cast_into::<PyString>()?)?;
        let mut tags = Vec::new();
        for tag in item.get_item(1)?.try_iter()? {
            tags.push(pool.intern(tag?.cast_into::<PyString>()?)?);
        }
        let codes = item.get_item(2)?.extract::<Option<Vec<u8>>>()?;
        classes.push(WireClass {
            row: item,
            key,
            tags,
            codes,
        });
    }
    Ok(WirePath {
        row,
        edges,
        classes,
    })
}
fn wire_output<'py>(
    py: Python<'py>,
    path: &WirePath<'py>,
    edges: Bound<'py, PyList>,
    mask: Bound<'py, PyAny>,
    classes: Bound<'py, PyList>,
) -> PyResult<Bound<'py, PyTuple>> {
    PyTuple::new(
        py,
        [
            path.row.get_item(0)?,
            path.row.get_item(1)?,
            path.row.get_item(2)?,
            edges.into_any(),
            path.row.get_item(4)?,
            mask,
            classes.into_any(),
        ],
    )
}

fn push_ascii_json_string(out: &mut String, value: &str) {
    use std::fmt::Write;
    out.push('"');
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
                    let _ = write!(out, "\\u{u:04x}");
                }
            }
        }
    }
    out.push('"');
}
fn ascii_json_string(value: &str) -> String {
    let mut out = String::new();
    push_ascii_json_string(&mut out, value);
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
fn id_counts(edges: &[WireEdge<'_>]) -> HashMap<EdgeId, usize> {
    let mut out = HashMap::new();
    for e in edges {
        *out.entry(e.id).or_default() += 1;
    }
    out
}
fn id_parse(
    key: usize,
    pool: &mut TextPool<'_>,
    cache: &mut HashMap<usize, IdConjunctions>,
) -> PyResult<IdConjunctions> {
    if let Some(parts) = cache.get(&key) {
        return Ok(parts.clone());
    }
    let parsed = parse(pool.text(key)?)?;
    let py = pool.string(key)?.py();
    let mut parts = Vec::with_capacity(parsed.len());
    for conjunction in parsed {
        let mut row = Vec::with_capacity(conjunction.len());
        for (key, branch) in conjunction {
            row.push((pool.new_string(py, &key)?, pool.new_string(py, &branch)?));
        }
        parts.push(row);
    }
    cache.insert(key, parts.clone());
    Ok(parts)
}
fn id_complement<'py>(
    parts: &[Vec<EdgeId>],
    pool: &mut TextPool<'py>,
    branches: &Bound<'py, PyDict>,
) -> PyResult<IdConjunctions> {
    if parts.is_empty() {
        return Ok(vec![vec![]]);
    }
    if parts.iter().any(Vec::is_empty) {
        return Ok(vec![]);
    }
    let key = parts[0][0].0;
    let options = branches.get_item(pool.string(key)?)?.ok_or_else(|| {
        PyValueError::new_err(format!(
            "missing branches for {}",
            pool.text(key).unwrap_or("<invalid>")
        ))
    })?;
    let mut out = Vec::new();
    for branch in options.try_iter()? {
        let branch = pool.intern(branch?.cast_into::<PyString>()?)?;
        let mut rest = Vec::new();
        for conjunction in parts {
            let chosen = conjunction.iter().rev().find(|e| e.0 == key).map(|e| e.1);
            if chosen.is_none() || chosen == Some(branch) {
                rest.push(conjunction.iter().filter(|e| e.0 != key).copied().collect());
            }
        }
        if rest.is_empty() {
            out.push(vec![(key, branch)]);
        } else {
            for tail in id_complement(&rest, pool, branches)? {
                let mut row = vec![(key, branch)];
                row.extend(tail);
                out.push(row);
            }
        }
    }
    Ok(out)
}
fn id_expand<'py>(
    edges: &[EdgeId],
    pool: &mut TextPool<'py>,
    cache: &mut HashMap<usize, IdConjunctions>,
    branches: &Bound<'py, PyDict>,
) -> PyResult<IdConjunctions> {
    let mut out = vec![vec![]];
    for &(key, branch) in edges {
        let options = if pool.text(key)?.starts_with('=') {
            let parts = id_parse(key, pool, cache)?;
            if pool.text(branch)? == "yes" {
                parts
            } else {
                id_complement(&parts, pool, branches)?
            }
        } else {
            vec![vec![(key, branch)]]
        };
        let mut next = Vec::new();
        for prefix in &out {
            for option in &options {
                let mut row = Vec::with_capacity(prefix.len() + option.len());
                row.extend_from_slice(prefix);
                row.extend_from_slice(option);
                next.push(row);
            }
        }
        out = next;
    }
    Ok(out)
}
fn encode_ids(parts: &[Vec<EdgeId>], pool: &TextPool<'_>) -> PyResult<String> {
    let mut out = String::from("=[");
    for (i, conjunction) in parts.iter().enumerate() {
        if i > 0 {
            out.push(',');
        }
        out.push('[');
        for (j, &(key, branch)) in conjunction.iter().enumerate() {
            if j > 0 {
                out.push(',');
            }
            out.push('[');
            push_ascii_json_string(&mut out, pool.text(key)?);
            out.push(',');
            push_ascii_json_string(&mut out, pool.text(branch)?);
            out.push(']');
        }
        out.push(']');
    }
    out.push(']');
    Ok(out)
}
fn merged_group<'py>(
    py: Python<'py>,
    paths: &[WirePath<'py>],
    members: &[usize],
    pool: &mut TextPool<'py>,
    generated: &mut TextPool<'py>,
    branches: &Bound<'py, PyDict>,
) -> PyResult<Bound<'py, PyAny>> {
    let first = &paths[members[0]];
    if members.len() == 1 {
        return Ok(first.row.clone().into_any());
    }
    let mut common = id_counts(&first.edges);
    for &j in &members[1..] {
        let counts = id_counts(&paths[j].edges);
        common.retain(|key, count| {
            *count = (*count).min(counts.get(key).copied().unwrap_or(0));
            *count > 0
        });
    }
    let edges = PyList::empty(py);
    let mut left = common.clone();
    for edge in &first.edges {
        if let Some(n) = left.get_mut(&edge.id) {
            if *n > 0 {
                edges.append(&edge.row)?;
                *n -= 1;
            }
        }
    }
    let mut conjunctions = Vec::new();
    let mut cache = HashMap::new();
    let mut classes = HashMap::new();
    for &j in members {
        let path = &paths[j];
        let mut seen = BTreeSet::new();
        let counts = id_counts(&path.edges);
        let mut rest = Vec::new();
        // Counter subtraction follows first occurrence, including repetitions.
        for edge in &path.edges {
            if seen.insert(edge.id) {
                let n = counts.get(&edge.id).copied().unwrap_or(0)
                    - common.get(&edge.id).copied().unwrap_or(0);
                rest.extend(std::iter::repeat_n(edge.id, n));
            }
        }
        if rest.is_empty() {
            return Err(PyValueError::new_err("a merged path's edges are a subset of another member's: the members are not disjoint"));
        }
        conjunctions.extend(id_expand(&rest, pool, &mut cache, branches)?);
        for class in &path.classes {
            classes.insert(class.key, class.row.clone());
        }
    }
    let encoded = encode_ids(&conjunctions, pool)?;
    let key = generated.new_string(py, &encoded)?;
    edges.append(PyTuple::new(
        py,
        [
            generated.string(key)?.as_any().clone(),
            PyString::new(py, "yes").into_any(),
        ],
    )?)?;
    let mut class_keys = classes.keys().copied().collect::<Vec<_>>();
    // IDs encode encounter order, never Python's lexical class order.
    let mut keyed = class_keys
        .drain(..)
        .map(|id| Ok((pool.text(id)?, id)))
        .collect::<PyResult<Vec<_>>>()?;
    keyed.sort_by(|a, b| a.0.cmp(b.0));
    let class_rows = PyList::new(py, keyed.into_iter().map(|(_, id)| &classes[&id]))?;
    Ok(wire_output(py, first, edges, first.row.get_item(5)?, class_rows)?.into_any())
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
fn walk_merge<'py>(
    py: Python<'py>,
    paths: &Bound<'py, PyAny>,
    groups: Vec<Vec<usize>>,
    branches: &Bound<'py, PyDict>,
) -> PyResult<Py<PyAny>> {
    let count = paths.len()?;
    let out = PyList::empty(py);
    let mut generated = TextPool::default();
    for group in groups {
        if group.is_empty() || group.iter().any(|&j| j >= count) {
            return Err(PyValueError::new_err("invalid path group"));
        }
        let mut pool = TextPool::default();
        let mut current = Vec::with_capacity(group.len());
        let mut compatible: Vec<(HashMap<usize, ClassSignature>, Vec<usize>)> = Vec::new();
        for index in group {
            let path = wire_path(paths.get_item(index)?, &mut pool)?;
            let signatures: HashMap<_, _> = path
                .classes
                .iter()
                .map(|c| {
                    (
                        c.key,
                        ClassSignature {
                            tags: c.tags.clone(),
                            codes: c.codes.clone(),
                        },
                    )
                })
                .collect();
            let j = current.len();
            current.push(path);
            if let Some((seen, members)) = compatible.iter_mut().find(|(seen, _)| {
                signatures
                    .iter()
                    .all(|(k, c)| seen.get(k).is_none_or(|s| s == c))
            }) {
                seen.extend(signatures);
                members.push(j);
            } else {
                compatible.push((signatures, vec![j]));
            }
        }
        for (_, members) in compatible {
            out.append(merged_group(
                py,
                &current,
                &members,
                &mut pool,
                &mut generated,
                branches,
            )?)?;
        }
    }
    Ok(out.into_any().unbind())
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
fn joined_class<'py>(
    py: Python<'py>,
    key: usize,
    tag: usize,
    pool: &mut TextPool<'py>,
    generated: &mut TextPool<'py>,
) -> PyResult<usize> {
    let joined = format!("{}|{}", pool.text(key)?, pool.text(tag)?);
    let id = generated.new_string(py, &joined)?;
    pool.intern(generated.string(id)?.clone())
}
fn rewritten_edges<'py>(
    py: Python<'py>,
    path: &WirePath<'py>,
    to: &HashMap<usize, usize>,
    pool: &mut TextPool<'py>,
    generated: &mut TextPool<'py>,
    cache: &mut HashMap<usize, IdConjunctions>,
) -> PyResult<Bound<'py, PyList>> {
    let out = PyList::empty(py);
    for edge in &path.edges {
        let key = edge.id.0;
        let replaced = if pool.text(key)?.starts_with('=') {
            let mut parts = id_parse(key, pool, cache)?;
            for conjunction in &mut parts {
                for atom in conjunction {
                    if let Some(&v) = to.get(&atom.0) {
                        atom.0 = v;
                    }
                }
            }
            let encoded = encode_ids(&parts, pool)?;
            let id = generated.new_string(py, &encoded)?;
            pool.intern(generated.string(id)?.clone())?
        } else {
            to.get(&key).copied().unwrap_or(key)
        };
        if replaced == key {
            out.append(&edge.row)?;
        } else {
            out.append(PyTuple::new(
                py,
                [
                    pool.string(replaced)?.as_any().clone(),
                    edge.row.get_item(1)?,
                ],
            )?)?;
        }
    }
    Ok(out)
}
#[pyfunction]
#[pyo3(signature=(paths, known, n, dead, first=None))]
fn walk_expand_classes<'py>(
    py: Python<'py>,
    paths: &Bound<'py, PyAny>,
    known: &Bound<'py, PyAny>,
    n: usize,
    dead: &Bound<'py, PyAny>,
    first: Option<&Bound<'py, PyDict>>,
) -> PyResult<Py<PyAny>> {
    let dead_set = PySet::empty(py)?;
    for key in dead.try_iter()? {
        dead_set.add(key?.cast::<PyString>()?)?;
    }
    let computed;
    let first = if let Some(first) = first {
        first
    } else {
        computed = PyDict::new(py);
        let mut pool = TextPool::default();
        for key in known.try_iter()? {
            pool.intern(key?.cast_into::<PyString>()?)?;
        }
        let mut sorted = (0..pool.strings.len())
            .map(|id| Ok((pool.text(id)?, id)))
            .collect::<PyResult<Vec<_>>>()?;
        sorted.sort_by(|a, b| a.0.cmp(b.0));
        for (key, id) in sorted {
            if let Some((base, _)) = key.split_once("|#") {
                let base = PyString::new(py, base);
                let old = computed.get_item(&base)?;
                let old_dead = match &old {
                    Some(value) => dead_set.contains(value)?,
                    None => true,
                };
                if old_dead {
                    computed.set_item(base, pool.string(id)?)?;
                }
            }
        }
        &computed
    };
    let out = PyList::empty(py);
    // This pool owns only strings actually created for output. Original strings
    // are retained by the input or a current-path pool, never copied wholesale.
    let mut generated = TextPool::default();
    for item in paths.try_iter()? {
        let mut pool = TextPool::default();
        let path = wire_path(item?, &mut pool)?;
        if path.classes.is_empty() {
            out.append(&path.row)?;
            continue;
        }
        let bytes = path.row.get_item(5)?.extract::<Option<Vec<u8>>>()?;
        let mask = unpack(bytes.as_deref(), n)?;
        let on = mask
            .iter()
            .enumerate()
            .filter_map(|(i, &v)| v.then_some(i))
            .collect::<Vec<_>>();
        let mut fixed = HashMap::new();
        let mut cols = Vec::new();
        for class in &path.classes {
            let key = class.key;
            let mut tags = class.tags.clone();
            let mut codes = class
                .codes
                .as_ref()
                .map(|a| a.iter().map(|&x| i64::from(x as i8)).collect::<Vec<_>>());
            let mut dead_tags = Vec::with_capacity(tags.len());
            for &tag in &tags {
                let joined = format!("{}|{}", pool.text(key)?, pool.text(tag)?);
                dead_tags.push(dead_set.contains(PyString::new(py, &joined))?);
            }
            if dead_tags.iter().any(|&v| v) {
                let mut c = codes.unwrap_or_else(|| vec![0; on.len()]);
                for x in &mut c {
                    if *x >= 0 && dead_tags.get(*x as usize).copied().unwrap_or(false) {
                        *x = -1;
                    }
                }
                codes = Some(c);
            }
            if tags.is_empty() {
                if let Some(value) = first.get_item(pool.string(key)?)? {
                    fixed.insert(key, pool.intern(value.cast_into::<PyString>()?)?);
                }
            } else if codes.is_none() {
                fixed.insert(
                    key,
                    joined_class(py, key, tags[0], &mut pool, &mut generated)?,
                );
            } else {
                let mut codes =
                    codes.ok_or_else(|| PyValueError::new_err("missing class codes"))?;
                if codes.len() != on.len() {
                    return Err(PyValueError::new_err(
                        "class code length differs from path draws",
                    ));
                }
                if codes.iter().any(|&v| v < 0) {
                    let first_value = first.get_item(pool.string(key)?)?.ok_or_else(|| {
                        PyValueError::new_err(format!(
                            "missing first class for {}",
                            pool.text(key).unwrap_or("<invalid>")
                        ))
                    })?;
                    let first_value = first_value.cast_into::<PyString>()?;
                    let tag = first_value.to_str()?.rsplit('|').next().unwrap_or("");
                    let tag = pool.new_string(py, tag)?;
                    let ix = if let Some(ix) = tags.iter().position(|&v| v == tag) {
                        ix
                    } else {
                        tags.push(tag);
                        tags.len() - 1
                    };
                    for v in &mut codes {
                        if *v < 0 {
                            *v = ix as i64;
                        }
                    }
                }
                if codes.iter().any(|&v| v < 0 || v as usize >= tags.len()) {
                    return Err(PyValueError::new_err("invalid class index"));
                }
                cols.push((key, tags, codes));
            }
        }
        let mut cache = HashMap::new();
        if cols.is_empty() {
            let edges = rewritten_edges(py, &path, &fixed, &mut pool, &mut generated, &mut cache)?;
            out.append(wire_output(
                py,
                &path,
                edges,
                path.row.get_item(5)?,
                PyList::empty(py),
            )?)?;
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
            for ((key, tags, _), ix) in cols.iter().zip(combo) {
                to.insert(
                    *key,
                    joined_class(py, *key, tags[ix as usize], &mut pool, &mut generated)?,
                );
            }
            let edges = rewritten_edges(py, &path, &to, &mut pool, &mut generated, &mut cache)?;
            let mut mask = vec![false; n];
            for i in draws {
                mask[i] = true;
            }
            let packed = pack(&mask).into_py_any(py)?;
            out.append(wire_output(
                py,
                &path,
                edges,
                packed.into_bound(py),
                PyList::empty(py),
            )?)?;
        }
    }
    Ok(out.into_any().unbind())
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

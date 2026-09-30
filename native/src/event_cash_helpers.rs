//! Cash queries and dated decision booking for the native event state.
//! Calls made through `NativeChain::invoke` stay inside Rust semantic dispatch.
use crate::cash::{self, LineInput};
use crate::events::{NativeChain, BIG};
use ndarray::{Array1, Array2, Array3};
use numpy::{IntoPyArray, PyArray1, PyArray2, PyArray3, PyArrayMethods};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyBytes, PyDict, PyList, PyModule, PyString, PyTuple};

type Obj = Py<PyAny>;
type ResultObj = PyResult<Obj>;

fn array(py: Python<'_>, value: Array1<i64>) -> Obj {
    value.into_pyarray(py).into_any().unbind()
}
fn boolean(py: Python<'_>, value: bool) -> Obj {
    PyBool::new(py, value).to_owned().into_any().unbind()
}
fn arg<'py>(args: &Bound<'py, PyTuple>, i: usize) -> PyResult<Bound<'py, PyAny>> {
    args.get_item(i)
}
fn optional<'py>(args: &Bound<'py, PyTuple>, i: usize) -> Option<Bound<'py, PyAny>> {
    args.get_item(i).ok().filter(|x| !x.is_none())
}
fn n(chain: &NativeChain, py: Python<'_>) -> PyResult<usize> {
    chain.get(py, "n")?.extract()
}
fn days(chain: &NativeChain, py: Python<'_>) -> PyResult<usize> {
    chain.get(py, "N")?.extract()
}
fn flag(chain: &NativeChain, py: Python<'_>, key: &str) -> PyResult<bool> {
    chain.get(py, key)?.is_truthy()
}
fn num(chain: &NativeChain, py: Python<'_>, key: &str) -> PyResult<i64> {
    chain.get(py, key)?.extract()
}
fn field1(chain: &NativeChain, py: Python<'_>, key: &str) -> PyResult<Array1<i64>> {
    chain.per_draw(py, &chain.get(py, key)?)
}
fn per_draw(
    chain: &NativeChain,
    py: Python<'_>,
    value: &Bound<'_, PyAny>,
) -> PyResult<Array1<i64>> {
    if let Ok(v) = value.extract::<i64>() {
        return Ok(Array1::from_elem(n(chain, py)?, v));
    }
    let arr = value.cast::<PyArray1<i64>>()?.readonly();
    if arr.len()? != n(chain, py)? {
        return Err(PyValueError::new_err(
            "dated decision must match draw count",
        ));
    }
    Ok(arr.as_array().to_owned())
}
fn call(chain: &NativeChain, py: Python<'_>, method: &str, values: Vec<Obj>) -> ResultObj {
    chain.invoke(py, method, &PyTuple::new(py, values)?)
}
fn call0(chain: &NativeChain, py: Python<'_>, method: &str) -> ResultObj {
    call(chain, py, method, vec![])
}
fn call1(chain: &NativeChain, py: Python<'_>, method: &str, day: &Array1<i64>) -> ResultObj {
    call(chain, py, method, vec![array(py, day.clone())])
}
fn result1(value: &Obj, py: Python<'_>) -> PyResult<Array1<i64>> {
    Ok(value
        .bind(py)
        .cast::<PyArray1<i64>>()?
        .readonly()
        .as_array()
        .to_owned())
}
fn result_bool(value: &Obj, py: Python<'_>) -> PyResult<Array1<bool>> {
    Ok(value
        .bind(py)
        .cast::<PyArray1<bool>>()?
        .readonly()
        .as_array()
        .to_owned())
}
fn str_obj(py: Python<'_>, value: &str) -> Obj {
    PyString::new(py, value).into_any().unbind()
}
fn memo(chain: &NativeChain, py: Python<'_>, name: &str) -> PyResult<Option<Obj>> {
    let Some(value) = chain.state.bind(py).get_item(name)? else {
        return Ok(None);
    };
    if value.is_none() {
        return Ok(None);
    }
    let value = value.cast::<PyTuple>()?;
    if value.get_item(0)?.extract::<i64>()? == num(chain, py, "_cv")? {
        Ok(Some(value.get_item(1)?.unbind()))
    } else {
        Ok(None)
    }
}
fn cache(chain: &NativeChain, py: Python<'_>, name: &str, value: Obj) -> ResultObj {
    let version = num(chain, py, "_cv")?
        .into_pyobject(py)?
        .into_any()
        .unbind();
    chain
        .state
        .bind(py)
        .set_item(name, PyTuple::new(py, [version, value.clone_ref(py)])?)?;
    Ok(value)
}
fn param(chain: &NativeChain, py: Python<'_>, key: &str) -> ResultObj {
    call(chain, py, "p", vec![str_obj(py, key)])
}
fn mark(
    chain: &NativeChain,
    py: Python<'_>,
    name: &str,
    day: &Array1<i64>,
    on: &Array1<bool>,
) -> ResultObj {
    call(
        chain,
        py,
        "mark",
        vec![
            str_obj(py, name),
            array(py, day.clone()),
            on.clone().into_pyarray(py).into_any().unbind(),
        ],
    )
}
fn mark_array(chain: &NativeChain, py: Python<'_>, name: &str) -> PyResult<Array1<i64>> {
    Ok(chain
        .get(py, "marks")?
        .get_item(name)?
        .cast::<PyArray1<i64>>()?
        .readonly()
        .as_array()
        .to_owned())
}
fn is_floor(node: &str) -> bool {
    matches!(node, "cash_floor" | "cash_out")
}
fn is_distress(node: &str) -> bool {
    matches!(node, "cash_floor" | "cash_out" | "offering" | "nonpayment")
}
fn is_response(node: &str) -> bool {
    matches!(node, "debtor_response" | "judgment_response")
}

fn terms(chain: &NativeChain, py: Python<'_>) -> PyResult<(i64, i64)> {
    let model = chain.get(py, "m")?;
    let sens = chain.get(py, "sens")?;
    let sens = sens.cast::<PyDict>()?;
    let mut values = [0i64; 2];
    for (i, key) in ["nonpayment_window_days", "nonpayment_unpaid_share_bps"]
        .iter()
        .enumerate()
    {
        let p = model.get_item("parameters")?.get_item(key)?;
        let pick = sens.get_item(key)?;
        let value = if let Some(pick) = pick {
            if pick.is_instance_of::<pyo3::types::PyBool>() {
                p.get_item(if pick.is_truthy()? {
                    "sensitivity"
                } else {
                    "value"
                })?
            } else {
                pick
            }
        } else {
            p.get_item("value")?
        };
        if value.is_instance_of::<PyList>() {
            return Err(PyValueError::new_err(format!(
                "{key}: name the sensitivity by its value, one of {value}"
            )));
        }
        values[i] = value.extract()?;
    }
    Ok((values[0], values[1]))
}

fn process_key(chain: &NativeChain, py: Python<'_>, daily: bool) -> ResultObj {
    let names = if daily {
        vec![
            "lock",
            "petition",
            "k:inflow",
            "k:levy",
            "k:reduction",
            "k:settlement",
            "k:notes_interest",
            "k:judgment",
            "i:settlement",
            "i:notes_interest",
            "i:judgment",
        ]
    } else {
        vec!["cash", "lock", "capacity", "petition"]
    };
    let head = if daily {
        let (w, s) = terms(chain, py)?;
        let mut b = Vec::with_capacity(16);
        b.extend(w.to_ne_bytes());
        b.extend(s.to_ne_bytes());
        b
    } else {
        Vec::new()
    };
    call(
        chain,
        py,
        "_run_key",
        vec![
            str_obj(py, if daily { "daily" } else { "net" }),
            PyTuple::new(py, names)?.into_any().unbind(),
            PyBytes::new(py, &head).into_any().unbind(),
        ],
    )
}

fn arrears_runs<'py>(chain: &NativeChain, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
    let basis = chain.get(py, "basis")?;
    let dict = basis.getattr("__dict__")?;
    let dict = dict.cast::<PyDict>()?;
    if let Some(ar) = dict.get_item("arrears_runs")? {
        return Ok(ar.cast_into::<PyDict>()?);
    }
    let ar = PyDict::new(py);
    dict.set_item("arrears_runs", &ar)?;
    Ok(ar)
}

fn process(chain: &NativeChain, py: Python<'_>, daily: bool) -> ResultObj {
    process_inner(chain, py, daily, false)
}

fn process_inner(chain: &NativeChain, py: Python<'_>, daily: bool, force: bool) -> ResultObj {
    let key = process_key(chain, py, daily)?;
    let basis = chain.get(py, "basis")?;
    let runs = basis.getattr("runs")?;
    // These functions manage shared storage and its byte budget, not execution semantics.
    let storage = PyModule::import(py, "app.analysis.events")?;
    if !force {
        let got = storage.getattr("runs_get")?.call1((&runs, key.bind(py)))?;
        if !got.is_none() {
            return Ok(got.unbind());
        }
    }
    let (w, share) = if daily { terms(chain, py)? } else { (0, 0) };
    let geometry = chain.get(py, "_kernel_line")?;
    let geometry = geometry.cast::<PyTuple>()?;
    let need_b = geometry.get_item(0)?;
    let limit_b = geometry.get_item(1)?;
    let month_b = geometry.get_item(2)?;
    let routes_b = geometry.get_item(3)?;
    let dueidx_b = geometry.get_item(4)?;
    let due0_b = geometry.get_item(5)?;
    let bookd_b = geometry.get_item(6)?;
    let booka_b = geometry.get_item(7)?;
    let need_r = need_b.cast::<PyArray2<i64>>()?.readonly();
    let limit_r = limit_b.cast::<PyArray2<i64>>()?.readonly();
    let month_r = month_b.cast::<PyArray1<bool>>()?.readonly();
    let routes_r = routes_b.cast::<PyArray3<i64>>()?.readonly();
    let dueidx_r = dueidx_b.cast::<PyArray2<i64>>()?.readonly();
    let due0_r = due0_b.cast::<PyArray1<i64>>()?.readonly();
    let bookd_r = bookd_b.cast::<PyArray1<i64>>()?.readonly();
    let booka_r = booka_b.cast::<PyArray1<i64>>()?.readonly();
    let s = chain.get(py, "s")?;
    let ex = s.getattr("exposure")?;
    let line = LineInput {
        need: need_r.as_array(),
        limit: limit_r.as_array(),
        month_end: month_r.as_array(),
        routes: routes_r.as_array(),
        due_idx: dueidx_r.as_array(),
        due0: due0_r.as_array(),
        book_d: bookd_r.as_array(),
        book_a: booka_r.as_array(),
        fee_bps: s.getattr("fee_bps")?.extract()?,
        inst: s.getattr("installments")?.extract()?,
        debit: s.getattr("collection")?.extract::<String>()? == "debit",
        opening: basis.getattr("opening")?.extract()?,
        funded0: ex.getattr("principal_cents")?.extract()?,
        contract0: ex.getattr("owed_cents")?.extract()?,
    };
    let ev = chain.get(py, "ev")?;
    let pet_b = ev.getattr("petition")?;
    let pet_r = pet_b.cast::<PyArray1<i64>>()?.readonly();
    let days = days(chain, py)?;
    let n = n(chain, py)?;
    let pet = Array1::from_iter(pet_r.as_array().iter().map(|&d| {
        if d >= 0 && d < days as i64 {
            d
        } else {
            days as i64
        }
    }));
    cash::validate(&line, n, days, pet.view())?;
    let lock_b = ev.getattr("lock")?;
    let lock_r = lock_b.cast::<PyArray2<i64>>()?.readonly();
    if lock_r.as_array().dim() != (n, days) {
        return Err(PyValueError::new_err(
            "event lock shape must match the cash horizon",
        ));
    }
    let draw_cap = geometry.get_item(8)?.extract()?;
    let output = if daily {
        let kinds = ev.getattr("kinds")?;
        let inflow_b = kinds.get_item("inflow")?;
        let levy_b = kinds.get_item("levy")?;
        let reduction_b = kinds.get_item("reduction")?;
        let inflow_r = inflow_b.cast::<PyArray2<i64>>()?.readonly();
        let levy_r = levy_b.cast::<PyArray2<i64>>()?.readonly();
        let reduction_r = reduction_b.cast::<PyArray2<i64>>()?.readonly();
        let oi_b = chain.get(py, "_ops_inflow")?;
        let oo_b = chain.get(py, "_ops_outflow")?;
        let oi_r = oi_b.cast::<PyArray2<i64>>()?.readonly();
        let oo_r = oo_b.cast::<PyArray2<i64>>()?.readonly();
        if [
            oi_r.as_array().dim(),
            oo_r.as_array().dim(),
            inflow_r.as_array().dim(),
            levy_r.as_array().dim(),
            reduction_r.as_array().dim(),
        ]
        .iter()
        .any(|&d| d != (n, days))
        {
            return Err(PyValueError::new_err(
                "daily operating and event cash shapes must match the cash horizon",
            ));
        }
        if inflow_r.as_array().iter().any(|&v| v < 0) || levy_r.as_array().iter().any(|&v| v > 0) {
            return Err(PyValueError::new_err("event cash of the wrong sign for its kind (a receipt paid out or an obligation received)"));
        }
        let mut post = Array2::zeros((n, days));
        let mut levy = Array2::zeros((n, days));
        let mut out = Array2::zeros((n, days));
        let mut obl = Array3::zeros((3, n, days));
        let mut inc = Array2::zeros((3, n));
        for (c, key) in ["settlement", "notes_interest", "judgment"]
            .iter()
            .enumerate()
        {
            let b = kinds.get_item(key)?;
            let r = b.cast::<PyArray2<i64>>()?.readonly();
            let ib = ev.getattr("incurred")?.get_item(key)?;
            let ir = ib.cast::<PyArray1<i64>>()?.readonly();
            if r.as_array().dim() != (n, days) || ir.len()? != n {
                return Err(PyValueError::new_err(
                    "daily obligation shape must match the cash horizon",
                ));
            }
            if r.as_array().iter().any(|&v| v > 0) {
                return Err(PyValueError::new_err("event cash of the wrong sign for its kind (a receipt paid out or an obligation received)"));
            }
            for row in 0..n {
                inc[[c, row]] = ir.as_array()[row];
                for t in 0..days {
                    obl[[c, row, t]] = r.as_array()[[row, t]].wrapping_neg();
                }
            }
        }
        for r in 0..n {
            for t in 0..days {
                post[[r, t]] = oi_r.as_array()[[r, t]]
                    .wrapping_add(inflow_r.as_array()[[r, t]])
                    .wrapping_sub(lock_r.as_array()[[r, t]]);
                levy[[r, t]] = levy_r.as_array()[[r, t]].wrapping_neg();
                out[[r, t]] = oo_r.as_array()[[r, t]]
                    .wrapping_neg()
                    .wrapping_sub(reduction_r.as_array()[[r, t]]);
            }
        }
        let first_op = s.getattr("same_day_order")?.extract::<String>()? == "operating_first";
        py.detach(|| {
            cash::daily_compute::<false>(
                post.view(),
                levy.view(),
                out.view(),
                obl.view(),
                inc.view(),
                pet.view(),
                &line,
                first_op,
                draw_cap,
                w,
                share,
            )
        })
    } else {
        let total_b = chain.get(py, "_ops_total")?;
        let total_r = total_b.cast::<PyArray2<i64>>()?.readonly();
        let cash_b = ev.getattr("cash")?;
        let cash_r = cash_b.cast::<PyArray2<i64>>()?.readonly();
        if total_r.as_array().dim() != (n, days) || cash_r.as_array().dim() != (n, days) {
            return Err(PyValueError::new_err(
                "net operating and event cash shapes must match the cash horizon",
            ));
        }
        let base = Array2::from_shape_fn((n, days), |(r, t)| {
            total_r.as_array()[[r, t]]
                .wrapping_add(cash_r.as_array()[[r, t]])
                .wrapping_sub(lock_r.as_array()[[r, t]])
        });
        py.detach(|| cash::net_compute(base.view(), pet.view(), &line, draw_cap))
    };
    let value = if daily {
        let cash = Array2::from_shape_vec((n, days), output.cash)
            .map_err(|e| PyValueError::new_err(e.to_string()))?;
        let ar = Array3::from_shape_vec((n, days, 5), output.arrears)
            .map_err(|e| PyValueError::new_err(e.to_string()))?;
        let aruns = arrears_runs(chain, py)?;
        if aruns.len() >= 4 {
            if let Some((old, _)) = aruns.iter().next() {
                aruns.del_item(old)?;
            }
        }
        aruns.set_item(key.bind(py), ar.into_pyarray(py))?;
        PyTuple::new(
            py,
            [
                cash.into_pyarray(py).into_any().unbind(),
                array(py, Array1::from(output.first_unpaid)),
                array(py, Array1::from(output.nonpay)),
            ],
        )?
        .into_any()
        .unbind()
    } else {
        let mut net = Array2::zeros((n, days));
        for r in 0..n {
            let mut sum = 0i64;
            for t in 0..days {
                let i = r * days + t;
                sum = sum.wrapping_add(output.fundings[i].wrapping_sub(output.collections[i]));
                net[[r, t]] = sum;
            }
        }
        net.into_pyarray(py).into_any().unbind()
    };
    storage
        .getattr("runs_put")?
        .call1((&runs, key.bind(py), value.bind(py)))?;
    Ok(value)
}

fn cum(chain: &NativeChain, py: Python<'_>) -> ResultObj {
    if let Some(value) = memo(chain, py, "_cum")? {
        return Ok(value);
    }
    if flag(chain, py, "daily")? {
        let p = process(chain, py, true)?;
        return cache(chain, py, "_cum", p.bind(py).get_item(0)?.unbind());
    }
    let basis = chain.get(py, "basis")?;
    let basis_cash = basis.getattr("cash")?;
    let bc = basis_cash.cast::<PyArray2<i64>>()?.readonly();
    let ev = chain.get(py, "ev")?;
    let e_cash = ev.getattr("cash")?;
    let e_lock = ev.getattr("lock")?;
    let ec = e_cash.cast::<PyArray2<i64>>()?.readonly();
    let el = e_lock.cast::<PyArray2<i64>>()?.readonly();
    let (n, d) = bc.as_array().dim();
    let mut c = Array2::zeros((n, d));
    for r in 0..n {
        let mut s = 0i64;
        for t in 0..d {
            s = s.wrapping_add(ec.as_array()[[r, t]].wrapping_sub(el.as_array()[[r, t]]));
            c[[r, t]] = bc.as_array()[[r, t]].wrapping_add(s);
        }
    }
    if flag(chain, py, "engine")? {
        let net = process(chain, py, false)?;
        let net = net.bind(py).cast::<PyArray2<i64>>()?.readonly();
        for ((r, t), v) in c.indexed_iter_mut() {
            *v = v.wrapping_add(net.as_array()[[r, t]]);
        }
    }
    cache(chain, py, "_cum", c.into_pyarray(py).into_any().unbind())
}

fn balance_state(chain: &NativeChain, py: Python<'_>, copy: bool) -> ResultObj {
    let c = cum(chain, py)?;
    let ev = chain.get(py, "ev")?;
    let kinds = ev.getattr("kinds")?;
    let values = [
        c,
        kinds.get_item("inflow")?.unbind(),
        ev.getattr("lock")?.unbind(),
        kinds.get_item("levy")?.unbind(),
    ];
    let values = if copy {
        values
            .into_iter()
            .map(|v| v.bind(py).call_method0("copy").map(Bound::unbind))
            .collect::<PyResult<Vec<_>>>()?
    } else {
        values.into_iter().collect()
    };
    Ok(PyTuple::new(py, values)?.into_any().unbind())
}

fn decision_cash(
    chain: &NativeChain,
    py: Python<'_>,
    day: Array1<i64>,
    at: Option<Obj>,
    force_daily: bool,
) -> ResultObj {
    let daily = force_daily || flag(chain, py, "daily")?;
    let horizon = days(chain, py)?;
    let state = if let Some(at) = at {
        at
    } else if daily {
        balance_state(chain, py, false)?
    } else {
        PyTuple::new(py, [cum(chain, py)?])?.into_any().unbind()
    };
    let c = state.bind(py).get_item(0)?;
    let c = c.cast::<PyArray2<i64>>()?.readonly();
    let mut result = Array1::zeros(day.len());
    if daily {
        let inflow = state.bind(py).get_item(1)?;
        let lock = state.bind(py).get_item(2)?;
        let levy = state.bind(py).get_item(3)?;
        let inflow = inflow.cast::<PyArray2<i64>>()?.readonly();
        let lock = lock.cast::<PyArray2<i64>>()?.readonly();
        let levy = levy.cast::<PyArray2<i64>>()?.readonly();
        let basis = chain.get(py, "basis")?;
        let opening: i64 = basis.getattr("opening")?.extract()?;
        let oi = basis.getattr("inflow")?;
        let oi = oi.cast::<PyArray2<i64>>()?.readonly();
        for r in 0..day.len() {
            let t = day[r].clamp(0, horizon as i64 - 1) as usize;
            let prev = if t > 0 {
                c.as_array()[[r, t - 1]]
            } else {
                opening
            };
            result[r] = prev
                .wrapping_add(oi.as_array()[[r, t]])
                .wrapping_add(inflow.as_array()[[r, t]])
                .wrapping_sub(lock.as_array()[[r, t]])
                .wrapping_add(levy.as_array()[[r, t]]);
        }
    } else {
        for r in 0..day.len() {
            result[r] = c.as_array()[[r, day[r].clamp(0, horizon as i64 - 1) as usize]];
        }
    }
    Ok(array(py, result))
}

fn judgment_default(chain: &NativeChain, py: Python<'_>, ctx: &str) -> ResultObj {
    let count = n(chain, py)?;
    let fin = chain.get(py, "fin")?;
    let dispute = chain.get(py, "d")?;
    let days = if fin.is_none() {
        0
    } else {
        fin.getattr("judgment_default_days")?
            .extract::<Option<i64>>()?
            .unwrap_or(0)
    };
    if days == 0 || dispute.is_none() {
        return Ok(PyTuple::new(
            py,
            [
                array(py, Array1::from_elem(count, BIG)),
                Array1::from_elem(count, false)
                    .into_pyarray(py)
                    .into_any()
                    .unbind(),
            ],
        )?
        .into_any()
        .unbind());
    }
    let reading = param(chain, py, "judgment_default_reading")?
        .bind(py)
        .extract::<String>()?;
    if ctx == "ruling" {
        return default_at_ruling(chain, py, &reading);
    }
    let e = field1(chain, py, "e_ix")?;
    let a = field1(chain, py, "A")?;
    let entered = num(chain, py, "entered")?;
    let cls = chain.get(py, "cls_amount")?;
    let cls = if cls.is_none() {
        entered
    } else {
        cls.extract()?
    };
    let acted_b = chain.get(py, "jd_acted")?;
    let acted = acted_b.cast::<PyArray1<bool>>()?.readonly();
    let ripe = if ctx == "I1" {
        e.mapv(|d| d.wrapping_add(days))
    } else {
        Array1::from_iter((0..count).map(|r| a[r].max(e[r]).wrapping_add(days)))
    };
    let amount = if ctx == "I1" && flag(chain, py, "pending")? {
        result1(&standing_amount(chain, py, &ripe)?, py)?
    } else {
        Array1::from_elem(count, if ctx == "I1" { entered } else { cls })
    };
    let has = call0(chain, py, "has_judgment")?
        .bind(py)
        .extract::<bool>()?;
    let threshold: i64 = fin.getattr("judgment_default_threshold_cents")?.extract()?;
    let insured: i64 = fin.getattr("insured_cents")?.extract()?;
    let stayed = field1(chain, py, "stayed_from")?;
    let live = result_bool(&call1(chain, py, "live", &ripe)?, py)?;
    let owed = result1(&call1(chain, py, "owed_at", &ripe)?, py)?;
    let cond = Array1::from_iter((0..count).map(|r| {
        let on = if ctx == "I1" {
            (reading == "both" || reading == "entered") && has
        } else {
            (reading == "both" || reading == "post_ruling") && a[r] >= 0 && !acted.as_array()[r]
        };
        on && amount[r].wrapping_sub(insured) > threshold
            && stayed[r] > ripe[r]
            && live[r]
            && owed[r] > 0
    }));
    Ok(PyTuple::new(
        py,
        [array(py, ripe), cond.into_pyarray(py).into_any().unbind()],
    )?
    .into_any()
    .unbind())
}

fn default_at_ruling(chain: &NativeChain, py: Python<'_>, reading: &str) -> ResultObj {
    let count = n(chain, py)?;
    let cls = chain.get(py, "cls_amount")?;
    let changed = flag(chain, py, "pending")?
        && !cls.is_none()
        && cls.extract::<i64>()? > 0
        && cls.extract::<i64>()? != num(chain, py, "entered")?;
    if reading != "entered" || !changed {
        return Ok(PyTuple::new(
            py,
            [
                array(py, Array1::from_elem(count, BIG)),
                Array1::from_elem(count, false)
                    .into_pyarray(py)
                    .into_any()
                    .unbind(),
            ],
        )?
        .into_any()
        .unbind());
    }
    let f = field1(chain, py, "F")?;
    let day = f.mapv(|v| v.max(0));
    let ri = judgment_default(chain, py, "I1")?;
    let ripe = result1(&ri.bind(py).get_item(0)?.unbind(), py)?;
    let open = result_bool(&ri.bind(py).get_item(1)?.unbind(), py)?;
    let acted_b = chain.get(py, "jd_acted")?;
    let acted = acted_b.cast::<PyArray1<bool>>()?.readonly();
    let fin = chain.get(py, "fin")?;
    let insured: i64 = fin.getattr("insured_cents")?.extract()?;
    let threshold: i64 = fin.getattr("judgment_default_threshold_cents")?.extract()?;
    let amount = cls.extract::<i64>()?;
    let stayed = field1(chain, py, "stayed_from")?;
    let live = result_bool(&call1(chain, py, "live", &day)?, py)?;
    let owed = result1(&call1(chain, py, "owed_at", &day)?, py)?;
    let due = mark_array(chain, py, "notes_due")?;
    let cond = Array1::from_iter((0..count).map(|r| {
        open[r]
            && ripe[r] <= day[r]
            && !acted.as_array()[r]
            && f[r] >= 0
            && amount.wrapping_sub(insured) > threshold
            && stayed[r] > day[r]
            && live[r]
            && owed[r] > 0
            && due[r] > day[r]
    }));
    Ok(PyTuple::new(
        py,
        [array(py, day), cond.into_pyarray(py).into_any().unbind()],
    )?
    .into_any()
    .unbind())
}

fn standing_amount(chain: &NativeChain, py: Python<'_>, day: &Array1<i64>) -> ResultObj {
    let count = n(chain, py)?;
    let entered = num(chain, py, "entered")?;
    let cls = chain.get(py, "cls_amount")?;
    let f = field1(chain, py, "F")?;
    let pending = flag(chain, py, "pending")?;
    let e = if pending {
        Some(field1(chain, py, "E_ix")?)
    } else {
        None
    };
    let has = call0(chain, py, "has_judgment")?
        .bind(py)
        .extract::<bool>()?;
    let cls = if cls.is_none() {
        None
    } else {
        Some(cls.extract::<i64>()?)
    };
    Ok(array(
        py,
        Array1::from_iter((0..count).map(|r| {
            if !has || e.as_ref().is_some_and(|e| day[r] < e[r]) {
                0
            } else if let Some(cls) = cls {
                if day[r] >= f[r] {
                    cls
                } else {
                    entered
                }
            } else {
                entered
            }
        })),
    ))
}

fn default_available(chain: &NativeChain, py: Python<'_>) -> ResultObj {
    let count = n(chain, py)?;
    let horizon = days(chain, py)? as i64;
    let mut out = Array1::from_elem(count, BIG);
    for ctx in ["I1", "post"] {
        let v = judgment_default(chain, py, ctx)?;
        let r = result1(&v.bind(py).get_item(0)?.unbind(), py)?;
        let c = result_bool(&v.bind(py).get_item(1)?.unbind(), py)?;
        for j in 0..count {
            if c[j] {
                out[j] = out[j].min(r[j]);
            }
        }
    }
    out.mapv_inplace(|d| if d < horizon { d } else { BIG });
    Ok(array(py, out))
}

fn holder_route_path(chain: &NativeChain, py: Python<'_>) -> ResultObj {
    let count = n(chain, py)?;
    let route = call0(chain, py, "holder_route_days")?
        .bind(py)
        .extract::<i64>()?;
    if !flag(chain, py, "daily")? {
        return Ok(array(py, Array1::from_elem(count, route)));
    }
    let p = process(chain, py, true)?;
    let nonpay = result1(&p.bind(py).get_item(2)?.unbind(), py)?;
    let due = mark_array(chain, py, "notes_due")?;
    Ok(array(
        py,
        Array1::from_iter((0..count).map(|r| if nonpay[r] <= due[r] { 0 } else { route })),
    ))
}

fn book_default(
    chain: &NativeChain,
    py: Python<'_>,
    ctx: &str,
    branch: &str,
    rows: &Array1<bool>,
) -> ResultObj {
    let v = judgment_default(chain, py, ctx)?;
    let ripe = result1(&v.bind(py).get_item(0)?.unbind(), py)?;
    let mut cond = result_bool(&v.bind(py).get_item(1)?.unbind(), py)?;
    for r in 0..cond.len() {
        cond[r] &= rows[r];
    }
    let lag = param(chain, py, "holder_notice_lag_days")?
        .bind(py)
        .extract::<i64>()?;
    let accel = ripe.mapv(|d| d.wrapping_add(lag));
    if matches!(branch, "yes" | "holders_file" | "accelerated") {
        mark(chain, py, "notes_due", &accel, &cond)?;
        if matches!(ctx, "I1" | "ruling") {
            let acted = chain.get(py, "jd_acted")?;
            let mut acted = acted
                .cast::<PyArray1<bool>>()?
                .readonly()
                .as_array()
                .to_owned();
            for r in 0..acted.len() {
                acted[r] |= cond[r];
            }
            chain
                .state
                .bind(py)
                .set_item("jd_acted", acted.into_pyarray(py))?;
        }
    }
    call0(chain, py, "coupon_when_due")?;
    if branch == "yes" {
        call(
            chain,
            py,
            "petition",
            vec![
                array(py, accel),
                cond.clone().into_pyarray(py).into_any().unbind(),
                str_obj(py, "notes"),
            ],
        )?;
    } else if branch == "holders_file" {
        let route = if flag(chain, py, "pending")? {
            result1(&holder_route_path(chain, py)?, py)?
        } else {
            Array1::from_elem(
                ripe.len(),
                call0(chain, py, "holder_route_days")?
                    .bind(py)
                    .extract::<i64>()?,
            )
        };
        let when = Array1::from_iter((0..ripe.len()).map(|r| accel[r].wrapping_add(route[r])));
        call(
            chain,
            py,
            "petition",
            vec![
                array(py, when),
                cond.clone().into_pyarray(py).into_any().unbind(),
                str_obj(py, "notes"),
            ],
        )?;
    }
    Ok(array(
        py,
        Array1::from_iter((0..ripe.len()).map(|r| if cond[r] { ripe[r] } else { BIG })),
    ))
}

fn first_cash_day(chain: &NativeChain, py: Python<'_>, out: bool) -> ResultObj {
    let zero_floor = chain.sensitivity(py, "cash_floor")?;
    let count = n(chain, py)?;
    if out && zero_floor {
        return Ok(array(py, Array1::from_elem(count, BIG)));
    }
    let key = if out { "_out" } else { "_tau" };
    if let Some(v) = memo(chain, py, key)? {
        return Ok(array(py, result1(&v, py)?));
    }
    let value = if flag(chain, py, "daily")? && (out || zero_floor) {
        let p = process(chain, py, true)?;
        array(py, result1(&p.bind(py).get_item(1)?.unbind(), py)?)
    } else {
        let c = cum(chain, py)?;
        let c = c.bind(py).cast::<PyArray2<i64>>()?.readonly();
        let basis = chain.get(py, "basis")?;
        let need = basis.getattr("need")?;
        let need = need.cast::<PyArray2<i64>>()?.readonly();
        let mut result = Array1::from_elem(count, BIG);
        for r in 0..count {
            for t in 0..c.as_array().ncols() {
                let floor = if out || zero_floor {
                    0
                } else {
                    need.as_array()[[r, t]]
                };
                if c.as_array()[[r, t]] < floor {
                    result[r] = t as i64;
                    break;
                }
            }
        }
        array(py, result)
    };
    cache(chain, py, key, value.clone_ref(py))?;
    Ok(array(py, result1(&value, py)?))
}

fn fall_after(chain: &NativeChain, py: Python<'_>, prev: &Array1<i64>) -> ResultObj {
    let c = cum(chain, py)?;
    let c = c.bind(py).cast::<PyArray2<i64>>()?.readonly();
    let horizon = days(chain, py)?;
    let count = n(chain, py)?;
    let basis = chain.get(py, "basis")?;
    let need = basis.getattr("need")?;
    let need = need.cast::<PyArray2<i64>>()?.readonly();
    let zero = chain.sensitivity(py, "cash_floor")?;
    let mut result = Array1::from_elem(count, BIG);
    for r in 0..count {
        let mut recovered = false;
        for t in 0..horizon {
            if t as i64 <= prev[r] {
                continue;
            }
            let floor = if zero { 0 } else { need.as_array()[[r, t]] };
            if !recovered {
                if c.as_array()[[r, t]] >= floor {
                    recovered = true;
                }
            } else if c.as_array()[[r, t]] < floor {
                result[r] = t as i64;
                break;
            }
        }
    }
    Ok(array(py, result))
}

fn new_money_after(chain: &NativeChain, py: Python<'_>, prev: &Array1<i64>) -> ResultObj {
    let mut result = Array1::from_elem(n(chain, py)?, BIG);
    let offers = chain.get(py, "_offers")?;
    for offer in offers.try_iter()? {
        let offer = offer?;
        let closed = offer.get_item("closed")?;
        let closed = closed.cast::<PyArray1<bool>>()?.readonly();
        let close = per_draw(chain, py, &offer.get_item("close")?)?;
        for r in 0..result.len() {
            if closed.as_array()[r] && close[r] > prev[r] {
                result[r] = result[r].min(close[r]);
            }
        }
    }
    let stays = chain.get(py, "stays")?;
    for (_, st) in stays.cast::<PyDict>()?.iter() {
        let st = st.cast::<PyDict>()?;
        if let Some(held) = st.get_item("held")? {
            let held = held.cast::<PyArray1<bool>>()?.readonly();
            let rel = per_draw(
                chain,
                py,
                &st.get_item("rel")?
                    .ok_or_else(|| PyValueError::new_err("stay without release day"))?,
            )?;
            for r in 0..result.len() {
                if held.as_array()[r] && rel[r] > prev[r] {
                    result[r] = result[r].min(rel[r]);
                }
            }
        }
    }
    Ok(array(py, result))
}

fn response_day(chain: &NativeChain, py: Python<'_>, ctx: &str) -> ResultObj {
    let count = n(chain, py)?;
    let horizon = days(chain, py)? as i64;
    let pending = chain.get(py, "pending_levy")?;
    let lv = if pending.is_none() {
        Array1::from_elem(count, BIG)
    } else {
        per_draw(chain, py, &pending)?
    };
    let mut milestone = if ctx == "entry" {
        if call0(chain, py, "has_judgment")?
            .bind(py)
            .extract::<bool>()?
        {
            field1(chain, py, "E_ix")?
        } else {
            Array1::from_elem(count, BIG)
        }
    } else if ctx == "I1" {
        let stay = field1(chain, py, "stayed_from")?;
        let f = field1(chain, py, "F")?;
        Array1::from_iter((0..count).map(|r| {
            if lv[r] < stay[r] && lv[r] < f[r] {
                lv[r]
            } else {
                BIG
            }
        }))
    } else if ctx == "post" {
        let stay = field1(chain, py, "stayed_from")?;
        Array1::from_iter((0..count).map(|r| {
            if lv[r] < stay[r] && lv[r] < horizon {
                lv[r]
            } else {
                BIG
            }
        }))
    } else if flag(chain, py, "pending")? {
        result1(&default_available(chain, py)?, py)?
    } else {
        let jd = judgment_default(chain, py, "post")?;
        let ripe = result1(&jd.bind(py).get_item(0)?.unbind(), py)?;
        let cond = result_bool(&jd.bind(py).get_item(1)?.unbind(), py)?;
        Array1::from_iter((0..count).map(|r| if cond[r] { ripe[r] } else { BIG }))
    };
    let owed = result1(&call1(chain, py, "owed_at", &milestone)?, py)?;
    for r in 0..count {
        if owed[r] <= 0 {
            milestone[r] = BIG;
        }
    }
    Ok(array(py, milestone))
}

fn distress_day(chain: &NativeChain, py: Python<'_>, node: &str, ctx: &str) -> ResultObj {
    match node {
        "cash_floor" => {
            let k = ctx
                .parse::<i64>()
                .map_err(|_| PyValueError::new_err("invalid floor context"))?;
            if k == 1 {
                return first_cash_day(chain, py, false);
            }
            let floors = chain.get(py, "floor_days")?;
            let floors = floors.cast::<PyDict>()?;
            let Some(prev) = floors.get_item(k - 1)? else {
                return Ok(array(py, Array1::from_elem(n(chain, py)?, BIG)));
            };
            let prev = per_draw(chain, py, &prev)?;
            let new = result1(&new_money_after(chain, py, &prev)?, py)?;
            let mut fall = result1(
                &fall_after(chain, py, &new.mapv(|d| d.wrapping_sub(1)))?,
                py,
            )?;
            for r in 0..fall.len() {
                if new[r] >= BIG {
                    fall[r] = BIG;
                }
            }
            Ok(array(py, fall))
        }
        "cash_out" => first_cash_day(chain, py, true),
        "offering" => call(chain, py, "offering_day", vec![str_obj(py, ctx)]),
        "nonpayment" => {
            let p = process(chain, py, true)?;
            let mut day = result1(&p.bind(py).get_item(2)?.unbind(), py)?;
            let due = mark_array(chain, py, "notes_due")?;
            for r in 0..day.len() {
                if day[r] >= BIG || due[r] <= day[r] {
                    day[r] = BIG;
                }
            }
            Ok(array(py, day))
        }
        _ => Err(PyValueError::new_err(format!("No distress step {node}"))),
    }
}

fn decide_distress(
    chain: &NativeChain,
    py: Python<'_>,
    node: &str,
    ctx: &str,
    branch: &str,
    t: &Array1<i64>,
) -> ResultObj {
    let count = n(chain, py)?;
    let horizon = days(chain, py)? as i64;
    let on = t.mapv(|d| d < horizon);
    if node == "offering" {
        call(
            chain,
            py,
            "offering_outcome",
            vec![str_obj(py, ctx), boolean(py, branch == "yes")],
        )?;
        return Ok(array(py, Array1::zeros(count)));
    }
    if node == "nonpayment" {
        chain.state.bind(py).set_item("_how", "automatic_j")?;
        mark(chain, py, "notes_due", t, &on)?;
        chain.state.bind(py).set_item("_how", "declared")?;
        call0(chain, py, "coupon_when_due")?;
        if branch == "petition" || branch == "holders_file" {
            call(
                chain,
                py,
                "petition",
                vec![
                    array(py, t.clone()),
                    on.into_pyarray(py).into_any().unbind(),
                    str_obj(py, "notes"),
                ],
            )?;
        } else if branch != "due" {
            return Err(PyValueError::new_err(format!("No §3.3 branch {branch:?}")));
        }
        return Ok(array(py, Array1::zeros(count)));
    }
    if node == "cash_floor" {
        let k = ctx
            .parse::<i64>()
            .map_err(|_| PyValueError::new_err("invalid floor context"))?;
        let floors = chain.get(py, "floor_days")?;
        let floors = floors.cast::<PyDict>()?;
        let mut values = if let Some(v) = floors.get_item(k)? {
            per_draw(chain, py, &v)?
        } else {
            Array1::from_elem(count, BIG)
        };
        for r in 0..count {
            if on[r] {
                values[r] = t[r];
            }
        }
        floors.set_item(k, values.into_pyarray(py))?;
    }
    let occasion = if node == "cash_floor" {
        format!("floor{ctx}")
    } else {
        "cash_out".into()
    };
    let net = result1(&call1(chain, py, "offer_available", t)?, py)?;
    let amt = Array1::from_iter((0..count).map(|r| if on[r] { net[r] } else { 0 }));
    let booking = match branch {
        "initiate_offering" => "offer",
        "file" => "petition",
        "neither" | "none" => "none",
        _ => {
            return Err(PyValueError::new_err(format!(
                "unknown distress branch {branch:?}"
            )))
        }
    };
    call(
        chain,
        py,
        "respond",
        vec![
            str_obj(py, booking),
            array(py, t.clone()),
            str_obj(py, "cash_floor"),
            str_obj(py, &occasion),
        ],
    )?;
    Ok(array(py, amt))
}

fn decide_floor(
    chain: &NativeChain,
    py: Python<'_>,
    node: &str,
    branch: &str,
    t: &Array1<i64>,
) -> ResultObj {
    let q = if node == "cash_floor" {
        "petition_cash_floor"
    } else if node == "cash_out" {
        "petition_cash_out"
    } else {
        return Err(PyValueError::new_err("unknown floor node"));
    };
    let booking = chain
        .get(py, "bookings")?
        .get_item(q)?
        .get_item(branch)?
        .extract::<String>()?;
    call(
        chain,
        py,
        "respond",
        vec![
            str_obj(py, &booking),
            array(py, t.clone()),
            str_obj(py, "cash_floor"),
        ],
    )?;
    Ok(array(py, Array1::zeros(n(chain, py)?)))
}

fn decide_waiting_inner(
    chain: &NativeChain,
    py: Python<'_>,
    i: i64,
    node: &str,
    branch: &str,
    t: &Array1<i64>,
) -> ResultObj {
    let ctx = chain.get(py, "wctx")?.get_item(i)?.extract::<String>()?;
    if flag(chain, py, "equity")? && is_distress(node) {
        return decide_distress(chain, py, node, &ctx, branch, t);
    }
    if is_floor(node) {
        return decide_floor(chain, py, node, branch, t);
    }
    if node == "judgment_default" {
        book_default(chain, py, &ctx, branch, &t.mapv(|d| d < BIG))?;
        return Ok(array(py, Array1::zeros(n(chain, py)?)));
    }
    let amt = if is_response(node) {
        call1(chain, py, "offer_available", t)?
    } else {
        array(py, Array1::zeros(n(chain, py)?))
    };
    let booking = call(
        chain,
        py,
        "booking",
        vec![str_obj(py, node), str_obj(py, branch)],
    )?
    .bind(py)
    .extract::<String>()?;
    call(
        chain,
        py,
        "respond",
        vec![
            str_obj(py, &booking),
            array(py, t.clone()),
            str_obj(py, "enforcement"),
            str_obj(py, &ctx),
        ],
    )?;
    Ok(amt)
}

fn decide_waiting(
    chain: &NativeChain,
    py: Python<'_>,
    i: i64,
    node: &str,
    branch: &str,
    t: &Array1<i64>,
) -> ResultObj {
    chain.state.bind(py).set_item("_grp", py.None())?;
    if (is_floor(node) || is_response(node))
        && (flag(chain, py, "pending")? || flag(chain, py, "equity")?)
    {
        let g = call(
            chain,
            py,
            "option_group",
            vec![str_obj(py, node), array(py, t.clone())],
        )?;
        chain.state.bind(py).set_item("_grp", g)?;
    }
    let amt = decide_waiting_inner(chain, py, i, node, branch, t)?;
    let group = chain.get(py, "_grp")?;
    if !group.is_none() {
        let groups = chain.get(py, "grec")?;
        let groups = groups.cast::<PyDict>()?;
        let old = groups.get_item(i)?;
        let old = if let Some(old) = old {
            old.cast::<PyArray1<i8>>()?.readonly().as_array().to_owned()
        } else {
            Array1::from_elem(n(chain, py)?, -1i8)
        };
        let g = group.cast::<PyArray1<i8>>()?.readonly();
        let result = Array1::from_iter((0..old.len()).map(|r| {
            if t[r] < BIG {
                g.as_array()[r]
            } else {
                old[r]
            }
        }));
        groups.set_item(i, result.into_pyarray(py))?;
    }
    Ok(amt)
}

fn waits(chain: &NativeChain, py: Python<'_>, node: &str, ctx: &str) -> PyResult<bool> {
    if flag(chain, py, "equity")? && is_distress(node) {
        return Ok(true);
    }
    if flag(chain, py, "ordinary")? {
        return Ok(is_floor(node));
    }
    Ok(flag(chain, py, "pending")?
        && (is_floor(node)
            || node == "judgment_default"
            || (is_response(node) && matches!(ctx, "post" | "ripe"))))
}

fn waiting_day(chain: &NativeChain, py: Python<'_>, node: &str, ctx: &str) -> ResultObj {
    if node == "judgment_default" {
        let v = judgment_default(chain, py, ctx)?;
        let ripe = result1(&v.bind(py).get_item(0)?.unbind(), py)?;
        let cond = result_bool(&v.bind(py).get_item(1)?.unbind(), py)?;
        return Ok(array(
            py,
            Array1::from_iter((0..ripe.len()).map(|r| if cond[r] { ripe[r] } else { BIG })),
        ));
    }
    response_day(chain, py, ctx)
}

fn response_waiting(chain: &NativeChain, py: Python<'_>) -> ResultObj {
    let mut out = Array1::from_elem(n(chain, py)?, false);
    let waiting = chain.get(py, "waiting")?;
    for w in waiting.try_iter()? {
        let w = w?;
        let node = w.get_item(1)?.extract::<String>()?;
        let ctx = w.get_item(4)?.extract::<String>()?;
        if is_response(&node) && ctx != "ripe" {
            let done = w.get_item(3)?;
            let done = done.cast::<PyArray1<bool>>()?.readonly();
            let day = result1(&response_day(chain, py, &ctx)?, py)?;
            for r in 0..out.len() {
                out[r] |= !done.as_array()[r] && day[r] < BIG;
            }
        }
    }
    Ok(out.into_pyarray(py).into_any().unbind())
}

fn record_fire(
    chain: &NativeChain,
    py: Python<'_>,
    i: i64,
    node: &str,
    branch: &str,
    t: &Array1<i64>,
    fire: &Array1<bool>,
) -> PyResult<()> {
    let cash = result1(&decision_cash(chain, py, t.clone(), None, false)?, py)?;
    let owed = result1(&call1(chain, py, "owed_at", t)?, py)?;
    let collateral = field1(chain, py, "collateral_required")?;
    let rec = chain.get(py, "rec")?;
    for (k, value) in [t.clone(), cash, owed, collateral].into_iter().enumerate() {
        let list = rec.get_item(k)?;
        let list = list.cast::<PyList>()?;
        let old = per_draw(chain, py, &list.get_item(i as usize)?)?;
        let selected =
            Array1::from_iter((0..old.len()).map(|r| if fire[r] { value[r] } else { old[r] }));
        list.set_item(i as usize, selected.into_pyarray(py))?;
    }
    let late = chain.get(py, "late")?.get_item(i)?;
    let late = late.cast::<PyDict>()?;
    let ev = chain.get(py, "ev")?;
    let petition = per_draw(chain, py, &ev.getattr("petition")?)?;
    let old = per_draw(
        chain,
        py,
        &late
            .get_item("petition")?
            .ok_or_else(|| PyValueError::new_err("late record without petition"))?,
    )?;
    late.set_item(
        "petition",
        Array1::from_iter((0..old.len()).map(|r| if fire[r] { petition[r] } else { old[r] }))
            .into_pyarray(py),
    )?;
    let triggers = call0(chain, py, "trigger_days")?;
    let old = late
        .get_item("triggers")?
        .ok_or_else(|| PyValueError::new_err("late record without triggers"))?;
    let old = old.cast::<PyDict>()?;
    let selected = PyDict::new(py);
    for (key, value) in triggers.bind(py).cast::<PyDict>()?.iter() {
        let value = per_draw(chain, py, &value)?;
        let previous = if let Some(v) = old.get_item(&key)? {
            per_draw(chain, py, &v)?
        } else {
            Array1::from_elem(value.len(), BIG)
        };
        selected.set_item(
            key,
            Array1::from_iter(
                (0..value.len()).map(|r| if fire[r] { value[r] } else { previous[r] }),
            )
            .into_pyarray(py),
        )?;
    }
    late.set_item("triggers", selected)?;
    let dated = Array1::from_iter((0..t.len()).map(|r| if fire[r] { t[r] } else { BIG }));
    let amt = result1(&decide_waiting(chain, py, i, node, branch, &dated)?, py)?;
    let old = per_draw(
        chain,
        py,
        &late
            .get_item("raise_offer")?
            .ok_or_else(|| PyValueError::new_err("late record without offering amount"))?,
    )?;
    late.set_item(
        "raise_offer",
        Array1::from_iter((0..amt.len()).map(|r| if fire[r] { amt[r] } else { old[r] }))
            .into_pyarray(py),
    )?;
    Ok(())
}

fn filter_waiting(chain: &NativeChain, py: Python<'_>) -> PyResult<()> {
    let waiting = chain.get(py, "waiting")?;
    let filtered = PyList::empty(py);
    for w in waiting.try_iter()? {
        let w = w?;
        let done = w.get_item(3)?;
        let done = done.cast::<PyArray1<bool>>()?.readonly();
        if !done.as_array().iter().all(|&v| v) {
            filtered.append(w)?;
        }
    }
    chain.state.bind(py).set_item("waiting", filtered)
}

fn upto(
    chain: &NativeChain,
    py: Python<'_>,
    before: Option<Array1<i64>>,
    every: bool,
    levy: Option<Array1<i64>>,
) -> PyResult<bool> {
    let waiting = chain.get(py, "waiting")?;
    if waiting.len()? == 0 || (before.is_none() && !every) {
        return Ok(false);
    }
    if flag(chain, py, "equity")? {
        return upto_dated(chain, py, before, every, levy, None);
    }
    let count = n(chain, py)?;
    let mut moved = false;
    let mut prior = Array1::from_elem(count, true);
    for w in waiting.try_iter()? {
        let w = w?;
        let i = w.get_item(0)?.extract::<i64>()?;
        let node = w.get_item(1)?.extract::<String>()?;
        let branch = w.get_item(2)?.extract::<String>()?;
        let ctx = w.get_item(4)?.extract::<String>()?;
        let db = w.get_item(3)?;
        let mut done = db
            .cast::<PyArray1<bool>>()?
            .readonly()
            .as_array()
            .to_owned();
        let floor = is_floor(&node);
        let t = result1(
            &if floor {
                first_cash_day(chain, py, node == "cash_out")?
            } else {
                waiting_day(chain, py, &node, &ctx)?
            },
            py,
        )?;
        let fire = Array1::from_iter((0..count).map(|r| {
            let bound = if let Some(ref before) = before {
                if let Some(ref levy) = levy {
                    if is_response(&node) && ctx != "ripe" {
                        before[r]
                    } else {
                        before[r].min(levy[r])
                    }
                } else {
                    before[r]
                }
            } else {
                BIG
            };
            (!floor || prior[r]) && !done[r] && (every || t[r] < bound)
        }));
        if fire.iter().any(|&v| v) {
            record_fire(chain, py, i, &node, &branch, &t, &fire)?;
            for r in 0..count {
                done[r] |= fire[r];
            }
            let mut data = db.cast::<PyArray1<bool>>()?.readwrite();
            for r in 0..count {
                data.as_array_mut()[r] = done[r];
            }
            moved = true;
        }
        if floor {
            prior = done;
        }
    }
    filter_waiting(chain, py)?;
    Ok(moved)
}

fn upto_dated(
    chain: &NativeChain,
    py: Python<'_>,
    mut before: Option<Array1<i64>>,
    every: bool,
    levy: Option<Array1<i64>>,
    target: Option<i64>,
) -> PyResult<bool> {
    let count = n(chain, py)?;
    let horizon = days(chain, py)? as i64;
    let mut moved = false;
    let tdone = if let Some(target) = target {
        let waiting = chain.get(py, "waiting")?;
        let mut result = None;
        for w in waiting.try_iter()? {
            let w = w?;
            if w.get_item(0)?.extract::<i64>()? == target {
                result = Some(w.get_item(3)?.unbind());
                break;
            }
        }
        result
    } else {
        None
    };
    loop {
        let waiting = chain.get(py, "waiting")?;
        if waiting.len()? == 0 {
            break;
        }
        if let Some(ref tdone) = tdone {
            let done = tdone.bind(py).cast::<PyArray1<bool>>()?.readonly();
            let last = chain
                .get(py, "rec")?
                .get_item(0)?
                .get_item(target.ok_or_else(|| PyValueError::new_err("missing dated target"))?)?;
            let last = per_draw(chain, py, &last)?;
            before = Some(Array1::from_iter((0..count).map(|r| {
                if done.as_array()[r] {
                    last[r].wrapping_add(1)
                } else {
                    horizon
                }
            })));
        }
        let mut best = Array1::from_elem(count, BIG + 1);
        let mut pick = Array1::from_elem(count, -1i64);
        let mut dates = Vec::new();
        for (j, w) in waiting.try_iter()?.enumerate() {
            let w = w?;
            let node = w.get_item(1)?.extract::<String>()?;
            let ctx = w.get_item(4)?.extract::<String>()?;
            let db = w.get_item(3)?;
            let done = db.cast::<PyArray1<bool>>()?.readonly();
            let t = result1(
                &if is_distress(&node) {
                    distress_day(chain, py, &node, &ctx)?
                } else {
                    waiting_day(chain, py, &node, &ctx)?
                },
                py,
            )?;
            for r in 0..count {
                let lim = if let Some(ref before) = before {
                    if let Some(ref levy) = levy {
                        if is_response(&node) {
                            before[r]
                        } else {
                            before[r].min(levy[r])
                        }
                    } else {
                        before[r]
                    }
                } else {
                    BIG
                };
                if !done.as_array()[r] && (every || t[r] < lim) && t[r] < best[r] {
                    best[r] = t[r];
                    pick[r] = j as i64;
                }
            }
            dates.push(t);
        }
        if pick.iter().all(|&v| v < 0) {
            break;
        }
        for (j, w) in waiting.try_iter()?.enumerate() {
            let w = w?;
            let fire = pick.mapv(|p| p == j as i64);
            if !fire.iter().any(|&v| v) {
                continue;
            }
            let i = w.get_item(0)?.extract::<i64>()?;
            let node = w.get_item(1)?.extract::<String>()?;
            let branch = w.get_item(2)?.extract::<String>()?;
            record_fire(chain, py, i, &node, &branch, &dates[j], &fire)?;
            let db = w.get_item(3)?;
            let mut done = db.cast::<PyArray1<bool>>()?.readwrite();
            for r in 0..count {
                done.as_array_mut()[r] |= fire[r];
            }
            moved = true;
        }
        filter_waiting(chain, py)?;
    }
    Ok(moved)
}

fn next_floor(chain: &NativeChain, py: Python<'_>) -> ResultObj {
    let count = n(chain, py)?;
    let equity = flag(chain, py, "equity")?;
    let mut out = Array1::from_elem(count, BIG);
    let mut prior = Array1::from_elem(count, true);
    let waiting = chain.get(py, "waiting")?;
    for w in waiting.try_iter()? {
        let w = w?;
        let node = w.get_item(1)?.extract::<String>()?;
        let ctx = w.get_item(4)?.extract::<String>()?;
        if equity && !is_distress(&node) || !equity && !is_floor(&node) {
            continue;
        }
        let db = w.get_item(3)?;
        let done = db.cast::<PyArray1<bool>>()?.readonly();
        let t = result1(
            &if equity {
                distress_day(chain, py, &node, &ctx)?
            } else {
                first_cash_day(chain, py, node == "cash_out")?
            },
            py,
        )?;
        for r in 0..count {
            if equity {
                if !done.as_array()[r] {
                    out[r] = out[r].min(t[r]);
                }
            } else {
                if prior[r] && !done.as_array()[r] && out[r] == BIG {
                    out[r] = t[r];
                }
                prior[r] = done.as_array()[r];
            }
        }
    }
    Ok(array(py, out))
}

fn until(chain: &NativeChain, py: Python<'_>, bound: &Array1<i64>) -> PyResult<()> {
    loop {
        if chain.get(py, "waiting")?.len()? == 0 {
            break;
        }
        let pending = chain.get(py, "pending_levy")?;
        let lv = if pending.is_none() {
            Array1::from_elem(n(chain, py)?, BIG)
        } else {
            per_draw(chain, py, &pending)?
        };
        let mut moved = upto(chain, py, Some(bound.clone()), false, Some(lv.clone()))?;
        let rows = Array1::from_iter((0..lv.len()).map(|r| lv[r] < bound[r] && lv[r] < BIG));
        if rows.iter().any(|&v| v) {
            call(
                chain,
                py,
                "flush_levy",
                vec![rows.into_pyarray(py).into_any().unbind()],
            )?;
            moved = true;
        }
        if !moved {
            break;
        }
    }
    Ok(())
}

fn seen_at(chain: &NativeChain, py: Python<'_>, day: &Array1<i64>, levy: bool) -> ResultObj {
    let horizon = days(chain, py)? as i64;
    let bound = day.mapv(|d| if d < horizon { d } else { -1 });
    let mut reads = field1(chain, py, "reads")?;
    for r in 0..reads.len() {
        reads[r] = reads[r].max(bound[r]);
    }
    chain
        .state
        .bind(py)
        .set_item("reads", reads.into_pyarray(py))?;
    if chain.get(py, "waiting")?.len()? == 0 {
        return Ok(chain.state.clone_ref(py).into_any());
    }
    let cloned = call0(chain, py, "clone")?;
    let view = NativeChain {
        state: cloned.bind(py).cast::<PyDict>()?.clone().unbind(),
    };
    if levy {
        until(&view, py, &bound)?;
    } else {
        upto(&view, py, Some(bound), false, None)?;
    }
    let fired = if let Some(v) = chain.state.bind(py).get_item("_vfired")? {
        v.cast_into::<PyDict>()?
    } else {
        let d = PyDict::new(py);
        chain.state.bind(py).set_item("_vfired", &d)?;
        d
    };
    let waiting = chain.get(py, "waiting")?;
    let rec = view.get(py, "rec")?.get_item(0)?;
    for w in waiting.try_iter()? {
        let w = w?;
        let i = w.get_item(0)?.extract::<i64>()?;
        let db = w.get_item(3)?;
        let done = db.cast::<PyArray1<bool>>()?.readonly();
        let days = per_draw(chain, py, &rec.get_item(i)?)?;
        let booked =
            Array1::from_iter((0..days.len()).map(|r| days[r] < BIG && !done.as_array()[r]));
        if booked.iter().any(|&v| v) {
            let old = if let Some(v) = fired.get_item(i)? {
                per_draw(chain, py, &v)?
            } else {
                Array1::from_elem(days.len(), BIG)
            };
            fired.set_item(
                i,
                Array1::from_iter(
                    (0..days.len()).map(|r| old[r].min(if booked[r] { days[r] } else { BIG })),
                )
                .into_pyarray(py),
            )?;
        }
    }
    Ok(view.state.into_any())
}

fn snapshot_day(chain: &NativeChain, py: Python<'_>, i: i64) -> ResultObj {
    let stays = chain.get(py, "stays")?;
    let stays = stays.cast::<PyDict>()?;
    if let Some(st) = stays.get_item(i)? {
        Ok(st.get_item("approval")?.unbind())
    } else {
        Ok(chain.get(py, "rec")?.get_item(0)?.get_item(i)?.unbind())
    }
}

fn book_to_day(chain: &NativeChain, py: Python<'_>) -> PyResult<()> {
    let rec = chain.get(py, "rec")?.get_item(0)?;
    let i = rec.len()? as i64 - 1;
    let waiting = chain.get(py, "waiting")?;
    let mut target = None;
    for w in waiting.try_iter()? {
        let w = w?;
        if w.get_item(0)?.extract::<i64>()? == i {
            target = Some(w);
            break;
        }
    }
    if let Some(tw) = target {
        upto_dated(chain, py, None, false, None, Some(i))?;
        let db = tw.get_item(3)?;
        let done = db.cast::<PyArray1<bool>>()?.readonly();
        let day = per_draw(chain, py, &chain.get(py, "rec")?.get_item(0)?.get_item(i)?)?;
        let horizon = days(chain, py)? as i64;
        chain.state.bind(py).set_item(
            "_booked_to",
            Array1::from_iter((0..day.len()).map(|r| {
                if done.as_array()[r] {
                    day[r]
                } else {
                    horizon - 1
                }
            }))
            .into_pyarray(py),
        )?;
        return Ok(());
    }
    let t = per_draw(chain, py, &rec.get_item(i)?)?;
    let u = result1(&snapshot_day(chain, py, i)?, py)?;
    let horizon = days(chain, py)? as i64;
    let bound = Array1::from_iter((0..t.len()).map(|r| {
        if u[r] < horizon {
            u[r].wrapping_add(1)
        } else if t[r] < horizon {
            horizon
        } else {
            0
        }
    }));
    let last = chain
        .state
        .bind(py)
        .get_item("_last_node")?
        .map(|v| v.extract::<String>())
        .transpose()?
        .unwrap_or_default();
    let booked = if last == "notes_due_date" {
        Array1::from_elem(t.len(), horizon - 1)
    } else {
        bound.mapv(|d| d.wrapping_sub(1))
    };
    chain
        .state
        .bind(py)
        .set_item("_booked_to", booked.into_pyarray(py))?;
    if chain.get(py, "waiting")?.len()? > 0 {
        upto_dated(chain, py, Some(bound), false, None, None)?;
    }
    Ok(())
}

fn judgment_standing(chain: &NativeChain, py: Python<'_>, day: &Array1<i64>) -> ResultObj {
    let count = n(chain, py)?;
    let has = call0(chain, py, "has_judgment")?
        .bind(py)
        .extract::<bool>()?;
    let e = if has {
        per_draw(chain, py, call0(chain, py, "entry_ix")?.bind(py))?
    } else {
        Array1::from_elem(count, BIG)
    };
    let amount = result1(&standing_amount(chain, py, day)?, py)?;
    let f = field1(chain, py, "F")?;
    let entered = num(chain, py, "entered")?;
    let cls = chain.get(py, "cls_amount")?;
    let reduced = !cls.is_none() && cls.extract::<i64>()? != entered;
    let taken = result1(&call1(chain, py, "taken_before", day)?, py)?;
    let stayed = field1(chain, py, "stayed_from")?;
    let paid = mark_array(chain, py, "paid")?;
    let settled = mark_array(chain, py, "settled")?;
    let labels = Array1::from_iter((0..count).map(|r| {
        let was_entered = has && day[r] >= e[r];
        let mut label = "unpaid";
        if reduced && day[r] >= f[r] && amount[r] > 0 {
            label = "reduced";
        }
        if taken[r] > 0 {
            label = "levied_in_part";
        }
        if stayed[r] <= day[r] {
            label = "stayed";
        }
        if was_entered && amount[r] == 0 {
            label = "set_aside";
        }
        if paid[r] <= day[r] {
            label = "paid";
        }
        if settled[r] <= day[r] {
            label = "settled";
        }
        if !was_entered {
            label = if settled[r] <= day[r] {
                "settled"
            } else {
                "none"
            };
        }
        str_obj(py, label)
    }));
    Ok(labels.into_pyarray(py).into_any().unbind())
}

fn listing_step(
    chain: &NativeChain,
    py: Python<'_>,
    node: &str,
    ctx: &str,
    branch: &str,
) -> ResultObj {
    let count = n(chain, py)?;
    let horizon = days(chain, py)? as i64;
    let d = call0(chain, py, "listing_dates")?;
    let date = |key: &str| -> PyResult<i64> { d.bind(py).get_item(key)?.extract() };
    let ev = chain.get(py, "ev")?;
    let pet = per_draw(chain, py, &ev.getattr("petition")?)?;
    if node == "listing_date" {
        let day = date(if ctx == "compliance" {
            "deadline"
        } else {
            "hearing_request"
        })?;
        return Ok(array(
            py,
            Array1::from_iter((0..count).map(|r| {
                if day < if pet[r] < 0 { BIG } else { pet[r] } {
                    day
                } else {
                    BIG
                }
            })),
        ));
    }
    let deadline = date("deadline")?;
    let on =
        Array1::from_iter((0..count).map(|r| deadline < if pet[r] < 0 { BIG } else { pet[r] }));
    if branch == "hearing" {
        if date("panel_decision")? < horizon {
            return Err(pyo3::exceptions::PyNotImplementedError::new_err(
                "the panel decides inside the period: its decision is not modeled",
            ));
        }
        let mut hearing = field1(chain, py, "hearing_requested")?;
        let day = date("hearing_request")?;
        for r in 0..count {
            if on[r] {
                hearing[r] = day;
            }
        }
        chain
            .state
            .bind(py)
            .set_item("hearing_requested", hearing.into_pyarray(py))?;
    } else if branch == "suspended" {
        let mut suspension = field1(chain, py, "suspended")?;
        let mut delisted = field1(chain, py, "delisted")?;
        let sd = date("suspension")?;
        let dd = date("delisted_suspension")?;
        for r in 0..count {
            if on[r] {
                suspension[r] = sd;
                delisted[r] = dd;
            }
        }
        chain
            .state
            .bind(py)
            .set_item("suspended", suspension.into_pyarray(py))?;
        chain
            .state
            .bind(py)
            .set_item("delisted", delisted.clone().into_pyarray(py))?;
        chain
            .state
            .bind(py)
            .set_item("_eq_v", num(chain, py, "_eq_v")?.wrapping_add(1))?;
        call(
            chain,
            py,
            "mark",
            vec![str_obj(py, "delisted"), array(py, delisted)],
        )?;
        call0(chain, py, "_atm_rebook")?;
    } else if branch != "compliant" {
        return Err(PyValueError::new_err(format!(
            "No listing branch {branch:?}"
        )));
    }
    Ok(array(
        py,
        Array1::from_iter((0..count).map(|r| if on[r] { deadline } else { BIG })),
    ))
}

fn arrears_by_class(chain: &NativeChain, py: Python<'_>, day: &Array1<i64>) -> ResultObj {
    if !flag(chain, py, "daily")? {
        return Err(PyValueError::new_err(
            "arrears are the daily cash processor's (cash_processing = daily)",
        ));
    }
    let key = process_key(chain, py, true)?;
    let aruns = arrears_runs(chain, py)?;
    if aruns.get_item(key.bind(py))?.is_none() {
        process_inner(chain, py, true, true)?;
    }
    let ar = aruns
        .get_item(key.bind(py))?
        .ok_or_else(|| PyValueError::new_err("missing processed arrears"))?;
    let ar = ar.cast::<PyArray3<i64>>()?.readonly();
    let horizon = days(chain, py)? as i64;
    let out = PyDict::new(py);
    for (c, name) in [
        "slope",
        "settlement",
        "notes_interest",
        "judgment",
        "operating",
    ]
    .iter()
    .enumerate()
    {
        out.set_item(
            name,
            Array1::from_iter((0..day.len()).map(|r| {
                if day[r] >= 0 && day[r] < horizon {
                    ar.as_array()[[r, day[r] as usize, c]]
                } else {
                    0
                }
            }))
            .into_pyarray(py),
        )?;
    }
    Ok(out.into_any().unbind())
}

pub(crate) fn dispatch(
    chain: &NativeChain,
    py: Python<'_>,
    name: &str,
    args: &Bound<'_, PyTuple>,
) -> Option<ResultObj> {
    let names = [
        "cum",
        "line_net",
        "processed",
        "_daily_run",
        "_arrears",
        "nonpayment_terms",
        "processing_balance",
        "balance_state",
        "decision_cash",
        "nonpayment_day",
        "book_default",
        "judgment_default",
        "_default_at_ruling",
        "standing_amount",
        "judgment_amount_entered",
        "judgment_standing",
        "default_available_day",
        "holder_route_days_path",
        "tau",
        "cash_out",
        "listing_step",
        "arrears_by_class",
        "response_day",
        "decide_waiting",
        "_decide_waiting",
        "decide_floor",
        "_fall_after",
        "_new_money_after",
        "distress_day",
        "decide_distress",
        "waits",
        "waiting_day",
        "response_waiting",
        "upto",
        "_upto_dated",
        "next_floor",
        "until",
        "seen_at",
        "_snapshot_day",
        "_book_to_day",
    ];
    if !names.contains(&name) {
        return None;
    }
    Some((|| match name {
        "cum" => cum(chain, py),
        "line_net" => process(chain, py, false),
        "processed" => process(chain, py, true),
        "_daily_run" => {
            let p = process_inner(chain, py, true, true)?;
            let key = process_key(chain, py, true)?;
            let ar = arrears_runs(chain, py)?
                .get_item(key.bind(py))?
                .ok_or_else(|| PyValueError::new_err("daily run did not cache arrears"))?;
            Ok(PyTuple::new(
                py,
                [
                    p.bind(py).get_item(0)?.unbind(),
                    p.bind(py).get_item(1)?.unbind(),
                    p.bind(py).get_item(2)?.unbind(),
                    ar.unbind(),
                ],
            )?
            .into_any()
            .unbind())
        }
        "_arrears" => {
            if !flag(chain, py, "daily")? {
                return Err(PyValueError::new_err(
                    "arrears are the daily cash processor's (cash_processing = daily)",
                ));
            }
            let key = process_key(chain, py, true)?;
            if arrears_runs(chain, py)?.get_item(key.bind(py))?.is_none() {
                process_inner(chain, py, true, true)?;
            }
            Ok(arrears_runs(chain, py)?
                .get_item(key.bind(py))?
                .ok_or_else(|| PyValueError::new_err("arrears cache missing after native run"))?
                .unbind())
        }
        "nonpayment_terms" => {
            let (w, s) = terms(chain, py)?;
            Ok((w, s).into_pyobject(py)?.into_any().unbind())
        }
        "balance_state" => balance_state(
            chain,
            py,
            optional(args, 0)
                .map(|x| x.is_truthy())
                .transpose()?
                .unwrap_or(false),
        ),
        "decision_cash" | "processing_balance" => {
            let day = per_draw(chain, py, &arg(args, 0)?)?;
            let at = optional(args, 1).map(Bound::unbind);
            decision_cash(chain, py, day, at, name == "processing_balance")
        }
        "nonpayment_day" => {
            if !flag(chain, py, "daily")? {
                return Err(PyValueError::new_err("general nonpayment is tested on the daily cash processor (cash_processing = daily)"));
            }
            let p = process(chain, py, true)?;
            Ok(array(py, result1(&p.bind(py).get_item(2)?.unbind(), py)?))
        }
        "judgment_default" => judgment_default(chain, py, &arg(args, 0)?.extract::<String>()?),
        "_default_at_ruling" => default_at_ruling(chain, py, &arg(args, 1)?.extract::<String>()?),
        "book_default" => {
            let rows = arg(args, 2)?;
            let rows = rows
                .cast::<PyArray1<bool>>()?
                .readonly()
                .as_array()
                .to_owned();
            book_default(
                chain,
                py,
                &arg(args, 0)?.extract::<String>()?,
                &arg(args, 1)?.extract::<String>()?,
                &rows,
            )
        }
        "standing_amount" => standing_amount(chain, py, &per_draw(chain, py, &arg(args, 0)?)?),
        "judgment_standing" => judgment_standing(chain, py, &per_draw(chain, py, &arg(args, 0)?)?),
        "listing_step" => listing_step(
            chain,
            py,
            &arg(args, 0)?.extract::<String>()?,
            &arg(args, 1)?.extract::<String>()?,
            &arg(args, 2)?.extract::<String>()?,
        ),
        "arrears_by_class" => {
            let day = optional(args, 0).unwrap_or(chain.get(py, "_at")?);
            arrears_by_class(chain, py, &per_draw(chain, py, &day)?)
        }
        "judgment_amount_entered" => {
            let count = n(chain, py)?;
            let has = call0(chain, py, "has_judgment")?
                .bind(py)
                .extract::<bool>()?;
            if !has {
                return Ok(array(py, Array1::zeros(count)));
            }
            let e = per_draw(chain, py, call0(chain, py, "entry_ix")?.bind(py))?;
            let entered = num(chain, py, "entered")?;
            let horizon = days(chain, py)? as i64;
            Ok(array(py, e.mapv(|d| if d < horizon { entered } else { 0 })))
        }
        "default_available_day" => default_available(chain, py),
        "holder_route_days_path" => holder_route_path(chain, py),
        "tau" => first_cash_day(chain, py, false),
        "cash_out" => first_cash_day(chain, py, true),
        "response_day" => response_day(chain, py, &arg(args, 0)?.extract::<String>()?),
        "_fall_after" => fall_after(chain, py, &per_draw(chain, py, &arg(args, 0)?)?),
        "_new_money_after" => new_money_after(chain, py, &per_draw(chain, py, &arg(args, 0)?)?),
        "distress_day" => distress_day(
            chain,
            py,
            &arg(args, 0)?.extract::<String>()?,
            &arg(args, 1)?.extract::<String>()?,
        ),
        "decide_distress" => decide_distress(
            chain,
            py,
            &arg(args, 0)?.extract::<String>()?,
            &arg(args, 1)?.extract::<String>()?,
            &arg(args, 2)?.extract::<String>()?,
            &per_draw(chain, py, &arg(args, 3)?)?,
        ),
        "decide_floor" => decide_floor(
            chain,
            py,
            &arg(args, 0)?.extract::<String>()?,
            &arg(args, 1)?.extract::<String>()?,
            &per_draw(chain, py, &arg(args, 2)?)?,
        ),
        "decide_waiting" | "_decide_waiting" => {
            let i = arg(args, 0)?.extract::<i64>()?;
            let node = arg(args, 1)?.extract::<String>()?;
            let branch = arg(args, 2)?.extract::<String>()?;
            let t = per_draw(chain, py, &arg(args, 3)?)?;
            if name == "decide_waiting" {
                decide_waiting(chain, py, i, &node, &branch, &t)
            } else {
                decide_waiting_inner(chain, py, i, &node, &branch, &t)
            }
        }
        "waits" => Ok(boolean(
            py,
            waits(
                chain,
                py,
                &arg(args, 0)?.extract::<String>()?,
                &arg(args, 1)?.extract::<String>()?,
            )?,
        )),
        "waiting_day" => waiting_day(
            chain,
            py,
            &arg(args, 0)?.extract::<String>()?,
            &arg(args, 1)?.extract::<String>()?,
        ),
        "response_waiting" => response_waiting(chain, py),
        "upto" | "_upto_dated" => {
            let before = optional(args, 0)
                .map(|v| per_draw(chain, py, &v))
                .transpose()?;
            let every = optional(args, 1)
                .map(|v| v.extract::<bool>())
                .transpose()?
                .unwrap_or(false);
            let levy = optional(args, 2)
                .map(|v| per_draw(chain, py, &v))
                .transpose()?;
            let moved = if name == "upto" {
                upto(chain, py, before, every, levy)?
            } else {
                upto_dated(
                    chain,
                    py,
                    before,
                    every,
                    levy,
                    optional(args, 3).map(|v| v.extract::<i64>()).transpose()?,
                )?
            };
            Ok(boolean(py, moved))
        }
        "next_floor" => next_floor(chain, py),
        "until" => {
            until(chain, py, &per_draw(chain, py, &arg(args, 0)?)?)?;
            Ok(py.None())
        }
        "seen_at" => seen_at(
            chain,
            py,
            &per_draw(chain, py, &arg(args, 0)?)?,
            optional(args, 1)
                .map(|v| v.extract::<bool>())
                .transpose()?
                .unwrap_or(false),
        ),
        "_snapshot_day" => snapshot_day(chain, py, arg(args, 0)?.extract()?),
        "_book_to_day" => {
            book_to_day(chain, py)?;
            Ok(py.None())
        }
        _ => Err(PyValueError::new_err("invalid cash helper dispatch")),
    })())
}

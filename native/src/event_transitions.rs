//! Native branch transition and decision record semantics.
use crate::event_core::{array1, asbool, bool1, call, ev, int_obj, out1, text_obj};
use crate::events::{NativeChain, BIG};
use crate::price::round_int;
use ndarray::Array1;
use numpy::{PyArray2, PyArrayMethods};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList, PySet, PyTuple};
type Obj = Py<PyAny>;
type ResultObj = PyResult<Obj>;
fn a_call(c: &NativeChain, py: Python<'_>, name: &str, day: &Array1<i64>) -> PyResult<Array1<i64>> {
    array1(&call(c, py, name, vec![out1(py, day.clone())])?, py)
}
fn b_call(
    c: &NativeChain,
    py: Python<'_>,
    name: &str,
    day: &Array1<i64>,
) -> PyResult<Array1<bool>> {
    asbool(call(c, py, name, vec![out1(py, day.clone())])?.bind(py))
}
fn mark(
    c: &NativeChain,
    py: Python<'_>,
    name: &str,
    day: &Array1<i64>,
    wh: Option<&Array1<bool>>,
) -> PyResult<()> {
    call(
        c,
        py,
        "mark",
        vec![
            text_obj(py, name),
            out1(py, day.clone()),
            wh.map(|a| bool1(py, a.clone()))
                .unwrap_or_else(|| py.None()),
        ],
    )?;
    Ok(())
}
fn pay(
    c: &NativeChain,
    py: Python<'_>,
    day: &Array1<i64>,
    amt: &Array1<i64>,
    kind: &str,
    inc: Option<&Array1<i64>>,
) -> PyResult<()> {
    call(
        c,
        py,
        "pay",
        vec![
            out1(py, day.clone()),
            out1(py, amt.clone()),
            text_obj(py, kind),
            inc.map(|a| out1(py, a.clone()))
                .unwrap_or_else(|| py.None()),
        ],
    )?;
    Ok(())
}
fn dict1<'py>(c: &NativeChain, py: Python<'py>, name: &str) -> PyResult<Bound<'py, PyDict>> {
    Ok(c.get(py, name)?.cast_into::<PyDict>()?)
}
fn view(obj: Obj, py: Python<'_>) -> PyResult<NativeChain> {
    Ok(NativeChain {
        state: obj.bind(py).cast::<PyDict>()?.clone().unbind(),
    })
}
fn seen(c: &NativeChain, py: Python<'_>, day: &Array1<i64>, levy: bool) -> PyResult<NativeChain> {
    view(
        call(
            c,
            py,
            "seen_at",
            vec![
                out1(py, day.clone()),
                levy.into_pyobject(py)?.to_owned().into_any().unbind(),
            ],
        )?,
        py,
    )
}
fn need(c: &NativeChain, py: Python<'_>, day: &Array1<i64>) -> PyResult<Array1<i64>> {
    let a = c.get(py, "basis")?.getattr("need")?;
    let read = a.cast::<PyArray2<i64>>()?.readonly();
    let a = read.as_array();
    let days = c.days(py)? as i64;
    Ok(Array1::from_iter(
        (0..day.len()).map(|r| a[[r, day[r].clamp(0, days - 1) as usize]]),
    ))
}
fn enter(c: &NativeChain, py: Python<'_>) -> PyResult<()> {
    let v = c.a1(py, "V")?;
    let n = v.len();
    let out = if c.p_str(py, "judgment_entry")? == "next_business_day" {
        let review = c
            .get(py, "s")?
            .getattr("review")?
            .call_method0("toordinal")?
            .extract::<i64>()?;
        Array1::from_iter(
            v.iter()
                .map(|d| crate::event_equity::after(review + *d + 1, 1, true) - review - 1),
        )
    } else {
        let extra =
            c.rule(py, "frcp_50b_59_deadline")? + c.p_i64(py, "briefing_days_new_motion")?;
        let lag = c.lag(py, "entry")?;
        Array1::from_iter((0..n).map(|r| v[r] + extra + lag[r]))
    };
    let stay = c.rule(py, "frcp_62a")? + 1;
    let e = out.mapv(|d| d + stay);
    c.put1(py, "E_ix", out)?;
    c.put1(py, "E0", e.mapv(|d| d.max(0)))?;
    c.put1(py, "e_ix", e)?;
    Ok(())
}
fn respond(
    c: &NativeChain,
    py: Python<'_>,
    booking: &str,
    day: &Array1<i64>,
    cause: &str,
    occasion: &str,
) -> PyResult<()> {
    let n = day.len();
    let days = c.days(py)? as i64;
    match booking {
        "pay" => {
            let live = b_call(c, py, "live", day)?;
            let cash = a_call(c, py, "cash_at", day)?;
            let owed = a_call(c, py, "owed_at", day)?;
            let ok =
                Array1::from_iter((0..n).map(|r| live[r] && day[r] < days && cash[r] >= owed[r]));
            let amt = Array1::from_iter((0..n).map(|r| if ok[r] { owed[r] } else { 0 }));
            let entry = c.call0(py, "entry_ix")?;
            let entry = c.per_draw(py, entry.bind(py))?;
            pay(
                c,
                py,
                day,
                &amt.mapv(i64::wrapping_neg),
                "judgment",
                Some(&entry),
            )?;
            let taken = c.a1(py, "taken")?;
            c.put1(
                py,
                "taken",
                Array1::from_iter((0..n).map(|r| taken[r].wrapping_add(amt[r]))),
            )?;
            c.get(py, "takes")?
                .cast::<PyList>()?
                .append((out1(py, day.clone()), out1(py, amt)))?;
            let end = if c.flag(py, "retrial")? {
                Array1::from_elem(n, false)
            } else {
                ok.clone()
            };
            call(
                c,
                py,
                "resolve",
                vec![out1(py, day.clone()), bool1(py, end)],
            )?;
            call(
                c,
                py,
                "release_lock",
                vec![out1(py, day.clone()), bool1(py, ok.clone())],
            )?;
            mark(c, py, "paid", day, Some(&ok))?;
            c.call0(py, "_atm_rebook")?;
        }
        "petition" => {
            let wh = if cause == "cash_floor" {
                day.mapv(|d| d < days)
            } else {
                let live = b_call(c, py, "live", day)?;
                Array1::from_iter((0..n).map(|r| live[r] && day[r] < days))
            };
            call(
                c,
                py,
                "petition",
                vec![out1(py, day.clone()), bool1(py, wh), text_obj(py, cause)],
            )?;
        }
        "seek" => {
            let live = b_call(c, py, "live", day)?;
            mark(c, py, "seeking", day, Some(&live))?;
        }
        "offer" => {
            call(
                c,
                py,
                "initiate",
                vec![out1(py, day.clone()), text_obj(py, occasion)],
            )?;
        }
        "none" => {}
        _ => {
            return Err(PyValueError::new_err(format!(
                "No NativeChain booking {booking:?}"
            )))
        }
    }
    Ok(())
}
fn take(c: &NativeChain, py: Python<'_>, day: &Array1<i64>) -> PyResult<()> {
    let v = seen(c, py, day, false)?;
    let live = b_call(&v, py, "live", day)?;
    let stayed = c.a1(py, "stayed_from")?;
    let days = c.days(py)? as i64;
    let n = day.len();
    let cash = a_call(
        &v,
        py,
        if c.flag(py, "daily")? {
            "processing_balance"
        } else {
            "cash_at"
        },
        day,
    )?;
    let owed = array1(
        &call(
            c,
            py,
            "owed_at",
            vec![
                out1(py, day.clone()),
                true.into_pyobject(py)?.to_owned().into_any().unbind(),
            ],
        )?,
        py,
    )?;
    let amt = Array1::from_iter((0..n).map(|r| {
        if live[r] && day[r] < stayed[r] && day[r] < days {
            owed[r].min(cash[r].max(0))
        } else {
            0
        }
    }));
    let all = a_call(c, py, "owed_at", day)?;
    let retrial = c.flag(py, "retrial")?;
    let satisfied = Array1::from_iter((0..n).map(|r| amt[r] > 0 && amt[r] >= all[r] && !retrial));
    pay(c, py, day, &amt.mapv(i64::wrapping_neg), "levy", None)?;
    let positive = amt.mapv(|v| v > 0);
    mark(c, py, "levied", day, Some(&positive))?;
    let taken = c.a1(py, "taken")?;
    c.put1(
        py,
        "taken",
        Array1::from_iter((0..n).map(|r| taken[r].wrapping_add(amt[r]))),
    )?;
    c.get(py, "takes")?
        .cast::<PyList>()?
        .append((out1(py, day.clone()), out1(py, amt.clone())))?;
    let levied = c.get(py, "levied")?;
    let levied = asbool(&levied)?;
    c.put(
        py,
        "levied",
        &bool1(
            py,
            Array1::from_iter((0..n).map(|r| levied[r] || positive[r])),
        )
        .into_bound(py),
    )?;
    if c.flag(py, "pending")? {
        call(
            c,
            py,
            "resolve",
            vec![out1(py, day.clone()), bool1(py, satisfied.clone())],
        )?;
        mark(c, py, "paid", day, Some(&satisfied))?;
    }
    c.get(py, "writs")?
        .cast::<PyList>()?
        .append((out1(py, day.clone()), out1(py, amt)))?;
    c.call0(py, "_atm_rebook")?;
    Ok(())
}
fn bond(c: &NativeChain, py: Python<'_>, day: &Array1<i64>) -> PyResult<Array1<i64>> {
    let years = c
        .parameter(py, "bond_forward_interest_years")?
        .extract::<f64>()?;
    let bps = c.int(py, "bps")?;
    let owed = a_call(c, py, "owed_at", day)?;
    let shares = c.get(py, "s")?.getattr("collateral_share")?;
    let share = if shares.is_truthy()? {
        shares.get_item(0)?.extract::<f64>()?
    } else {
        let p = c
            .get(py, "m")?
            .get_item("parameters")?
            .get_item("bond_collateral_share_bps")?;
        p.get_item(if c.sensitivity(py, "bond_collateral_share_bps")? {
            "lower"
        } else {
            "value"
        })?
        .extract::<f64>()?
            / 10_000.0
    };
    Ok(owed.mapv(|v| {
        round_int(
            (v.wrapping_add(round_int(v.wrapping_mul(bps) as f64 / 10_000.0 * years))) as f64
                * share,
        )
    }))
}
fn months(c: &NativeChain, py: Python<'_>, day: &Array1<i64>, k: i64) -> PyResult<Array1<i64>> {
    let review = c
        .get(py, "s")?
        .getattr("review")?
        .call_method0("toordinal")?
        .extract::<i64>()?;
    let date = py.import("datetime")?.getattr("date")?;
    let nday = c.days(py)? as i64;
    let mut out = day.clone();
    for (r, d) in day.iter().enumerate() {
        if *d >= nday {
            continue;
        }
        let when = date.call_method1("fromordinal", (review + *d + 1,))?;
        let y = when.getattr("year")?.extract::<i64>()?;
        let mo = when.getattr("month")?.extract::<i64>()?;
        let dd = when.getattr("day")?.extract::<i64>()?;
        let ix = mo - 1 + k;
        let yy = y + ix.div_euclid(12);
        let mm = ix.rem_euclid(12) + 1;
        let leap = yy % 4 == 0 && (yy % 100 != 0 || yy % 400 == 0);
        let mlen = match mm {
            2 => {
                if leap {
                    29
                } else {
                    28
                }
            }
            4 | 6 | 9 | 11 => 30,
            _ => 31,
        };
        out[r] = date
            .call1((yy, mm, dd.min(mlen)))?
            .call_method0("toordinal")?
            .extract::<i64>()?
            - review
            - 1;
    }
    Ok(out)
}
fn settle(
    c: &NativeChain,
    py: Python<'_>,
    start: &Array1<i64>,
    end: &Array1<i64>,
    agreed: bool,
    cap: Option<i64>,
) -> ResultObj {
    let pd = if c.sensitivity(py, "settlement_date_in_interval")? {
        end.clone()
    } else {
        let lag = c
            .get(py, "m")?
            .get_item("parameters")?
            .get_item("settlement_date_in_interval")?
            .get_item("value")?
            .extract::<i64>()?;
        start.mapv(|d| d + lag)
    };
    let v = seen(c, py, &pd, true)?;
    let n = pd.len();
    let days = c.days(py)? as i64;
    let live = b_call(&v, py, "live", &pd)?;
    let need = need(c, py, &pd)?;
    let owed = if let Some(cap) = cap {
        Array1::from_elem(n, cap)
    } else {
        a_call(&v, py, "owed_at", &pd)?
    };
    let cash = a_call(&v, py, "cash_at", &pd)?;
    let bound = Array1::from_iter((0..n).map(|r| {
        if live[r] && pd[r] < days {
            cash[r].wrapping_sub(need[r]).max(0).min(owed[r])
        } else {
            0
        }
    }));
    c.put1(py, "settle_offer", bound.clone())?;
    let ok = Array1::from_iter((0..n).map(|r| live[r] && pd[r] < days && bound[r] > 0));
    if !agreed {
        return Ok(PyTuple::new(py, [out1(py, pd), bool1(py, ok)])?
            .into_any()
            .unbind());
    }
    let m = c.get(py, "m")?;
    let parameters = m.get_item("parameters")?.cast_into::<PyDict>()?;
    let spec = parameters.get_item("settlement_payment")?;
    let monthly = m
        .get_item("settlement_scenarios")?
        .get_item("base")?
        .extract::<String>()?
        == "monthly"
        || c.sensitivity(py, "settlement_monthly")?;
    let mode = if monthly {
        "monthly".into()
    } else if spec.is_some() {
        c.p_str(py, "settlement_payment")?
    } else {
        "lump_sum".into()
    };
    let mut release = pd.clone();
    let parts = c.get(py, "settlement_parts")?.cast_into::<PyList>()?;
    if mode == "installments" {
        let count = spec
            .ok_or_else(|| PyValueError::new_err("Installment terms absent"))?
            .get_item("installments")?
            .extract::<i64>()?;
        if count <= 0 {
            return Err(PyValueError::new_err(
                "Invalid settlement installment count",
            ));
        }
        for i in 0..count {
            let part = Array1::from_iter((0..n).map(|r| {
                if ok[r] {
                    bound[r] / count + if i == count - 1 { bound[r] % count } else { 0 }
                } else {
                    0
                }
            }));
            let d = months(c, py, &pd, i)?;
            pay(
                c,
                py,
                &d,
                &part.mapv(i64::wrapping_neg),
                "settlement",
                Some(&pd),
            )?;
            parts.append((out1(py, d), out1(py, part)))?;
        }
    } else if mode == "monthly" {
        let k = pd.mapv(|d| ((days - d + 29).div_euclid(30)).max(1));
        let max = *k.iter().max().unwrap_or(&1);
        for i in 0..max {
            let part = Array1::from_iter((0..n).map(|r| {
                if ok[r] && i < k[r] {
                    bound[r] / k[r] + if i == k[r] - 1 { bound[r] % k[r] } else { 0 }
                } else {
                    0
                }
            }));
            let d = pd.mapv(|d| d + 30 * i);
            pay(
                c,
                py,
                &d,
                &part.mapv(i64::wrapping_neg),
                "settlement",
                Some(&pd),
            )?;
            parts.append((out1(py, d), out1(py, part)))?;
        }
        release = Array1::from_iter((0..n).map(|r| pd[r] + 30 * (k[r] - 1)));
    } else {
        let part = Array1::from_iter((0..n).map(|r| if ok[r] { bound[r] } else { 0 }));
        pay(
            c,
            py,
            &pd,
            &part.mapv(i64::wrapping_neg),
            "settlement",
            Some(&pd),
        )?;
        parts.append((out1(py, pd.clone()), out1(py, part)))?;
    }
    let live = b_call(c, py, "live", &release)?;
    call(
        c,
        py,
        "resolve",
        vec![
            out1(py, release),
            bool1(py, Array1::from_iter((0..n).map(|r| ok[r] && live[r]))),
        ],
    )?;
    mark(c, py, "settled", &pd, Some(&ok))?;
    c.call0(py, "_atm_rebook")?;
    Ok(PyTuple::new(py, [out1(py, pd), bool1(py, ok)])?
        .into_any()
        .unbind())
}
fn size_stay(c: &NativeChain, py: Python<'_>, st: &Bound<'_, PyDict>, read: bool) -> PyResult<()> {
    let required = |name: &str| {
        st.get_item(name)?
            .ok_or_else(|| PyValueError::new_err(format!("Stay missing {name}")))
    };
    let approval = c.per_draw(py, &required("approval")?)?;
    let n = approval.len();
    let days = c.days(py)? as i64;
    if let Some(lock) = st.get_item("lock")? {
        let lock = c.per_draw(py, &lock)?;
        call(
            c,
            py,
            "book",
            vec![
                ev(c, py, "lock")?.unbind(),
                out1(py, approval.clone()),
                out1(py, lock.mapv(i64::wrapping_neg)),
            ],
        )?;
        let rel = c.per_draw(py, &required("rel")?)?;
        let held = asbool(&required("held")?)?;
        call(
            c,
            py,
            "book",
            vec![
                ev(c, py, "lock")?.unbind(),
                out1(py, rel),
                out1(
                    py,
                    Array1::from_iter((0..n).map(|r| if held[r] { lock[r] } else { 0 })),
                ),
            ],
        )?;
    }
    let reads = c.get(py, "reads")?;
    let v = seen(c, py, &approval, true)?;
    if !read {
        c.put(py, "reads", &reads)?;
    }
    let collateral = bond(&v, py, &approval)?;
    let cash = a_call(&v, py, "cash_at", &approval)?;
    let need = need(c, py, &approval)?;
    let live = b_call(&v, py, "live", &approval)?;
    let live = Array1::from_iter((0..n).map(|r| live[r] && approval[r] < days));
    let covers = Array1::from_iter((0..n).map(|r| cash[r].wrapping_sub(need[r]) >= collateral[r]));
    let offer = Array1::from_iter((0..n).map(|r| {
        if live[r] && !covers[r] {
            cash[r].wrapping_sub(need[r]).max(0)
        } else {
            0
        }
    }));
    st.set_item("day", out1(py, approval.clone()))?;
    st.set_item("cash", out1(py, cash))?;
    st.set_item("owed", out1(py, a_call(&v, py, "owed_at", &approval)?))?;
    st.set_item("collateral", out1(py, collateral.clone()))?;
    st.set_item("stay_offer", out1(py, offer.clone()))?;
    st.set_item("petition", ev(&v, py, "petition")?.call_method0("copy")?)?;
    if read {
        c.put1(py, "stay_offer", offer.clone())?;
        c.put1(py, "collateral_required", collateral.clone())?;
    }
    if !required("approved")?.extract::<bool>()? {
        return Ok(());
    }
    let noncash = c.p_str(py, "stay_security")? == "noncash";
    let effective =
        Array1::from_iter((0..n).map(|r| live[r] && (covers[r] || offer[r] > 0 || noncash)));
    let lock = Array1::from_iter((0..n).map(|r| {
        if effective[r] && covers[r] {
            collateral[r]
        } else if effective[r] {
            offer[r]
        } else {
            0
        }
    }));
    let original = c.per_draw(py, &required("stayed_from")?)?;
    c.put1(
        py,
        "stayed_from",
        Array1::from_iter(
            (0..n).map(|r| original[r].min(if effective[r] { approval[r] } else { BIG })),
        ),
    )?;
    let rel = c.a1(py, "release_at")?;
    let held = Array1::from_iter((0..n).map(|r| lock[r] > 0 && rel[r] < days));
    let late = Array1::from_iter((0..n).map(|r| held[r] && approval[r] >= rel[r]));
    dict1(c, py, "marks")?.set_item("stayed", required("mark")?.call_method0("copy")?)?;
    mark(
        c,
        py,
        "stayed",
        &approval,
        Some(&Array1::from_iter((0..n).map(|r| effective[r] && !late[r]))),
    )?;
    let rel = Array1::from_iter((0..n).map(|r| if late[r] { approval[r] } else { rel[r] }));
    st.set_item("lock", out1(py, lock.clone()))?;
    st.set_item("held", bool1(py, held.clone()))?;
    st.set_item("rel", out1(py, rel.clone()))?;
    call(
        c,
        py,
        "book",
        vec![
            ev(c, py, "lock")?.unbind(),
            out1(py, approval.clone()),
            out1(py, lock.clone()),
        ],
    )?;
    call(
        c,
        py,
        "book",
        vec![
            ev(c, py, "lock")?.unbind(),
            out1(py, rel),
            out1(
                py,
                Array1::from_iter((0..n).map(|r| if held[r] { lock[r].wrapping_neg() } else { 0 })),
            ),
        ],
    )?;
    let amount = Array1::from_iter((0..n).map(|r| if held[r] { 0 } else { lock[r] }));
    c.put1(
        py,
        "lock_day",
        Array1::from_iter((0..n).map(|r| if amount[r] > 0 { approval[r] } else { BIG })),
    )?;
    c.put1(py, "lock_amount", amount)?;
    Ok(())
}
fn restay(c: &NativeChain, py: Python<'_>, force: bool) -> PyResult<()> {
    let stays = dict1(c, py, "stays")?;
    if !c.flag(py, "daily")? || stays.is_empty() || c.flag(py, "_restaying")? {
        return Ok(());
    }
    let key = c.call0(py, "_owed_key")?;
    let previous = c.opt(py, "_stay_owed")?;
    if c.int(py, "_stay_cv")? == c.int(py, "_cv")?
        && !force
        && previous.is_some_and(|p| p.eq(key.bind(py)).unwrap_or(false))
    {
        return Ok(());
    }
    c.put(py, "_stay_owed", key.bind(py))?;
    c.put_bool(py, "_restaying", true)?;
    let result: PyResult<()> = (|| {
        for (_, st) in stays.iter() {
            let st = st.cast::<PyDict>()?;
            if st
                .get_item("approved")?
                .ok_or_else(|| PyValueError::new_err("Stay missing approved"))?
                .extract::<bool>()?
            {
                size_stay(c, py, st, false)?;
            }
        }
        Ok(())
    })();
    c.put_bool(py, "_restaying", false)?;
    result?;
    c.put_i64(py, "_stay_cv", c.int(py, "_cv")?)?;
    Ok(())
}
fn release_lock(
    c: &NativeChain,
    py: Python<'_>,
    day: &Array1<i64>,
    wh: &Array1<bool>,
) -> PyResult<()> {
    let n = day.len();
    let days = c.days(py)? as i64;
    if c.flag(py, "daily")? {
        let old = c.a1(py, "release_at")?;
        c.put1(
            py,
            "release_at",
            Array1::from_iter((0..n).map(|r| {
                if wh[r] && day[r] < days {
                    old[r].min(day[r])
                } else {
                    old[r]
                }
            })),
        )?;
        return restay(c, py, true);
    }
    let amount = c.a1(py, "lock_amount")?;
    let held = Array1::from_iter((0..n).map(|r| wh[r] && amount[r] > 0 && day[r] < days));
    if !held.iter().any(|v| *v) {
        return Ok(());
    }
    let locked = c.a1(py, "lock_day")?;
    let late = Array1::from_iter((0..n).map(|r| held[r] && locked[r] >= day[r]));
    let d = Array1::from_iter((0..n).map(|r| if late[r] { locked[r] } else { day[r] }));
    let amt = Array1::from_iter((0..n).map(|r| if held[r] { amount[r].wrapping_neg() } else { 0 }));
    call(
        c,
        py,
        "book",
        vec![ev(c, py, "lock")?.unbind(), out1(py, d), out1(py, amt)],
    )?;
    let marks = dict1(c, py, "marks")?;
    let old = c.per_draw(
        py,
        &marks
            .get_item("stayed")?
            .ok_or_else(|| PyValueError::new_err("Missing stayed mark"))?,
    )?;
    marks.set_item(
        "stayed",
        out1(
            py,
            Array1::from_iter((0..n).map(|r| if late[r] { BIG } else { old[r] })),
        ),
    )?;
    c.put1(
        py,
        "lock_amount",
        Array1::from_iter((0..n).map(|r| if held[r] { 0 } else { amount[r] })),
    )?;
    c.put1(
        py,
        "lock_day",
        Array1::from_iter((0..n).map(|r| if held[r] { BIG } else { locked[r] })),
    )?;
    Ok(())
}
fn stay_security(
    c: &NativeChain,
    py: Python<'_>,
    motion: &Array1<i64>,
    key: &str,
    approved: bool,
) -> PyResult<Array1<i64>> {
    let lag = c.lag(py, key)?;
    let extra = c.p_i64(py, "briefing_days_new_motion")?;
    let approval = Array1::from_iter((0..motion.len()).map(|r| motion[r] + extra + lag[r]));
    if c.flag(py, "daily")? {
        if approved {
            let live = b_call(c, py, "live", motion)?;
            mark(c, py, "stay_moved", motion, Some(&live))?;
        }
        let st = PyDict::new(py);
        st.set_item("approval", out1(py, approval.clone()))?;
        st.set_item("approved", approved)?;
        st.set_item(
            "stayed_from",
            c.get(py, "stayed_from")?.call_method0("copy")?,
        )?;
        st.set_item(
            "mark",
            dict1(c, py, "marks")?
                .get_item("stayed")?
                .ok_or_else(|| PyValueError::new_err("Missing stayed"))?
                .call_method0("copy")?,
        )?;
        st.set_item("triggers", c.call0(py, "trigger_days")?)?;
        let i = c.get(py, "rec")?.get_item(0)?.len()?;
        dict1(c, py, "stays")?.set_item(i, &st)?;
        size_stay(c, py, &st, true)?;
        return Ok(approval);
    }
    let v = seen(c, py, &approval, true)?;
    let collateral = bond(&v, py, &approval)?;
    let cash = a_call(&v, py, "cash_at", &approval)?;
    let need = need(c, py, &approval)?;
    let live = b_call(&v, py, "live", &approval)?;
    let n = motion.len();
    let days = c.days(py)? as i64;
    let covers = Array1::from_iter((0..n).map(|r| cash[r].wrapping_sub(need[r]) >= collateral[r]));
    let offer = Array1::from_iter((0..n).map(|r| {
        if live[r] && approval[r] < days && !covers[r] {
            cash[r].wrapping_sub(need[r]).max(0)
        } else {
            0
        }
    }));
    c.put1(py, "stay_offer", offer.clone())?;
    c.put1(py, "collateral_required", collateral.clone())?;
    if !approved {
        return Ok(approval);
    }
    let noncash = c.p_str(py, "stay_security")? == "noncash";
    let effective =
        Array1::from_iter((0..n).map(|r| live[r] && (covers[r] || offer[r] > 0 || noncash)));
    let lock = Array1::from_iter((0..n).map(|r| {
        if covers[r] {
            collateral[r]
        } else if offer[r] > 0 {
            offer[r]
        } else {
            0
        }
    }));
    let moved = b_call(c, py, "live", motion)?;
    mark(c, py, "stay_moved", motion, Some(&moved))?;
    mark(c, py, "stayed", &approval, Some(&effective))?;
    let old = c.a1(py, "stayed_from")?;
    c.put1(
        py,
        "stayed_from",
        Array1::from_iter((0..n).map(|r| {
            old[r].min(if effective[r] && approval[r] < days {
                approval[r]
            } else {
                BIG
            })
        })),
    )?;
    let amount = Array1::from_iter((0..n).map(|r| if effective[r] { lock[r] } else { 0 }));
    c.put1(
        py,
        "lock_day",
        Array1::from_iter((0..n).map(|r| if amount[r] > 0 { approval[r] } else { BIG })),
    )?;
    c.put1(py, "lock_amount", amount.clone())?;
    call(
        c,
        py,
        "book",
        vec![
            ev(c, py, "lock")?.unbind(),
            out1(py, approval.clone()),
            out1(py, amount),
        ],
    )?;
    Ok(approval)
}
fn answers_levy(node: &str, ctx: &str) -> bool {
    ["debtor_response", "judgment_response"].contains(&node) && ["I1", "post"].contains(&ctx)
}
fn group(c: &NativeChain, py: Python<'_>, node: &str, day: &Array1<i64>) -> PyResult<()> {
    if [
        "debtor_response",
        "judgment_response",
        "cash_floor",
        "cash_out",
    ]
    .contains(&node)
        && (c.flag(py, "pending")? || c.flag(py, "equity")?)
    {
        let value = call(
            c,
            py,
            "option_group",
            vec![text_obj(py, node), out1(py, day.clone())],
        )?;
        c.put(py, "_grp", value.bind(py))?;
    } else {
        c.put(py, "_grp", &py.None().into_bound(py))?;
    }
    Ok(())
}
fn step(
    c: &NativeChain,
    py: Python<'_>,
    node: &str,
    ctx: &str,
    branch: &str,
) -> PyResult<Array1<i64>> {
    let n = c.n(py)?;
    let days = c.days(py)? as i64;
    let full = |d: i64| Array1::from_elem(n, d);
    let waiting = c.get(py, "waiting")?;
    if !answers_levy(node, ctx) && waiting.len()? == 0 {
        c.call0(py, "flush_levy")?;
    }
    match node {
        "settle" => {
            let (start, end) = if ctx == "I0" {
                (full(-1), c.a1(py, "V")?)
            } else {
                let e1 = if c.flag(py, "pending")? {
                    c.a1(py, "E_ix")?
                } else {
                    full(-1)
                };
                let f = c.a1(py, "F")?;
                let ad = c.a1(py, "AD")?;
                let stayed = c.a1(py, "stayed_from")?;
                match ctx {
                    "I1" => (e1, f),
                    "I2" => (f, ad),
                    "I3" => {
                        let ef = c.a1(py, "EF")?;
                        (
                            Array1::from_iter((0..n).map(|r| ef[r].max(ad[r]))),
                            full(days - 1),
                        )
                    }
                    "I4" => (
                        if c.flag(py, "pending")? {
                            Array1::from_iter((0..n).map(|r| stayed[r].max(f[r])))
                        } else {
                            stayed
                        },
                        full(days - 1),
                    ),
                    _ => return Err(PyValueError::new_err("Unknown settlement context")),
                }
            };
            let start = start.mapv(|d| d.max(-1));
            let cap = if ctx == "I0" {
                Some(c.call0(py, "claimed")?.bind(py).extract::<i64>()?)
            } else {
                None
            };
            settle(c, py, &start, &end, branch == "yes", cap)?;
            Ok(Array1::from_iter((0..n).map(|r| {
                if end[r] < 0 {
                    BIG
                } else {
                    start[r].max(0)
                }
            })))
        }
        "execute_pre_ruling" => {
            c.put_bool(py, "q1", branch == "yes")?;
            let day = c.a1(py, "E0")?;
            if branch == "yes" {
                mark(c, py, "executing", &day, None)?;
            }
            Ok(day)
        }
        "stay" => {
            let day = if ctx == "I1" {
                c.a1(py, "E0")?
            } else {
                c.a1(py, "F")?.mapv(|d| d.max(0))
            };
            stay_security(c, py, &day, &format!("stay_{ctx}"), branch == "yes")?;
            Ok(day)
        }
        "court_order" => {
            let (kind, phase) = ctx
                .split_once('_')
                .ok_or_else(|| PyValueError::new_err("Court-order context"))?;
            let motion = if phase == "I1" {
                c.a1(py, "E0")?
            } else if kind == "stay" {
                c.a1(py, "F")?.mapv(|d| d.max(0))
            } else {
                c.a1(py, "EF")?
            };
            if kind == "stay" {
                stay_security(c, py, &motion, &format!("stay_{phase}"), false)
            } else {
                let lag = c.lag(py, &format!("registration_{phase}"))?;
                let extra = c.p_i64(py, "briefing_days_new_motion")?;
                Ok(Array1::from_iter(
                    (0..n).map(|r| motion[r] + extra + lag[r]),
                ))
            }
        }
        "debtor_response" | "judgment_response" => {
            let day = array1(&call(c, py, "response_day", vec![text_obj(py, ctx)])?, py)?;
            let offer = a_call(c, py, "offer_available", &day)?;
            c.put1(py, "raise_offer", offer)?;
            group(c, py, node, &day)?;
            let booking = call(
                c,
                py,
                "booking",
                vec![text_obj(py, node), text_obj(py, branch)],
            )?
            .bind(py)
            .extract::<String>()?;
            respond(c, py, &booking, &day, "enforcement", ctx)?;
            Ok(day)
        }
        "registration_early" => {
            let motion = if ctx == "I1" {
                c.a1(py, "E0")?
            } else {
                c.a1(py, "F")?
            };
            let lag = c.lag(py, &format!("registration_{ctx}"))?;
            let extra = c.p_i64(py, "briefing_days_new_motion")?;
            let order = Array1::from_iter((0..n).map(|r| motion[r] + extra + lag[r]));
            if branch == "yes" {
                let old = c.a1(py, "early_registration")?;
                c.put1(
                    py,
                    "early_registration",
                    Array1::from_iter((0..n).map(|r| old[r].min(order[r]))),
                )?;
                if ctx == "I1" {
                    let lag = c.p_i64(py, "levy_lag_days")?;
                    c.put1(py, "pending_levy", order.mapv(|d| d + lag))?;
                } else {
                    call(c, py, "levy", vec![out1(py, order)])?;
                }
            }
            Ok(motion)
        }
        "judgment_default" => array1(
            &call(
                c,
                py,
                "book_default",
                vec![
                    text_obj(py, ctx),
                    text_obj(py, branch),
                    bool1(py, Array1::from_elem(n, true)),
                ],
            )?,
            py,
        ),
        "ruling" => {
            let retrial = branch == "retrial" || branch.ends_with(":retrial");
            c.put_bool(py, "retrial", retrial)?;
            let amount = if branch == "none" || branch == "retrial" {
                0
            } else {
                let parts = branch.split(':').collect::<Vec<_>>();
                if parts.len() < 3 {
                    return Err(PyValueError::new_err("Malformed ruling branch"));
                }
                let total = parts[1]
                    .parse::<i64>()
                    .map_err(|_| PyValueError::new_err("Malformed ruling amount"))?;
                let fees = parts[2]
                    .parse::<i64>()
                    .map_err(|_| PyValueError::new_err("Malformed ruling fees"))?;
                c.put_i64(py, "cls_fees", fees)?;
                total
            };
            c.put_i64(py, "cls_amount", amount)?;
            let f = c.a1(py, "F")?;
            if branch == "none" {
                let live = b_call(c, py, "live", &f)?;
                call(
                    c,
                    py,
                    "resolve",
                    vec![out1(py, f.mapv(|d| d.max(0))), bool1(py, live)],
                )?;
            } else if branch == "retrial" {
                let live = b_call(c, py, "live", &f)?;
                release_lock(c, py, &f.mapv(|d| d.max(0)), &live)?;
            }
            mark(c, py, "ruled", &f, None)?;
            let increase = (amount - c.int(py, "entered")?).max(0);
            c.put_i64(py, "increase", increase)?;
            let restart = c
                .get(py, "m")?
                .get_item("parameters")?
                .get_item("stay_restart_on_increase_days")?;
            let mode = restart
                .get_item(if c.sensitivity(py, "stay_restart_on_increase_days")? {
                    "sensitivity"
                } else {
                    "base"
                })?
                .extract::<String>()?;
            let lag = if increase > 0 {
                restart.get_item("value")?.extract::<i64>()?
            } else {
                0
            };
            let ei = f.mapv(|d| d + lag);
            c.put1(py, "EI", ei.clone())?;
            c.put1(
                py,
                "EF",
                if mode == "whole_amount" {
                    ei
                } else {
                    f.clone()
                },
            )?;
            Ok(f)
        }
        "appeal" => {
            c.put_bool(py, "appealed", branch == "yes")?;
            let f = c.a1(py, "F")?;
            let ad = c.a1(py, "AD")?;
            let day = Array1::from_iter((0..n).map(|r| if ad[r] < 0 { BIG } else { f[r].max(0) }));
            if branch == "yes" {
                let live = b_call(c, py, "live", &day)?;
                mark(c, py, "appealed", &day, Some(&live))?;
            }
            Ok(day)
        }
        "enforce" => {
            let ef = c.a1(py, "EF")?;
            if branch == "levy" {
                let er = c.a1(py, "early_registration")?;
                let ad = c.a1(py, "AD")?;
                let mut day = Array1::from_iter((0..n).map(|r| {
                    if er[r] < BIG {
                        ef[r].max(er[r])
                    } else {
                        ad[r] + 1
                    }
                }));
                if c.flag(py, "appealed")? {
                    let lag = c.lag(py, "registration_post")?;
                    let extra = c.p_i64(py, "briefing_days_new_motion")?;
                    day = Array1::from_iter((0..n).map(|r| {
                        if er[r] < BIG {
                            ef[r].max(er[r])
                        } else {
                            ef[r] + extra + lag[r]
                        }
                    }));
                }
                let lag = c.p_i64(py, "levy_lag_days")?;
                c.put1(py, "pending_levy", day.mapv(|d| d.max(0) + lag))?;
            }
            Ok(ef.mapv(|d| d.max(0)))
        }
        "listing" | "listing_date" => {
            if c.flag(py, "equity")? {
                return array1(
                    &call(
                        c,
                        py,
                        "listing_step",
                        vec![text_obj(py, node), text_obj(py, ctx), text_obj(py, branch)],
                    )?,
                    py,
                );
            }
            let dates = c.call0(py, "listing_dates")?;
            let dates = dates.bind(py).cast::<PyDict>()?;
            let get = |key: &str| {
                dates
                    .get_item(key)?
                    .ok_or_else(|| PyValueError::new_err("Missing listing date"))?
                    .extract::<i64>()
            };
            let gate = get(if ctx == "kept" {
                "hearing_request"
            } else {
                "vote_call"
            })?;
            let due = c.per_draw(
                py,
                &dict1(c, py, "marks")?
                    .get_item("notes_due")?
                    .ok_or_else(|| PyValueError::new_err("Missing notes_due"))?,
            )?;
            if node == "listing_date" {
                let day = get(if ctx == "kept" {
                    "hearing_request"
                } else {
                    ctx
                })?;
                return Ok(due.mapv(|d| if d > gate { day } else { BIG }));
            }
            if branch.starts_with("delisted") {
                let d = get(branch)?;
                let delisted = due.mapv(|v| if v > gate { d } else { BIG });
                c.put1(py, "delisted", delisted.clone())?;
                c.put_i64(py, "_eq_v", c.int(py, "_eq_v")? + 1)?;
                mark(c, py, "delisted", &delisted, None)?;
                let day = if ctx == "kept" {
                    gate
                } else {
                    get("determination")?
                };
                Ok(due.mapv(|d| if d > gate { day } else { BIG }))
            } else {
                Ok(due.mapv(|d| if d > gate { gate } else { BIG }))
            }
        }
        "delisting_notes" => {
            let dl = c.a1(py, "delisted")?;
            let pet = c.per_draw(py, &ev(c, py, "petition")?)?;
            let alive = if c.flag(py, "pending")? || c.flag(py, "ordinary")? {
                Array1::from_iter((0..n).map(|r| dl[r] < if pet[r] < 0 { BIG } else { pet[r] }))
            } else {
                b_call(c, py, "live", &dl)?
            };
            let due = c.per_draw(
                py,
                &dict1(c, py, "marks")?
                    .get_item("notes_due")?
                    .ok_or_else(|| PyValueError::new_err("Missing notes_due"))?,
            )?;
            let open =
                Array1::from_iter((0..n).map(|r| alive[r] && due[r] > dl[r] && dl[r] < days));
            let lag = c.p_i64(py, "holder_notice_lag_days")?;
            let accel = dl.mapv(|d| d + lag);
            let mut rep = full(BIG);
            for r in 0..n {
                if dl[r] < days {
                    rep[r] = call(c, py, "repurchase_day", vec![int_obj(py, dl[r])])?
                        .bind(py)
                        .extract()?;
                }
            }
            if branch.starts_with("petition_delist") || branch == "accelerated" {
                mark(c, py, "notes_due", &accel, Some(&open))?;
            } else if branch.starts_with("petition_repurchase") || branch == "repurchase_unpaid" {
                mark(c, py, "notes_due", &rep, Some(&open))?;
            }
            c.call0(py, "coupon_when_due")?;
            let route = if branch.ends_with("_holders") {
                c.call0(py, "holder_route_days")?
                    .bind(py)
                    .extract::<i64>()?
            } else {
                0
            };
            let p = match branch {
                "petition_delist" | "petition_delist_holders" => Some(accel.mapv(|d| d + route)),
                "petition_repurchase" | "petition_repurchase_holders" => {
                    Some(rep.mapv(|d| d + route))
                }
                _ => None,
            };
            if let Some(p) = p {
                call(
                    c,
                    py,
                    "petition",
                    vec![out1(py, p), bool1(py, open.clone()), text_obj(py, "notes")],
                )?;
            }
            Ok(Array1::from_iter((0..n).map(|r| {
                if open[r] {
                    dl[r]
                } else {
                    BIG
                }
            })))
        }
        "notes_due_date" => {
            let due = c.per_draw(
                py,
                &dict1(c, py, "marks")?
                    .get_item("notes_due")?
                    .ok_or_else(|| PyValueError::new_err("Missing notes_due"))?,
            )?;
            let f = c.a1(py, "F")?;
            mark(c, py, "ruled", &f, None)?;
            let route = if c.flag(py, "pending")? {
                array1(&c.call0(py, "holder_route_days_path")?, py)?
            } else {
                full(c.call0(py, "holder_route_days")?.bind(py).extract()?)
            };
            Ok(Array1::from_iter((0..n).map(|r| {
                if due[r] < BIG {
                    due[r] + if ctx == "holders" { route[r] } else { 0 }
                } else {
                    BIG
                }
            })))
        }
        "cash_floor" | "cash_out" | "offering" | "nonpayment" => {
            if c.flag(py, "equity")? {
                let day = array1(
                    &call(
                        c,
                        py,
                        "distress_day",
                        vec![text_obj(py, node), text_obj(py, ctx)],
                    )?,
                    py,
                )?;
                group(c, py, node, &day)?;
                let offer = call(
                    c,
                    py,
                    "decide_distress",
                    vec![
                        text_obj(py, node),
                        text_obj(py, ctx),
                        text_obj(py, branch),
                        out1(py, day.clone()),
                    ],
                )?;
                c.put(py, "raise_offer", offer.bind(py))?;
                Ok(day)
            } else if node == "cash_floor" || node == "cash_out" {
                let day = array1(
                    &c.call0(
                        py,
                        if node == "cash_floor" {
                            "tau"
                        } else {
                            "cash_out"
                        },
                    )?,
                    py,
                )?;
                let offer = call(
                    c,
                    py,
                    "decide_floor",
                    vec![
                        text_obj(py, node),
                        text_obj(py, branch),
                        out1(py, day.clone()),
                    ],
                )?;
                c.put(py, "raise_offer", offer.bind(py))?;
                Ok(day)
            } else {
                Err(PyValueError::new_err("Distress node requires equity model"))
            }
        }
        "verdict" => {
            let v = c.a1(py, "V")?;
            if branch.starts_with("award:") {
                let p = branch.split(':').collect::<Vec<_>>();
                if p.len() != 4 {
                    return Err(PyValueError::new_err("Malformed award branch"));
                }
                let total = p[1]
                    .parse::<i64>()
                    .map_err(|_| PyValueError::new_err("Malformed award amount"))?;
                let lo = p[2]
                    .parse::<i64>()
                    .map_err(|_| PyValueError::new_err("Malformed award low"))?;
                let hi = if p[3] == "top" {
                    None
                } else {
                    Some(
                        p[3].parse::<i64>()
                            .map_err(|_| PyValueError::new_err("Malformed award high"))?,
                    )
                };
                c.put_i64(py, "entered", total)?;
                c.put(
                    py,
                    "band",
                    &format!("{}-{}", p[2], p[3]).into_pyobject(py)?.into_any(),
                )?;
                c.state.bind(py).set_item("band_range", (lo, hi))?;
                enter(c, py)?;
                if p[3] == "top" {
                    c.put1(py, "adverse_from", c.a1(py, "E_ix")?)?;
                }
            } else {
                let template = crate::events_free::pending_template(py, &c.get(py, "m")?)?;
                let spec = template.get_item("verdict_branches")?.get_item(branch)?;
                if spec.get_item("judgment")?.is_truthy()? {
                    let amount = crate::events_free::verdict_amount(
                        py,
                        &c.get(py, "d")?,
                        &c.get(py, "m")?,
                        branch,
                        Some(&c.get(py, "sens")?),
                    )?;
                    c.put_i64(py, "entered", amount)?;
                    enter(c, py)?;
                    let spec = spec.cast::<PyDict>()?;
                    if spec
                        .get_item("adverse")?
                        .is_some_and(|v| v.is_truthy().unwrap_or(false))
                    {
                        c.put1(py, "adverse_from", c.a1(py, "E_ix")?)?;
                    }
                }
            }
            Ok(v)
        }
        "post_trial_motions" => {
            let entry = c.a1(py, "E_ix")?;
            let extra = c.rule(py, "frcp_50b_59_deadline")?;
            let filed = entry.mapv(|d| d + extra);
            let f = if branch == "yes" {
                let mut lag = c.lag(py, "common")?;
                if c.sensitivity(py, "ruling_lag_days")? {
                    let second = c.lag(py, "second")?;
                    for r in 0..n {
                        lag[r] = lag[r].max(second[r]);
                    }
                }
                let extra = c.p_i64(py, "briefing_days_new_motion")?;
                Array1::from_iter((0..n).map(|r| filed[r] + extra + lag[r]))
            } else {
                mark(c, py, "ruled", &filed, None)?;
                entry
            };
            let notice = c.rule(py, "frap_4a1a")?;
            c.put1(py, "AD", f.mapv(|d| d + notice))?;
            for key in ["F", "A", "EF", "EI"] {
                c.put1(py, key, f.clone())?;
            }
            Ok(filed)
        }
        "post_trial_ruling" => {
            let f = c.a1(py, "F")?;
            if branch.starts_with("reduced:") {
                let amount = branch
                    .split(':')
                    .nth(1)
                    .ok_or_else(|| PyValueError::new_err("Malformed reduced amount"))?
                    .parse::<i64>()
                    .map_err(|_| PyValueError::new_err("Malformed reduced amount"))?;
                c.put_i64(py, "cls_amount", amount)?;
                let live = b_call(c, py, "live", &f)?;
                c.put1(
                    py,
                    "remitted_amount",
                    Array1::from_iter(
                        (0..n).map(|r| if live[r] && f[r] < days { amount } else { 0 }),
                    ),
                )?;
            }
            if branch == "set_aside" {
                c.put_i64(py, "cls_amount", 0)?;
                c.put_bool(py, "retrial", true)?;
                let old = c.a1(py, "adverse_until")?;
                c.put1(
                    py,
                    "adverse_until",
                    Array1::from_iter((0..n).map(|r| old[r].min(f[r]))),
                )?;
                let live = b_call(c, py, "live", &f)?;
                release_lock(c, py, &f.mapv(|d| d.max(0)), &live)?;
            }
            mark(c, py, "ruled", &f, None)?;
            c.put_i64(py, "increase", 0)?;
            c.put1(py, "EF", f.clone())?;
            c.put1(py, "EI", f.clone())?;
            Ok(f)
        }
        _ => Err(PyValueError::new_err(format!(
            "Unknown native chain step {node:?}"
        ))),
    }
}
fn advance(c: &NativeChain, py: Python<'_>, node: &str, ctx: &str, branch: &str) -> PyResult<()> {
    let branch = if branch.starts_with('@') {
        branch
            .split_once('=')
            .ok_or_else(|| PyValueError::new_err("Malformed grouped branch"))?
            .1
    } else {
        branch
    };
    let n = c.n(py)?;
    let days = c.days(py)? as i64;
    let waits = call(c, py, "waits", vec![text_obj(py, node), text_obj(py, ctx)])?
        .bind(py)
        .extract::<bool>()?;
    let waiting = c.get(py, "waiting")?.cast_into::<PyList>()?;
    if !waits && waiting.is_empty() && !answers_levy(node, ctx) {
        c.call0(py, "flush_levy")?;
    }
    for key in ["settle_offer", "stay_offer", "raise_offer"] {
        c.put1(py, key, Array1::zeros(n))?;
    }
    c.put1(py, "reads", Array1::from_elem(n, -1))?;
    let rec = c.get(py, "rec")?;
    if waits {
        let i = rec.get_item(0)?.len()?;
        for k in 0..4 {
            rec.get_item(k)?
                .cast::<PyList>()?
                .append(out1(py, Array1::from_elem(n, if k == 0 { BIG } else { 0 })))?;
        }
        let late = PyDict::new(py);
        late.set_item("petition", ev(c, py, "petition")?.call_method0("copy")?)?;
        late.set_item("triggers", PyDict::new(py))?;
        late.set_item("raise_offer", out1(py, Array1::zeros(n)))?;
        dict1(c, py, "late")?.set_item(i, late)?;
        waiting.append(PyList::new(
            py,
            [
                i.into_pyobject(py)?.into_any().unbind(),
                text_obj(py, node),
                text_obj(py, branch),
                bool1(py, Array1::from_elem(n, false)),
                text_obj(py, ctx),
            ],
        )?)?;
        dict1(c, py, "wctx")?.set_item(i, ctx)?;
        return Ok(());
    }
    c.put(py, "_last_node", &node.into_pyobject(py)?.into_any())?;
    if node == "notes_due_date" && !waiting.is_empty() {
        call(c, py, "until", vec![out1(py, Array1::from_elem(n, days))])?;
    }
    if !waiting.is_empty() {
        let clone = c.call0(py, "clone")?;
        let probe = view(clone, py)?;
        probe.put(py, "waiting", &PyList::empty(py).into_any())?;
        let d = step(&probe, py, node, ctx, branch)?;
        call(
            c,
            py,
            "until",
            vec![out1(py, d.mapv(|d| if d < days { d } else { -1 }))],
        )?;
        let pending = c.get(py, "pending_levy")?;
        if !answers_levy(node, ctx) && !pending.is_none() {
            let pending = c.per_draw(py, &pending)?;
            let floor = array1(&c.call0(py, "next_floor")?, py)?;
            let response = asbool(c.call0(py, "response_waiting")?.bind(py))?;
            call(
                c,
                py,
                "flush_levy",
                vec![bool1(
                    py,
                    Array1::from_iter((0..n).map(|r| !(floor[r] < pending[r]) && !response[r])),
                )],
            )?;
        }
    }
    restay(c, py, false)?;
    let before = if c.flag(py, "daily")? {
        let own = if let Some(own) = c.opt(py, "_ev_own")? {
            own.cast_into::<PySet>()?
        } else {
            let own = PySet::empty(py)?;
            c.put(py, "_ev_own", &own.clone().into_any())?;
            own
        };
        for key in ["k:inflow", "lock", "k:levy"] {
            own.discard(key)?;
        }
        c.call0(py, "balance_state")?
    } else {
        PyTuple::new(py, [c.call0(py, "cum")?])?.into_any().unbind()
    };
    c.put(py, "_grp", &py.None().into_bound(py))?;
    let day = step(c, py, node, ctx, branch)?;
    c.call0(py, "_atm_rebook")?;
    let grp = c.get(py, "_grp")?;
    if !grp.is_none() {
        dict1(c, py, "grec")?.set_item(rec.get_item(0)?.len()?, grp)?;
    }
    let cash = call(c, py, "decision_cash", vec![out1(py, day.clone()), before])?;
    let owed = a_call(c, py, "owed_at", &day)?;
    let collateral = c
        .get(py, "collateral_required")?
        .call_method0("copy")?
        .unbind();
    let values = [out1(py, day), cash, out1(py, owed), collateral];
    for (k, value) in values.into_iter().enumerate() {
        rec.get_item(k)?.cast::<PyList>()?.append(value)?;
    }
    restay(c, py, false)?;
    Ok(())
}
pub(crate) fn dispatch(
    c: &NativeChain,
    py: Python<'_>,
    name: &str,
    args: &Bound<'_, PyTuple>,
) -> Option<ResultObj> {
    Some((|| match name {
        "_enter" => {
            enter(c, py)?;
            Ok(py.None())
        }
        "respond" => {
            let booking = args.get_item(0)?.extract::<String>()?;
            let day = c.per_draw(py, &args.get_item(1)?)?;
            let cause = args
                .get_item(2)
                .ok()
                .map(|v| v.extract::<String>())
                .transpose()?
                .unwrap_or_else(|| "enforcement".into());
            let occasion = args
                .get_item(3)
                .ok()
                .map(|v| v.extract::<String>())
                .transpose()?
                .unwrap_or_default();
            respond(c, py, &booking, &day, &cause, &occasion)?;
            Ok(py.None())
        }
        "_take" => {
            take(c, py, &c.per_draw(py, &args.get_item(0)?)?)?;
            Ok(py.None())
        }
        "levy" => {
            let lagged = args
                .get_item(1)
                .ok()
                .is_some_and(|v| v.is_truthy().unwrap_or(false));
            let lag = if lagged {
                0
            } else {
                c.p_i64(py, "levy_lag_days")?
            };
            let day = c.per_draw(py, &args.get_item(0)?)?.mapv(|d| d + lag);
            take(c, py, &day)?;
            if c.int(py, "increase")? != 0 {
                restay(c, py, false)?;
                let ei = c.a1(py, "EI")?;
                take(
                    c,
                    py,
                    &Array1::from_iter((0..day.len()).map(|r| {
                        if day[r] < ei[r] {
                            ei[r]
                        } else {
                            BIG
                        }
                    })),
                )?;
            }
            Ok(py.None())
        }
        "flush_levy" => {
            let pending = c.get(py, "pending_levy")?;
            if !pending.is_none() {
                let day = c.per_draw(py, &pending)?;
                let rows = args
                    .get_item(0)
                    .ok()
                    .filter(|v| !v.is_none())
                    .map(|v| asbool(&v))
                    .transpose()?
                    .unwrap_or_else(|| Array1::from_elem(day.len(), true));
                let rest =
                    Array1::from_iter((0..day.len()).map(|r| if rows[r] { BIG } else { day[r] }));
                if rest.iter().all(|d| *d >= BIG) {
                    c.put(py, "pending_levy", &py.None().into_bound(py))?;
                } else {
                    c.put1(py, "pending_levy", rest)?;
                }
                let day =
                    Array1::from_iter((0..day.len()).map(|r| if rows[r] { day[r] } else { BIG }));
                call(
                    c,
                    py,
                    "levy",
                    vec![
                        out1(py, day),
                        true.into_pyobject(py)?.to_owned().into_any().unbind(),
                    ],
                )?;
            }
            Ok(py.None())
        }
        "bond_collateral" => Ok(out1(py, bond(c, py, &c.per_draw(py, &args.get_item(0)?)?)?)),
        "months_after" => Ok(out1(
            py,
            months(
                c,
                py,
                &c.per_draw(py, &args.get_item(0)?)?,
                args.get_item(1)?.extract()?,
            )?,
        )),
        "settle" => settle(
            c,
            py,
            &c.per_draw(py, &args.get_item(0)?)?,
            &c.per_draw(py, &args.get_item(1)?)?,
            args.get_item(2)
                .ok()
                .map(|v| v.extract::<bool>())
                .transpose()?
                .unwrap_or(true),
            args.get_item(3)
                .ok()
                .filter(|v| !v.is_none())
                .map(|v| v.extract())
                .transpose()?,
        ),
        "stay_security" => Ok(out1(
            py,
            stay_security(
                c,
                py,
                &c.per_draw(py, &args.get_item(0)?)?,
                &args.get_item(1)?.extract::<String>()?,
                args.get_item(2)?.extract()?,
            )?,
        )),
        "release_lock" => {
            release_lock(
                c,
                py,
                &c.per_draw(py, &args.get_item(0)?)?,
                &asbool(&args.get_item(1)?)?,
            )?;
            Ok(py.None())
        }
        "_size_stay" => {
            size_stay(
                c,
                py,
                args.get_item(0)?.cast::<PyDict>()?,
                args.get_item(1)?.extract()?,
            )?;
            Ok(py.None())
        }
        "restay" => {
            restay(
                c,
                py,
                args.get_item(0)
                    .ok()
                    .map(|v| v.extract::<bool>())
                    .transpose()?
                    .unwrap_or(false),
            )?;
            Ok(py.None())
        }
        "claimed" => {
            let model = c.get(py, "m")?;
            let template = crate::events_free::pending_template(py, &model)?;
            let branches = template
                .get_item("verdict_branches")?
                .cast_into::<PyDict>()?;
            let mut max = None;
            for (branch, _) in branches.iter() {
                let branch = branch.extract::<String>()?;
                let amount = crate::events_free::verdict_amount(
                    py,
                    &c.get(py, "d")?,
                    &model,
                    &branch,
                    Some(&c.get(py, "sens")?),
                )?;
                max = Some(max.map_or(amount, |v: i64| v.max(amount)));
            }
            Ok(int_obj(
                py,
                max.ok_or_else(|| PyValueError::new_err("No verdict branches"))?,
            ))
        }
        "step" => Ok(out1(
            py,
            step(
                c,
                py,
                &args.get_item(0)?.extract::<String>()?,
                &args.get_item(1)?.extract::<String>()?,
                &args.get_item(2)?.extract::<String>()?,
            )?,
        )),
        "advance" => {
            advance(
                c,
                py,
                &args.get_item(1)?.extract::<String>()?,
                &args.get_item(2)?.extract::<String>()?,
                &args.get_item(3)?.extract::<String>()?,
            )?;
            Ok(py.None())
        }
        "run" => {
            c.call0(py, "instrument_cash")?;
            let tr = py
                .import("app.analysis.events")?
                .getattr("Trace")?
                .call1((c.get(py, "ev")?,))?;
            for item in args.get_item(0)?.try_iter()? {
                let item = item?;
                advance(
                    c,
                    py,
                    &item.get_item(0)?.extract::<String>()?,
                    &item.get_item(1)?.extract::<String>()?,
                    &item.get_item(2)?.extract::<String>()?,
                )?;
            }
            let only = args
                .get_item(1)
                .ok()
                .map(|v| v.extract::<bool>())
                .transpose()?
                .unwrap_or(false);
            call(
                c,
                py,
                "finish",
                vec![
                    tr.unbind(),
                    only.into_pyobject(py)?.to_owned().into_any().unbind(),
                ],
            )
        }
        _ => Err(PyValueError::new_err(format!("__not_transition__{name}"))),
    })())
    .filter(|r| !matches!(r,Err(e)if e.to_string().contains("__not_transition__")))
}

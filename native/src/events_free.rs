//! Domain arithmetic over immutable dispute/model inputs.
use pyo3::exceptions::{PyOverflowError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyDict, PyFloat, PyInt, PyList, PyString};

fn integer(value: &Bound<'_, PyAny>) -> PyResult<i64> {
    value.py().get_type::<PyInt>().call1((value,))?.extract()
}

fn checked_sum(values: impl Iterator<Item = i64>) -> PyResult<i64> {
    let total: i128 = values.map(i128::from).sum();
    i64::try_from(total).map_err(|_| PyOverflowError::new_err("Domain amount exceeds int64 cents"))
}

pub(crate) fn pval<'py>(
    model: &Bound<'py, PyAny>,
    key: &str,
    sensitivity: Option<&Bound<'py, PyAny>>,
) -> PyResult<Bound<'py, PyAny>> {
    let p = model.get_item("parameters")?.get_item(key)?;
    if let Some(v) = sensitivity {
        if v.is_instance_of::<PyString>()
            || (!v.is_instance_of::<PyBool>()
                && (v.is_instance_of::<PyInt>() || v.is_instance_of::<PyFloat>()))
        {
            return Ok(v.clone());
        }
        if v.is_truthy()? && p.contains("sensitivity")? {
            return p.get_item("sensitivity");
        }
    }
    p.get_item("value")
}

fn choice<'py>(sens: Option<&Bound<'py, PyAny>>, key: &str) -> PyResult<Option<Bound<'py, PyAny>>> {
    match sens {
        Some(s) if !s.is_none() => s.cast::<PyDict>()?.get_item(key),
        _ => Ok(None),
    }
}

fn component<'py>(d: &Bound<'py, PyAny>, kind: &str) -> PyResult<Option<Bound<'py, PyAny>>> {
    for c in d.getattr("components")?.try_iter()? {
        let c = c?;
        if c.getattr("kind")?.extract::<String>()? == kind {
            return Ok(Some(c));
        }
    }
    Ok(None)
}

fn unknown(py: Python<'_>, message: String) -> PyErr {
    match py
        .import("app.analysis.events")
        .and_then(|m| m.getattr("UnknownAmount"))
        .and_then(|t| t.call1((message.clone(),)))
    {
        Ok(value) => PyErr::from_value(value),
        Err(_) => PyValueError::new_err(message),
    }
}

fn known(c: &Bound<'_, PyAny>, what: &str) -> PyResult<i64> {
    let amount = c.getattr("amount_cents")?;
    if amount.is_none() {
        return Err(unknown(
            c.py(),
            format!(
                "{what}: component {} has no quoted amount, and the path needs it",
                c.getattr("component_id")?.extract::<String>()?
            ),
        ));
    }
    integer(&amount)
}

pub(crate) fn entered_cents(d: &Bound<'_, PyAny>) -> PyResult<i64> {
    let mut amounts = Vec::new();
    for c in d.getattr("components")?.try_iter()? {
        let c = c?;
        let a = c.getattr("amount_cents")?;
        if c.getattr("status")?.extract::<String>()? == "awarded" && !a.is_none() {
            amounts.push(integer(&a)?);
        }
    }
    if !amounts.is_empty() {
        return checked_sum(amounts.into_iter());
    }
    let amount = d.getattr("amount")?;
    let value = amount.getattr("value")?;
    integer(&if value.is_none() {
        amount.getattr("upper")?
    } else {
        value
    })
}

pub(crate) fn pending_template<'py>(
    _py: Python<'py>,
    model: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    for t in model
        .get_item("templates")?
        .call_method0("values")?
        .try_iter()?
    {
        let t = t?;
        if t.cast::<PyDict>()?.get_item("stage")?.is_some_and(|v| {
            v.extract::<String>()
                .is_ok_and(|v| v == "liability_pending")
        }) {
            return Ok(t);
        }
    }
    Err(PyValueError::new_err("No pending claim template"))
}

fn counted<'py>(
    d: &Bound<'py, PyAny>,
    model: &Bound<'py, PyAny>,
    principal: bool,
) -> PyResult<Vec<Bound<'py, PyAny>>> {
    let mut barred = std::collections::HashSet::new();
    for c in d.getattr("claims")?.try_iter()? {
        let c = c?;
        if c.getattr("damages_barred")?.is_truthy()? {
            barred.insert(c.getattr("claim_id")?.extract::<String>()?);
        }
    }
    let parameters = model.get_item("parameters")?.cast_into::<PyDict>()?;
    let plus = parameters
        .get_item("claimant_enhancements")?
        .map(|p| p.cast_into::<PyDict>()?.get_item("kinds"))
        .transpose()?
        .flatten();
    let enhancements: Vec<String> = plus.map(|v| v.extract()).transpose()?.unwrap_or_default();
    let mut out = Vec::new();
    for c in d.getattr("components")?.try_iter()? {
        let c = c?;
        let claim = c.getattr("claim")?.extract::<Option<String>>()?;
        if c.getattr("status")?.extract::<String>()? == "requested"
            && !c.getattr("duplicates")?.is_truthy()?
            && !claim.is_some_and(|s| barred.contains(&s))
            && c.getattr("theory")?.extract::<Option<String>>()?.as_deref() != Some("defense")
            && !enhancements.contains(&c.getattr("kind")?.extract()?)
            && (principal || !c.getattr("principal")?.is_truthy()?)
        {
            out.push(c);
        }
    }
    Ok(out)
}

pub(crate) fn verdict_basis(
    py: Python<'_>,
    d: &Bound<'_, PyAny>,
    model: &Bound<'_, PyAny>,
    branch: &str,
    sens: Option<&Bound<'_, PyAny>>,
) -> PyResult<(i64, String)> {
    if let Some(s) = branch.strip_prefix("award:") {
        let amount = s
            .split(':')
            .next()
            .ok_or_else(|| PyValueError::new_err("Malformed award branch"))?
            .parse::<i64>()
            .map_err(|e| PyValueError::new_err(e.to_string()))?;
        return Ok((amount, "band".into()));
    }
    let template = pending_template(py, model)?;
    let spec = template
        .get_item("verdict_branches")?
        .get_item(branch)?
        .cast_into::<PyDict>()?;
    if !spec
        .get_item("judgment")?
        .ok_or_else(|| PyValueError::new_err("Verdict branch lacks judgment"))?
        .is_truthy()?
    {
        return Ok((0, "none".into()));
    }
    let rule = spec
        .get_item("amount")?
        .ok_or_else(|| PyValueError::new_err("Verdict branch lacks amount"))?
        .extract::<String>()?;
    let sensitivity = choice(sens, &rule)?;
    let v = if rule == "components" {
        PyString::new(py, "components").into_any()
    } else {
        pval(model, &rule, sensitivity.as_ref())?
    };
    let mode = v.extract::<String>().ok();
    let (mut amount, how) = if mode.as_deref() == Some("components")
        || mode.as_deref() == Some("components_without_principal")
    {
        let cs = counted(d, model, mode.as_deref() == Some("components"))?;
        let mut amounts = Vec::new();
        let mut missing = Vec::new();
        for c in cs {
            let a = c.getattr("amount_cents")?;
            if a.is_none() {
                missing.push(c.getattr("component_id")?.extract::<String>()?);
            } else {
                amounts.push(integer(&a)?);
            }
        }
        if missing.is_empty() {
            (checked_sum(amounts.into_iter())?, "record".to_string())
        } else {
            let p = model
                .get_item("parameters")?
                .cast_into::<PyDict>()?
                .get_item(&rule)?;
            let bound = p
                .map(|p| p.cast_into::<PyDict>()?.get_item("bound"))
                .transpose()?
                .flatten();
            let bound = bound.filter(|v| !v.is_none());
            if let Some(b) = bound {
                (integer(&b)?, "bound".into())
            } else {
                let missing = PyList::new(py, missing)?.repr()?.extract::<String>()?;
                return Err(unknown(py,format!("The '{branch}' verdict amount is unknown: components {missing} have no quoted amount, and the case declares no bound for it (scenario.json parameters). Record the amounts from the passages that state them, or declare the bound.")));
            }
        }
    } else {
        (integer(&v)?, "declared".into())
    };
    if let Some(plus) = spec.get_item("plus")? {
        if plus.is_truthy()? {
            let key = plus.extract::<String>()?;
            let s = choice(sens, &key)?;
            let extra = integer(&pval(model, &key, s.as_ref())?)?;
            amount = checked_sum([amount, extra].into_iter())?;
        }
    }
    Ok((amount, how))
}

pub(crate) fn verdict_amount(
    py: Python<'_>,
    d: &Bound<'_, PyAny>,
    model: &Bound<'_, PyAny>,
    branch: &str,
    sens: Option<&Bound<'_, PyAny>>,
) -> PyResult<i64> {
    Ok(verdict_basis(py, d, model, branch, sens)?.0)
}

pub(crate) fn settlement_terms(
    model: &Bound<'_, PyAny>,
    sens: Option<&Bound<'_, PyAny>>,
) -> PyResult<(String, i64)> {
    if model
        .get_item("settlement_scenarios")?
        .get_item("base")?
        .extract::<String>()?
        == "monthly"
        || choice(sens, "settlement_monthly")?.is_some_and(|v| v.is_truthy().unwrap_or(false))
    {
        return Ok(("monthly".into(), 1));
    }
    let parameters = model.get_item("parameters")?.cast_into::<PyDict>()?;
    let p = parameters.get_item("settlement_payment")?;
    let mode = if let Some(ref p) = p {
        if p.is_truthy()? {
            let s = choice(sens, "settlement_payment")?;
            pval(model, "settlement_payment", s.as_ref())?.extract::<String>()?
        } else {
            "lump_sum".into()
        }
    } else {
        "lump_sum".into()
    };
    let installments = if mode == "installments" {
        let v = p
            .as_ref()
            .map(|p| p.cast::<PyDict>()?.get_item("installments"))
            .transpose()?
            .flatten();
        match v {
            Some(v) if v.is_truthy()? => integer(&v)?,
            _ => 1,
        }
    } else {
        1
    };
    Ok((mode, installments))
}

pub(crate) fn prejudgment_interest(
    d: &Bound<'_, PyAny>,
    comp: i64,
    model: &Bound<'_, PyAny>,
) -> PyResult<i64> {
    let start = d.getattr("commenced")?;
    let judgment = d.getattr("judgment_date")?;
    if comp == 0 || start.is_none() || judgment.is_none() {
        return Ok(0);
    }
    let rules = model.get_item("rules")?;
    let rate = rules
        .get_item("nc_24_5_b")?
        .get_item("rate_rule")?
        .extract::<String>()?;
    let bps = integer(&rules.get_item(rate)?.get_item("value")?)?;
    let days = judgment.call_method0("toordinal")?.extract::<i64>()?
        - start.call_method0("toordinal")?.extract::<i64>()?;
    let value = crate::event_core::rate_coefficient(comp, bps) * days as f64 / 365.0;
    if !value.is_finite() || !(-9223372036854775808.0..9223372036854775808.0).contains(&value) {
        return Err(PyOverflowError::new_err("Interest exceeds int64 cents"));
    }
    Ok(value.round_ties_even() as i64)
}

pub(crate) fn ruling_amounts<'py>(
    py: Python<'py>,
    d: &Bound<'py, PyAny>,
    outcome: &Bound<'py, PyAny>,
    model: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyDict>> {
    let o = outcome.cast::<PyDict>()?;
    let answer = |key: &str| -> PyResult<Option<String>> {
        o.get_item(key)?.map(|v| v.extract()).transpose()
    };
    let comp_c = component(d, "compensatory")?;
    let ex_c = component(d, "exemplary")?;
    let pat_c = component(d, "patent")?;
    let fee_c = component(d, "fees")?;
    let mut comp = 0;
    if let Some(ref c) = comp_c {
        if answer("liability")?.as_deref() != Some("granted") {
            let damage = answer("damages")?.unwrap_or_else(|| "stands".into());
            if damage == "stands" {
                comp = known(c, "the judgment as entered")?;
            } else if damage == "remit" && answer("remittitur")?.as_deref() == Some("accept") {
                let scenarios = model.get_item("remittitur_scenarios")?;
                let mode = scenarios.get_item("base")?.extract::<String>()?;
                let scenario = scenarios
                    .get_item("scenarios")?
                    .cast_into::<PyDict>()?
                    .get_item("remitted")?;
                let amount = scenario
                    .map(|v| v.cast::<PyDict>()?.get_item("amount_cents"))
                    .transpose()?
                    .flatten();
                let remitted = if let Some(v) = amount {
                    if v.is_truthy()? {
                        Some(integer(&v)?)
                    } else {
                        c.getattr("remittitur_cents")?.extract::<Option<i64>>()?
                    }
                } else {
                    c.getattr("remittitur_cents")?.extract::<Option<i64>>()?
                };
                comp = if mode == "remitted" && remitted.is_some_and(|v| v != 0) {
                    remitted.unwrap_or(0)
                } else {
                    known(c, "the remitted judgment")?
                };
            }
        }
    }
    let mut exemplary = if comp > 0 {
        ex_c.as_ref()
            .map(|c| known(c, "exemplary damages"))
            .transpose()?
            .unwrap_or(0)
    } else {
        0
    };
    let patent = if answer("patent")?.as_deref() != Some("granted") {
        pat_c
            .as_ref()
            .map(|c| known(c, "patent damages"))
            .transpose()?
            .unwrap_or(0)
    } else {
        0
    };
    let mut trebling = 0;
    if comp > 0 && answer("trebling")?.as_deref() == Some("granted") {
        let factor = integer(
            &model
                .get_item("rules")?
                .get_item("nc_75_16")?
                .get_item("value")?,
        )?;
        let trebled = i64::try_from(comp as i128 * factor as i128)
            .map_err(|_| PyOverflowError::new_err("Trebled amount exceeds int64 cents"))?;
        if trebled as i128 >= comp as i128 + exemplary as i128 {
            trebling = trebled - comp;
            exemplary = 0;
        }
    }
    let fees = if comp > 0 && answer("fees")?.as_deref() == Some("granted") {
        fee_c
            .as_ref()
            .map(|c| known(c, "fees"))
            .transpose()?
            .unwrap_or(0)
    } else {
        0
    };
    let interest = if comp > 0 && answer("interest")?.as_deref() == Some("granted") {
        prejudgment_interest(d, comp, model)?
    } else {
        0
    };
    let out = PyDict::new(py);
    for (k, v) in [
        ("compensatory", comp),
        ("exemplary", exemplary),
        ("patent", patent),
        ("trebling", trebling),
        ("fees", fees),
        ("prejudgment_interest", interest),
    ] {
        out.set_item(k, v)?;
    }
    Ok(out)
}

#[pyfunction]
#[pyo3(signature=(model,key,sensitivity=None))]
fn parameter_value<'py>(
    model: Bound<'py, PyAny>,
    key: &str,
    sensitivity: Option<Bound<'py, PyAny>>,
) -> PyResult<Bound<'py, PyAny>> {
    pval(&model, key, sensitivity.as_ref())
}
#[pyfunction(name = "entered_cents")]
fn entered_cents_py(d: Bound<'_, PyAny>) -> PyResult<i64> {
    entered_cents(&d)
}
#[pyfunction(name = "verdict_basis")]
#[pyo3(signature=(d,model,branch,sens=None))]
fn verdict_basis_py(
    py: Python<'_>,
    d: Bound<'_, PyAny>,
    model: Bound<'_, PyAny>,
    branch: &str,
    sens: Option<Bound<'_, PyAny>>,
) -> PyResult<(i64, String)> {
    verdict_basis(py, &d, &model, branch, sens.as_ref())
}
#[pyfunction(name = "verdict_amount")]
#[pyo3(signature=(d,model,branch,sens=None))]
fn verdict_amount_py(
    py: Python<'_>,
    d: Bound<'_, PyAny>,
    model: Bound<'_, PyAny>,
    branch: &str,
    sens: Option<Bound<'_, PyAny>>,
) -> PyResult<i64> {
    verdict_amount(py, &d, &model, branch, sens.as_ref())
}
#[pyfunction(name = "settlement_terms")]
#[pyo3(signature=(model,sens=None))]
fn settlement_terms_py(
    model: Bound<'_, PyAny>,
    sens: Option<Bound<'_, PyAny>>,
) -> PyResult<(String, i64)> {
    settlement_terms(&model, sens.as_ref())
}
#[pyfunction(name = "prejudgment_interest_cents")]
fn prejudgment_interest_py(
    d: Bound<'_, PyAny>,
    compensatory: i64,
    model: Bound<'_, PyAny>,
) -> PyResult<i64> {
    prejudgment_interest(&d, compensatory, &model)
}
#[pyfunction(name = "ruling_amounts")]
fn ruling_amounts_py<'py>(
    py: Python<'py>,
    d: Bound<'py, PyAny>,
    outcome: Bound<'py, PyAny>,
    model: Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyDict>> {
    ruling_amounts(py, &d, &outcome, &model)
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(parameter_value, m)?)?;
    m.add_function(wrap_pyfunction!(entered_cents_py, m)?)?;
    m.add_function(wrap_pyfunction!(verdict_basis_py, m)?)?;
    m.add_function(wrap_pyfunction!(verdict_amount_py, m)?)?;
    m.add_function(wrap_pyfunction!(settlement_terms_py, m)?)?;
    m.add_function(wrap_pyfunction!(prejudgment_interest_py, m)?)?;
    m.add_function(wrap_pyfunction!(ruling_amounts_py, m)?)?;
    Ok(())
}

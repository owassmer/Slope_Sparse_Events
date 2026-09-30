//! Rust execution primitives exposed through a single Python extension.
//!
//! Floating-point expressions deliberately use ordinary IEEE-754 operations.
//! Do not introduce fast-math, multiply-add fusion, or reassociation: the Python
//! reference checks floating-point bits as well as integer-cent outputs.

// Boundary functions retain the Python reference signatures for direct parity.
#![allow(clippy::too_many_arguments)]

use pyo3::prelude::*;

mod atm;
mod cash;
mod event_cash_helpers;
mod event_core;
mod event_equity;
mod event_snapshot;
mod event_transitions;
mod events;
mod events_free;
mod owed;
mod price;
mod reduction;
mod walk;
mod walk_classes;
mod walk_queries;
mod walk_traversal;

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    m.add("__backend__", "rust")?;
    atm::register(m)?;
    cash::register(m)?;
    events::register(m)?;
    events_free::register(m)?;
    owed::register(m)?;
    price::register(m)?;
    reduction::register(m)?;
    walk::register(m)?;
    walk_classes::register(m)?;
    walk_queries::register(m)?;
    walk_traversal::register(m)?;
    Ok(())
}

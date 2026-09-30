//! Exact integer cash recurrences. Row order and queue order match the Python oracle.
//! No floating-point reassociation is permitted: the one float accumulation below
//! deliberately retains the reference's arrears-queue conversion and truncation.
use numpy::ndarray::{Array2, Array3, ArrayView1, ArrayView2, ArrayView3};
use numpy::{IntoPyArray, PyReadonlyArray1, PyReadonlyArray2, PyReadonlyArray3};
use pyo3::exceptions::{PyMemoryError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyModule, PyTuple};
use std::borrow::Cow;
use std::ops::Index;

const BIG: i64 = 1_000_000;
const NCLASS: usize = 5;
const OPERATING: usize = 4;
const OPENING_INCURRED: i64 = -1;

// Numba's integer expressions use int64 wraparound, including intermediates.
#[inline]
fn add(a: i64, b: i64) -> i64 {
    a.wrapping_add(b)
}
#[inline]
fn sub(a: i64, b: i64) -> i64 {
    a.wrapping_sub(b)
}
#[inline]
fn mul(a: i64, b: i64) -> i64 {
    a.wrapping_mul(b)
}
#[inline]
fn div(a: i64, b: i64) -> i64 {
    let q = a.wrapping_div(b);
    let r = a.wrapping_rem(b);
    if r != 0 && ((r < 0) != (b < 0)) {
        q.wrapping_sub(1)
    } else {
        q
    }
}
#[inline]
fn principal(funded: i64, collected: i64, contract: i64) -> i64 {
    sub(
        funded,
        if contract > 0 {
            div(mul(collected, funded), contract)
        } else {
            0
        },
    )
}

pub(crate) struct LineInput<'a> {
    pub need: ArrayView2<'a, i64>,
    pub limit: ArrayView2<'a, i64>,
    pub month_end: ArrayView1<'a, bool>,
    pub routes: ArrayView3<'a, i64>,
    pub due_idx: ArrayView2<'a, i64>,
    pub due0: ArrayView1<'a, i64>,
    pub book_d: ArrayView1<'a, i64>,
    pub book_a: ArrayView1<'a, i64>,
    pub fee_bps: i64,
    pub inst: usize,
    pub debit: bool,
    pub opening: i64,
    pub funded0: i64,
    pub contract0: i64,
}

// Cash execution reads compact row-major buffers. Standard contiguous inputs
// are borrowed without allocation; arbitrary-stride oracle inputs are copied
// once in logical order, rather than performing strided ndarray dispatch at
// every daily scalar access. All indexing remains checked and safe.
struct Flat2<'a> {
    values: Cow<'a, [i64]>,
    shape: [usize; 2],
}
impl<'a> Flat2<'a> {
    fn of(a: ArrayView2<'a, i64>) -> Self {
        Self {
            shape: [a.nrows(), a.ncols()],
            values: match a.to_slice() {
                Some(values) => Cow::Borrowed(values),
                None => Cow::Owned(a.iter().copied().collect()),
            },
        }
    }
    fn shape(&self) -> &[usize; 2] {
        &self.shape
    }
    fn dim(&self) -> (usize, usize) {
        (self.shape[0], self.shape[1])
    }
    fn row(&self, row: usize) -> &[i64] {
        let start = row * self.shape[1];
        &self.values[start..start + self.shape[1]]
    }
}
impl Index<[usize; 2]> for Flat2<'_> {
    type Output = i64;
    #[inline]
    fn index(&self, i: [usize; 2]) -> &i64 {
        &self.values[i[0] * self.shape[1] + i[1]]
    }
}
struct Flat3<'a> {
    values: Cow<'a, [i64]>,
    shape: [usize; 3],
}
impl<'a> Flat3<'a> {
    fn of(a: ArrayView3<'a, i64>) -> Self {
        Self {
            shape: [a.shape()[0], a.shape()[1], a.shape()[2]],
            values: match a.to_slice() {
                Some(values) => Cow::Borrowed(values),
                None => Cow::Owned(a.iter().copied().collect()),
            },
        }
    }
    fn shape(&self) -> &[usize; 3] {
        &self.shape
    }
    fn row(&self, first: usize, second: usize) -> &[i64] {
        let start = (first * self.shape[1] + second) * self.shape[2];
        &self.values[start..start + self.shape[2]]
    }
}
impl Index<[usize; 3]> for Flat3<'_> {
    type Output = i64;
    #[inline]
    fn index(&self, i: [usize; 3]) -> &i64 {
        &self.values[(i[0] * self.shape[1] + i[1]) * self.shape[2] + i[2]]
    }
}
struct PackedLine<'a> {
    need: Flat2<'a>,
    limit: Flat2<'a>,
    month_end: ArrayView1<'a, bool>,
    routes: Flat3<'a>,
    due_idx: Flat2<'a>,
    due0: ArrayView1<'a, i64>,
    book_d: ArrayView1<'a, i64>,
    book_a: ArrayView1<'a, i64>,
    fee_bps: i64,
    inst: usize,
    debit: bool,
    opening: i64,
    funded0: i64,
    contract0: i64,
}
impl<'a> PackedLine<'a> {
    fn of(line: &LineInput<'a>) -> Self {
        Self {
            need: Flat2::of(line.need),
            limit: Flat2::of(line.limit),
            month_end: line.month_end,
            routes: Flat3::of(line.routes),
            due_idx: Flat2::of(line.due_idx),
            due0: line.due0,
            book_d: line.book_d,
            book_a: line.book_a,
            fee_bps: line.fee_bps,
            inst: line.inst,
            debit: line.debit,
            opening: line.opening,
            funded0: line.funded0,
            contract0: line.contract0,
        }
    }
}

pub(crate) fn validate(
    line: &LineInput<'_>,
    rn: usize,
    days: usize,
    pet: ArrayView1<'_, i64>,
) -> PyResult<()> {
    let n = line.need.shape()[0];
    let tail = line.due0.len();
    if n == 0
        || days > tail
        || line.inst == 0
        || pet.len() != rn
        || line.need.shape()[1] < days
        || line.limit.shape() != line.need.shape()
        || line.month_end.len() < days
        || line.routes.shape()[0] != n
        || line.routes.shape()[1] < days
        || line.due_idx.shape()[0] < days
        || line.due_idx.shape()[1] != line.inst
        || line.book_d.len() != line.book_a.len()
        || line.book_d.iter().any(|&d| d < 0 || d as usize >= tail)
        || line.due_idx.iter().any(|&d| d < 0 || d as usize >= tail)
    {
        return Err(PyValueError::new_err(
            "invalid cash-kernel array shapes or installment indices",
        ));
    }
    let element_limit = (isize::MAX as usize) / std::mem::size_of::<i64>();
    let volumes = [
        Some(tail),
        rn.checked_mul(days).and_then(|v| v.checked_mul(NCLASS)),
        rn.checked_mul(tail),
        rn.checked_mul(days)
            .and_then(|v| v.checked_mul(line.routes.shape()[2])),
    ];
    let entries = days
        .checked_mul(line.routes.shape()[2])
        .and_then(|v| v.checked_mul(line.inst))
        .and_then(|v| v.checked_add(line.book_d.len()));
    if volumes.iter().any(|v| v.is_none_or(|v| v > element_limit))
        || entries
            .and_then(|v| v.checked_add(NCLASS))
            .is_none_or(|v| v > (isize::MAX as usize) / std::mem::size_of::<Item>())
        || days
            .checked_mul(NCLASS)
            .is_none_or(|v| v > (isize::MAX as usize) / std::mem::size_of::<Arrear>())
    {
        return Err(PyValueError::new_err(
            "cash-kernel allocation dimensions are too large",
        ));
    }
    Ok(())
}

#[derive(Clone, Copy)]
struct Entry {
    amount: i64,
    incurred: i64,
    next: i64,
}
#[derive(Clone, Copy)]
struct Pending {
    amount: i64,
    incurred: i64,
    day: i64,
}
#[derive(Clone, Copy)]
struct Arrear {
    class: usize,
    amount: i64,
    day: i64,
}
#[derive(Clone, Copy)]
struct Item {
    amount: i64,
    incurred: i64,
    class: i64,
    seq: usize,
    pos: usize,
    paid: bool,
}

fn book(
    entries: &mut Vec<Entry>,
    head: &mut [i64],
    last: &mut [i64],
    day: usize,
    amount: i64,
    incurred: i64,
) {
    let ne = entries.len() as i64;
    entries.push(Entry {
        amount,
        incurred,
        next: -1,
    });
    if head[day] < 0 {
        head[day] = ne;
    } else {
        entries[last[day] as usize].next = ne;
    }
    last[day] = ne;
}

pub(crate) struct Output {
    pub cash: Vec<i64>,
    pub collections: Vec<i64>,
    pub fundings: Vec<i64>,
    pub outstanding: Vec<i64>,
    pub due: Vec<i64>,
    pub funded: Vec<i64>,
    pub contract: Vec<i64>,
    pub collected: Vec<i64>,
    pub failed: Vec<i64>,
    pub hr_r: Vec<i64>,
    pub hr_t: Vec<i64>,
    pub hr_v: Vec<i64>,
    pub dr_t: Vec<i64>,
    pub dr_k: Vec<i64>,
    pub dr_r: Vec<i64>,
    pub dr_a: Vec<i64>,
    pub arrears: Vec<i64>,
    pub first_unpaid: Vec<i64>,
    pub nonpay: Vec<i64>,
    pub levy_unmet: Vec<i64>,
    pub rn: usize,
    pub days: usize,
    pub tail: usize,
}

impl Output {
    fn new(rn: usize, days: usize, tail: usize, draw_cap: usize, daily: bool, full: bool) -> Self {
        let series = || if full { vec![0; rn * days] } else { Vec::new() };
        let summary = || if full { vec![0; rn] } else { Vec::new() };
        let log_cap = if full { draw_cap } else { 0 };
        Self {
            cash: vec![0; rn * days],
            collections: series(),
            fundings: series(),
            outstanding: series(),
            due: vec![0; rn * tail],
            funded: summary(),
            contract: summary(),
            collected: summary(),
            failed: summary(),
            hr_r: Vec::new(),
            hr_t: Vec::new(),
            hr_v: Vec::new(),
            dr_t: Vec::with_capacity(log_cap),
            dr_k: Vec::with_capacity(log_cap),
            dr_r: Vec::with_capacity(log_cap),
            dr_a: Vec::with_capacity(log_cap),
            arrears: if daily {
                vec![0; rn * days * NCLASS]
            } else {
                Vec::new()
            },
            first_unpaid: if daily { vec![0; rn] } else { Vec::new() },
            nonpay: if daily { vec![0; rn] } else { Vec::new() },
            levy_unmet: if daily && full {
                vec![0; rn]
            } else {
                Vec::new()
            },
            rn,
            days,
            tail,
        }
    }

    fn into_python(self, py: Python<'_>, daily: bool) -> PyResult<Py<PyTuple>> {
        let array1 = |v: Vec<i64>| v.into_pyarray(py).into_any().unbind();
        let array2 = |v: Vec<i64>, cols: usize| -> PyResult<Py<PyAny>> {
            Ok(Array2::from_shape_vec((self.rn, cols), v)
                .map_err(|e| PyValueError::new_err(e.to_string()))?
                .into_pyarray(py)
                .into_any()
                .unbind())
        };
        let hr = PyTuple::new(
            py,
            [array1(self.hr_r), array1(self.hr_t), array1(self.hr_v)],
        )?
        .into_any()
        .unbind();
        let dr = PyTuple::new(
            py,
            [
                array1(self.dr_t),
                array1(self.dr_k),
                array1(self.dr_r),
                array1(self.dr_a),
            ],
        )?
        .into_any()
        .unbind();
        let mut fields = vec![
            array2(self.cash, self.days)?,
            array2(self.collections, self.days)?,
            array2(self.fundings, self.days)?,
            array2(self.outstanding, self.days)?,
            array2(self.due, self.tail)?,
            array1(self.funded),
            array1(self.contract),
            array1(self.collected),
            array1(self.failed),
        ];
        if daily {
            fields.push(
                Array3::from_shape_vec((self.rn, self.days, NCLASS), self.arrears)
                    .map_err(|e| PyValueError::new_err(e.to_string()))?
                    .into_pyarray(py)
                    .into_any()
                    .unbind(),
            );
            fields.extend([
                array1(self.first_unpaid),
                array1(self.nonpay),
                array1(self.levy_unmet),
            ]);
        }
        fields.extend([hr, dr]);
        Ok(PyTuple::new(py, fields)?.unbind())
    }

    fn into_cash_python(self, py: Python<'_>) -> PyResult<Py<PyTuple>> {
        let cash = Array2::from_shape_vec((self.rn, self.days), self.cash)
            .map_err(|e| PyValueError::new_err(e.to_string()))?
            .into_pyarray(py)
            .into_any()
            .unbind();
        let arrears = Array3::from_shape_vec((self.rn, self.days, NCLASS), self.arrears)
            .map_err(|e| PyValueError::new_err(e.to_string()))?
            .into_pyarray(py)
            .into_any()
            .unbind();
        Ok(PyTuple::new(
            py,
            [
                cash,
                self.first_unpaid.into_pyarray(py).into_any().unbind(),
                self.nonpay.into_pyarray(py).into_any().unbind(),
                arrears,
            ],
        )?
        .unbind())
    }
}

fn route<const FULL: bool>(
    line: &PackedLine<'_>,
    result: &mut Output,
    entries: &mut Vec<Entry>,
    head: &mut [i64],
    last: &mut [i64],
    r: usize,
    i: usize,
    t: usize,
    fu: &mut i64,
    co: &mut i64,
    cl: i64,
) -> i64 {
    let mut routed = 0;
    for k in 0..line.routes.shape()[2] {
        let amt = line.routes[[i, t, k]];
        if amt <= 0 || add(principal(*fu, cl, *co), amt) > line.limit[[i, t]] {
            continue;
        }
        let total = add(
            amt,
            div(add(mul(mul(2, amt), line.fee_bps), 10_000), 20_000),
        );
        let inst = line.inst as i64;
        let part = div(add(mul(2, total), inst), mul(2, inst));
        for kk in 0..line.inst {
            let a = if kk < line.inst - 1 {
                part
            } else {
                sub(total, mul(part, sub(inst, 1)))
            };
            let d = line.due_idx[[t, kk]] as usize;
            result.due[r * result.tail + d] = add(result.due[r * result.tail + d], a);
            if line.debit {
                book(entries, head, last, d, a, t as i64);
            }
        }
        *fu = add(*fu, amt);
        *co = add(*co, total);
        routed = add(routed, amt);
        if FULL {
            result.fundings[r * result.days + t] = add(result.fundings[r * result.days + t], amt);
            result.dr_t.push(t as i64);
            result.dr_k.push(k as i64);
            result.dr_r.push(r as i64);
            result.dr_a.push(amt);
        }
    }
    routed
}

pub(crate) fn net_compute(
    base: ArrayView2<'_, i64>,
    pet: ArrayView1<'_, i64>,
    line: &LineInput<'_>,
    draw_cap: usize,
) -> Output {
    let packed = PackedLine::of(line);
    let line = &packed;
    let base = Flat2::of(base);
    let (rn, days) = base.dim();
    let n = line.need.shape()[0];
    let tail = line.due0.len();
    let cap = line.book_d.len() + days * line.routes.shape()[2] * line.inst;
    // Capacity is a hint only; allocating beyond the maximum possible log
    // length wastes memory and lets malformed boundary hints panic.
    let draw_cap = draw_cap.min(rn * days * line.routes.shape()[2]);
    let mut result = Output::new(rn, days, tail, draw_cap, false, true);
    let mut entries = Vec::with_capacity(cap);
    let mut head = vec![-1; tail];
    let mut last = vec![-1; tail];
    let mut pending = Vec::<Pending>::with_capacity(cap);
    for r in 0..rn {
        let i = r % n;
        let base_row = base.row(r);
        let need_row = line.need.row(i);
        for d in 0..tail {
            result.due[r * tail + d] = line.due0[d];
            head[d] = -1;
            last[d] = -1;
        }
        entries.clear();
        pending.clear();
        if line.debit {
            for q in 0..line.book_d.len() {
                book(
                    &mut entries,
                    &mut head,
                    &mut last,
                    line.book_d[q] as usize,
                    line.book_a[q],
                    OPENING_INCURRED,
                );
            }
        }
        let mut avail = line.opening;
        let mut owed = 0;
        let mut fu = line.funded0;
        let mut co = line.contract0;
        let mut cl = 0;
        let mut fl = 0;
        for t in 0..days {
            avail = add(avail, base_row[t]);
            let live = (t as i64) < pet[r];
            owed = add(owed, result.due[r * tail + t]);
            let falls_due = result.due[r * tail + t] > 0 && live;
            if falls_due {
                result.hr_r.push(r as i64);
                result.hr_t.push(t as i64);
                result.hr_v.push(sub(sub(avail, need_row[t]), owed));
            }
            let attempt = live && (falls_due || line.month_end[t]) && owed > 0;
            if line.debit {
                let mut e = head[t];
                while e >= 0 {
                    let entry = entries[e as usize];
                    pending.push(Pending {
                        amount: entry.amount,
                        incurred: entry.incurred,
                        day: t as i64,
                    });
                    e = entry.next;
                }
            }
            if attempt {
                let mut take = 0;
                if line.debit {
                    pending.retain(|p| {
                        if avail >= p.amount {
                            avail = sub(avail, p.amount);
                            take = add(take, p.amount);
                            false
                        } else {
                            fl = add(fl, 1);
                            true
                        }
                    });
                } else {
                    let x = sub(avail, need_row[t]).max(0);
                    take = owed.min(x);
                    avail = sub(avail, take);
                    if owed > take {
                        fl = add(fl, 1);
                    }
                }
                owed = sub(owed, take);
                cl = add(cl, take);
                result.collections[r * days + t] = take;
            }
            if live && owed == 0 {
                avail = add(
                    avail,
                    route::<true>(
                        line,
                        &mut result,
                        &mut entries,
                        &mut head,
                        &mut last,
                        r,
                        i,
                        t,
                        &mut fu,
                        &mut co,
                        cl,
                    ),
                );
            }
            result.cash[r * days + t] = avail;
            result.outstanding[r * days + t] = principal(fu, cl, co);
        }
        result.funded[r] = fu;
        result.contract[r] = co;
        result.collected[r] = cl;
        result.failed[r] = fl;
    }
    result
}

// Both public output forms execute this same recurrence. The cash-only form
// removes unused logs, principal snapshots, and dense result buffers at compile
// time; all payment ordering and nonpayment calculations remain identical.
pub(crate) fn daily_compute<const FULL: bool>(
    post: ArrayView2<'_, i64>,
    levy: ArrayView2<'_, i64>,
    out: ArrayView2<'_, i64>,
    obl: ArrayView3<'_, i64>,
    inc: ArrayView2<'_, i64>,
    pet: ArrayView1<'_, i64>,
    line: &LineInput<'_>,
    first_op: bool,
    draw_cap: usize,
    w: i64,
    share: i64,
) -> Output {
    let packed = PackedLine::of(line);
    let line = &packed;
    let post = Flat2::of(post);
    let levy = Flat2::of(levy);
    let out = Flat2::of(out);
    let obl = Flat3::of(obl);
    let inc = Flat2::of(inc);
    let (rn, days) = post.dim();
    let n = line.need.shape()[0];
    let tail = line.due0.len();
    let nobl = obl.shape()[0];
    let cap = line.book_d.len() + days * line.routes.shape()[2] * line.inst;
    let draw_cap = draw_cap.min(rn * days * line.routes.shape()[2]);
    let mut result = Output::new(rn, days, tail, draw_cap, true, FULL);
    let mut entries = Vec::with_capacity(cap);
    let mut head = vec![-1; tail];
    let mut last = vec![-1; tail];
    let mut pending = Vec::<Pending>::with_capacity(cap);
    let mut queue = Vec::<Arrear>::with_capacity(days * (nobl + 1));
    let mut items = Vec::<Item>::with_capacity(cap + nobl + 1);
    let mut order = Vec::<usize>::with_capacity(cap + nobl + 1);
    let mut fell = vec![0i64; days];
    let mut slope_due = vec![0i64; days];
    for r in 0..rn {
        let i = r % n;
        let post_row = post.row(r);
        let levy_row = levy.row(r);
        let out_row = out.row(r);
        let need_row = line.need.row(i);
        let mut obligation_rows = [&[][..]; NCLASS - 1];
        for (c, row) in obligation_rows.iter_mut().enumerate().take(nobl) {
            *row = obl.row(c, r);
        }
        for d in 0..tail {
            result.due[r * tail + d] = line.due0[d];
            head[d] = -1;
            last[d] = -1;
        }
        entries.clear();
        pending.clear();
        queue.clear();
        if line.debit {
            for q in 0..line.book_d.len() {
                book(
                    &mut entries,
                    &mut head,
                    &mut last,
                    line.book_d[q] as usize,
                    line.book_a[q],
                    OPENING_INCURRED,
                );
            }
        }
        for t in 0..days {
            let mut f = 0;
            for row in obligation_rows.iter().take(nobl) {
                f = add(f, row[t]);
            }
            fell[t] = f;
        }
        let mut bc = [0i64; NCLASS];
        let mut avail = line.opening;
        let mut owed = 0;
        let mut fu = line.funded0;
        let mut co = line.contract0;
        let mut cl = 0;
        let mut fl = 0;
        let mut lu = 0;
        let mut fu_day = BIG;
        let mut np_day = BIG;
        let mut streak = 0i64;
        for t in 0..days {
            let ti = t as i64;
            let live = ti < pet[r];
            if w > 0 && ti >= w && streak >= w {
                let start = (ti - w) as usize;
                let mut dw = 0;
                for &f in &fell[start..t] {
                    dw = add(dw, f);
                }
                // Preserve np.bincount's ordered float64 accumulation, then int64 truncation.
                let mut lf = 0.0f64;
                for q in &queue {
                    if q.day >= ti - w {
                        lf += q.amount as f64;
                    }
                }
                // Numba's float-to-int64 instruction returns MIN on out-of-range conversion.
                let mut left = if !lf.is_finite()
                    || lf >= 9_223_372_036_854_775_808.0
                    || lf < -9_223_372_036_854_775_808.0
                {
                    i64::MIN
                } else {
                    lf as i64
                };
                if line.debit {
                    for p in &pending {
                        if p.day >= ti - w {
                            left = add(left, p.amount);
                        }
                    }
                } else {
                    let mut sd_w = 0;
                    for &sd in &slope_due[start..t] {
                        sd_w = add(sd_w, sd);
                    }
                    left = add(left, bc[0].min(sd_w));
                }
                if live && np_day == BIG && dw > 0 && mul(left, 10_000) >= mul(share, dw) {
                    np_day = ti;
                }
            }
            avail = add(avail, post_row[t]);
            let dt = result.due[r * tail + t];
            owed = add(owed, dt);
            let sd = if live { dt } else { 0 };
            slope_due[t] = sd;
            let lv = levy_row[t];
            let take = lv.min(avail.max(0));
            lu = add(lu, sub(lv, take));
            avail = sub(avail, take);
            let falls_due = sd > 0;
            if FULL && falls_due {
                result.hr_r.push(r as i64);
                result.hr_t.push(ti);
                result.hr_v.push(sub(sub(avail, need_row[t]), owed));
            }
            let attempt = live && (falls_due || line.month_end[t]) && owed > 0;
            if line.debit {
                let mut e = head[t];
                while e >= 0 {
                    let entry = entries[e as usize];
                    pending.push(Pending {
                        amount: entry.amount,
                        incurred: entry.incurred,
                        day: ti,
                    });
                    e = entry.next;
                }
            }
            let a0 = avail;
            let mut pay0 = 0;
            if first_op {
                pay0 = out_row[t].min(avail.max(0));
                avail = sub(avail, pay0);
            }
            let mut unpaid = false;
            items.clear();
            order.clear();
            if attempt {
                if line.debit {
                    for (q, p) in pending.iter().enumerate() {
                        items.push(Item {
                            amount: p.amount,
                            incurred: p.incurred,
                            class: -1,
                            seq: q,
                            pos: q,
                            paid: false,
                        });
                    }
                } else {
                    items.push(Item {
                        amount: 0,
                        incurred: OPENING_INCURRED,
                        class: -1,
                        seq: 0,
                        pos: 0,
                        paid: false,
                    });
                }
            }
            let nslope = items.len();
            for (c, row) in obligation_rows.iter().enumerate().take(nobl) {
                let a = row[t];
                if a != 0 {
                    items.push(Item {
                        amount: a,
                        incurred: inc[[c, r]],
                        class: c as i64,
                        seq: 0,
                        pos: 0,
                        paid: false,
                    });
                }
            }
            let mut coll = 0;
            if !items.is_empty() {
                for q in nslope..items.len() {
                    let mut pos = nslope;
                    for s in 0..nslope {
                        if items[s].incurred > items[q].incurred && items[s].seq < pos {
                            pos = items[s].seq;
                        }
                    }
                    items[q].pos = pos;
                }
                order.extend(0..items.len());
                order.sort_by_key(|&q| {
                    let item = items[q];
                    (
                        item.pos,
                        item.class < 0,
                        if item.class < 0 { 0 } else { item.incurred },
                        item.class,
                    )
                });
                for &m in &order {
                    if !line.debit && items[m].class < 0 {
                        let x = sub(avail, need_row[t]).max(0);
                        items[m].amount = owed.min(x);
                    }
                    items[m].paid = avail >= items[m].amount;
                    if items[m].paid {
                        avail = sub(avail, items[m].amount);
                    }
                }
                for &m in &order {
                    let item = items[m];
                    let slope = item.class < 0;
                    if item.paid {
                        if slope {
                            coll = add(coll, item.amount);
                        }
                        continue;
                    }
                    if !slope || line.debit {
                        unpaid = true;
                    }
                    if !slope && live {
                        let class = item.class as usize + 1;
                        queue.push(Arrear {
                            class,
                            amount: item.amount,
                            day: ti,
                        });
                        bc[class] = add(bc[class], item.amount);
                    }
                    if slope && line.debit {
                        fl = add(fl, 1);
                    }
                }
                if line.debit && nslope > 0 {
                    let mut q = 0;
                    pending.retain(|_| {
                        let keep = !items[q].paid;
                        q += 1;
                        keep
                    });
                }
            }
            if attempt {
                if !line.debit && owed > coll {
                    fl = add(fl, 1);
                    unpaid = true;
                }
                owed = sub(owed, coll);
                cl = add(cl, coll);
                if FULL {
                    result.collections[r * days + t] = coll;
                }
            }
            let mut g = out_row[t];
            if live && owed == 0 {
                let routed = route::<FULL>(
                    line,
                    &mut result,
                    &mut entries,
                    &mut head,
                    &mut last,
                    r,
                    i,
                    t,
                    &mut fu,
                    &mut co,
                    cl,
                );
                g = sub(g, routed);
            }
            let pay;
            if first_op {
                pay = g.min(a0.max(0));
                avail = add(avail, sub(pay0, pay));
            } else {
                pay = g.min(avail.max(0));
                avail = sub(avail, pay);
            }
            let short = sub(g, pay);
            fell[t] = add(fell[t], sd);
            fell[t] = add(fell[t], g.max(0));
            if short > 0 {
                unpaid = true;
            }
            if short != 0 && live {
                queue.push(Arrear {
                    class: OPERATING,
                    amount: short,
                    day: ti,
                });
                bc[OPERATING] = add(bc[OPERATING], short);
            }
            if !queue.is_empty() {
                let bal = if live { avail.max(0) } else { 0 };
                let mut left = bal;
                for q in &mut queue {
                    if left <= 0 {
                        break;
                    }
                    let p = if q.class == OPERATING {
                        q.amount.min(left)
                    } else if left >= q.amount {
                        q.amount
                    } else {
                        0
                    };
                    q.amount = sub(q.amount, p);
                    left = sub(left, p);
                    bc[q.class] = sub(bc[q.class], p);
                }
                let paid = sub(bal, left);
                if paid != 0 {
                    avail = sub(avail, paid);
                    queue.retain(|q| q.amount > 0);
                }
            }
            if live {
                bc[0] = owed;
            }
            let mut tot = 0;
            for (c, &amount) in bc.iter().enumerate() {
                result.arrears[(r * days + t) * NCLASS + c] = amount;
                tot = add(tot, amount);
            }
            streak = if tot > 0 { add(streak, 1) } else { 0 };
            if unpaid && fu_day == BIG {
                fu_day = ti;
            }
            result.cash[r * days + t] = avail;
            if FULL {
                result.outstanding[r * days + t] = principal(fu, cl, co);
            }
        }
        if FULL {
            result.funded[r] = fu;
            result.contract[r] = co;
            result.collected[r] = cl;
            result.failed[r] = fl;
            result.levy_unmet[r] = lu;
        }
        result.first_unpaid[r] = fu_day;
        result.nonpay[r] = np_day;
    }
    result
}

#[pyfunction]
#[allow(clippy::too_many_arguments)]
fn net_kernel(
    py: Python<'_>,
    base: PyReadonlyArray2<'_, i64>,
    pet: PyReadonlyArray1<'_, i64>,
    need: PyReadonlyArray2<'_, i64>,
    limit: PyReadonlyArray2<'_, i64>,
    month_end: PyReadonlyArray1<'_, bool>,
    routes: PyReadonlyArray3<'_, i64>,
    due_idx: PyReadonlyArray2<'_, i64>,
    fee_bps: i64,
    inst: usize,
    debit: bool,
    due0: PyReadonlyArray1<'_, i64>,
    book_d: PyReadonlyArray1<'_, i64>,
    book_a: PyReadonlyArray1<'_, i64>,
    opening: i64,
    funded0: i64,
    contract0: i64,
    draw_cap: usize,
) -> PyResult<Py<PyTuple>> {
    let line = LineInput {
        need: need.as_array(),
        limit: limit.as_array(),
        month_end: month_end.as_array(),
        routes: routes.as_array(),
        due_idx: due_idx.as_array(),
        due0: due0.as_array(),
        book_d: book_d.as_array(),
        book_a: book_a.as_array(),
        fee_bps,
        inst,
        debit,
        opening,
        funded0,
        contract0,
    };
    let base = base.as_array();
    let pet = pet.as_array();
    validate(&line, base.shape()[0], base.shape()[1], pet)?;
    py.detach(|| net_compute(base, pet, &line, draw_cap))
        .into_python(py, false)
}

#[allow(clippy::too_many_arguments)]
fn daily_kernel_impl<const FULL: bool>(
    py: Python<'_>,
    post: PyReadonlyArray2<'_, i64>,
    levy: PyReadonlyArray2<'_, i64>,
    out: PyReadonlyArray2<'_, i64>,
    obl: PyReadonlyArray3<'_, i64>,
    inc: PyReadonlyArray2<'_, i64>,
    pet: PyReadonlyArray1<'_, i64>,
    need: PyReadonlyArray2<'_, i64>,
    limit: PyReadonlyArray2<'_, i64>,
    month_end: PyReadonlyArray1<'_, bool>,
    routes: PyReadonlyArray3<'_, i64>,
    due_idx: PyReadonlyArray2<'_, i64>,
    fee_bps: i64,
    inst: usize,
    debit: bool,
    first_op: bool,
    due0: PyReadonlyArray1<'_, i64>,
    book_d: PyReadonlyArray1<'_, i64>,
    book_a: PyReadonlyArray1<'_, i64>,
    opening: i64,
    funded0: i64,
    contract0: i64,
    draw_cap: usize,
    w: i64,
    share: i64,
) -> PyResult<Py<PyTuple>> {
    let line = LineInput {
        need: need.as_array(),
        limit: limit.as_array(),
        month_end: month_end.as_array(),
        routes: routes.as_array(),
        due_idx: due_idx.as_array(),
        due0: due0.as_array(),
        book_d: book_d.as_array(),
        book_a: book_a.as_array(),
        fee_bps,
        inst,
        debit,
        opening,
        funded0,
        contract0,
    };
    let post = post.as_array();
    let levy = levy.as_array();
    let out = out.as_array();
    let obl = obl.as_array();
    let inc = inc.as_array();
    let pet = pet.as_array();
    let (rn, days) = post.dim();
    validate(&line, rn, days, pet)?;
    if levy.dim() != post.dim()
        || out.dim() != post.dim()
        || obl.shape()[1..] != [rn, days]
        || inc.dim() != (obl.shape()[0], rn)
        || obl.shape()[0] >= NCLASS
    {
        return Err(PyValueError::new_err(
            "invalid daily-kernel obligations or cash array shapes",
        ));
    }
    let output = py.detach(|| {
        daily_compute::<FULL>(
            post, levy, out, obl, inc, pet, &line, first_op, draw_cap, w, share,
        )
    });
    if FULL {
        output.into_python(py, true)
    } else {
        output.into_cash_python(py)
    }
}

#[pyfunction]
fn daily_kernel(
    py: Python<'_>,
    post: PyReadonlyArray2<'_, i64>,
    levy: PyReadonlyArray2<'_, i64>,
    out: PyReadonlyArray2<'_, i64>,
    obl: PyReadonlyArray3<'_, i64>,
    inc: PyReadonlyArray2<'_, i64>,
    pet: PyReadonlyArray1<'_, i64>,
    need: PyReadonlyArray2<'_, i64>,
    limit: PyReadonlyArray2<'_, i64>,
    month_end: PyReadonlyArray1<'_, bool>,
    routes: PyReadonlyArray3<'_, i64>,
    due_idx: PyReadonlyArray2<'_, i64>,
    fee_bps: i64,
    inst: usize,
    debit: bool,
    first_op: bool,
    due0: PyReadonlyArray1<'_, i64>,
    book_d: PyReadonlyArray1<'_, i64>,
    book_a: PyReadonlyArray1<'_, i64>,
    opening: i64,
    funded0: i64,
    contract0: i64,
    draw_cap: usize,
    w: i64,
    share: i64,
) -> PyResult<Py<PyTuple>> {
    daily_kernel_impl::<true>(
        py, post, levy, out, obl, inc, pet, need, limit, month_end, routes, due_idx, fee_bps, inst,
        debit, first_op, due0, book_d, book_a, opening, funded0, contract0, draw_cap, w, share,
    )
}

#[pyfunction]
fn daily_cash_kernel(
    py: Python<'_>,
    post: PyReadonlyArray2<'_, i64>,
    levy: PyReadonlyArray2<'_, i64>,
    out: PyReadonlyArray2<'_, i64>,
    obl: PyReadonlyArray3<'_, i64>,
    inc: PyReadonlyArray2<'_, i64>,
    pet: PyReadonlyArray1<'_, i64>,
    need: PyReadonlyArray2<'_, i64>,
    limit: PyReadonlyArray2<'_, i64>,
    month_end: PyReadonlyArray1<'_, bool>,
    routes: PyReadonlyArray3<'_, i64>,
    due_idx: PyReadonlyArray2<'_, i64>,
    fee_bps: i64,
    inst: usize,
    debit: bool,
    first_op: bool,
    due0: PyReadonlyArray1<'_, i64>,
    book_d: PyReadonlyArray1<'_, i64>,
    book_a: PyReadonlyArray1<'_, i64>,
    opening: i64,
    funded0: i64,
    contract0: i64,
    draw_cap: usize,
    w: i64,
    share: i64,
) -> PyResult<Py<PyTuple>> {
    daily_kernel_impl::<false>(
        py, post, levy, out, obl, inc, pet, need, limit, month_end, routes, due_idx, fee_bps, inst,
        debit, first_op, due0, book_d, book_a, opening, funded0, contract0, draw_cap, w, share,
    )
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(net_kernel, m)?)?;
    m.add_function(wrap_pyfunction!(daily_kernel, m)?)?;
    m.add_function(wrap_pyfunction!(daily_cash_kernel, m)?)?;
    m.add_function(wrap_pyfunction!(stable_buckets, m)?)?;
    Ok(())
}

#[pyfunction]
fn stable_buckets<'py>(
    py: Python<'py>,
    bucket: PyReadonlyArray1<'py, i64>,
    nb: usize,
) -> PyResult<Bound<'py, numpy::PyArray1<i64>>> {
    let bucket = bucket.as_array();
    let count_len = nb
        .checked_add(1)
        .ok_or_else(|| PyValueError::new_err("bucket count is too large"))?;
    let buckets = match bucket.to_slice() {
        Some(values) => Cow::Borrowed(values),
        None => Cow::Owned(bucket.iter().copied().collect::<Vec<_>>()),
    };
    if buckets.iter().any(|&v| v < 0 || v as usize >= nb) {
        return Err(PyValueError::new_err(
            "bucket values must be within the bucket count",
        ));
    }
    let output = py.detach(|| -> PyResult<Vec<i64>> {
        let mut start = Vec::<usize>::new();
        start
            .try_reserve_exact(count_len)
            .map_err(|_| PyMemoryError::new_err("unable to allocate bucket counts"))?;
        start.resize(count_len, 0);
        for &b in buckets.iter() {
            start[b as usize + 1] += 1;
        }
        for j in 0..nb {
            start[j + 1] += start[j];
        }
        let mut out = Vec::<i64>::new();
        out.try_reserve_exact(buckets.len())
            .map_err(|_| PyMemoryError::new_err("unable to allocate bucket order"))?;
        out.resize(buckets.len(), 0);
        for (q, &b) in buckets.iter().enumerate() {
            out[start[b as usize]] = q as i64;
            start[b as usize] += 1;
        }
        Ok(out)
    })?;
    Ok(output.into_pyarray(py))
}

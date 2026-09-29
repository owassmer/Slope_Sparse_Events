"""The company's share price after the verdict (case parameter `share_price`): a structural (Merton) model in which
equity is a call on the firm's value V with a strike of the notes' face plus the judgment amount owed, over the
horizon T. Calibrated once (KMV) on data public by the review date: V and the asset volatility solve
E(V) = close x shares outstanding and equity volatility = N(d1) x asset volatility x V / E, with the equity volatility
of the daily log returns in the kit's price table."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from functools import cache, lru_cache

import numpy as np

from app.config import KIT

ROW = re.compile(r"<tr><td>(\d{4}-\d\d-\d\d)</td><td>[\d.]+</td><td>[\d.]+</td><td>[\d.]+</td><td>([\d.]+)</td>")


def _n(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def closes(table: str) -> list[float]:
    """The closes of the kit's daily price table, in date order."""
    rows = ROW.findall((KIT / table).read_text())
    return [float(c) for _, c in sorted(rows)]


def equity_vol(px: list[float]) -> float:
    """Annualised (x 252) sample standard deviation of the daily log returns."""
    r = [math.log(px[i] / px[i - 1]) for i in range(1, len(px))]
    m = sum(r) / len(r)
    return math.sqrt(sum((a - m) ** 2 for a in r) / (len(r) - 1) * 252)


@dataclass(frozen=True)
class Merton:
    shares: int
    close_cents: int
    notes_cents: int  # the notes' face (the strike with no judgment)
    rate: float  # continuously applied in the call, as a decimal
    T: float  # years
    equity_vol: float
    V: float = 0.0  # calibrated firm value, cents
    asset_vol: float = 0.0

    def call(self, V: float, K: float, s: float) -> tuple[float, float]:
        """Black-Scholes call value on V at strike K (cents) and N(d1)."""
        sq = s * math.sqrt(self.T)
        d1 = (math.log(V / K) + (self.rate + s * s / 2) * self.T) / sq
        return V * _n(d1) - K * math.exp(-self.rate * self.T) * _n(d1 - sq), _n(d1)

    def calibrated(self) -> Merton:
        """Solve V and the asset volatility (KMV): E(V, s) = E0 and equity vol x E0 = N(d1) x s x V (Newton on V
        with the volatility fixed point, to machine precision)."""
        E0, K, sE = self.shares * self.close_cents, self.notes_cents, self.equity_vol
        V, s = E0 + K, sE * E0 / (E0 + K)
        for _ in range(10_000):
            e, nd1 = self.call(V, K, s)
            V2 = V + (E0 - e) / max(nd1, 1e-9)
            s2 = sE * E0 / (nd1 * V2)
            if abs(V2 - V) < 1e-9 * V and abs(s2 - s) < 1e-15:
                V, s = V2, s2
                break
            V, s = V2, s2
        return Merton(**{**self.__dict__, "V": V, "asset_vol": s})

    def price(self, owed_cents) -> np.ndarray:
        """Cents per share at the amount owed (cents; scalar or array), as floats: the call at strike
        notes + owed, per share. Vectorised over its distinct amounts."""
        a = np.asarray(owed_cents, dtype=np.int64)
        u, inv = np.unique(a, return_inverse=True)
        vals = np.array([self._price1(int(x)) for x in u], dtype=float)
        return vals[inv].reshape(a.shape)

    @cache  # noqa: B019 (one calibrated instance per case; the amounts owed accrue daily)
    def _price1(self, owed: int) -> float:
        return self.call(self.V, self.notes_cents + max(owed, 0), self.asset_vol)[0] / self.shares


@lru_cache(maxsize=8)
def _model(shares: int, close: int, notes: int, rate_bps: int, T: float, table: str) -> Merton:
    return Merton(shares, close, notes, rate_bps / 10_000, T, equity_vol(closes(table))).calibrated()


def model_for(params: dict) -> Merton | None:
    """The calibrated model of a case's `share_price` parameter (None: the case declares none)."""
    p = params.get("share_price") or {}
    if p.get("value") != "structural":
        return None
    return _model(int(p["shares_outstanding"]), int(p["close_cents"]), int(p["notes_face_cents"]),
                  int(p["rate_bps"]), float(p["horizon_years"]), p["price_table"])

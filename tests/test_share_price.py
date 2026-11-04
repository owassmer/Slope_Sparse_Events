"""The post-verdict share price (case parameter share_price): the calibrated structural model against an independent
Black-Scholes computation and the record's close and volatility."""
import csv
import math
import statistics
from pathlib import Path

import numpy as np

from app.analysis.share_price import model_for
from app.disputes.rules import load_model

CSV = Path(__file__).resolve().parents[1] / "cases/akoustis_20240514/akts_daily_px.csv"
SHARES, NOTES = 98_669_282, 44_000_000.0  # 8 May 2024 outstanding; the notes' face, USD


def bs_call(S: float, K: float, r: float, sigma: float, T: float) -> float:
    """Black-Scholes call, written from the textbook with erfc (independent of the module's erf form)."""
    Phi = lambda x: 0.5 * math.erfc(-x / math.sqrt(2))  # noqa: E731
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return S * Phi(d1) - K * math.exp(-r * T) * Phi(d2)


def csv_vol() -> float:
    with CSV.open() as f:
        px = [float(r["close"]) for r in csv.DictReader(f)]
    assert len(px) == 240 and px[-1] == 0.44
    return statistics.stdev([math.log(b / a) for a, b in zip(px, px[1:], strict=False)]) * math.sqrt(252)


def test_the_calibration_reproduces_the_close_and_the_equity_volatility():
    M = model_for(load_model("akoustis_20240514")["parameters"])
    V, s = M.V / 100, M.asset_vol  # USD
    E = bs_call(V, NOTES, 0.0516, s, 1.0)
    assert abs(E / SHARES - 0.44) < 1e-9
    d1 = (math.log(V / NOTES) + (0.0516 + s * s / 2)) / s
    sE = 0.5 * math.erfc(-d1 / math.sqrt(2)) * s * V / E
    assert abs(sE - csv_vol()) < 1e-9
    assert round(sE, 2) == 1.13


def test_the_price_at_two_amounts_owed_equals_a_hand_black_scholes():
    M = model_for(load_model("akoustis_20240514")["parameters"])
    V, s = M.V / 100, M.asset_vol
    for owed in (1_426_412, 38_600_000):  # the lower award; a USD 38.6M judgment
        hand = bs_call(V, NOTES + owed, 0.0516, s, 1.0) / SHARES
        got = float(M.price(owed * 100)) / 100
        assert abs(got - hand) < 1e-9, (owed, got, hand)
    arr = M.price(np.array([[0, 142_641_200], [3_860_000_000, 0]]))
    assert arr.shape == (2, 2) and abs(arr[0, 0] - 44.0) < 1e-7 and arr[0, 1] > arr[1, 0]

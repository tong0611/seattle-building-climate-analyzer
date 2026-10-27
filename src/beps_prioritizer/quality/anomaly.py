"""Isolation Forest on internal-consistency features, as a catch-all for patterns no
single rule covers.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

# Consistency features only. Level features (EUI, GHG intensity, fuel shares) were
# tried first, but the model then flagged genuine high emitters (steam-heated and
# gas-heavy buildings). Lowering their trust would push the buildings that most
# need attention *down* the priority list, so performance levels are left to the
# peer model and only internal inconsistency counts against data trust.
ANOMALY_FEATURES = [
    "log_eui_prev_ratio",
    "log_eui_next_ratio",
    "log_elec_prev_ratio",
    "log_elec_next_ratio",
    "log_eui_area_mismatch",
    "log_self_city_gfa_ratio",
    "log_ghg_fuel_ratio",
    "log_energy_fuel_ratio",
]


def _safe_log(s: pd.Series) -> pd.Series:
    """Natural log, with non-positive values as NaN instead of -inf/errors."""
    return np.log(s.where(s > 0))


def anomaly_features(ctx: pd.DataFrame) -> pd.DataFrame:
    """Log-ratios where 0 means 'consistent'; missing comparisons count as consistent."""
    x = pd.DataFrame(index=ctx.index)
    x["log_eui_prev_ratio"] = _safe_log(ctx["eui_prev_ratio"])
    x["log_eui_next_ratio"] = _safe_log(ctx["eui_next_ratio"])
    x["log_elec_prev_ratio"] = _safe_log(ctx["elec_prev_ratio"])
    x["log_elec_next_ratio"] = _safe_log(ctx["elec_next_ratio"])
    x["log_eui_area_mismatch"] = np.log1p(ctx["eui_area_mismatch"])
    x["log_self_city_gfa_ratio"] = _safe_log(ctx["self_gfa_buildings"] / ctx["gfa_buildings"])
    x["log_ghg_fuel_ratio"] = _safe_log(ctx["ghg_fuel_ratio"])
    x["log_energy_fuel_ratio"] = _safe_log(ctx["energy_fuel_ratio"])
    return x[ANOMALY_FEATURES].fillna(0).clip(-10, 10)


def anomaly_percentile(ctx: pd.DataFrame, seed: int = 0) -> pd.Series:
    """Isolation Forest anomaly score as a percentile rank (NaN if core metrics missing)."""
    ok = ctx["site_eui_wn"].notna() & ctx["ghg_intensity"].notna()
    xs = anomaly_features(ctx)[ok]
    model = IsolationForest(n_estimators=300, random_state=seed, n_jobs=-1).fit(xs)
    score = pd.Series(-model.score_samples(xs), index=xs.index)  # higher = more anomalous
    return score.rank(pct=True).reindex(ctx.index)

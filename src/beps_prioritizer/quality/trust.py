"""Data-quality layer: rule checks, an anomaly model, and a trust score per building-year.

Output is one row per (building_id, data_year) with:
- ``dq_<rule>`` boolean flags (see ``RULES``),
- ``anomaly_pct``: Isolation Forest anomaly percentile (1.0 = most unusual),
- ``trust_score`` (0-100), ``trust_tier`` (high / medium / low),
- ``usable_for_modeling``: no critical flag, so peer models can train on it.

Flags mean "worth a look", not "wrong". Nothing here edits the source values.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from beps_prioritizer.quality.anomaly import anomaly_percentile
from beps_prioritizer.quality.rules import RULES, add_context, apply_rules

ANOMALY_PENALTIES = ((0.99, 25), (0.97, 10))  # (percentile threshold, penalty)


HIGH_TRUST, MEDIUM_TRUST = 80, 50


def trust_score(flags: pd.DataFrame, anomaly_pct: pd.Series) -> pd.Series:
    """Transparent additive score: 100 minus rule and anomaly penalties, floored at 0."""
    weights = pd.Series({f"dq_{r.name}": r.penalty for r in RULES})
    weights = weights[weights.index.intersection(flags.columns)]
    penalty = flags[weights.index].astype(int) @ weights
    pct = anomaly_pct.fillna(0)
    for threshold, p in ANOMALY_PENALTIES:  # highest matching threshold only
        penalty += np.where(pct >= threshold, p, 0)
        pct = pct.where(pct < threshold, 0)
    return (100 - penalty).clip(lower=0).astype(int)


def trust_tier(score: pd.Series) -> pd.Series:
    """high >= 80, medium 50-79, low < 50 (ordered categorical)."""
    tier = np.select([score >= HIGH_TRUST, score >= MEDIUM_TRUST], ["high", "medium"], "low")
    return pd.Series(pd.Categorical(tier, categories=["low", "medium", "high"]), index=score.index)


def assess(df: pd.DataFrame, seed: int = 0) -> pd.DataFrame:
    """Run all checks. Returns the quality table aligned to ``df``'s index."""
    ctx = add_context(df)
    flags = apply_rules(ctx)
    crit_cols = [f"dq_{r.name}" for r in RULES if r.severity == "critical"]

    out = ctx[["building_id", "data_year"]].copy()
    out = out.join(flags)
    out["n_critical"] = flags[crit_cols].sum(axis=1)
    out["n_warning"] = flags.drop(columns=crit_cols).sum(axis=1)
    out["anomaly_pct"] = anomaly_percentile(ctx, seed=seed)
    out["trust_score"] = trust_score(flags, out["anomaly_pct"])
    out["trust_tier"] = trust_tier(out["trust_score"])
    out["usable_for_modeling"] = out["n_critical"] == 0
    out["gas_assumed_zero"] = ctx["gas_assumed_zero"]
    out["gas_stopped"] = ctx["gas_stopped"]
    out["peer_z"] = ctx["peer_z"]
    out["ghg_fuel_ratio"] = ctx["ghg_fuel_ratio"]
    return out

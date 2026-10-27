"""Peer model: expected weather-normalized site EUI and GHG intensity per building-year.

"Expected" means: what similar buildings (by use mix, size, age, and form) use.
Two choices keep the comparison honest:

1. **Out-of-building predictions.** Each building appears in up to 11 years. Models
   are fit with GroupKFold by building, and each building is predicted only by a
   model that never saw it. Otherwise the model learns a building's own history and
   a persistently wasteful building looks "as expected".
2. **Peers are defined by what a building does, not by fixable traits.** No fuel-mix
   features (gas vs electric heating is the main BEPS lever) and no building age
   (old systems and envelopes are retrofit opportunities; EPA's ENERGY STAR model
   and the BEPS targets do not excuse them either). See README, Design decisions.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pandas as pd
from sklearn.model_selection import GroupKFold

from beps_prioritizer.peers.features import FEATURE_GROUPS, build_features, common_use_types
from beps_prioritizer.peers.models import MODELS, PRODUCTION_MODEL, BlendedPlusBoosting

TARGETS = {"eui": "site_eui_wn", "ghg": "ghg_intensity"}


N_FOLDS = 5


MIN_SINGLE_USE_PEERS = 50  # a use type is "well represented" with this many >=80% buildings


def folds(df: pd.DataFrame, n: int = N_FOLDS, seed: int = 0) -> pd.Series:
    """Fold id per row; all years of a building share one fold."""
    gkf = GroupKFold(n_splits=n, shuffle=True, random_state=seed)
    out = pd.Series(-1, index=df.index)
    for k, (_, test) in enumerate(gkf.split(df, groups=df["building_id"])):
        out.iloc[test] = k
    return out


def training_rows(df: pd.DataFrame, q: pd.DataFrame, target: str) -> pd.Series:
    """Rows a model may learn from: no critical data-quality flag and a positive target."""
    return q["usable_for_modeling"].to_numpy() & (df[target] > 0)


def cross_fit(
    df: pd.DataFrame, x: pd.DataFrame, train_ok: pd.Series, target: str,
    make_model: Callable[[], object], fold_id: pd.Series,
) -> pd.Series:
    """Predict every row with a model trained on usable rows of *other* buildings."""
    pred = pd.Series(np.nan, index=df.index)
    for k in sorted(fold_id.unique()):
        test = fold_id == k
        y = df.loc[~test & train_ok, target]
        model = make_model().fit(df, x, y)
        pred[test] = model.predict(df, x[test]).to_numpy()
    return pred


def peer_confidence(x: pd.DataFrame, train_ok: pd.Series) -> pd.Series:
    """Share of a building's floor area in use types with enough single-use peers.

    Low values (data centers, port terminals, rare use mixes) mean the expected
    value rests on few comparable buildings and the gap should be read cautiously.
    """
    shares = x.filter(like="share__").drop(columns="share__rare_use")
    n_single_use = (shares[train_ok] >= 0.8).sum()
    represented = n_single_use[n_single_use >= MIN_SINGLE_USE_PEERS].index
    return shares[represented].sum(axis=1).round(3)


def log_metrics(actual: pd.Series, pred: pd.Series) -> dict[str, float]:
    """Accuracy on the log scale (errors are multiplicative for EUI-like targets)."""
    ok = (actual > 0) & (pred > 0)
    err = np.log(actual[ok]) - np.log(pred[ok])
    centered = np.log(actual[ok]) - np.log(actual[ok]).mean()
    return {
        "rows": int(ok.sum()),
        "mae_log": float(err.abs().mean()),
        "typical_error_pct": float(100 * (np.exp(err.abs().median()) - 1)),
        "r2_log": float(1 - (err**2).sum() / (centered**2).sum()),
    }


def grouped_permutation_importance(
    model: BlendedPlusBoosting, x: pd.DataFrame, y: pd.Series, seed: int = 0
) -> pd.Series:
    """Increase in log-MAE when a whole feature group is shuffled together."""
    rng = np.random.default_rng(seed)
    base = np.abs(np.log(y) - np.log(model.predict(None, x))).mean()
    out = {}
    for name, member in FEATURE_GROUPS.items():
        cols = [c for c in x.columns if member(c)]
        shuffled = x.copy()
        perm = rng.permutation(len(x))
        shuffled[cols] = x[cols].iloc[perm].to_numpy()
        for c in cols:  # restore dtypes lost by to_numpy (categoricals)
            shuffled[c] = shuffled[c].astype(x[c].dtype)
        out[name] = np.abs(np.log(y) - np.log(model.predict(None, shuffled))).mean() - base
    return pd.Series(out).sort_values(ascending=False)


def run(df: pd.DataFrame, q: pd.DataFrame, seed: int = 0) -> tuple[pd.DataFrame, dict]:
    """Fit all models out-of-building. Returns per-row outputs and evaluation details."""
    x = build_features(df, common_use_types(df))
    fold_id = folds(df, seed=seed)
    out = df[["building_id", "data_year"]].copy()
    metrics, preds = [], {}

    for short, target in TARGETS.items():
        train_ok = pd.Series(training_rows(df, q, target), index=df.index)
        for name, make in MODELS.items():
            pred = cross_fit(df, x, train_ok, target, make, fold_id)
            preds[(short, name)] = pred
            m = log_metrics(df.loc[train_ok, target], pred[train_ok])
            metrics.append({"target": target, "model": name, **m})

        best = preds[(short, PRODUCTION_MODEL)]
        actual = df[target].where(df[target] > 0)
        resid = np.log(actual) - np.log(best)
        out[f"expected_{target}"] = best
        # The blended peer median alone, for plain-language explanations.
        out[f"peer_median_{target}"] = preds[(short, "blended use median")]
        out[f"{short}_ratio"] = actual / best
        # Percentile of the gap among trustworthy rows: 0.95 = worse than 95% of peers.
        ref = np.sort(resid[train_ok].dropna().to_numpy())
        out[f"{short}_gap_pct"] = pd.Series(
            np.searchsorted(ref, resid.to_numpy()) / len(ref), index=df.index
        ).where(resid.notna())

    out["peer_confidence"] = peer_confidence(x, pd.Series(training_rows(df, q, "site_eui_wn"),
                                                          index=df.index))

    # Grouped importance for the EUI model on one held-out fold.
    train_ok = pd.Series(training_rows(df, q, TARGETS["eui"]), index=df.index)
    test = (fold_id == 0) & train_ok
    gbm = BlendedPlusBoosting().fit(df, x, df.loc[(fold_id != 0) & train_ok, TARGETS["eui"]])
    importance = grouped_permutation_importance(gbm, x[test], df.loc[test, TARGETS["eui"]])

    details = {
        "metrics": pd.DataFrame(metrics),
        "importance": importance,
        "fold_id": fold_id,
        "train_ok": train_ok,
        "features": x,
        "preds": preds,
    }
    return out, details

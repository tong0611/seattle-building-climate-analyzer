"""Candidate peer models: two domain baselines, gradient boosting, and the hybrid.

All share one shape: ``fit(df, x, y)`` learns from the rows in ``y.index``;
``predict(df, x)`` returns one value per row of ``x`` on the original (not log)
scale. ``df`` gives access to raw columns (e.g. the largest use type).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

# Chosen among 4 configurations on the same building folds (see README, Design decisions).
GBM_PARAMS = dict(
    max_iter=300,
    learning_rate=0.03,
    max_leaf_nodes=15,
    min_samples_leaf=100,
    l2_regularization=1.0,
    categorical_features="from_dtype",
    random_state=0,
)


@dataclass
class UseTypeMedian:
    """Baseline 1: median of buildings with the same largest use type."""
    medians: pd.Series | None = None
    overall: float = np.nan

    def fit(self, df: pd.DataFrame, x: pd.DataFrame, y: pd.Series) -> UseTypeMedian:
        key = df.loc[y.index, "use1_type"].astype("string")
        self.medians = y.groupby(key).median()
        self.overall = float(y.median())
        return self

    def predict(self, df: pd.DataFrame, x: pd.DataFrame) -> pd.Series:
        key = df.loc[x.index, "use1_type"].astype("string")
        return key.map(self.medians).astype("float64").fillna(self.overall)


@dataclass
class BlendedUseMedian:
    """Baseline 2: floor-area-weighted blend of use-type medians.

    Mirrors how BEPS and CBPS set targets for mixed-use buildings. Each use type's
    median comes from buildings where it is >= 80% of floor area.
    """
    medians: pd.Series | None = None
    overall: float = np.nan

    def fit(self, df: pd.DataFrame, x: pd.DataFrame, y: pd.Series) -> BlendedUseMedian:
        shares = x.loc[y.index].filter(like="share__")
        self.overall = float(y.median())
        meds = {}
        for col in shares.columns:
            mostly = shares[col] >= 0.8
            meds[col] = y[mostly].median() if mostly.sum() >= 10 else self.overall
        self.medians = pd.Series(meds)
        return self

    def predict(self, df: pd.DataFrame, x: pd.DataFrame) -> pd.Series:
        shares = x.filter(like="share__")[self.medians.index]
        pred = shares.to_numpy() @ self.medians.to_numpy()
        covered = shares.sum(axis=1).to_numpy()
        return pd.Series(np.where(covered > 0, pred, self.overall), index=x.index)


@dataclass
class GradientBoosting:
    """Gradient boosting on log(target); returns predictions on the original scale."""
    model: HistGradientBoostingRegressor | None = None

    def fit(self, df: pd.DataFrame, x: pd.DataFrame, y: pd.Series) -> GradientBoosting:
        self.model = HistGradientBoostingRegressor(**GBM_PARAMS).fit(x.loc[y.index], np.log(y))
        return self

    def predict(self, df: pd.DataFrame | None, x: pd.DataFrame) -> pd.Series:
        return pd.Series(np.exp(self.model.predict(x)), index=x.index)


@dataclass
class BlendedPlusBoosting:
    """Production model: blended use-type median, corrected by gradient boosting.

    Boosting learns log(actual / blended median) from size, floors, building type,
    and use mix. Accuracy ties plain boosting with the same settings; this form is
    kept because "expected = peer median x adjustment" is easy to explain.
    """
    base: BlendedUseMedian | None = None
    model: HistGradientBoostingRegressor | None = None

    def fit(self, df: pd.DataFrame, x: pd.DataFrame, y: pd.Series) -> BlendedPlusBoosting:
        self.base = BlendedUseMedian().fit(df, x, y)
        offset = np.log(self.base.predict(df, x.loc[y.index]))
        self.model = HistGradientBoostingRegressor(**GBM_PARAMS).fit(
            x.loc[y.index], np.log(y) - offset
        )
        return self

    def predict(self, df: pd.DataFrame | None, x: pd.DataFrame) -> pd.Series:
        offset = np.log(self.base.predict(df, x))
        return pd.Series(np.exp(offset + self.model.predict(x)), index=x.index)


MODELS: dict[str, Callable[[], object]] = {
    "use-type median": UseTypeMedian,
    "blended use median": BlendedUseMedian,
    "gradient boosting": GradientBoosting,
    "blended median + boosting": BlendedPlusBoosting,
}


PRODUCTION_MODEL = "blended median + boosting"

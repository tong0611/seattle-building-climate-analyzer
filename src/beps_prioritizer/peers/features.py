"""Peer features from the use-type mix, size, and form of each building.

Fuel mix and building age are deliberately excluded (see peers.crossfit).
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pandas as pd

MIN_USE_TYPE_ROWS = 100


# Not part of EUI floor area (parking) or recorded with zero area (pool, EV charging).
NON_AREA_USES = {"Parking", "Swimming Pool", "Electric Vehicle Charging Station"}


def uses_long(df: pd.DataFrame) -> pd.DataFrame:
    """The three use-type/GFA column pairs as long rows: (row, use_type, gfa)."""
    parts = []
    for i in (1, 2, 3):
        part = df[[f"use{i}_type", f"use{i}_gfa"]].set_axis(["use_type", "gfa"], axis=1)
        parts.append(part.assign(row=df.index))
    long = pd.concat(parts).dropna(subset=["use_type"])
    long["use_type"] = long["use_type"].astype("string")
    return long


def common_use_types(df: pd.DataFrame, min_rows: int = MIN_USE_TYPE_ROWS) -> list[str]:
    """Use types frequent enough to get their own share feature (others are pooled)."""
    counts = uses_long(df)["use_type"].value_counts()
    keep = counts[(counts >= min_rows) & ~counts.index.isin(NON_AREA_USES)].index
    return sorted(keep)


def use_shares(df: pd.DataFrame, use_types: list[str]) -> pd.DataFrame:
    """Share of (non-parking) floor area in each common use type; rare types pooled."""
    long = uses_long(df)
    long = long[~long["use_type"].isin(NON_AREA_USES)]
    long["gfa"] = long["gfa"].fillna(0).clip(lower=0)
    long["col"] = np.where(long["use_type"].isin(use_types), long["use_type"], "rare_use")
    wide = long.pivot_table(index="row", columns="col", values="gfa", aggfunc="sum", fill_value=0)
    wide = wide.reindex(index=df.index, columns=use_types + ["rare_use"], fill_value=0)
    total = wide.sum(axis=1).replace(0, np.nan)
    return wide.div(total, axis=0).fillna(0)


def build_features(df: pd.DataFrame, use_types: list[str]) -> pd.DataFrame:
    """Peer features: use-mix shares, size, form, building type, year.

    Deliberately excludes fuel mix and building age (see module docstring).
    """
    shares = use_shares(df, use_types)
    long = uses_long(df)
    present = long.groupby("row")["use_type"].agg(set).reindex(df.index)

    def has(use: str) -> pd.Series:
        return present.map(lambda s: isinstance(s, set) and use in s).astype(int)

    x = pd.DataFrame(index=df.index)
    x["log_gfa_buildings"] = np.log(df["gfa_buildings"].clip(lower=1))
    x["parking_ratio"] = (df["gfa_parking"].clip(lower=0) / df["gfa_buildings"]).clip(upper=5)
    x["num_floors"] = df["num_floors"].astype("float64").where(df["num_floors"] > 0)
    x["num_buildings"] = df["num_buildings"].astype("float64").clip(1, 50)
    x["data_year"] = df["data_year"].astype("float64")
    x["building_type"] = df["building_type"].astype("category")
    x["largest_use_share"] = shares.max(axis=1)
    x["has_pool"] = has("Swimming Pool")
    x["has_ev_charging"] = has("Electric Vehicle Charging Station")
    return pd.concat([x, shares.add_prefix("share__")], axis=1)


FEATURE_GROUPS: dict[str, Callable[[str], bool]] = {
    "use mix": lambda c: c.startswith("share__") or c in {"largest_use_share", "has_pool",
                                                          "has_ev_charging"},
    "size": lambda c: c in {"log_gfa_buildings", "num_buildings", "parking_ratio"},
    "floors": lambda c: c == "num_floors",
    "building type": lambda c: c == "building_type",
    "data year": lambda c: c == "data_year",
}

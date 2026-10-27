"""Profile the standardized benchmarking table and write a Markdown report.

The report is descriptive: it counts what looks wrong, but does not decide what
to do about it. Those decisions belong in the data-quality layer.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd

from beps_prioritizer.reporting import md_table

YEAR_COLUMNS = {"data_year", "year_built"}


KEY_METRICS = [
    "site_eui",
    "site_eui_wn",
    "source_eui",
    "energy_star_score",
    "electricity_kwh",
    "natural_gas_therms",
    "steam_kbtu",
    "ghg_intensity",
]


@dataclass(frozen=True)
class Check:
    """A descriptive profiling check (not a data-quality decision)."""

    name: str
    description: str
    rule: Callable[[pd.DataFrame], pd.Series]


def _gfa_mismatch(df: pd.DataFrame) -> pd.Series:
    rel = (df["self_gfa_buildings"] - df["gfa_buildings"]).abs() / df["gfa_buildings"]
    return (df["self_gfa_buildings"] > 0) & (rel > 0.20)


def _implied_gfa_mismatch(df: pd.DataFrame) -> pd.Series:
    # Site EUI should equal site energy / building floor area (parking excluded).
    implied = df["site_energy_kbtu"] / df["site_eui"]
    rel = (implied - df["gfa_buildings"]).abs() / df["gfa_buildings"]
    return rel > 0.20


def _use_type_share(df: pd.DataFrame) -> pd.Series:
    total = df[["use1_gfa", "use2_gfa", "use3_gfa"]].sum(axis=1, min_count=1)
    return df["use1_gfa"] / total


# Candidate data-quality rules. Thresholds are deliberately loose: these are
# "worth a look" counts for profiling, not final DQ decisions.
CHECKS: list[Check] = [
    Check("site_eui_gt_1000", "Site EUI > 1,000 kBtu/sf (typical: 20-300)",
          lambda d: d["site_eui"] > 1000),
    Check("site_eui_lt_5", "Site EUI < 5 kBtu/sf (implausibly low)",
          lambda d: d["site_eui"] < 5),
    Check("source_eui_nonpositive", "Source EUI <= 0",
          lambda d: d["source_eui"] <= 0),
    Check("ghg_intensity_gt_50", "GHG intensity > 50 kgCO2e/sf (BEPS max target is 6.3)",
          lambda d: d["ghg_intensity"] > 50),
    Check("negative_parking_gfa", "Parking GFA < 0",
          lambda d: d["gfa_parking"] < 0),
    Check("electricity_lt_1000_kwh", "Electricity < 1,000 kWh/year for a 20k+ sf building",
          lambda d: d["electricity_kwh"] < 1000),
    Check("gas_le_10_therms", "Natural gas recorded but <= 10 therms/year",
          lambda d: d["natural_gas_therms"] <= 10),
    Check("zero_buildings", "Number of buildings = 0",
          lambda d: d["num_buildings"] == 0),
    Check("zero_floors", "Number of floors = 0",
          lambda d: d["num_floors"] == 0),
    Check("self_gfa_zero", "Self-reported total GFA = 0",
          lambda d: d["self_gfa_total"] == 0),
    Check("gfa_self_vs_city_gt_20pct", "Self-reported vs city building GFA differ by > 20%",
          _gfa_mismatch),
    Check("eui_vs_energy_gfa_gt_20pct", "Site EUI disagrees with site energy / GFA by > 20%",
          _implied_gfa_mismatch),
    Check("built_after_data_year", "Year built later than data year",
          lambda d: d["year_built"] > d["data_year"]),
    Check("bad_parcel_id", "Tax parcel ID not 10 digits",
          lambda d: ~d["tax_parcel_id"].str.fullmatch(r"\d{10}", na=False)),
]


def column_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Type, completeness, cardinality, and quantiles for every column."""
    rows = []
    for col in df.columns:
        s = df[col]
        row: dict[str, object] = {
            "column": col,
            "dtype": str(s.dtype),
            "non_null_pct": round(100 * s.notna().mean(), 1),
            "n_unique": int(s.nunique()),
        }
        if pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s):
            q = s.astype("float64").quantile([0.0, 0.01, 0.5, 0.99, 1.0])
            stats = dict(min=q[0.0], p01=q[0.01], median=q[0.5], p99=q[0.99], max=q[1.0])
            if col in YEAR_COLUMNS:  # print 2015, not 2,015
                stats = {k: str(int(v)) for k, v in stats.items()}
            row.update(stats)
        rows.append(row)
    return pd.DataFrame(rows).set_index("column")


def missing_by_year(df: pd.DataFrame, cols: list[str] = KEY_METRICS) -> pd.DataFrame:
    """Rows per year and % missing for each key metric."""
    pct = df.groupby("data_year")[cols].agg(lambda s: round(100 * s.isna().mean(), 1))
    pct.insert(0, "rows", df.groupby("data_year").size())
    return pct


def run_checks(df: pd.DataFrame, checks: list[Check] = CHECKS) -> pd.DataFrame:
    """Count rows hit by each check, and how often the city already labels them."""
    labeled = df["compliance_issue"].astype("string").fillna("(missing)") != "No Issue"
    flagged = df["compliance_issue"] == "Flagged for Possible Reporting Errors"
    rows = []
    for c in checks:
        hit = c.rule(df).fillna(False).astype(bool)
        n = int(hit.sum())
        rows.append({
            "check": c.name,
            "description": c.description,
            "rows": n,
            "buildings": int(df.loc[hit, "building_id"].nunique()),
            "pct_city_labeled_issue": round(100 * labeled[hit].mean(), 1) if n else None,
            "pct_city_flagged_error": round(100 * flagged[hit].mean(), 1) if n else None,
        })
    return pd.DataFrame(rows).set_index("check")


def coverage_summary(df: pd.DataFrame) -> pd.DataFrame:
    """How many buildings reported for 1, 2, ..., 11 years."""
    years = df.groupby("building_id")["data_year"].nunique()
    counts = years.value_counts().sort_index().rename("buildings")
    return counts.rename_axis("years_reported").to_frame()


def mixed_use_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Rows binned by the floor-area share of their largest use."""
    share = _use_type_share(df)
    bins = pd.cut(share, [0, 0.5, 0.6, 0.8, 0.95, 1.0001],
                  labels=["<50%", "50-60%", "60-80%", "80-95%", ">=95%"])
    counts = bins.value_counts().sort_index().rename("rows")
    return counts.rename_axis("largest_use_share").to_frame()


def build_report(df: pd.DataFrame) -> str:
    latest = int(df["data_year"].max())
    n_bldg = df["building_id"].nunique()
    gas_missing = df["natural_gas_therms"].isna()
    no_issue = (df["compliance_issue"] == "No Issue").sum()

    parts = [
        "# Data profile: Seattle Building Energy Benchmarking (2015-present)",
        "",
        "_Generated by `beps-profile`. Descriptive only; no rows have been removed or edited._",
        "",
        "## Overview",
        "",
        f"- **Rows:** {len(df):,} building-years",
        f"- **Buildings:** {n_bldg:,} unique `building_id`s",
        f"- **Years:** {df['data_year'].min()}-{latest}",
        f"- **Latest year:** {(df['data_year'] == latest).sum():,} buildings reported",
        f"- **Demolished rows:** {int(df['demolished'].sum()):,}",
        f"- **City compliance issue = 'No Issue':** {no_issue:,} rows "
        f"({100 * no_issue / len(df):.1f}%)",
        f"- **Natural gas missing:** {gas_missing.sum():,} rows ({100 * gas_missing.mean():.1f}%); "
        "may mean 'no gas service' rather than unknown",
        "",
        "## Rows per year and missingness of key metrics (% missing)",
        "",
        md_table(missing_by_year(df)),
        "",
        "## Building coverage (years reported per building)",
        "",
        md_table(coverage_summary(df)),
        "",
        "## Building type",
        "",
        md_table(df["building_type"].value_counts().rename("rows").to_frame()),
        "",
        "## City compliance issue labels",
        "",
        md_table(df["compliance_issue"].value_counts(dropna=False).rename("rows").to_frame()),
        "",
        "## Mixed-use buildings (share of floor area in the largest use)",
        "",
        "Buildings whose largest use is under ~60% of floor area need a blended peer "
        "baseline and a blended BEPS/CBPS target, not a single use-type label.",
        "",
        md_table(mixed_use_summary(df)),
        "",
        "## Candidate data-quality checks",
        "",
        "`pct_city_labeled_issue` = share of hit rows the city already labels with any "
        "issue; `pct_city_flagged_error` = share labeled 'Flagged for Possible Reporting "
        "Errors'. Low values mean the city labels miss these rows, so city labels are an "
        "incomplete ground truth.",
        "",
        md_table(run_checks(df)),
        "",
        "## Top 15 largest-use types",
        "",
        md_table(df["use1_type"].value_counts().head(15).rename("rows").to_frame()),
        "",
        "## Column summary",
        "",
        md_table(column_summary(df)),
        "",
    ]
    return "\n".join(parts)

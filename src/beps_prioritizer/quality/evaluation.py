"""Evaluate the data-quality layer by injecting realistic reporting errors.

The city's issue labels are a weak ground truth: "Flagged for Possible Reporting
Errors" exists only for 2024-2025, and labels like "Default Data" describe the
reporting process rather than the values. So the main evaluation injects
realistic reporting errors into clean records and measures how many the layer
surfaces (trust tier below "high"). City labels are reported as a secondary view.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd

from beps_prioritizer.quality.rules import RULES
from beps_prioritizer.quality.trust import HIGH_TRUST, assess, trust_score
from beps_prioritizer.regulations.beps import BEPS_FACTORS_BASELINE
from beps_prioritizer.reporting import md_table

ANOMALY_ALERT_PCT = 0.97


EUI_COLS = ["site_eui", "site_eui_wn", "source_eui", "source_eui_wn"]


ENERGY_COLS = ["site_energy_kbtu", "site_energy_wn_kbtu"]


def _scale(df: pd.DataFrame, idx: pd.Index, cols: list[str], factor: pd.Series | float) -> None:
    """Multiply columns for rows idx in place."""
    for c in cols:
        df.loc[idx, c] = df.loc[idx, c] * factor


def _change_fuel(df: pd.DataFrame, idx: pd.Index, fuel_kbtu: str, new_kbtu: pd.Series) -> None:
    """Set one fuel's kBtu and propagate to site energy, EUIs, and GHG consistently."""
    old = df.loc[idx, fuel_kbtu].fillna(0)
    delta = new_kbtu.fillna(0) - old
    energy_factor = (df.loc[idx, "site_energy_kbtu"] + delta) / df.loc[idx, "site_energy_kbtu"]
    ghg_delta = delta * BEPS_FACTORS_BASELINE[fuel_kbtu] / 1000
    ghg_factor = (df.loc[idx, "ghg_total_mtco2e"] + ghg_delta) / df.loc[idx, "ghg_total_mtco2e"]

    df.loc[idx, fuel_kbtu] = new_kbtu
    if fuel_kbtu == "electricity_kbtu":
        df.loc[idx, "electricity_kwh"] = new_kbtu / 3.412
    elif fuel_kbtu == "natural_gas_kbtu":
        df.loc[idx, "natural_gas_therms"] = new_kbtu / 100
    _scale(df, idx, ENERGY_COLS + EUI_COLS, energy_factor)
    _scale(df, idx, ["ghg_total_mtco2e", "ghg_intensity"], ghg_factor)


def _floor_area(factor: float) -> Callable[[pd.DataFrame, pd.Index], None]:
    """Owner typo in floor area: the area and every per-sq-ft metric shift together."""
    def inject(df: pd.DataFrame, idx: pd.Index) -> None:
        _scale(df, idx, ["self_gfa_buildings", "self_gfa_total"], factor)
        _scale(df, idx, EUI_COLS + ["ghg_intensity"], 1 / factor)
    return inject


def _electricity(factor: float) -> Callable[[pd.DataFrame, pd.Index], None]:
    """Meter entered in wrong units (x1000 / x0.001) or double-counted (x2)."""
    def inject(df: pd.DataFrame, idx: pd.Index) -> None:
        _change_fuel(df, idx, "electricity_kbtu", df.loc[idx, "electricity_kbtu"] * factor)
    return inject


def _drop_gas(df: pd.DataFrame, idx: pd.Index) -> None:
    """Gas meter left out of the report."""
    _change_fuel(df, idx, "natural_gas_kbtu", pd.Series(np.nan, index=idx))


@dataclass(frozen=True)
class ErrorType:
    """A realistic reporting error and how to inject it into rows ``idx`` in place."""

    name: str
    description: str
    inject: Callable[[pd.DataFrame, pd.Index], None]
    needs_gas: bool = False


ERROR_TYPES: list[ErrorType] = [
    ErrorType("floor_area_x10", "Floor area typed 10x too large", _floor_area(10)),
    ErrorType("floor_area_x0.1", "Floor area typed 10x too small", _floor_area(0.1)),
    ErrorType("electricity_x1000", "Electricity in wrong units (Wh as kWh)", _electricity(1000)),
    ErrorType("electricity_x0.001", "Electricity in wrong units (MWh as kWh)", _electricity(0.001)),
    ErrorType("electricity_x2", "Electricity meter double-counted", _electricity(2)),
    ErrorType("gas_meter_missing", "Gas meter left out (gas >= 20% of energy)", _drop_gas,
              needs_gas=True),
]


def clean_pool(df: pd.DataFrame, q: pd.DataFrame) -> pd.Index:
    """Rows we can treat as correct: city says No Issue and the layer raises nothing."""
    flag_cols = [c for c in q.columns if c.startswith("dq_")]
    ok = (
        (df["compliance_issue"] == "No Issue")
        & (q["trust_tier"] == "high")
        & ~q[flag_cols].any(axis=1)
        & (q["anomaly_pct"] < ANOMALY_ALERT_PCT)
    )
    return df.index[ok]


def run_injection(
    df: pd.DataFrame, q: pd.DataFrame, n: int = 300, seed: int = 0
) -> pd.DataFrame:
    """Inject each error type into n clean rows, rerun the whole layer, and report
    how often the rows are caught by rules, by the model, and surfaced overall."""
    rng = np.random.default_rng(seed)
    pool = clean_pool(df, q)
    gas_share = df["natural_gas_kbtu"].fillna(0) / df["site_energy_kbtu"]
    flag_cols = [c for c in q.columns if c.startswith("dq_")]

    rows = []
    for et in ERROR_TYPES:
        candidates = pool[(gas_share.loc[pool] >= 0.2).to_numpy()] if et.needs_gas else pool
        idx = pd.Index(rng.choice(candidates, size=min(n, len(candidates)), replace=False))
        corrupted = df.copy()
        et.inject(corrupted, idx)
        qc = assess(corrupted, seed=seed).loc[idx]

        by_rules = qc[flag_cols].any(axis=1)
        by_model = qc["anomaly_pct"] >= ANOMALY_ALERT_PCT
        surfaced = qc["trust_tier"] != "high"
        no_model = trust_score(qc[flag_cols], qc["anomaly_pct"] * 0) < HIGH_TRUST
        rows.append({
            "error_type": et.name,
            "description": et.description,
            "n_injected": len(idx),
            "caught_by_rules_pct": round(100 * by_rules.mean(), 1),
            "caught_by_model_pct": round(100 * by_model.mean(), 1),
            "surfaced_pct": round(100 * surfaced.mean(), 1),
            "surfaced_without_model_pct": round(100 * no_model.mean(), 1),
            "unusable_for_modeling_pct": round(100 * (~qc["usable_for_modeling"]).mean(), 1),
        })
    return pd.DataFrame(rows).set_index("error_type")


def rule_table(q: pd.DataFrame) -> pd.DataFrame:
    """Report table: each rule's severity, penalty, and hit counts on real data."""
    rows = []
    for r in RULES:
        hit = q[f"dq_{r.name}"]
        rows.append({
            "rule": r.name,
            "severity": r.severity,
            "penalty": r.penalty,
            "description": r.description,
            "rows": int(hit.sum()),
            "buildings": int(q.loc[hit, "building_id"].nunique()),
        })
    return pd.DataFrame(rows).set_index("rule")


def city_label_view(df: pd.DataFrame, q: pd.DataFrame) -> pd.DataFrame:
    """Share of rows surfaced, per city compliance-issue label."""
    label = df["compliance_issue"].astype("string").fillna("(missing)")
    surfaced = q["trust_tier"] != "high"
    out = pd.DataFrame({"label": label, "surfaced": surfaced})
    agg = out.groupby("label").agg(rows=("surfaced", "size"), surfaced_pct=("surfaced", "mean"))
    agg["surfaced_pct"] = (100 * agg["surfaced_pct"]).round(1)
    return agg.sort_values("rows", ascending=False)


def examples_missed_by_city(df: pd.DataFrame, q: pd.DataFrame, k: int = 12) -> pd.DataFrame:
    """Low-trust rows the city labels 'No Issue', largest emitters first."""
    flag_cols = [c for c in q.columns if c.startswith("dq_")]
    m = (df["compliance_issue"] == "No Issue") & (q["trust_tier"] == "low")
    sub = df.loc[m].assign(
        flags=q.loc[m, flag_cols].apply(
            lambda r: ", ".join(c[3:] for c, v in r.items() if v), axis=1
        ),
        trust=q.loc[m, "trust_score"],
    )
    sub = sub.sort_values("ghg_total_mtco2e", ascending=False).head(k)
    cols = ["building_name", "data_year", "use1_type", "site_eui", "ghg_intensity",
            "trust", "flags"]
    return sub[cols].reset_index(drop=True)


def build_report(df: pd.DataFrame, q: pd.DataFrame, inj: pd.DataFrame) -> str:
    tiers = q["trust_tier"].value_counts().reindex(["high", "medium", "low"])
    no_issue = df["compliance_issue"] == "No Issue"
    alert_rate = 100 * (q.loc[no_issue, "trust_tier"] != "high").mean()
    flagged = df["compliance_issue"] == "Flagged for Possible Reporting Errors"
    flagged_recall = 100 * (q.loc[flagged, "trust_tier"] != "high").mean()
    flagged_with_values = flagged & ~q["dq_missing_core_metrics"]
    n_fv = int(flagged_with_values.sum())
    n_fv_surfaced = int((q.loc[flagged_with_values, "trust_tier"] != "high").sum())
    tier_by_year = pd.crosstab(q["data_year"], q["trust_tier"]).reindex(
        columns=["high", "medium", "low"]
    )

    return "\n".join([
        "# Data-quality layer: results and evaluation",
        "",
        "_Generated by `beps-quality-eval`. Flags mean \"worth a look\", not wrongdoing._",
        "",
        "## How the trust score works",
        "",
        "Each building-year starts at 100. Each **critical** flag subtracts 60; each "
        "**warning** subtracts 10-25 depending on its impact (see Rules); the Isolation "
        "Forest anomaly model subtracts 25 "
        "(top 1% most unusual) or 10 (top 3%). Tiers: **high** >= 80, **medium** 50-79, "
        "**low** < 50. Rows with any critical flag are excluded from peer modeling.",
        "",
        "## Summary",
        "",
        f"- Trust tiers: high **{tiers['high']:,}**, medium **{tiers['medium']:,}**, "
        f"low **{tiers['low']:,}** (of {len(q):,} building-years)",
        f"- Usable for modeling: **{int(q['usable_for_modeling'].sum()):,}** rows",
        f"- Review workload: **{alert_rate:.1f}%** of rows the city marks 'No Issue' are "
        "surfaced (below high trust)",
        f"- Of {int(flagged.sum())} rows the city flags for possible reporting errors, "
        f"{flagged_recall:.1f}% are surfaced, but mostly because their metrics are missing; "
        f"among the {n_fv} that have values, **{n_fv_surfaced}** are surfaced (see caveat below)",
        "",
        "## Evaluation: injected reporting errors",
        "",
        "Realistic errors were injected into clean records (city 'No Issue', no flags, not "
        "anomalous), with derived fields (site energy, EUI, GHG) recomputed consistently "
        "so the error is not trivially visible. The full pipeline, including the anomaly "
        "model, was then re-run. `surfaced_pct` = share of corrupted rows that drop below "
        "high trust.",
        "",
        md_table(inj),
        "",
        "`surfaced_without_model_pct` is an ablation: the same score with the anomaly "
        "model removed. On these known error types the rules do all of the work; the "
        "model is kept, at low weight, as a catch-all for inconsistency patterns no single "
        "rule covers.",
        "",
        "## Rules",
        "",
        md_table(rule_table(q)),
        "",
        "## Trust tier by year",
        "",
        md_table(tier_by_year),
        "",
        "## Comparison with city issue labels",
        "",
        "Caveat: most labels describe reporting *process* (missing utility data, account "
        "not verified, default values), not suspicious *values*. 'Flagged for Possible "
        "Reporting Errors' only appears in 2024-2025, and many of those rows have "
        "ordinary-looking values, so the city's criteria likely use information not in "
        "the public dataset.",
        "",
        md_table(city_label_view(df, q)),
        "",
        "## Examples: low trust, but labeled 'No Issue' by the city",
        "",
        md_table(examples_missed_by_city(df, q), index=False),
        "",
    ])

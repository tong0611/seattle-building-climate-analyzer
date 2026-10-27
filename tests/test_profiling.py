from __future__ import annotations

import pandas as pd

from beps_prioritizer.ingestion.ingest import standardize
from beps_prioritizer.ingestion.profiling import build_report, missing_by_year, run_checks


def test_checks_flag_extreme_eui(raw_df: pd.DataFrame) -> None:
    checks = run_checks(standardize(raw_df))
    assert checks.loc["site_eui_gt_1000", "rows"] == 1
    assert checks.loc["site_eui_gt_1000", "buildings"] == 1
    assert checks.loc["negative_parking_gfa", "rows"] == 0


def test_missing_by_year_counts_rows(raw_row) -> None:
    raw = pd.DataFrame([
        raw_row(),
        raw_row(OSEBuildingID="2", **{"NaturalGas(therms)": None}),
    ])
    out = missing_by_year(standardize(raw), cols=["natural_gas_therms"])
    assert out.loc[2024, "rows"] == 2
    assert out.loc[2024, "natural_gas_therms"] == 50.0


def test_report_renders(raw_df: pd.DataFrame) -> None:
    report = build_report(standardize(raw_df))
    assert report.startswith("# Data profile")
    assert "Candidate data-quality checks" in report

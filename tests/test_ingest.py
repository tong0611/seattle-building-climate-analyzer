from __future__ import annotations

import pandas as pd
import pytest

from beps_prioritizer.ingestion.ingest import standardize


def test_renames_and_types(raw_df: pd.DataFrame) -> None:
    df = standardize(raw_df)
    assert "site_eui" in df.columns and "SiteEUI(kBtu/sf)" not in df.columns
    assert df["data_year"].dtype == "Int64"
    assert df["site_eui"].dtype == "float64"
    assert df["demolished"].dtype == "boolean"
    assert df["building_type"].dtype == "category"


def test_text_normalization(raw_df: pd.DataFrame) -> None:
    df = standardize(raw_df)
    assert set(df["neighborhood"]) == {"MAGNOLIA/QUEEN ANNE"}
    assert set(df["city"]) == {"SEATTLE"}
    assert df.loc[0, "building_name"] == "Example Office"
    assert df.loc[0, "tax_parcel_id"] == "0659000030"


def test_suspicious_values_are_kept(raw_df: pd.DataFrame) -> None:
    df = standardize(raw_df)
    assert len(df) == len(raw_df)
    assert df["site_eui"].max() == pytest.approx(44197.9)


def test_sorted_by_building_and_year(raw_df: pd.DataFrame) -> None:
    df = standardize(raw_df)
    assert list(zip(df["building_id"], df["data_year"], strict=True)) == [
        (1, 2023), (1, 2024), (2, 2024)
    ]


def test_rejects_duplicate_building_year(raw_row) -> None:
    raw = pd.DataFrame([raw_row(), raw_row()])
    with pytest.raises(ValueError, match="one row per"):
        standardize(raw)


def test_rejects_missing_columns(raw_df: pd.DataFrame) -> None:
    with pytest.raises(ValueError, match="missing expected columns"):
        standardize(raw_df.drop(columns=["SiteEUI(kBtu/sf)"]))


def test_missing_numeric_stays_missing(raw_row) -> None:
    df = standardize(pd.DataFrame([raw_row(**{"NaturalGas(therms)": None})]))
    assert pd.isna(df.loc[0, "natural_gas_therms"])

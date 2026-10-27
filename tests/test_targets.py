from __future__ import annotations

import pandas as pd
import pytest

from beps_prioritizer.ingestion.ingest import standardize
from beps_prioritizer.peers.features import uses_long
from beps_prioritizer.quality.trust import assess
from beps_prioritizer.regulations.beps import (
    BEPS_DEADLINES,
    BEPS_FACTORS_BASELINE,
    BEPS_GHGIT,
    PM_TO_BEPS,
)
from beps_prioritizer.regulations.cbps import PM_TO_CBPS_EUIT
from beps_prioritizer.scoring.targets import (
    assess_targets,
    blended_target,
    compliance_ghgi,
    deadline_for,
)


def test_every_beps_activity_has_targets() -> None:
    used = {a for a in PM_TO_BEPS.values() if a}
    assert used <= set(BEPS_GHGIT)


def test_mappings_cover_the_same_use_types() -> None:
    assert set(PM_TO_BEPS) == set(PM_TO_CBPS_EUIT)


def test_mixed_use_target_is_area_weighted(raw_row) -> None:
    df = standardize(pd.DataFrame([raw_row(**{
        "LargestPropertyUseType": "Office", "LargestPropertyUseTypeGFA": "60000",
        "SecondLargestPropertyUseType": "Multifamily Housing",
        "SecondLargestPropertyUseTypeGFA": "30000",
        "ThirdLargestPropertyUseType": "Parking", "ThirdLargestPropertyUseTypeGFA": "50000",
    })]))
    values = {pm: (BEPS_GHGIT[a][0] if a else None) for pm, a in PM_TO_BEPS.items()}
    target, coverage = blended_target(df, values)
    assert target.iloc[0] == pytest.approx((60_000 * 0.81 + 30_000 * 0.89) / 90_000)
    assert coverage.iloc[0] == 1.0  # parking is excluded from floor area, not "uncovered"


def test_exempt_use_lowers_coverage(raw_row) -> None:
    df = standardize(pd.DataFrame([raw_row(**{
        "LargestPropertyUseType": "Manufacturing/Industrial Plant",
        "LargestPropertyUseTypeGFA": "45000",
        "SecondLargestPropertyUseType": "Office", "SecondLargestPropertyUseTypeGFA": "45000",
    })]))
    values = {pm: (BEPS_GHGIT[a][0] if a else None) for pm, a in PM_TO_BEPS.items()}
    target, coverage = blended_target(df, values)
    assert target.iloc[0] == pytest.approx(0.81)
    assert coverage.iloc[0] == pytest.approx(0.5)


def test_baseline_factors_reproduce_reported_ghgi(raw_row) -> None:
    df = standardize(pd.DataFrame([raw_row(**{"SiteEnergyUseWN(kBtu)": "5412000"})]))
    ghgi = compliance_ghgi(df, BEPS_FACTORS_BASELINE)
    assert ghgi.iloc[0] == pytest.approx(df["ghg_intensity"].iloc[0], rel=0.01)


def test_deadlines_by_size() -> None:
    gfa = pd.Series([20_000, 25_000, 60_000, 100_000, 300_000], dtype=float)
    assert deadline_for(gfa, BEPS_DEADLINES).tolist() == [pd.NA, 2035, 2033, 2032, 2031]


def test_over_target_office_gets_penalty_and_capped_acp(raw_row) -> None:
    """Office, 90,000 sf, gas-heavy: GHGI ~1.2 vs 0.81 target -> over."""
    df = standardize(pd.DataFrame([raw_row(DataYear=str(y)) for y in (2023, 2024, 2025)]))
    t = assess_targets(df, assess(df))
    row = t.iloc[0]
    assert row["basis_year"] == 2025
    assert row["beps_over_2031"]
    assert row["beps_first_deadline"] == 2032
    assert row["beps_penalty_est"] == pytest.approx(10 * row["area_sf"])
    expected_acp = row["beps_excess_tco2e_yr"] * 5 * 190
    assert row["beps_acp_est"] == pytest.approx(max(2_500, expected_acp))
    assert row["beps_acp_est"] <= row["beps_penalty_est"]
    assert row["cbps_tier1"]  # 90,000 sf of office


def test_all_electric_building_meets_target(raw_row) -> None:
    rows = [raw_row(DataYear=str(y), **{
        "NaturalGas(therms)": None, "NaturalGas(kBtu)": None,
        "SiteEnergyUse(kBtu)": "3412000", "SiteEnergyUseWN(kBtu)": "3412000",
        "SiteEUI(kBtu/sf)": "37.91", "SiteEUIWN(kBtu/sf)": "37.91",
        "TotalGHGEmissions": "19.79", "GHGEmissionsIntensity": "0.22",
    }) for y in (2023, 2024, 2025)]
    df = standardize(pd.DataFrame(rows))
    t = assess_targets(df, assess(df))
    assert not t.iloc[0]["beps_over_2031"]
    assert t.iloc[0]["beps_acp_est"] == 0
    assert not t.iloc[0]["cbps_over_euit"]  # 37.9 < office EUIt 63


def test_dataset_use_types_are_all_mapped() -> None:
    """Guard: if the city adds a new use type, the mapping must be extended."""
    from beps_prioritizer.config import BENCHMARKING_PARQUET
    if not BENCHMARKING_PARQUET.exists():
        pytest.skip("real data not built")
    types = set(uses_long(pd.read_parquet(BENCHMARKING_PARQUET))["use_type"].dropna())
    assert types <= set(PM_TO_BEPS), sorted(types - set(PM_TO_BEPS))

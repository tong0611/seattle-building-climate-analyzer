from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from beps_prioritizer.ingestion.ingest import standardize
from beps_prioritizer.quality.evaluation import _drop_gas, _electricity, _floor_area
from beps_prioritizer.quality.rules import _spike, add_context, apply_rules, fuel_ghg_mtco2e
from beps_prioritizer.quality.trust import assess, trust_score


@pytest.fixture
def panel(raw_row) -> pd.DataFrame:
    """Building 1 reported 2019-2023 with stable, internally consistent values."""
    rows = [raw_row(DataYear=str(y)) for y in range(2019, 2024)]
    return standardize(pd.DataFrame(rows))


def test_fixture_ghg_matches_beps_factors(panel: pd.DataFrame) -> None:
    ratio = panel["ghg_total_mtco2e"] / fuel_ghg_mtco2e(panel)
    assert ratio.between(0.99, 1.01).all()


def test_clean_panel_has_no_flags(panel: pd.DataFrame) -> None:
    flags = apply_rules(add_context(panel))
    assert not flags.any().any(), flags.columns[flags.any()].tolist()


def test_gas_history_classification(panel: pd.DataFrame) -> None:
    df = panel.copy()
    years = df["data_year"]
    df.loc[years == 2021, "natural_gas_therms"] = np.nan  # gap: gas before and after
    df.loc[years == 2023, "natural_gas_therms"] = np.nan  # stopped: never returns
    ctx = add_context(df).set_index("data_year")
    assert ctx.loc[2021, "gas_gap"] and not ctx.loc[2021, "gas_stopped"]
    assert ctx.loc[2023, "gas_stopped"] and not ctx.loc[2023, "gas_gap"]


def test_gas_never_present_is_assumed_zero(panel: pd.DataFrame) -> None:
    df = panel.assign(natural_gas_therms=np.nan)
    ctx = add_context(df)
    assert ctx["gas_assumed_zero"].all() and not ctx["gas_gap"].any()


def test_spike_flags_blip_not_step() -> None:
    blip = pd.Series([100, 100, 300, 100, 100], dtype=float)
    step = pd.Series([100, 100, 300, 300, 300], dtype=float)
    for series, expected_any in [(blip, True), (step, False)]:
        prev, nxt = series / series.shift(1), series / series.shift(-1)
        interior = _spike(prev, nxt, 2.0, 2.5).iloc[1:-1]
        assert interior.any() == expected_any


def test_spike_flags_jump_in_latest_year() -> None:
    s = pd.Series([100, 100, 100, 300], dtype=float)
    flags = _spike(s / s.shift(1), s / s.shift(-1), 2.0, 2.5)
    assert flags.iloc[-1] and not flags.iloc[:-1].any()


def test_trust_score_penalties() -> None:
    flags = pd.DataFrame({
        "dq_eui_implausible": [True, False, False, False],
        "dq_gas_gap": [False, True, False, False],
        "dq_gfa_self_vs_city": [False, False, True, False],
    })
    anomaly = pd.Series([0.0, 0.0, 0.0, 0.995])
    assert trust_score(flags, anomaly).tolist() == [40, 75, 90, 75]


def test_trust_score_floors_at_zero() -> None:
    flags = pd.DataFrame({"dq_eui_implausible": [True], "dq_ghg_implausible": [True]})
    assert trust_score(flags, pd.Series([1.0])).iloc[0] == 0


def test_ghg_inconsistency_is_critical(panel: pd.DataFrame) -> None:
    df = panel.copy()
    df.loc[df["data_year"] == 2022, "ghg_total_mtco2e"] *= 2
    flags = apply_rules(add_context(df))
    assert flags["dq_ghg_inconsistent_with_fuels"].sum() == 1


def test_assess_end_to_end(panel: pd.DataFrame) -> None:
    q = assess(panel)
    assert len(q) == len(panel)
    assert q["trust_score"].between(0, 100).all()
    assert q["usable_for_modeling"].all()
    assert set(q["trust_tier"].astype(str)) <= {"high", "medium", "low"}


@pytest.mark.parametrize("inject", [_electricity(2), _electricity(1000), _drop_gas])
def test_injected_fuel_errors_stay_internally_consistent(panel: pd.DataFrame, inject) -> None:
    """Injection must not create trivially detectable GHG or energy mismatches."""
    df = panel.copy()
    idx = df.index[df["data_year"] == 2021]
    inject(df, idx)
    ctx = add_context(df).loc[idx]
    assert ctx["ghg_fuel_ratio"].iloc[0] == pytest.approx(1, abs=0.01)
    assert ctx["energy_fuel_ratio"].iloc[0] == pytest.approx(1, abs=0.01)


def test_floor_area_injection_scales_intensities(panel: pd.DataFrame) -> None:
    df = panel.copy()
    idx = df.index[:1]
    before = df.loc[idx, ["site_eui", "self_gfa_buildings"]].iloc[0]
    _floor_area(10)(df, idx)
    after = df.loc[idx, ["site_eui", "self_gfa_buildings"]].iloc[0]
    assert after["site_eui"] == pytest.approx(before["site_eui"] / 10)
    assert after["self_gfa_buildings"] == pytest.approx(before["self_gfa_buildings"] * 10)

from __future__ import annotations

import pandas as pd
import pytest

from beps_prioritizer.scoring.priority import (
    WEIGHTS,
    components,
    pathway,
    prioritize,
    score,
    sensitivity,
    verify_reason,
)


def _buildings(**overrides) -> pd.DataFrame:
    """Five synthetic buildings covering each pathway; columns as produced upstream."""
    base = pd.DataFrame({
        "building_id": [1, 2, 3, 4, 5],
        "building_name": ["waste", "electrify", "steam", "on track", "bad data"],
        "use1_type": ["Office"] * 5,
        "beps_over_2031": [True, True, True, False, True],
        "beps_over_2036": [True, True, True, False, True],
        "cbps_over_euit": [False] * 5,
        "beps_excess_tco2e_yr": [100.0, 50.0, 80.0, 0.0, 500.0],
        "beps_excess_kg_sf": [0.4, 0.3, 0.5, 0.0, 1.0],
        "beps_target_2031": [0.81] * 5,
        "ghgi_2031_factors": [1.2, 1.1, 1.3, 0.5, 1.8],
        "beps_first_deadline": [2031, 2033, 2032, 2031, 2031],
        "cbps_deadline": [pd.NA] * 5,
        "cbps_euit": [63.0] * 5,
        "site_eui_wn": [120.0, 60.0, 70.0, 40.0, 90.0],
        "area_sf": [100_000.0] * 5,
        "eui_ratio": [2.0, 1.0, 1.1, 0.8, 1.5],
        "peer_confidence": [1.0] * 5,
        "trust_score": [100, 100, 100, 100, 100],
        "trust_tier": ["high"] * 5,
        "area_disagrees_with_city": [False, False, False, False, True],
        "beps_acp_est": [95_000.0, 47_500.0, 76_000.0, 0.0, 475_000.0],
        "electricity_kbtu": [1e6, 1e6, 1e6, 3e6, 1e6],
        "natural_gas_kbtu": [2e6, 2e6, 0.0, 0.0, 2e6],
        "steam_kbtu": [0.0, 0.0, 3e6, 0.0, 0.0],
    })
    return base.assign(**overrides)


def test_score_formula() -> None:
    c = pd.DataFrame({"impact": [1.0, 0.5], "urgency": [1.0, 0.0],
                      "opportunity": [1.0, 0.0], "confidence": [1.0, 0.8]})
    assert score(c).tolist() == [100.0, pytest.approx(100 * 0.8 * 0.5 * WEIGHTS["impact"])]


def test_components_are_bounded_and_zero_when_on_track() -> None:
    c = components(_buildings())
    assert c[["impact", "urgency", "opportunity", "confidence"]].stack().between(0, 1).all()
    on_track = c.iloc[3]
    assert on_track["impact"] == 0 and on_track["urgency"] == 0


def test_pathways() -> None:
    d = _buildings()
    d = d.join(pd.DataFrame({
        "steam_emission_share": [0.0, 0.0, 0.9, 0.0, 0.0],
    }))
    got = pathway(d).tolist()
    # waste: 1.2 / 2.0 = 0.6 <= 0.81 target, so matching peers would be enough
    assert got == ["efficiency_may_suffice", "electrification", "district_steam",
                   "on_track", "verify_data"]


def test_verify_reason_lists_all_causes() -> None:
    d = _buildings(trust_tier=["medium", "high", "high", "high", "high"],
                   eui_ratio=[5.0, 1.0, 1.0, 1.0, 1.0])
    reasons = verify_reason(d)
    assert "contradict" in reasons.iloc[0] and "four times" in reasons.iloc[0]
    assert reasons.iloc[1] == ""
    assert "floor area" in reasons.iloc[4]


def test_action_rank_excludes_verify_and_on_track() -> None:
    # The on-track building uses 2x its peers' energy: it scores above zero but has
    # no target to act on, so it must still stay off the action list.
    out = prioritize(_buildings(eui_ratio=[2.0, 1.0, 1.1, 2.0, 1.5]))
    assert out.loc[out["building_name"] == "on track", "priority_score"].iloc[0] > 0
    ranked = out.dropna(subset=["action_rank"])
    assert "bad data" not in set(ranked["building_name"])
    assert "on track" not in set(ranked["building_name"])
    assert ranked["action_rank"].tolist() == list(range(1, len(ranked) + 1))
    assert ranked["priority_score"].is_monotonic_decreasing
    on_track = out[out["building_name"] == "on track"].iloc[0]
    assert pd.isna(on_track["required_reduction_pct"])  # blank, not "0% cut"


def test_sensitivity_outputs_are_bounded() -> None:
    out = prioritize(_buildings())
    sens, robustness = sensitivity(out, n=2, draws=20)
    assert sens["top2_overlap_pct"].between(0, 100).all()
    assert robustness.between(0, 1).all()

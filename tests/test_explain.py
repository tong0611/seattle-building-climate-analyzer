from __future__ import annotations

import pytest

from beps_prioritizer.explain.facts import fmt_money
from beps_prioritizer.explain.faithfulness import check, extract_numbers
from beps_prioritizer.explain.templates import TemplateExplainer


def _facts(**overrides):
    f = {
        "building_id": 1, "name": "1000 Example Building", "address": "123 4TH AVE",
        "use_type": "K-12 School", "data_year": 2025, "area_sf": 100_000.0,
        "site_eui": 60.0, "expected_eui": 40.0, "peer_median_eui": 38.0, "eui_ratio": 1.5,
        "peer_confidence": 1.0, "ghgi_2031": 1.6, "beps_target_2031": 0.95,
        "beps_target_2036": 0.56, "beps_coverage_pct": 100.0,
        "beps_over_2031": True, "beps_over_2036": True,
        "beps_deadline": 2032, "required_cut_pct": 41.0, "excess_tco2e_yr": 65.0,
        "acp_est": 61_750.0, "ghgi_if_peer_efficiency": 1.07, "gas_share_pct": 95.0,
        "steam_share_pct": 0.0, "cbps_tier1": True, "cbps_over": False, "cbps_euit": 49.0,
        "cbps_deadline": 2027, "trust_score": 100, "pathway": "efficiency_and_electrification",
        "pathway_label": "Efficiency + electrification", "verify_reason": "",
        "priority_rank": 12,
    }
    f.update(overrides)
    return f


@pytest.fixture
def text() -> str:
    return TemplateExplainer().explain(_facts())


def test_template_passes_its_own_check(text: str) -> None:
    result = check(text, _facts())
    assert result.passed, result.issues
    assert result.missing == []


@pytest.mark.parametrize("pathway", [
    "verify_data", "district_steam", "efficiency_may_suffice",
    "efficiency_and_electrification", "electrification",
])
def test_every_over_target_pathway_is_faithful(pathway: str) -> None:
    f = _facts(pathway=pathway, verify_reason="data-quality flags", steam_share_pct=60.0)
    assert check(TemplateExplainer().explain(f), f).passed


def test_tiny_cut_is_not_shown_as_zero() -> None:
    f = _facts(required_cut_pct=0.0)
    text = TemplateExplainer().explain(f)
    assert "0% cut" not in text and "under 1%" in text
    result = check(text, f)
    assert result.passed and result.missing == []


def test_on_track_building_is_faithful() -> None:
    f = _facts(beps_over_2031=False, beps_over_2036=False, required_cut_pct=None,
               excess_tco2e_yr=None, acp_est=None, ghgi_2031=0.5, pathway="on_track",
               eui_ratio=0.8, site_eui=32.0)
    text = TemplateExplainer().explain(f)
    assert "meets" in text and "$" not in text
    assert check(text, f).passed


def test_partial_target_coverage_is_explained() -> None:
    f = _facts(beps_coverage_pct=40.0)
    text = TemplateExplainer().explain(f)
    assert "Only 40% of its floor area" in text
    assert check(text, f).passed


def test_digits_in_name_address_and_use_type_are_ignored(text: str) -> None:
    kinds = {t.text for t in extract_numbers(text.replace("1000 Example Building", ""))}
    assert "1000" not in kinds
    assert check(text, _facts()).passed  # "K-12" would otherwise be an unsupported 12


def test_extract_numbers_kinds() -> None:
    tokens = extract_numbers("Costs $1.6M or $34,000; 45% cut by 2032; 1.5x peers; 61.2 kBtu")
    got = [(t.kind, t.value) for t in tokens]
    assert got == [("money", 1.6e6), ("money", 34_000), ("pct", 45), ("year", 2032),
                   ("ratio", 1.5), ("number", 61.2)]


def test_co2_and_interval_ranges_are_not_numbers() -> None:
    tokens = extract_numbers("target for 2031-2035 in kgCO2e/sf")
    assert [(t.kind, t.value) for t in tokens] == [("year", 2031), ("year", 2035)]


@pytest.mark.parametrize("broken, issue", [
    ("needs an estimated 41% cut by 2033.", "deadline"),
    ("needs an estimated 30% cut by 2032.", "unsupported pct"),
    ("It uses less energy than similar buildings.", "less energy"),
    ("It meets its Seattle BEPS 2031-2035 emissions target.", "meets its BEPS"),
    ("The payment is $62,000.", "estimate"),
    ("The owner will be fined.", "prohibited"),
    ("Its boiler is 27 years old.", "unsupported number"),
])
def test_checker_catches_broken_claims(broken: str, issue: str) -> None:
    result = check(broken, _facts())
    assert not result.passed
    assert any(issue in i for i in result.issues), result.issues


def test_money_tolerates_display_rounding() -> None:
    assert check(f"An estimated {fmt_money(61_750)} payment.", _facts()).passed
    assert check("An estimated $62K payment.", _facts()).passed
    assert not check("An estimated $90,000 payment.", _facts()).passed


def test_missing_key_facts_reported_but_not_failed() -> None:
    result = check("It is a school.", _facts())
    assert result.passed
    assert set(result.missing) == {"deadline", "required cut"}

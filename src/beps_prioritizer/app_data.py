"""Small, app-ready tables: one row per building, plus yearly history for charts.
"""

from __future__ import annotations

import pandas as pd

from beps_prioritizer.explain.facts import tidy_name
from beps_prioritizer.regulations.beps import BEPS_FACTORS_2031
from beps_prioritizer.scoring.priority import PATHWAYS
from beps_prioritizer.scoring.targets import compliance_ghgi

# --------------------------------------------------------------------------- #
# Plain-language labels for the app. The audience includes non-technical viewers,
# so every label avoids jargon and comes with a one-line explanation.
# --------------------------------------------------------------------------- #

# Status: at most three colored groups plus neutral gray on the map, so categories
# stay distinguishable for colorblind viewers when any two points sit side by side.
STATUS = {
    "verify_data": "Data needs review",
    "efficiency_may_suffice": "Action needed",
    "efficiency_and_electrification": "Action needed",
    "electrification": "Action needed",
    "district_steam": "Action needed",
    "cbps_energy_only": "Action needed",
    "on_track_2031_not_2036": "Fine now, off track for 2036",
    "on_track": "On track",
}
STATUS_NOTES = {
    "Action needed": "Emits more than its 2031 city limit, or uses more energy than its "
                     "state limit.",
    "Data needs review": "Its reported numbers don't add up. Confirm them before acting.",
    "Fine now, off track for 2036": "Meets the 2031 limit, but not the stricter 2036 limit.",
    "On track": "Meets its current limits.",
}
STATUS_ORDER = list(STATUS_NOTES)

NEXT_STEP = {
    "efficiency_may_suffice": (
        "Energy upgrades may be enough",
        "Using as much energy as similar buildings would bring its emissions under the "
        "limit. Start with an energy audit and upgrades such as better controls, lighting, "
        "and insulation."),
    "efficiency_and_electrification": (
        "Energy upgrades + replace gas equipment",
        "Cutting energy waste helps, but will not reach the limit alone. Plan to replace "
        "gas heating and hot water with electric equipment such as heat pumps."),
    "electrification": (
        "Replace gas equipment",
        "The gap comes mainly from burning gas. Replacing gas heating and hot water with "
        "electric equipment such as heat pumps is the main option."),
    "district_steam": (
        "Mostly heated by district steam",
        "Most of its emissions come from steam bought from a central plant. Progress "
        "depends on the supplier's emissions as well as on using less steam."),
    "cbps_energy_only": (
        "Cut energy use (state limit only)",
        "It meets the city emissions limit but uses more energy than Washington State "
        "allows. Energy upgrades are the main option."),
    "verify_data": (
        "Check reported data",
        "Its reported numbers don't add up. Confirm floor area, meters, and building type "
        "with the owner before planning any work."),
    "on_track_2031_not_2036": (
        "Plan ahead for 2036",
        "It meets the 2031 limit, but the 2036 limit is stricter. There is time to plan "
        "upgrades."),
    "on_track": (
        "No action needed now",
        "It meets its current limits."),
}

BUILDING_TYPE = {
    "NonResidential": "Commercial",
    "Multifamily LR (1-4)": "Apartments (1-4 floors)",
    "Multifamily MR (5-9)": "Apartments (5-9 floors)",
    "Multifamily HR (10+)": "Apartments (10+ floors)",
    "School": "School",
    "Hospital": "Hospital",
    "University": "University",
}

# Size groups follow the BEPS deadline tiers (floor area excluding parking).
SIZE_GROUPS = [
    (220_000, "Very large (over 220,000 sq ft)"),
    (90_000, "Large (90,000-220,000 sq ft)"),
    (50_000, "Medium (50,000-90,000 sq ft)"),
    (0, "Small (under 50,000 sq ft)"),
]

NEIGHBORHOOD = {"MAGNOLIA/QUEEN ANNE": "Magnolia / Queen Anne",
                "DELRIDGE NEIGHBORHOODS": "Delridge"}

RELIABILITY = {"high": "High", "medium": "Medium", "low": "Low"}


BUILDING_COLUMNS = [
    "building_id", "building_name", "address", "neighborhood", "building_type", "use1_type",
    "data_year", "area_sf", "year_built", "energy_star_score", "site_eui_wn",
    "expected_site_eui_wn", "eui_ratio", "peer_confidence", "ghgi_2031_factors",
    "beps_target_2031", "beps_target_2036", "beps_over_2031", "beps_over_2036",
    "beps_first_deadline", "required_reduction_pct", "beps_excess_tco2e_yr", "beps_acp_est",
    "beps_penalty_est", "cbps_tier1", "cbps_euit", "cbps_over_euit", "cbps_deadline",
    "gas_emission_share", "steam_emission_share", "electric_emission_share", "trust_score",
    "trust_tier", "priority_score", "impact", "urgency", "opportunity", "confidence",
    "pathway", "verify_reason", "rank", "action_rank", "top50_robustness",
    "multi_building", "area_disagrees_with_city",
]


def build_buildings(priority: pd.DataFrame, bench: pd.DataFrame,
                    explanations: pd.DataFrame) -> pd.DataFrame:
    """Priority table + coordinates + checked explanation + display labels."""
    coords = bench.sort_values("data_year").groupby("building_id")[
        ["latitude", "longitude"]].last()
    ex = explanations[["building_id", "text", "passed"]].rename(
        columns={"text": "explanation", "passed": "explanation_checked"})
    out = (priority[BUILDING_COLUMNS]
           .merge(coords, left_on="building_id", right_index=True, how="left")
           .merge(ex, on="building_id", how="left"))
    out["pathway_label"] = out["pathway"].map(PATHWAYS)
    out["building_name"] = out["building_name"].map(tidy_name)  # same as explanations
    for col in ["building_type", "neighborhood", "use1_type"]:
        out[col] = out[col].astype("string")
    return out.join(display_columns(out))


def size_group(area_sf: pd.Series) -> pd.Series:
    """Plain size label following the BEPS deadline tiers."""
    out = pd.Series(SIZE_GROUPS[-1][1], index=area_sf.index)
    for min_sf, label in reversed(SIZE_GROUPS[:-1]):
        out = out.mask(area_sf > min_sf, label)
    return out


def display_columns(b: pd.DataFrame) -> pd.DataFrame:
    """Plain-language columns for the app: status, next step, labels, deadline."""
    status = b["pathway"].map(STATUS)
    over_city = b["beps_over_2031"].fillna(False).astype(bool)
    over_state = b["cbps_over_euit"].fillna(False).astype(bool)
    # The deadline that matters: the city one if over it, else the state one if over
    # that, else (for information) the city deadline.
    deadline = (b["beps_first_deadline"].where(over_city)
                .fillna(b["cbps_deadline"].where(over_state))
                .fillna(b["beps_first_deadline"]))
    hood = b["neighborhood"].map(lambda h: NEIGHBORHOOD.get(h, h.title()) if pd.notna(h)
                                 else "Unknown")
    return pd.DataFrame({
        "status": status,
        "status_note": status.map(STATUS_NOTES),
        "next_step": b["pathway"].map(lambda p: NEXT_STEP[p][0]),
        "next_step_detail": b["pathway"].map(lambda p: NEXT_STEP[p][1]),
        "type_label": b["building_type"].map(BUILDING_TYPE).fillna(b["building_type"]),
        "size_group": size_group(b["area_sf"].fillna(0)),
        "neighborhood_label": hood,
        "deadline": deadline.astype("Int64"),
        "reliability": b["trust_tier"].astype("string").map(RELIABILITY),
        "emissions_vs_limit": b["ghgi_2031_factors"] / b["beps_target_2031"],
    }, index=b.index)


def build_history(bench: pd.DataFrame, peer: pd.DataFrame, quality: pd.DataFrame,
                  building_ids: pd.Series) -> pd.DataFrame:
    """Every reported year, with GHGI on the 2031-35 factors so it compares to targets."""
    key = ["building_id", "data_year"]
    bench = bench[bench["building_id"].isin(building_ids)]
    h = bench[[*key, "site_eui_wn"]].copy()
    h["ghgi_2031_factors"] = compliance_ghgi(bench, BEPS_FACTORS_2031)
    h = h.merge(peer[[*key, "expected_site_eui_wn"]], on=key, how="left")
    h = h.merge(quality[[*key, "trust_tier", "usable_for_modeling"]], on=key, how="left")
    h["trust_tier"] = h["trust_tier"].astype("string")
    return h.sort_values(key, ignore_index=True)

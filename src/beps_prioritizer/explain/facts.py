"""The fact record per building: the single source of every number an explanation
may state, already rounded the way it should be displayed.
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

from beps_prioritizer.scoring.priority import PATHWAYS

Facts = dict[str, Any]


def fmt_money(x: float) -> str:
    """Rounded for reading ($1.6M, $34,000, $2,500); reports use exact figures."""
    if x >= 1e6:
        return f"${x / 1e6:,.1f}M"
    if x >= 10_000:
        return f"${round(x, -3):,.0f}"
    return f"${round(x, -2):,.0f}"


def fmt_pct(x: float) -> str:
    return f"{x:.0f}%"


def fmt_ratio(x: float) -> str:
    return f"{x:.1f}x"


def tidy_name(name: str) -> str:
    """Title-case names the city recorded in ALL CAPS; leave mixed-case names alone."""
    return name.title() if name.isupper() else name


def _is_missing(x: Any) -> bool:
    return x is None or x is pd.NA or (isinstance(x, float) and math.isnan(x))


def _num(x: Any, digits: int) -> float | None:
    """Round for display; missing values become None (never NaN in facts)."""
    return None if _is_missing(x) else round(float(x), digits)


def _int(x: Any) -> int | None:
    return None if _is_missing(x) else int(x)


def building_facts(r: pd.Series) -> Facts:
    """Facts for one row of the priority table, rounded for display."""
    over = bool(r["beps_over_2031"])
    return {
        "building_id": int(r["building_id"]),
        "name": tidy_name(str(r["building_name"])),
        "address": str(r["address"]),
        "use_type": str(r["use1_type"]),
        "data_year": int(r["data_year"]),
        "area_sf": _num(r["area_sf"], -3),
        "site_eui": _num(r["site_eui_wn"], 1),
        "expected_eui": _num(r["expected_site_eui_wn"], 1),
        "peer_median_eui": _num(r["peer_median_site_eui_wn"], 1),
        "eui_ratio": _num(r["eui_ratio"], 1),
        "peer_confidence": _num(r["peer_confidence"], 2),
        "ghgi_2031": _num(r["ghgi_2031_factors"], 2),
        "beps_target_2031": _num(r["beps_target_2031"], 2),
        "beps_target_2036": _num(r["beps_target_2036"], 2),
        "beps_coverage_pct": _num(100 * r["beps_target_coverage"], 0),
        "beps_over_2031": over,
        "beps_over_2036": bool(r["beps_over_2036"]),
        "beps_deadline": _int(r["beps_first_deadline"]),
        "required_cut_pct": _num(r["required_reduction_pct"], 0) if over else None,
        "excess_tco2e_yr": _num(r["beps_excess_tco2e_yr"], 0) if over else None,
        "acp_est": float(r["beps_acp_est"]) if over else None,
        "ghgi_if_peer_efficiency": _num(r["ghgi_if_peer_efficiency"], 2),
        "gas_share_pct": _num(100 * r["gas_emission_share"], 0),
        "steam_share_pct": _num(100 * r["steam_emission_share"], 0),
        "cbps_tier1": bool(r["cbps_tier1"]),
        "cbps_over": bool(r["cbps_over_euit"]),
        "cbps_euit": _num(r["cbps_euit"], 0),
        "cbps_deadline": _int(r["cbps_deadline"]),
        "trust_score": _int(r["trust_score"]),
        "pathway": str(r["pathway"]),
        "pathway_label": PATHWAYS[str(r["pathway"])],
        "verify_reason": str(r["verify_reason"] or ""),
        "priority_rank": _int(r["action_rank"]),
    }

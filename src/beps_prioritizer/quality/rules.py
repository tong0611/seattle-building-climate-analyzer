"""Rule-based checks, and the context columns (consistency ratios, neighbor years,
peer z-scores, gas history) they are computed from.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd

from beps_prioritizer.regulations.beps import BEPS_FACTORS_BASELINE

CRITICAL = 60  # one critical flag alone drops a row to the low tier


MIN_PEER_GROUP = 30


AREA_FIELDS = ["gfa_buildings", "self_gfa_buildings", "self_gfa_total", "gfa_total", "use1_gfa"]


@dataclass(frozen=True)
class Rule:
    """One data-quality check: a vectorized test plus how much a hit costs in trust."""

    name: str
    severity: str  # "critical" makes the row unusable for modeling; "warning" lowers trust
    penalty: int  # points subtracted from the trust score
    description: str
    fn: Callable[[pd.DataFrame], pd.Series]


def _rel_diff(a: pd.Series, b: pd.Series) -> pd.Series:
    """|a - b| / |b|."""
    return (a - b).abs() / b.abs()


def fuel_ghg_mtco2e(df: pd.DataFrame) -> pd.Series:
    """GHG recomputed from fuel use with the BEPS factors, in metric tons."""
    kg = sum(df[col].fillna(0) * f for col, f in BEPS_FACTORS_BASELINE.items())
    return kg / 1000


def _neighbor_ratios(df: pd.DataFrame, col: str) -> tuple[pd.Series, pd.Series]:
    """Value / previous reported year, and value / next reported year, per building."""
    order = df.sort_values(["building_id", "data_year"])
    g = order.groupby("building_id")[col]
    prev = order[col] / g.shift(1)
    nxt = order[col] / g.shift(-1)
    return prev.reindex(df.index), nxt.reindex(df.index)


def _robust_peer_z(df: pd.DataFrame, col: str) -> pd.Series:
    """Robust z-score of log(col) within largest-use type (fallback: building type)."""
    logv = np.log(df[col].where(df[col] > 0))
    use_n = df.groupby("use1_type", observed=True)["use1_type"].transform("size")
    group = df["use1_type"].astype("string").where(use_n >= MIN_PEER_GROUP)
    group = group.fillna("BT:" + df["building_type"].astype("string"))

    med = logv.groupby(group).transform("median")
    mad = (logv - med).abs().groupby(group).transform("median")
    return (logv - med) / (1.4826 * mad.replace(0, np.nan))


def add_context(df: pd.DataFrame) -> pd.DataFrame:
    """Add the derived columns the rules need: consistency ratios, neighbor-year
    ratios, peer z-score, and gas-history classes."""
    out = df.copy()
    out["ghg_fuel_ratio"] = out["ghg_total_mtco2e"] / fuel_ghg_mtco2e(out)
    fuel_sum = out[list(BEPS_FACTORS_BASELINE)].fillna(0).sum(axis=1)
    out["energy_fuel_ratio"] = out["site_energy_kbtu"] / fuel_sum.replace(0, np.nan)
    out["eui_area_mismatch"] = _eui_area_mismatch(out)
    out["eui_prev_ratio"], out["eui_next_ratio"] = _neighbor_ratios(out, "site_eui_wn")
    out["elec_prev_ratio"], out["elec_next_ratio"] = _neighbor_ratios(out, "electricity_kwh")
    out["peer_z"] = _robust_peer_z(out, "site_eui_wn")

    out = out.join(_gas_history(out))
    return out


def _eui_area_mismatch(df: pd.DataFrame) -> pd.Series:
    """Smallest relative gap between energy/EUI and any reported floor-area field.

    EUI comes from Portfolio Manager, usually from the owner's self-reported area,
    so a mismatch with the city's area alone is a GFA question, not an EUI error.
    """
    implied = df["site_energy_kbtu"] / df["site_eui"]
    gaps = [_rel_diff(implied, df[c].where(df[c] > 0)) for c in AREA_FIELDS]
    return pd.concat(gaps, axis=1).min(axis=1)


def _gas_history(df: pd.DataFrame) -> pd.DataFrame:
    """Classify missing gas using the building's other years.

    - gas_gap: missing, with gas both earlier and later (likely a missing meter)
    - gas_stopped: missing, gas earlier but never again (possibly electrified)
    - gas_assumed_zero: missing and never any gas (treated as no gas service)
    """
    order = df.sort_values(["building_id", "data_year"])
    b = order["building_id"]
    has = (order["natural_gas_therms"].fillna(0) > 0).astype(int)
    earlier = has.groupby(b).cummax().groupby(b).shift(1).fillna(0) > 0
    later = has.iloc[::-1].groupby(b.iloc[::-1]).cummax().iloc[::-1]
    later = later.groupby(b).shift(-1).fillna(0) > 0
    missing = order["natural_gas_therms"].isna()
    out = pd.DataFrame({
        "gas_gap": missing & earlier & later,
        "gas_stopped": missing & earlier & ~later,
        "gas_assumed_zero": missing & ~earlier & ~later,
    }, index=order.index)
    return out.reindex(df.index)


def _spike(prev_ratio: pd.Series, next_ratio: pd.Series, factor: float,
           edge_factor: float) -> pd.Series:
    """One-year blip vs both neighbor years, or a big jump at the first/last reported year."""
    lp, ln = np.log(prev_ratio), np.log(next_ratio)
    t, te = np.log(factor), np.log(edge_factor)
    blip = ((lp > t) & (ln > t)) | ((lp < -t) & (ln < -t))
    edge = (lp.notna() & ln.isna() & (lp.abs() > te)) | (ln.notna() & lp.isna() & (ln.abs() > te))
    return blip | edge


# Warning penalties reflect how much the issue could distort a building's emissions
# or energy ranking: 25 alone moves a row out of the high tier; 10-20 needs company.
RULES: list[Rule] = [
    # -- critical: values cannot be used for peer modeling or target gaps --
    Rule("missing_core_metrics", "critical", CRITICAL,
         "Site EUI (weather-normalized) or GHG intensity missing",
         lambda d: d["site_eui_wn"].isna() | d["ghg_intensity"].isna()),
    Rule("eui_implausible", "critical", CRITICAL, "Site EUI > 1,000 or < 5 kBtu/sf",
         lambda d: (d["site_eui"] > 1000) | (d["site_eui"] < 5)),
    Rule("ghg_implausible", "critical", CRITICAL, "GHG intensity > 50 kgCO2e/sf",
         lambda d: d["ghg_intensity"] > 50),
    Rule("source_eui_nonpositive", "critical", CRITICAL, "Source EUI <= 0",
         lambda d: d["source_eui"] <= 0),
    Rule("electricity_tiny", "critical", CRITICAL, "Electricity < 1,000 kWh/year",
         lambda d: d["electricity_kwh"] < 1000),
    Rule("ghg_inconsistent_with_fuels", "critical", CRITICAL,
         "Reported GHG differs > 15% from fuel use x BEPS factors",
         lambda d: (d["ghg_fuel_ratio"] - 1).abs() > 0.15),
    # -- warnings: plausible but worth a look --
    Rule("gas_gap", "warning", 25,
         "No gas this year, but gas in both earlier and later years (missing meter?)",
         lambda d: d["gas_gap"]),
    Rule("yoy_spike", "warning", 25,
         "EUI differs > 2x from both neighbor years (> 2.5x at first/last year)",
         lambda d: _spike(d["eui_prev_ratio"], d["eui_next_ratio"], 2.0, 2.5)),
    Rule("electricity_yoy_spike", "warning", 25,
         "Electricity differs > 1.6x from both neighbor years (> 1.9x at first/last year)",
         lambda d: _spike(d["elec_prev_ratio"], d["elec_next_ratio"], 1.6, 1.9)),
    Rule("eui_inconsistent_with_gfa", "warning", 20,
         "Site energy / EUI matches none of the reported floor areas within 20%",
         lambda d: d["eui_area_mismatch"] > 0.20),
    Rule("peer_outlier", "warning", 20,
         "EUI > 3.5 robust SDs from same-use peers (log scale)",
         lambda d: d["peer_z"].abs() > 3.5),
    # Often an unlisted fuel or onsite solar: the fuel-mix explanation is unreliable,
    # but EUI and GHG themselves are still usable.
    Rule("energy_inconsistent_with_fuels", "warning", 15,
         "Site energy differs > 15% from the sum of listed fuels",
         lambda d: (d["energy_fuel_ratio"] - 1).abs() > 0.15),
    Rule("gas_tiny", "warning", 15, "Natural gas recorded but <= 10 therms/year",
         lambda d: d["natural_gas_therms"] <= 10),
    Rule("gfa_self_vs_city", "warning", 10, "Self-reported vs city building GFA differ > 20%",
         lambda d: (d["self_gfa_buildings"] > 0)
         & (_rel_diff(d["self_gfa_buildings"], d["gfa_buildings"]) > 0.20)),
    Rule("gfa_fields_invalid", "warning", 10,
         "Zero buildings/floors, zero self GFA, or negative parking",
         lambda d: (d["num_buildings"] == 0) | (d["num_floors"] == 0)
         | (d["self_gfa_total"] == 0) | (d["gfa_parking"] < 0)),
]


def apply_rules(ctx: pd.DataFrame, rules: list[Rule] = RULES) -> pd.DataFrame:
    """One boolean ``dq_<rule>`` column per rule; missing inputs count as no flag."""
    flags = {f"dq_{r.name}": r.fn(ctx).fillna(False).astype(bool) for r in rules}
    return pd.DataFrame(flags, index=ctx.index)

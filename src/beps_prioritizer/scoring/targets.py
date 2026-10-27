"""Target gaps and estimated penalty exposure under Seattle BEPS and Washington CBPS.

Every dollar figure here is an ESTIMATE of exposure *if the building's most recent
performance continued unchanged to its deadline*. It is not a prediction that a
penalty will be issued: owners can improve, use alternative pathways, qualify for
extensions, or appeal. Regulatory values live in ``beps_prioritizer.regulations``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from beps_prioritizer.peers.features import uses_long
from beps_prioritizer.regulations.beps import (
    BEPS_ACP_MINIMUM,
    BEPS_COST_PER_MTCO2E,
    BEPS_DEADLINES,
    BEPS_FACTORS_2031,
    BEPS_FACTORS_BASELINE,
    BEPS_GHGIT,
    BEPS_PENALTY_PER_SF,
    PM_TO_BEPS,
)
from beps_prioritizer.regulations.cbps import (
    CBPS_DEADLINES,
    CBPS_NOT_TIER1_USES,
    CBPS_PENALTY_BASE,
    CBPS_PENALTY_PER_SF,
    CBPS_TIER1_MIN_SF,
    PM_TO_CBPS_EUIT,
)
from beps_prioritizer.reporting import md_table, money_short

MIN_BASIS_YEAR = 2023  # use a building's latest trustworthy year, if this recent


MIN_TARGET_COVERAGE = 0.5  # share of floor area that must have a BEPS target


def _area_uses(df: pd.DataFrame) -> pd.DataFrame:
    """Use rows that count toward floor area (parking and zero-area uses removed)."""
    long = uses_long(df)
    long["gfa"] = long["gfa"].fillna(0).clip(lower=0)
    return long[(long["use_type"] != "Parking") & (long["gfa"] > 0)]


def blended_target(
    df: pd.DataFrame, value_of: dict[str, float | None]
) -> tuple[pd.Series, pd.Series]:
    """Floor-area-weighted target over uses that have one, and the share they cover."""
    long = _area_uses(df)
    long["target"] = long["use_type"].map(value_of).astype("float64")
    has = long["target"].notna()
    weighted = (long["gfa"] * long["target"]).where(has).groupby(long["row"]).sum()
    covered = long["gfa"].where(has, 0).groupby(long["row"]).sum()
    total = long["gfa"].groupby(long["row"]).sum()
    target = (weighted / covered.replace(0, np.nan)).reindex(df.index)
    share = (covered / total).reindex(df.index).fillna(0)
    return target, share


def beps_interval_targets(df: pd.DataFrame) -> pd.DataFrame:
    """Blended BEPS targets for each 5-year interval, plus the floor-area share covered."""
    out = {}
    for i, label in enumerate(["2031", "2036", "2041", "2046"]):
        values = {pm: (BEPS_GHGIT[a][i] if a else None) for pm, a in PM_TO_BEPS.items()}
        out[f"beps_target_{label}"], share = blended_target(df, values)
    out["beps_target_coverage"] = share
    out = pd.DataFrame(out, index=df.index)
    # Mostly exempt uses (e.g. manufacturing with a small lab): a target from a sliver
    # of floor area judged against whole-building emissions would be misleading.
    mostly_exempt = share < MIN_TARGET_COVERAGE
    target_cols = [c for c in out.columns if c.startswith("beps_target_20")]
    out.loc[mostly_exempt, target_cols] = np.nan
    return out


def reporting_area(df: pd.DataFrame) -> pd.Series:
    """Floor area the energy data was reported for: site energy / site EUI.

    This is the Portfolio Manager area (excluding parking). It usually matches the
    city's verified area, but some properties report campus-wide energy against a
    single building's city area (e.g. 2.3M sf of energy on 171k sf), which would
    inflate intensity ~13x. Falls back to the city area when EUI is missing.
    """
    implied = df["site_energy_kbtu"] / df["site_eui"]
    return implied.where(implied > 0).fillna(df["gfa_buildings"])


def compliance_ghgi(df: pd.DataFrame, factors: dict[str, float]) -> pd.Series:
    """Weather-normalized fuel use x BEPS factors / reporting floor area.

    The dataset gives weather normalization only for total site energy, so each
    fuel is scaled by the same total-energy weather adjustment (an approximation).
    """
    wn_scale = (df["site_energy_wn_kbtu"] / df["site_energy_kbtu"]).fillna(1.0)
    kg = sum(df[col].fillna(0) * f for col, f in factors.items()) * wn_scale
    return kg / reporting_area(df)


def deadline_for(gfa: pd.Series, schedule: list[tuple[int, int]]) -> pd.Series:
    """First compliance year by size tier; schedule is [(min_sf_exclusive, year), ...]."""
    out = pd.Series(pd.NA, index=gfa.index, dtype="Int64")
    for min_sf, year in reversed(schedule):  # smallest tier first, larger tiers overwrite
        out = out.mask(gfa > min_sf, year)
    return out


def latest_trusted(df: pd.DataFrame, q: pd.DataFrame) -> pd.Index:
    """Each building's most recent usable year, if it is recent enough."""
    ok = q["usable_for_modeling"].to_numpy() & (df["data_year"] >= MIN_BASIS_YEAR)
    ok &= ~df["demolished"].fillna(False).to_numpy()
    sub = df[ok]
    return sub.groupby("building_id")["data_year"].idxmax().to_numpy()


def assess_targets(df: pd.DataFrame, q: pd.DataFrame) -> pd.DataFrame:
    """One row per building (latest trusted year): BEPS and CBPS targets, gaps,
    deadlines, and estimated penalty / alternative-compliance-payment exposure."""
    idx = latest_trusted(df, q)
    d = df.loc[idx]
    out = d[["building_id", "data_year", "building_name", "building_type"]].rename(
        columns={"data_year": "basis_year"})
    area = reporting_area(d)
    out["gfa_buildings"] = d["gfa_buildings"]
    out["area_sf"] = area.round(0)
    out["area_disagrees_with_city"] = (area / d["gfa_buildings"] - 1).abs() > 0.25
    # Campuses and multi-building properties may comply in aggregate (deadline 2033).
    out["multi_building"] = d["num_buildings"].fillna(1) > 1

    # --- BEPS ---
    out = out.join(beps_interval_targets(d))
    out["ghgi_reported"] = d["ghg_intensity"]
    out["ghgi_2031_factors"] = compliance_ghgi(d, BEPS_FACTORS_2031)
    out["ghgi_baseline_factors"] = compliance_ghgi(d, BEPS_FACTORS_BASELINE)
    excess = (out["ghgi_2031_factors"] - out["beps_target_2031"]).clip(lower=0)
    out["beps_excess_kg_sf"] = excess.where(out["beps_target_2031"].notna())
    out["beps_over_2031"] = out["beps_excess_kg_sf"] > 0
    out["beps_over_2036"] = out["ghgi_2031_factors"] > out["beps_target_2036"]
    out["beps_first_deadline"] = deadline_for(area, BEPS_DEADLINES)

    is_mf = d["building_type"].astype("string").str.startswith("Multifamily")
    rate = np.where(is_mf, BEPS_PENALTY_PER_SF["multifamily"],
                    BEPS_PENALTY_PER_SF["nonresidential"])
    penalty = pd.Series(rate, index=d.index) * area
    excess_t = out["beps_excess_kg_sf"] * area / 1000
    minimum = np.where(area > 50_000, BEPS_ACP_MINIMUM["large"],
                       BEPS_ACP_MINIMUM["small"])
    acp = np.maximum(minimum, excess_t * 5 * BEPS_COST_PER_MTCO2E).clip(upper=penalty)
    out["beps_excess_tco2e_yr"] = excess_t
    out["beps_penalty_est"] = penalty.where(out["beps_over_2031"], 0)
    out["beps_acp_est"] = acp.where(out["beps_over_2031"], 0)

    # --- CBPS ---
    long = _area_uses(d)
    nonres = long.loc[~long["use_type"].isin(CBPS_NOT_TIER1_USES)]
    nonres_sf = nonres.groupby("row")["gfa"].sum().reindex(d.index).fillna(0)
    out["cbps_tier1"] = nonres_sf > CBPS_TIER1_MIN_SF
    out["cbps_euit"], out["cbps_target_coverage"] = blended_target(d, PM_TO_CBPS_EUIT)
    out["site_eui_wn"] = d["site_eui_wn"]
    out["cbps_over_euit"] = out["cbps_tier1"] & (d["site_eui_wn"] > out["cbps_euit"])
    out["cbps_gap_pct"] = (100 * (d["site_eui_wn"] / out["cbps_euit"] - 1)).round(1)
    out["cbps_deadline"] = deadline_for(area, CBPS_DEADLINES).where(
        out["cbps_tier1"])
    cbps_penalty = CBPS_PENALTY_BASE + CBPS_PENALTY_PER_SF * area
    out["cbps_penalty_est"] = cbps_penalty.where(out["cbps_over_euit"], 0)
    return out.reset_index(drop=True)


def by_deadline(t: pd.DataFrame) -> pd.DataFrame:
    """Report table: buildings, over-target counts, and estimated exposure per deadline."""
    g = t[t["beps_target_2031"].notna()].groupby("beps_first_deadline")
    out = pd.DataFrame({
        "buildings": g.size(),
        "over_target": g["beps_over_2031"].sum(),
        "excess_tco2e_yr": g["beps_excess_tco2e_yr"].sum().round(0),
        "acp_est": g["beps_acp_est"].sum().map(money_short),
        "penalty_est": g["beps_penalty_est"].sum().map(money_short),
    })
    out.index.name = "first_ghgit_deadline"
    return out


def by_building_type(t: pd.DataFrame) -> pd.DataFrame:
    """Report table: share over BEPS 2031 / 2036 targets and over CBPS, per type."""
    g = t[t["beps_target_2031"].notna()].groupby("building_type", observed=True)
    out = pd.DataFrame({
        "buildings": g.size(),
        "over_2031_pct": (100 * g["beps_over_2031"].mean()).round(1),
        "over_2036_pct": (100 * g["beps_over_2036"].mean()).round(1),
        "cbps_tier1": g["cbps_tier1"].sum(),
        "cbps_over_pct_of_tier1": (100 * g["cbps_over_euit"].sum()
                                   / g["cbps_tier1"].sum().replace(0, np.nan)).round(1),
    })
    return out.sort_values("buildings", ascending=False)


def build_report(t: pd.DataFrame) -> str:
    has = t["beps_target_2031"].notna()
    over = t["beps_over_2031"]
    over_base = has & (t["ghgi_baseline_factors"] > t["beps_target_2031"])
    flipped = over_base & ~over
    t1 = t["cbps_tier1"]
    both = over & t["cbps_over_euit"]
    single = ~t["multi_building"]
    top = t[over].nlargest(15, "beps_excess_tco2e_yr")[[
        "building_name", "building_type", "basis_year", "area_sf", "ghgi_2031_factors",
        "beps_target_2031", "beps_excess_tco2e_yr", "beps_first_deadline", "beps_acp_est",
        "multi_building", "cbps_over_euit"]].round(2)
    top["beps_acp_est"] = top["beps_acp_est"].map(money_short)

    return "\n".join([
        "# Target gaps and estimated penalty exposure",
        "",
        "_Generated by `beps-targets`. **All dollar figures are estimates** of exposure if "
        "each building's latest trustworthy performance continued unchanged to its "
        "deadline. They are not predictions that penalties will be issued._",
        "",
        "## Method",
        "",
        f"- Basis: each building's most recent year with no critical data-quality flag, "
        f"{MIN_BASIS_YEAR} or later ({len(t):,} buildings).",
        "- **BEPS GHGI** recomputed as weather-normalized fuel use x the 2031-2035 "
        "provisional emission factors (electricity 0.0029, gas 0.053, steam 0.081 kg/kBtu) "
        "/ the floor area the energy was reported for (excluding parking). Targets are "
        "floor-area-weighted across uses using the Director's Rule Appendix A mapping.",
        "- **BEPS penalty** = $10/sf nonresidential or $7.50/sf multifamily, once per 5-year "
        "interval. **Alternative compliance payment (ACP)** = max($1,250 or $2,500, excess "
        "tCO2e/yr x 5 x $190), capped at the penalty. The ACP is the cheaper path, so it is "
        "the more realistic cost of non-performance.",
        "- **CBPS** applies EUI targets to Tier 1 buildings (> 50,000 sf of nonresidential, "
        "hotel, or dormitory space). Maximum penalty $5,000 + $1.50/sf. Owners can also "
        "comply through a 15% EUI reduction or investment-criteria pathway, which this "
        "estimate cannot see.",
        "",
        "Known limits: affordable housing ($2.50/sf and a possible 2031-35 extension) cannot "
        "be identified in the data, so multifamily estimates may be high; end-use "
        "deductions, campus/portfolio compliance, and CBPS target adjustments are not "
        "modeled; per-fuel weather normalization is approximated.",
        "",
        "## Seattle BEPS: 2031-2035 targets",
        "",
        f"- **{int(over.sum()):,} of {int(has.sum()):,}** buildings with a target "
        f"({100 * over.sum() / has.sum():.0f}%) are above their 2031-35 GHGIT today.",
        f"- They emit an estimated **{t['beps_excess_tco2e_yr'].sum():,.0f} tCO2e/yr** above "
        "target.",
        f"- Estimated ACP exposure: **{money_short(t['beps_acp_est'].sum())}**; estimated maximum "
        f"penalty exposure: **{money_short(t['beps_penalty_est'].sum())}** (one 5-year interval).",
        f"  - Single-building properties: ACP {money_short(t.loc[single, 'beps_acp_est'].sum())}, "
        f"penalty {money_short(t.loc[single, 'beps_penalty_est'].sum())}. Multi-building "
        f"properties and campuses ({int(t['multi_building'].sum())}) may comply in aggregate, "
        "so their figures are less certain.",
        f"- Median estimated ACP per over-target building: "
        f"**{money_short(t.loc[over, 'beps_acp_est'].median())}** (totals are dominated by a few "
        "large campuses).",
        f"- {int(t['area_disagrees_with_city'].sum())} buildings report energy for a floor "
        "area that differs from the city's by > 25%; the reporting area is used.",
        f"- The provisional electricity factor matters: with today's factors, "
        f"{int(over_base.sum()):,} buildings would be over target; {int(flipped.sum()):,} of "
        "them drop below target only because grid electricity is credited as cleaner.",
        f"- 2036-40 provisional targets: **{int((has & t['beps_over_2036']).sum()):,}** "
        "buildings would be over at current performance.",
        "",
        md_table(by_deadline(t)),
        "",
        "## Washington CBPS",
        "",
        f"- **{int(t1.sum()):,}** buildings look like Tier 1 (> 50,000 sf nonresidential).",
        f"- **{int(t['cbps_over_euit'].sum()):,}** of them are above their EUI target; "
        f"estimated maximum penalty exposure **{money_short(t['cbps_penalty_est'].sum())}** per "
        "year of continued non-compliance (before alternative pathways).",
        f"- **{int(both.sum()):,}** buildings are over **both** targets.",
        "",
        "## By building type",
        "",
        md_table(by_building_type(t)),
        "",
        "## Largest excess emissions vs 2031-35 target",
        "",
        md_table(top, index=False),
        "",
    ])

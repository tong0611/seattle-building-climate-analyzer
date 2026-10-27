"""Rank buildings for outreach or retrofit with a transparent priority score.

    score = 100 x confidence x (0.5 x impact + 0.3 x urgency + 0.2 x opportunity)

- impact (0-1): excess emissions above the 2031-35 BEPS target, or excess energy above
  the CBPS target, as a percentile among over-target buildings.
- urgency (0-1): how soon the relevant deadline is (CBPS deadlines are already active).
- opportunity (0-1): energy use above peers (actual / expected EUI - 1, capped at 1),
  scaled by peer confidence. High values suggest cheaper efficiency wins exist.
- confidence (0-1): the data-quality trust score / 100.

Each building also gets a likely pathway label. Scores rank attention; they do not
judge owners. All exposure figures are estimates.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from beps_prioritizer.regulations.beps import BEPS_FACTORS_2031
from beps_prioritizer.reporting import md_table, money_short

WEIGHTS = {"impact": 0.5, "urgency": 0.3, "opportunity": 0.2}


BEPS_URGENCY = {2031: 1.0, 2032: 0.8, 2033: 0.6, 2034: 0.4, 2035: 0.2}


CBPS_URGENCY = {2026: 1.0, 2027: 0.9, 2028: 0.8}


EFFICIENCY_GAP = 1.2  # EUI >= 1.2x peers counts as a meaningful efficiency gap


# Gaps this large were mostly misclassified uses on inspection (a proton-therapy
# center filed as medical office, a crematorium as "other"), so check data first.
IMPLAUSIBLE_PEER_GAP = 4.0


TOP_N = 50


PATHWAYS = {
    "verify_data": "Verify data first",
    "efficiency_may_suffice": "Efficiency may close the gap",
    "efficiency_and_electrification": "Efficiency + electrification",
    "electrification": "Electrification (fuel switching)",
    "district_steam": "District steam (depends on utility)",
    "cbps_energy_only": "Energy efficiency (state target only)",
    "on_track_2031_not_2036": "On track for 2031, not 2036",
    "on_track": "On track",
}


def combine_inputs(targets: pd.DataFrame, peer: pd.DataFrame, quality: pd.DataFrame,
                   bench: pd.DataFrame) -> pd.DataFrame:
    """One row per building: its latest trustworthy year with targets, peers, quality."""
    key = ["building_id", "data_year"]
    t = targets.rename(columns={"basis_year": "data_year"})
    q = quality[[*key, "trust_score", "trust_tier"]]
    b = bench[[*key, "address", "neighborhood", "use1_type", "electricity_kbtu",
               "natural_gas_kbtu", "steam_kbtu", "energy_star_score", "year_built"]]
    return t.merge(peer, on=key, how="left").merge(q, on=key, how="left").merge(b, on=key)


def emission_shares(d: pd.DataFrame) -> pd.DataFrame:
    """Share of 2031-factor emissions from each fuel."""
    kg = pd.DataFrame({c: d[c].fillna(0) * f for c, f in BEPS_FACTORS_2031.items()})
    total = kg.sum(axis=1).replace(0, np.nan)
    return pd.DataFrame({
        "gas_emission_share": kg["natural_gas_kbtu"] / total,
        "steam_emission_share": kg["steam_kbtu"] / total,
        "electric_emission_share": kg["electricity_kbtu"] / total,
    }, index=d.index)


def _pct_rank_where(values: pd.Series, mask: pd.Series) -> pd.Series:
    """Percentile rank (0-1] among masked rows; 0 elsewhere."""
    out = pd.Series(0.0, index=values.index)
    out[mask] = values[mask].rank(pct=True)
    return out


def components(d: pd.DataFrame) -> pd.DataFrame:
    """The four 0-1 score components (see module docstring) per building."""
    over_beps = d["beps_over_2031"].fillna(False).astype(bool)
    over_cbps = d["cbps_over_euit"].fillna(False).astype(bool)
    cbps_excess_kbtu = ((d["site_eui_wn"] - d["cbps_euit"]).clip(lower=0) * d["area_sf"])

    impact = np.maximum(
        _pct_rank_where(d["beps_excess_tco2e_yr"], over_beps),
        _pct_rank_where(cbps_excess_kbtu, over_cbps),
    )
    beps_u = d["beps_first_deadline"].map(BEPS_URGENCY).astype("float64").fillna(0)
    cbps_u = d["cbps_deadline"].map(CBPS_URGENCY).astype("float64").fillna(0)
    urgency = np.maximum(beps_u.where(over_beps, 0), cbps_u.where(over_cbps, 0))
    opportunity = ((d["eui_ratio"] - 1).clip(0, 1) * d["peer_confidence"]).fillna(0)
    confidence = (d["trust_score"] / 100).fillna(0)
    return pd.DataFrame({"impact": impact, "urgency": urgency, "opportunity": opportunity,
                         "confidence": confidence}, index=d.index)


def score(c: pd.DataFrame, weights: dict[str, float] = WEIGHTS) -> pd.Series:
    """100 x confidence x weighted sum of impact, urgency, opportunity."""
    base = sum(w * c[k] for k, w in weights.items())
    return (100 * c["confidence"] * base).round(1)


def verify_reason(d: pd.DataFrame) -> pd.Series:
    """Why a building's numbers should be checked before acting on them ('' if not)."""
    reasons = [
        (d["trust_tier"] != "high", "reported numbers contradict each other"),
        (d["area_disagrees_with_city"].fillna(False),
         "floor area does not match city records"),
        (d["eui_ratio"] >= IMPLAUSIBLE_PEER_GAP,
         "energy use at least four times similar buildings (type may be wrong)"),
    ]
    out = pd.Series("", index=d.index)
    for cond, text in reasons:
        hit = cond.fillna(False).astype(bool)
        out = out.where(~hit, out.where(out == "", out + "; ") + text)
    return out


def pathway(d: pd.DataFrame) -> pd.Series:
    """Likely next step. Order matters: the first matching rule wins."""
    over = d["beps_over_2031"].fillna(False).astype(bool)
    ratio = d["eui_ratio"]
    # GHGI if the building used only as much energy as its peers (same fuel mix).
    ghgi_at_peer = d["ghgi_2031_factors"] * np.minimum(1, 1 / ratio)
    rules = [
        ("verify_data", (over | d["cbps_over_euit"]) & (verify_reason(d) != "")),
        ("district_steam", over & (d["steam_emission_share"] > 0.5)),
        ("efficiency_may_suffice", over & (ghgi_at_peer <= d["beps_target_2031"])),
        ("efficiency_and_electrification", over & (ratio >= EFFICIENCY_GAP)),
        ("electrification", over),
        ("cbps_energy_only", d["cbps_over_euit"].fillna(False).astype(bool)),
        ("on_track_2031_not_2036", d["beps_over_2036"].fillna(False).astype(bool)),
    ]
    out = pd.Series("on_track", index=d.index)
    for name, cond in reversed(rules):  # apply lowest priority first
        out = out.mask(cond.fillna(False).astype(bool), name)
    return out


def prioritize(d: pd.DataFrame) -> pd.DataFrame:
    """Score, label, and rank buildings. ``action_rank`` skips verify-data and on-track
    buildings so the action list only contains buildings ready to act on."""
    d = d.join(emission_shares(d))
    c = components(d)
    out = d.join(c)
    out["priority_score"] = score(c)
    out["pathway"] = pathway(d)
    out["verify_reason"] = verify_reason(d).where(out["pathway"] == "verify_data", "")
    # Blank (not 0) when the building already meets its BEPS target.
    out["required_reduction_pct"] = (100 * d["beps_excess_kg_sf"]
                                     / d["ghgi_2031_factors"]).round(1).where(d["beps_over_2031"])
    out["ghgi_if_peer_efficiency"] = (d["ghgi_2031_factors"]
                                      * np.minimum(1, 1 / d["eui_ratio"])).round(3)
    out = out.sort_values("priority_score", ascending=False, ignore_index=True)
    out["rank"] = np.arange(1, len(out) + 1)
    # On-track buildings can still score a little (energy above peers) but have no
    # target to act on, so the action list holds only over-target, verified buildings.
    not_ready = {"verify_data", "on_track", "on_track_2031_not_2036"}
    ready = ~out["pathway"].isin(not_ready) & (out["priority_score"] > 0)
    out["action_rank"] = pd.Series(pd.NA, index=out.index, dtype="Int64")
    out.loc[ready, "action_rank"] = np.arange(1, ready.sum() + 1)
    return out


ALT_WEIGHTS = {
    "equal": {"impact": 1 / 3, "urgency": 1 / 3, "opportunity": 1 / 3},
    "impact-heavy": {"impact": 0.7, "urgency": 0.2, "opportunity": 0.1},
    "urgency-heavy": {"impact": 0.3, "urgency": 0.6, "opportunity": 0.1},
    "opportunity-heavy": {"impact": 0.3, "urgency": 0.2, "opportunity": 0.5},
    "no opportunity": {"impact": 0.6, "urgency": 0.4, "opportunity": 0.0},
}


def top_ids(s: pd.Series, ids: pd.Series, n: int) -> set:
    """Building ids of the n highest scores."""
    return set(ids[s.sort_values(ascending=False).index[:n]])


def sensitivity(out: pd.DataFrame, n: int = TOP_N, draws: int = 500,
                seed: int = 0) -> tuple[pd.DataFrame, pd.Series]:
    """Top-N overlap under alternative weights, and how often each building stays top-N
    when weights are drawn at random (Dirichlet(1,1,1))."""
    c = out[["impact", "urgency", "opportunity", "confidence"]]
    ids = out["building_id"]
    scored = out["priority_score"] > 0  # on-track buildings all score 0 and inflate rho
    base_top = top_ids(out["priority_score"], ids, n)
    rows = []
    for name, w in ALT_WEIGHTS.items():
        s = score(c, w)
        rows.append({
            "weights": name,
            f"top{n}_overlap_pct": round(100 * len(base_top & top_ids(s, ids, n)) / n, 1),
            "spearman_scored": round(
                out.loc[scored, "priority_score"].corr(s[scored], method="spearman"), 3),
        })
    rng = np.random.default_rng(seed)
    hits = pd.Series(0, index=out.index)
    for w in rng.dirichlet([1, 1, 1], size=draws):
        s = score(c, dict(zip(["impact", "urgency", "opportunity"], w, strict=True)))
        hits[s.sort_values(ascending=False).index[:n]] += 1
    return pd.DataFrame(rows).set_index("weights"), (hits / draws).round(3)


def pathway_table(out: pd.DataFrame) -> pd.DataFrame:
    """Report table: buildings and excess emissions per likely pathway."""
    g = out.groupby("pathway")
    t = pd.DataFrame({
        "buildings": g.size(),
        "excess_tco2e_yr": g["beps_excess_tco2e_yr"].sum().round(0),
        "median_eui_vs_peers": g["eui_ratio"].median().round(2),
        "median_gas_share_of_emissions": g["gas_emission_share"].median().round(2),
    })
    t.index = t.index.map(PATHWAYS)
    return t.sort_values("buildings", ascending=False)


def top_table(out: pd.DataFrame, k: int = 25) -> pd.DataFrame:
    """Report table: the top k of the action list."""
    t = out[out["action_rank"].notna()].head(k)
    return pd.DataFrame({
        "action_rank": t["action_rank"],
        "building": t["building_name"],
        "type": t["use1_type"],
        "score": t["priority_score"],
        "deadline": t["beps_first_deadline"].where(t["beps_over_2031"], t["cbps_deadline"]),
        "cut_needed_pct": t["required_reduction_pct"],
        "eui_vs_peers": t["eui_ratio"].round(2),
        "est_acp": t["beps_acp_est"].map(money_short),
        "pathway": t["pathway"].map(PATHWAYS),
        "robust_top50_pct": (100 * t["top50_robustness"]).round(0),
    })


def build_report(out: pd.DataFrame, sens: pd.DataFrame) -> str:
    over = out["beps_over_2031"].fillna(False).astype(bool)
    eff = out["pathway"] == "efficiency_may_suffice"
    verify = out["pathway"] == "verify_data"
    excess = out["beps_excess_tco2e_yr"].fillna(0)
    total_excess = excess.sum()
    top = out.head(TOP_N)
    naive_ids = set(out.nlargest(TOP_N, "beps_excess_tco2e_yr")["building_id"])
    same_as_naive = len(set(top["building_id"]) & naive_ids)
    median_cut = out.loc[over, "required_reduction_pct"].median()
    median_gas = 100 * out.loc[over, "gas_emission_share"].median()
    return "\n".join([
        "# Priority ranking",
        "",
        "_Generated by `beps-priority`. Scores rank where attention is likely to matter "
        "most; they do not judge owners. Exposure figures are estimates._",
        "",
        "## Score",
        "",
        "`score = 100 x confidence x (0.5 x impact + 0.3 x urgency + 0.2 x opportunity)`",
        "",
        "| component | meaning |",
        "|---|---|",
        "| impact | excess emissions above the 2031-35 BEPS target (or excess energy above "
        "the CBPS target), percentile among over-target buildings |",
        "| urgency | deadline proximity: BEPS 2031 = 1.0 ... 2035 = 0.2; CBPS 2026 = 1.0, "
        "2027 = 0.9, 2028 = 0.8 |",
        "| opportunity | EUI above peers (actual / expected - 1, capped at 1) x peer "
        "confidence |",
        "| confidence | data-quality trust score / 100 |",
        "",
        "## Summary",
        "",
        f"- {len(out):,} buildings scored; {int(over.sum()):,} are over their 2031-35 BEPS "
        "target.",
        f"- Over-target buildings need a median **{median_cut:.0f}%** emissions cut; gas is "
        f"a median **{median_gas:.0f}%** of their 2031 emissions.",
        f"- **{int(eff.sum())}** over-target buildings could reach the target by matching "
        "their peers' energy efficiency alone (same fuel mix); the rest need fuel switching.",
        f"- **Verify data first:** {int(verify.sum())} buildings hold "
        f"**{100 * excess[verify].sum() / total_excess:.0f}%** of all excess emissions, "
        "mostly large campuses whose reported floor area differs from the city's. "
        "Confirming these few records is the highest-leverage first step; they are listed "
        "separately below and excluded from the action list.",
        f"- The top {TOP_N} by score account for **{excess[top.index].sum():,.0f}** of "
        f"{total_excess:,.0f} tCO2e/yr of excess emissions "
        f"({100 * excess[top.index].sum() / total_excess:.0f}%).",
        f"- Versus simply ranking by excess emissions, {same_as_naive} of the top {TOP_N} "
        "are the same; the score adds deadline urgency, efficiency opportunity, and data "
        "confidence.",
        "",
        "## Likely pathway",
        "",
        "Rule-based: verify data if trust or floor area is uncertain; district steam if "
        "steam is > 50% of emissions; 'efficiency may close the gap' if matching peer EUI "
        "(same fuel mix) would meet the target; 'efficiency + electrification' if EUI is "
        f">= {EFFICIENCY_GAP}x peers but that is not enough; otherwise electrification.",
        "",
        md_table(pathway_table(out)),
        "",
        "## Sensitivity to the weights",
        "",
        f"Overlap of the top {TOP_N} with alternative weightings, and rank correlation "
        "among buildings with a nonzero score. `robust_top50_pct` in the "
        "table below is the share of 500 random weightings (Dirichlet) in which the "
        "building stays in the top 50.",
        "",
        md_table(sens),
        "",
        f"- {int((out['top50_robustness'] >= 0.8).sum())} buildings are in the top {TOP_N} "
        "under at least 80% of random weightings.",
        "",
        "## Top 25 ready to act on",
        "",
        md_table(top_table(out), index=False),
        "",
        "## Verify data first: largest excess emissions",
        "",
        md_table(out[verify].nlargest(15, "beps_excess_tco2e_yr")[[
            "rank", "building_name", "use1_type", "beps_excess_tco2e_yr", "eui_ratio",
            "verify_reason"]].round(2), index=False),
        "",
    ])

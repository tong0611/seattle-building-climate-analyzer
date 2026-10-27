"""Explainers: turn a fact record into plain language. Only the template exists today.
"""

from __future__ import annotations

from typing import Protocol

from beps_prioritizer.explain.facts import Facts, fmt_money, fmt_pct, fmt_ratio


class Explainer(Protocol):
    """Anything that turns a fact record into text (template today, LLM later)."""

    name: str

    def explain(self, facts: Facts) -> str: ...


def _peer_sentence(f: Facts) -> str:
    ratio = f["eui_ratio"]
    if ratio is None or f["expected_eui"] is None:
        return ""
    if ratio >= 1.15:
        comparison = f"uses {fmt_ratio(ratio)} the energy of similar buildings"
    elif ratio <= 0.85:
        comparison = f"uses less energy than similar buildings ({fmt_ratio(ratio)})"
    else:
        comparison = "uses about the same energy as similar buildings"
    text = (f"It {comparison}: {f['site_eui']} kBtu per sq ft versus about {f['expected_eui']} "
            "expected for its use mix and size.")
    if f["peer_confidence"] is not None and f["peer_confidence"] < 0.5:
        text += " Few comparable buildings exist, so treat this comparison with caution."
    return text


def _target_sentence(f: Facts) -> str:
    if f["beps_target_2031"] is None or f["ghgi_2031"] is None:
        return "No Seattle emissions target applies to this type of building."
    if not f["beps_over_2031"]:
        return (f"At current performance it meets Seattle's 2031-2035 emissions target "
                f"({f['ghgi_2031']} vs {f['beps_target_2031']} kg CO2e per sq ft).")
    cut = f["required_cut_pct"]
    cut_text = ("an estimated cut of under 1%" if cut < 1  # avoid "a 0% cut"
                else f"an estimated {fmt_pct(cut)} cut")
    return (f"It is above Seattle's 2031-2035 emissions target ({f['ghgi_2031']} vs "
            f"{f['beps_target_2031']} kg CO2e per sq ft) and needs {cut_text} by "
            f"{f['beps_deadline']}. Gas is {fmt_pct(f['gas_share_pct'])} of its emissions.")


def _coverage_sentence(f: Facts) -> str:
    cov = f.get("beps_coverage_pct")
    if cov is None or cov >= 90 or f["beps_target_2031"] is None:
        return ""
    return (f"Only {fmt_pct(cov)} of its floor area has a Seattle emissions target (the rest "
            "is an exempt use), so its target is based on that portion.")


def _pathway_sentence(f: Facts) -> str:
    p = f["pathway"]
    if p == "verify_data":
        return f"Before acting on these numbers, verify the data: {f['verify_reason']}."
    if p == "district_steam":
        return (f"Most of its emissions ({fmt_pct(f['steam_share_pct'])}) come from district "
                "steam, so progress depends on the steam supplier's emissions as well as on "
                "using less steam.")
    if p == "efficiency_may_suffice":
        return (f"Using as much energy as similar buildings, with the same fuel mix, would "
                f"bring it to about {f['ghgi_if_peer_efficiency']} kg CO2e per sq ft, within the "
                "target, so efficiency improvements alone may be enough.")
    if p == "efficiency_and_electrification":
        return (f"Matching similar buildings' energy use would bring it to about "
                f"{f['ghgi_if_peer_efficiency']} kg CO2e per sq ft, still above the target, so it "
                "likely needs both efficiency work and replacing gas equipment with electric.")
    if p == "electrification":
        return ("Efficiency alone is unlikely to close the gap, which comes mainly from "
                "burning gas; replacing gas equipment with electric is the main lever.")
    if p == "cbps_energy_only":
        return (f"It uses more energy than its Washington state target of {f['cbps_euit']:.0f} "
                f"kBtu per sq ft, with a deadline of {f['cbps_deadline']}.")
    if p == "on_track_2031_not_2036":
        return (f"It would be above the provisional 2036-2040 target of "
                f"{f['beps_target_2036']} kg CO2e per sq ft, so planning ahead is worthwhile.")
    return ""


def _exposure_sentence(f: Facts) -> str:
    if not f["beps_over_2031"] or not f["acp_est"]:
        return ""
    return (f"If performance stays the same, the estimated alternative compliance payment "
            f"is {fmt_money(f['acp_est'])} for the 2031-2035 interval (an estimate, not a "
            "penalty notice).")


class TemplateExplainer:
    """Deterministic explanations; always faithful by construction."""

    name = "template"

    def explain(self, facts: Facts) -> str:
        f = facts
        head = (f"{f['name']} ({f['use_type']}, {f['data_year']} data, "
                f"{f['area_sf']:,.0f} sq ft).")
        parts = [head, _peer_sentence(f), _target_sentence(f), _coverage_sentence(f),
                 _pathway_sentence(f), _exposure_sentence(f)]
        return " ".join(p for p in parts if p)

"""Generate explanations and evaluate both the explanations and the checker.

1. Faithfulness: every building's explanation is checked against its facts.
2. Checker validation: correct explanations are deliberately broken in six realistic
   ways (wrong number, wrong deadline, flipped claim, missing estimate label,
   overclaiming, invented fact). The checker should flag every broken text and no
   correct one. This is what makes the pass rate meaningful once an LLM writes the
   text instead of a template.
"""

from __future__ import annotations

import re
from collections.abc import Callable

import numpy as np
import pandas as pd

from beps_prioritizer.explain.facts import Facts, building_facts, fmt_money
from beps_prioritizer.explain.faithfulness import NUMBER, check
from beps_prioritizer.explain.templates import Explainer
from beps_prioritizer.reporting import md_table
from beps_prioritizer.scoring.priority import PATHWAYS

# A perturbation returns the broken text, or None if it does not apply to this text.
# All share one signature, so some ignore the facts or the random generator.
Perturb = Callable[[str, Facts, np.random.Generator], str | None]


def _body_spans(text: str, facts: Facts) -> list[re.Match]:
    """Numeric tokens that state a fact: not years, and not inside the building's
    name, address, or use type (e.g. the 12 in "K-12 School")."""
    protected = []
    for ident in (facts.get("name"), facts.get("address"), facts.get("use_type")):
        if ident:
            protected += [(m.start(), m.end()) for m in re.finditer(re.escape(ident), text)]
    out = []
    for m in NUMBER.finditer(text):
        if any(a <= m.start() < b for a, b in protected):
            continue
        if re.fullmatch(r"20\d\d", m.group(0).strip()):
            continue
        out.append(m)
    return out


def change_number(text: str, facts: Facts, rng: np.random.Generator) -> str | None:
    """Scale one stated number by 0.6-1.6x, keeping its format."""
    spans = _body_spans(text, facts)
    if not spans:
        return None
    m = spans[rng.integers(len(spans))]
    raw = m.group(0)
    factor = rng.choice([0.6, 0.75, 1.35, 1.6])
    if raw.startswith("$"):
        value = facts["acp_est"] * factor
        new = fmt_money(value)
    else:
        num_text = m["num"].replace(",", "") + (m["dec"] or "")
        decimals = len(m["dec"]) - 1 if m["dec"] else 0
        value = float(num_text) * factor
        new_num = f"{value:,.{decimals}f}" if "," in m["num"] else f"{value:.{decimals}f}"
        if new_num == (m["num"] + (m["dec"] or "")):
            return None
        new = raw.replace(m["num"] + (m["dec"] or ""), new_num, 1)
    return text[:m.start()] + new + text[m.end():]


def change_deadline(text: str, facts: Facts, rng: np.random.Generator) -> str | None:
    m = re.search(r"\b(by|deadline of) (20\d\d)\b", text)
    if not m:
        return None
    wrong = int(m.group(2)) + int(rng.choice([-2, -1, 1, 2]))
    return text[:m.start(2)] + str(wrong) + text[m.end(2):]


FLIPS = [
    ("It is above Seattle's", "It meets Seattle's"),
    ("it meets Seattle's", "it is above Seattle's"),
    ("uses less energy than similar buildings", "uses more energy than similar buildings"),
    ("uses about the same energy as similar buildings",
     "uses more energy than similar buildings"),
]


def flip_claim(text: str, facts: Facts, rng: np.random.Generator) -> str | None:
    for old, new in FLIPS:
        if old in text:
            return text.replace(old, new, 1)
    m = re.search(r"uses (\d+\.\d)x the energy of similar buildings", text)
    if m:
        return text.replace(m.group(0), "uses less energy than similar buildings", 1)
    return None


def drop_estimate_label(text: str, facts: Facts, rng: np.random.Generator) -> str | None:
    if "$" not in text:
        return None
    out = text.replace("the estimated alternative", "the alternative")
    out = out.replace(" (an estimate, not a penalty notice)", "")
    return out if out != text else None


def add_overclaim(text: str, facts: Facts, rng: np.random.Generator) -> str | None:
    phrase = rng.choice([
        " The owner will be fined if nothing changes.",
        " The building is violating the emissions standard.",
        " The owner must pay the city unless emissions fall.",
    ])
    return text + str(phrase)


def invent_fact(text: str, facts: Facts, rng: np.random.Generator) -> str | None:
    template = rng.choice([
        " Its heating system is about {n} years old.",
        " Retrofits could save about {n}% of energy.",
        " Upgrades would cost roughly ${n},000.",
    ])
    return text + str(template).format(n=int(rng.integers(13, 97)))


PERTURBATIONS: dict[str, Perturb] = {
    "wrong number": change_number,
    "wrong deadline": change_deadline,
    "flipped claim": flip_claim,
    "estimate label removed": drop_estimate_label,
    "overclaiming language": add_overclaim,
    "invented fact": invent_fact,
}


def explain_all(priority: pd.DataFrame, explainer: Explainer) -> pd.DataFrame:
    """Explain every building and check each explanation against its facts."""
    facts = [building_facts(r) for _, r in priority.iterrows()]
    texts = [explainer.explain(f) for f in facts]
    results = [check(t, f) for t, f in zip(texts, facts, strict=True)]
    return pd.DataFrame({
        "building_id": [f["building_id"] for f in facts],
        "pathway": [f["pathway"] for f in facts],
        "beps_over_2031": [f["beps_over_2031"] for f in facts],
        "explainer": explainer.name,
        "text": texts,
        "passed": [r.passed for r in results],
        "issues": ["; ".join(r.issues) for r in results],
        "missing": [r.missing for r in results],
        "facts": facts,
    })


def evaluate_checker(texts: pd.Series, facts: list[Facts], n: int = 500,
                     seed: int = 0) -> pd.DataFrame:
    """Share of broken texts the checker flags, per perturbation (up to n each)."""
    rng = np.random.default_rng(seed)
    order = rng.permutation(len(texts))
    rows = []
    for name, perturb in PERTURBATIONS.items():
        caught = tried = 0
        for i in order:
            broken = perturb(texts.iloc[i], facts[i], rng)
            if broken is None or broken == texts.iloc[i]:
                continue
            tried += 1
            caught += not check(broken, facts[i]).passed
            if tried >= n:
                break
        rows.append({"perturbation": name, "cases": tried,
                     "caught_pct": round(100 * caught / tried, 1) if tried else None})
    return pd.DataFrame(rows).set_index("perturbation")


def build_report(ex: pd.DataFrame, checker_eval: pd.DataFrame, seconds: float) -> str:
    passed = ex["passed"].mean()
    over = ex["beps_over_2031"]
    complete = (ex.loc[over, "missing"].str.len() == 0).mean()
    samples = []
    for p in PATHWAYS:
        sub = ex[ex["pathway"] == p]
        if len(sub):
            row = sub.iloc[0]
            samples += [f"**{PATHWAYS[p]}**", "", f"> {row['text']}", ""]
    failures = ex[~ex["passed"]].head(10)
    return "\n".join([
        "# Building explanations",
        "",
        "_Generated by `beps-explain`. Every number comes from the computed building "
        "facts; exposure figures are estimates._",
        "",
        "## Summary",
        "",
        f"- Explainer: **template** (deterministic). {len(ex):,} explanations generated "
        f"and checked in {seconds:.1f} s.",
        f"- Faithfulness pass rate: **{100 * passed:.1f}%** ({int(ex['passed'].sum()):,} / "
        f"{len(ex):,}).",
        f"- Completeness (over-target buildings mention deadline and required cut): "
        f"**{100 * complete:.1f}%**.",
        "",
        "Templates are faithful by construction, so a 100% pass rate here checks the "
        "pipeline, not the writing. The checker validation below is what will make the "
        "pass rate meaningful when an LLM writes the text.",
        "",
        "## Checker validation: deliberately broken explanations",
        "",
        "Correct explanations were broken in six ways; `caught_pct` is the share the "
        "checker flagged. A good checker catches nearly all of them while passing every "
        "correct explanation (false-alarm rate = 100% - pass rate above).",
        "",
        md_table(checker_eval),
        "",
        "## Example explanations by pathway",
        "",
        *samples,
        "## Failures",
        "",
        md_table(failures[["building_id", "issues"]], index=False) if len(failures)
        else "None.",
        "",
    ])

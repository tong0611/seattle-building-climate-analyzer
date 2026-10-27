"""Check that an explanation says nothing the building's facts do not support.

Rule-based and deliberately strict. A text fails if it contains:
- a number that matches no fact of the same kind (money, %, ratio, year, other),
- a deadline ("by 2032") that is not the building's deadline,
- a target claim ("above / meets its 2031-2035 target") contradicting the data,
- a peer claim ("more / less energy than similar buildings") contradicting the data,
- a dollar figure in a sentence that does not call it an estimate,
- accusatory or overconfident language ("will be fined", "violating").

Separately, ``missing`` lists key facts an over-target explanation should mention
(completeness), which does not affect pass/fail.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from beps_prioritizer.explain.facts import Facts

INTERVAL_YEARS = {2031, 2035, 2036, 2040, 2041, 2045, 2046, 2050}


NUMBER = re.compile(
    r"(?<![\w.$])(?P<cur>\$)?(?P<num>\d{1,3}(?:,\d{3})+|\d+)(?P<dec>\.\d+)?"
    r"(?P<mag>\s?(?:M|K|million|thousand)\b)?"
    r"(?P<unit>%|x\b|\s?percent\b|\s?times\b)?"
)


SENTENCE = re.compile(r"(?<=[.!?])\s+")


PROHIBITED = re.compile(
    r"\bwill be fined\b|\bwill (?:have to )?pay\b|\bmust pay\b|\bviolat\w*|\billegal\b"
    r"|\bwrongdoing\b|\bnon-?compliant\b|\bguarantee\w*\b|\bcertainly\b",
    re.IGNORECASE,
)


HIGHER = re.compile(r"more energy than|higher (?:energy use )?than (?:similar|its peers)"
                    r"|\d(?:\.\d+)?\s?(?:x|times) the energy", re.IGNORECASE)


LOWER = re.compile(r"less energy than|lower (?:energy use )?than (?:similar|its peers)",
                   re.IGNORECASE)


SIMILAR = re.compile(r"about the same energy|close to similar|in line with similar",
                     re.IGNORECASE)


OVER = re.compile(r"\b(?:above|over|exceeds|misses)\b", re.IGNORECASE)


UNDER = re.compile(r"\b(?:meets|below|under|within)\b", re.IGNORECASE)


NUMBER_FACTS = ["site_eui", "expected_eui", "peer_median_eui", "ghgi_2031",
                "beps_target_2031", "beps_target_2036", "ghgi_if_peer_efficiency", "area_sf",
                "cbps_euit", "excess_tco2e_yr", "trust_score", "priority_rank"]


PCT_FACTS = ["required_cut_pct", "gas_share_pct", "steam_share_pct", "beps_coverage_pct"]


@dataclass
class Token:
    """A number found in text, classified by what kind of quantity it looks like."""

    text: str
    kind: str  # money | pct | ratio | year | number
    value: float
    decimals: int


@dataclass
class CheckResult:
    """``passed`` is False if any issue was found; ``missing`` never affects it."""

    passed: bool
    issues: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


def _strip_identifiers(text: str, facts: Facts) -> str:
    """Remove the building's name, address, and use type (e.g. "K-12 School")."""
    for ident in (facts.get("name"), facts.get("address"), facts.get("use_type")):
        if ident:
            text = text.replace(ident, "<BUILDING>")
    return text


def extract_numbers(text: str) -> list[Token]:
    """All numbers in text, with $/M/K/%/x/times units resolved to a kind and value.

    "CO2e", "2031-2035" interval labels, and "Tier 1/2" are neutralized first so
    their digits are not mistaken for claims.
    """
    clean = re.sub(r"CO2e?", "CO-two", text)
    clean = re.sub(r"\b(20\d\d)-(20\d\d)\b", r"\1 to \2", clean)
    clean = re.sub(r"\bTier [12]\b", "Tier", clean)
    tokens = []
    for m in NUMBER.finditer(clean):
        num = float(m["num"].replace(",", "") + (m["dec"] or ""))
        decimals = len(m["dec"]) - 1 if m["dec"] else 0
        mag = (m["mag"] or "").strip().lower()
        unit = (m["unit"] or "").strip().lower()
        if mag in ("m", "million"):
            num *= 1e6
        elif mag in ("k", "thousand"):
            num *= 1e3
        if m["cur"]:
            kind = "money"
        elif unit in ("%", "percent"):
            kind = "pct"
        elif unit in ("x", "times"):
            kind = "ratio"
        elif not m["dec"] and not mag and 1900 <= num <= 2100 and "," not in m["num"]:
            kind = "year"
        else:
            kind = "number"
        tokens.append(Token(m.group(0).strip(), kind, num, decimals))
    return tokens


def _matches(token: Token, facts: Facts) -> bool:
    """Does the token equal some fact of the same kind, allowing display rounding?"""

    def near(value: float | None, tol: float) -> bool:
        return value is not None and abs(token.value - value) <= tol

    if token.kind == "money":
        v = facts.get("acp_est")
        return v is not None and abs(token.value - v) <= 0.05 * v + 100
    if token.kind == "pct":
        return any(near(facts.get(k), 1.0) for k in PCT_FACTS)
    if token.kind == "ratio":
        return near(facts.get("eui_ratio"), 0.06)
    if token.kind == "year":
        years = {facts.get("data_year"), facts.get("beps_deadline"),
                 facts.get("cbps_deadline")} | INTERVAL_YEARS
        return int(token.value) in years
    tol_abs = 0.5 * 10 ** -token.decimals
    return any(
        facts.get(k) is not None
        and abs(token.value - facts[k]) <= max(tol_abs, 0.005 * abs(facts[k]))
        for k in NUMBER_FACTS
    )


def _claim_issues(text: str, facts: Facts) -> list[str]:
    """Deadline, direction (peers, target), estimate-label, and framing checks."""
    issues = []
    deadlines = {d for d in (facts.get("beps_deadline"), facts.get("cbps_deadline")) if d}
    for m in re.finditer(r"\b(?:by|deadline (?:of|is))\s+(\d{4})\b", text):
        if int(m.group(1)) not in deadlines:
            issues.append(f"deadline {m.group(1)} is not this building's deadline")

    ratio = facts.get("eui_ratio")
    for s in SENTENCE.split(text):
        if "similar buildings" in s or "peers" in s:
            if ratio is not None:
                if HIGHER.search(s) and ratio < 1.15:
                    issues.append("claims more energy than peers, but it is not")
                if LOWER.search(s) and ratio > 0.85:
                    issues.append("claims less energy than peers, but it is not")
                if SIMILAR.search(s) and not 0.85 < ratio < 1.15:
                    issues.append("claims similar energy to peers, but it is not")
        if "target" in s and ("BEPS" in s or "2031" in s or "2036" in s):
            over = facts.get("beps_over_2036") if "2036" in s else facts.get("beps_over_2031")
            says_over, says_under = bool(OVER.search(s)), bool(UNDER.search(s))
            if says_over and not says_under and not over:
                issues.append("claims it is above its BEPS target, but it is not")
            if says_under and not says_over and over:
                issues.append("claims it meets its BEPS target, but it does not")
        if "$" in s and not re.search(r"estimat", s, re.IGNORECASE):
            issues.append("dollar figure not labeled as an estimate")
    if (m := PROHIBITED.search(text)):
        issues.append(f"prohibited framing: '{m.group(0)}'")
    return issues


def check(text: str, facts: Facts) -> CheckResult:
    """Verify an explanation against its building's facts (see module docstring)."""
    text = _strip_identifiers(text, facts)
    tokens = extract_numbers(text)
    issues = [f"unsupported {t.kind}: '{t.text}'" for t in tokens if not _matches(t, facts)]
    issues += _claim_issues(text, facts)

    missing = []
    if facts.get("beps_over_2031"):
        if facts.get("beps_deadline") and not any(
                t.kind == "year" and t.value == facts["beps_deadline"] for t in tokens):
            missing.append("deadline")
        if facts.get("required_cut_pct") is not None and not any(
                t.kind == "pct" and abs(t.value - facts["required_cut_pct"]) <= 1
                for t in tokens):
            missing.append("required cut")
    return CheckResult(passed=not issues, issues=issues, missing=missing)

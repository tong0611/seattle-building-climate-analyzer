"""The pipeline: every step from raw CSV to app-ready tables, with timings.

This is the only module that reads or writes files. Each step loads its inputs from
data/processed, calls pure functions from the domain packages, and writes its outputs
and Markdown report. Used by the CLI (`beps run`), by the app on first start (raw
data is not in git), and by the scheduled refresh workflow.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from beps_prioritizer import app_data
from beps_prioritizer.config import (
    APP_BUILDINGS_PARQUET,
    APP_HISTORY_PARQUET,
    BENCHMARKING_PARQUET,
    EXPLANATIONS_PARQUET,
    EXPLANATIONS_REPORT,
    PEER_PARQUET,
    PEER_REPORT,
    PIPELINE_RUN_JSON,
    PRIORITY_PARQUET,
    PRIORITY_REPORT,
    PROFILE_REPORT,
    QUALITY_PARQUET,
    QUALITY_REPORT,
    RAW_CSV,
    TARGETS_PARQUET,
    TARGETS_REPORT,
)
from beps_prioritizer.explain import evaluation as explain_eval
from beps_prioritizer.explain.templates import TemplateExplainer
from beps_prioritizer.ingestion import ingest, profiling
from beps_prioritizer.peers import crossfit
from beps_prioritizer.peers import report as peer_report
from beps_prioritizer.quality import evaluation as quality_eval
from beps_prioritizer.quality.trust import assess
from beps_prioritizer.scoring import priority, targets


def _read(path: Path) -> pd.DataFrame:
    return pd.read_parquet(path)


def _write(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)


def _report(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


# --------------------------------------------------------------------------- #
# Steps. Each returns a one-line summary for the log.
# --------------------------------------------------------------------------- #

def step_ingest(download: bool = False) -> str:
    """Download (if needed) and standardize the raw CSV."""
    ingest.download_raw(force=download)
    df = ingest.standardize(ingest.load_raw(RAW_CSV))
    _write(df, BENCHMARKING_PARQUET)
    return (f"{len(df):,} rows, {df['building_id'].nunique():,} buildings, "
            f"{df['data_year'].min()}-{df['data_year'].max()}")


def step_profile() -> str:
    """Descriptive data profile."""
    _report(PROFILE_REPORT, profiling.build_report(_read(BENCHMARKING_PARQUET)))
    return f"report -> {PROFILE_REPORT.name}"


def step_quality() -> str:
    """Rule flags, anomaly score, and trust score per building-year."""
    q = assess(_read(BENCHMARKING_PARQUET))
    _write(q, QUALITY_PARQUET)
    tiers = q["trust_tier"].value_counts()
    return (f"trust high={tiers['high']:,} medium={tiers['medium']:,} low={tiers['low']:,}; "
            f"usable for modeling {int(q['usable_for_modeling'].sum()):,}")


def step_quality_eval(n: int = 300) -> str:
    """Error-injection evaluation of the data-quality layer."""
    df, q = _read(BENCHMARKING_PARQUET), _read(QUALITY_PARQUET)
    inj = quality_eval.run_injection(df, q, n=n)
    _report(QUALITY_REPORT, quality_eval.build_report(df, q, inj))
    lo, hi = inj["surfaced_pct"].min(), inj["surfaced_pct"].max()
    return f"surfaced {lo:.0f}-{hi:.0f}% of injected errors"


def step_peer() -> str:
    """Out-of-building peer model: expected EUI and GHG intensity."""
    df, q = _read(BENCHMARKING_PARQUET), _read(QUALITY_PARQUET)
    out, details = crossfit.run(df, q)
    _write(out, PEER_PARQUET)
    _report(PEER_REPORT, peer_report.build_report(df, q, out, details))
    m = details["metrics"].set_index(["target", "model"])["r2_log"]
    prod = crossfit.PRODUCTION_MODEL
    return (f"R2 (log) EUI {m[('site_eui_wn', prod)]:.3f}, "
            f"GHG {m[('ghg_intensity', prod)]:.3f}")


def step_targets() -> str:
    """BEPS / CBPS target gaps and estimated exposure."""
    t = targets.assess_targets(_read(BENCHMARKING_PARQUET), _read(QUALITY_PARQUET))
    _write(t, TARGETS_PARQUET)
    _report(TARGETS_REPORT, targets.build_report(t))
    return f"{int(t['beps_over_2031'].sum()):,} of {len(t):,} buildings over the 2031 target"


def step_priority() -> str:
    """Priority score, pathway, and weight sensitivity."""
    combined = priority.combine_inputs(_read(TARGETS_PARQUET), _read(PEER_PARQUET),
                                       _read(QUALITY_PARQUET), _read(BENCHMARKING_PARQUET))
    out = priority.prioritize(combined)
    sens, robustness = priority.sensitivity(out)
    out["top50_robustness"] = robustness
    _write(out, PRIORITY_PARQUET)
    _report(PRIORITY_REPORT, priority.build_report(out, sens))
    return f"{int(out['action_rank'].notna().sum()):,} buildings on the action list"


def step_explain(n: int = 500) -> str:
    """Plain-language explanations, faithfulness check, and checker validation."""
    start = time.perf_counter()
    ex = explain_eval.explain_all(_read(PRIORITY_PARQUET), TemplateExplainer())
    seconds = time.perf_counter() - start
    checker_eval = explain_eval.evaluate_checker(ex["text"], ex["facts"].tolist(), n=n)
    ex = ex.drop(columns="facts")
    _write(ex, EXPLANATIONS_PARQUET)
    _report(EXPLANATIONS_REPORT, explain_eval.build_report(ex, checker_eval, seconds))
    return f"faithfulness pass rate {100 * ex['passed'].mean():.1f}%"


def step_app_data() -> str:
    """Small, app-ready tables."""
    bench = _read(BENCHMARKING_PARQUET)
    buildings = app_data.build_buildings(_read(PRIORITY_PARQUET), bench,
                                         _read(EXPLANATIONS_PARQUET))
    history = app_data.build_history(bench, _read(PEER_PARQUET), _read(QUALITY_PARQUET),
                                     buildings["building_id"])
    _write(buildings, APP_BUILDINGS_PARQUET)
    _write(history, APP_HISTORY_PARQUET)
    return f"{len(buildings):,} buildings, {len(history):,} history rows"


STEPS: dict[str, Callable[..., str]] = {
    "ingest": step_ingest,
    "profile": step_profile,
    "quality": step_quality,
    "quality-eval": step_quality_eval,
    "peer": step_peer,
    "targets": step_targets,
    "priority": step_priority,
    "explain": step_explain,
    "app-data": step_app_data,
}


def run_all(download: bool = False, log: Callable[[str], None] = print) -> dict:
    """Run every step in order; return (and save) row counts and per-step seconds."""
    timings = {}
    start = time.perf_counter()
    for name, step in STEPS.items():
        t0 = time.perf_counter()
        summary = step(download=download) if name == "ingest" else step()
        timings[name] = round(time.perf_counter() - t0, 2)
        log(f"[{name}] {summary} ({timings[name]} s)")
    bench = _read(BENCHMARKING_PARQUET)
    run = {
        "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "total_seconds": round(time.perf_counter() - start, 1),
        "step_seconds": timings,
        "rows": len(bench),
        "buildings": int(bench["building_id"].nunique()),
        "latest_data_year": int(bench["data_year"].max()),
        "buildings_ranked": len(_read(APP_BUILDINGS_PARQUET)),
    }
    PIPELINE_RUN_JSON.write_text(json.dumps(run, indent=2))
    log(f"Pipeline finished in {run['total_seconds']} s")
    return run

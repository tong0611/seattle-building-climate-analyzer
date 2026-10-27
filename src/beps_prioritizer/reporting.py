"""Small helpers shared by the Markdown reports.
"""

from __future__ import annotations

import pandas as pd


def _fmt(v: object) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)) or v is pd.NA:
        return ""
    if isinstance(v, float):
        if abs(v) >= 1e6:
            return f"{v:.3g}"
        return f"{v:,.2f}".rstrip("0").rstrip(".") if v != int(v) else f"{int(v):,}"
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def money_short(x: float) -> str:
    """$1.6M above a million, else whole dollars ($34,445). Used in reports."""
    return f"${x / 1e6:,.1f}M" if abs(x) >= 1e6 else f"${x:,.0f}"


def md_table(df: pd.DataFrame, index: bool = True) -> str:
    """Render a DataFrame as a GitHub Markdown table (index included by default)."""
    frame = df.reset_index() if index else df
    cols = [str(c) for c in frame.columns]
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for row in frame.itertuples(index=False):
        lines.append("| " + " | ".join(_fmt(v) for v in row) + " |")
    return "\n".join(lines)

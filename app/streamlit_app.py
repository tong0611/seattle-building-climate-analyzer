"""Seattle Building Climate Compliance Dashboard.

Four views: an overview, a priority list (with buildings whose data needs review
listed separately), a map, and a building profile. Written for non-technical
viewers: plain labels, a one-line explanation for every status and number, and
technical units only where they are explained.

Run locally:  streamlit run app/streamlit_app.py
On first start (e.g. a fresh deploy) the app downloads the city data and builds
everything with the same pipeline as `beps run` (about 30 seconds).
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import altair as alt
import numpy as np
import pandas as pd
import pydeck as pdk
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from beps_prioritizer.app_data import (  # noqa: E402
    BUILDING_TYPE,
    SIZE_GROUPS,
    STATUS_NOTES,
    STATUS_ORDER,
)
from beps_prioritizer.config import (  # noqa: E402
    APP_BUILDINGS_PARQUET,
    APP_HISTORY_PARQUET,
    PIPELINE_RUN_JSON,
)
from beps_prioritizer.explain.facts import fmt_money  # noqa: E402

TITLE = "Seattle Building Climate Compliance Dashboard"
SUBTITLE = "See which buildings need to cut emissions or energy use, by when, and why."
REPO_URL = ""  # set to the GitHub repository URL once published; adds a footer link
TONNES_PER_CAR = 4.6  # EPA: typical passenger vehicle, metric tons CO2 per year
VIEWS = ["Overview", "Priority list", "Map", "Building profile"]

# Reference palette (dataviz skill): first three categorical slots + neutral gray.
# Map points are compared all-pairs, where only three slots stay colorblind-safe.
# The same status colors are used everywhere a status appears.
STATUS_COLORS = {
    "Action needed": "#eb6834",                 # slot 2 orange
    "Data needs review": "#2a78d6",             # slot 1 blue
    "Fine now, off track for 2036": "#1baf7a",  # slot 3 aqua
    "On track": "#898781",                      # neutral gray
}
SERIES = "#2a78d6"
REFERENCE = "#898781"
INK_SECONDARY = "#52514e"

st.set_page_config(page_title=TITLE, page_icon="🏢", layout="wide")


# --------------------------------------------------------------------------- #
# Data
# --------------------------------------------------------------------------- #

def ensure_data() -> None:
    """Build all data on first start (e.g. a fresh deploy, where data/ is not in git)."""
    if APP_BUILDINGS_PARQUET.exists() and APP_HISTORY_PARQUET.exists():
        return
    from beps_prioritizer.pipeline import run_all  # heavy (sklearn); only when needed
    with st.status("First start: downloading city data and building the dashboard...",
                   expanded=True) as status:
        run_all(download=True, log=st.write)
        status.update(label="Data ready", state="complete")


@st.cache_data
def load(data_version: float) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Read the app tables. ``data_version`` (file mtime) refreshes the cache after a
    pipeline rerun; otherwise a running app would keep serving stale data."""
    buildings = pd.read_parquet(APP_BUILDINGS_PARQUET)
    history = pd.read_parquet(APP_HISTORY_PARQUET)
    run = json.loads(PIPELINE_RUN_JSON.read_text()) if PIPELINE_RUN_JSON.exists() else {}
    return buildings, history, run


def data_version() -> float:
    """Latest modification time of the app tables (a cache key)."""
    return max(p.stat().st_mtime for p in (APP_BUILDINGS_PARQUET, APP_HISTORY_PARQUET))


# --------------------------------------------------------------------------- #
# Small formatting helpers
# --------------------------------------------------------------------------- #

def hex_to_rgb(h: str) -> list[int]:
    return [int(h[i:i + 2], 16) for i in (1, 3, 5)]


def round_to(x: float, step: int) -> int:
    return int(round(x / step) * step)


def peer_words(ratio: float) -> str:
    """Plain-language comparison with similar buildings."""
    if pd.isna(ratio):
        return ""
    if ratio >= 1.15:
        return "more energy than similar buildings"
    if ratio <= 0.85:
        return "less energy than similar buildings"
    return "about the same as similar buildings"


def years_until(year: int | float | None) -> str:
    if year is None or pd.isna(year):
        return ""
    left = int(year) - dt.date.today().year
    if left <= 0:
        return "already in effect"
    return f"in {left} year{'s' if left > 1 else ''}"


def status_badge(status: str) -> str:
    color = STATUS_COLORS[status]
    return (f"<span style='display:inline-flex;align-items:center;gap:8px;"
            f"padding:4px 12px;border-radius:999px;border:1px solid {color};"
            f"font-weight:600'><span style='width:10px;height:10px;border-radius:50%;"
            f"background:{color}'></span>{status}</span>")


# --------------------------------------------------------------------------- #
# Navigation (a view can be opened programmatically, e.g. from a table click)
# --------------------------------------------------------------------------- #

def open_profile(building_id: int) -> None:
    """Show a building's profile on the next run."""
    st.session_state["selected_id"] = int(building_id)
    st.session_state["goto"] = "Building profile"
    # New widget keys clear the click, so returning to the list does not reopen it.
    st.session_state["click_nonce"] = st.session_state.get("click_nonce", 0) + 1
    st.rerun()


def _keep_a_view_selected() -> None:
    # Clicking the selected segment again would deselect it; keep the last view instead.
    if st.session_state.get("view") is None:
        st.session_state["view"] = st.session_state.get("last_view", VIEWS[0])
    st.session_state["last_view"] = st.session_state["view"]


def navigation() -> str:
    """View switcher. A pending ``goto`` (e.g. from a table click) is applied first."""
    if "goto" in st.session_state:
        st.session_state["view"] = st.session_state.pop("goto")
    st.session_state.setdefault("view", VIEWS[0])
    st.segmented_control("View", VIEWS, key="view", label_visibility="collapsed",
                         on_change=_keep_a_view_selected)
    return st.session_state["view"] or VIEWS[0]


# --------------------------------------------------------------------------- #
# Sidebar
# --------------------------------------------------------------------------- #

FILTER_KEYS = ["f_query", "f_status", "f_type", "f_hood", "f_deadline", "f_size"]


def reset_filters() -> None:
    for k in FILTER_KEYS:
        st.session_state.pop(k, None)


def filters(b: pd.DataFrame) -> pd.DataFrame:
    """'Find buildings' sidebar; returns the matching buildings."""
    sb = st.sidebar
    sb.header("Find buildings")
    query = sb.text_input("Search by name or address", key="f_query",
                          placeholder="e.g. Denny, 5th Ave")
    status = sb.multiselect(
        "Status", STATUS_ORDER, key="f_status", placeholder="All statuses",
        help="\n\n".join(f"**{s}:** {n}" for s, n in STATUS_NOTES.items()))
    types = sb.multiselect("Building type", list(dict.fromkeys(BUILDING_TYPE.values())),
                           key="f_type", placeholder="All types")
    hoods = sb.multiselect("Neighborhood", sorted(b["neighborhood_label"].unique()),
                           key="f_hood", placeholder="All neighborhoods")
    deadlines = sb.multiselect(
        "First deadline", sorted(b["deadline"].dropna().astype(int).unique()),
        key="f_deadline", placeholder="Any year",
        help="The first year the building must meet a limit. Larger buildings come first.")
    sizes = sb.multiselect("Building size", [label for _, label in SIZE_GROUPS],
                           key="f_size", placeholder="Any size",
                           help="Floor area, excluding parking.")
    sb.button("Reset filters", on_click=reset_filters, width="stretch")

    m = pd.Series(True, index=b.index)
    if query:
        q = query.lower()
        m &= (b["building_name"].str.lower().str.contains(q, regex=False, na=False)
              | b["address"].str.lower().str.contains(q, regex=False, na=False))
    for selected, col in [(status, "status"), (types, "type_label"),
                          (hoods, "neighborhood_label"), (deadlines, "deadline"),
                          (sizes, "size_group")]:
        if selected:
            m &= b[col].isin(selected)
    sb.caption(f"Showing **{int(m.sum()):,}** of {len(b):,} buildings")
    return b[m]


# --------------------------------------------------------------------------- #
# Overview
# --------------------------------------------------------------------------- #

def bar_chart(df: pd.DataFrame, category: str, value: str, title: str,
              colors: dict[str, str] | None = None, horizontal: bool = True,
              sort: list | None = None) -> alt.Chart:
    """A simple labeled bar chart with tooltips (one series, or colored by status)."""
    if horizontal:
        cat = alt.Y(f"{category}:N", sort=sort, title=None,
                    axis=alt.Axis(labelLimit=260, ticks=False, domain=False))
        val = alt.X(f"{value}:Q", title=None, axis=None)
    else:
        cat = alt.X(f"{category}:O", sort=sort, title=None,
                    axis=alt.Axis(labelAngle=0, ticks=False))
        val = alt.Y(f"{value}:Q", title=None, axis=None)
    color = (alt.Color(f"{category}:N", legend=None,
                       scale=alt.Scale(domain=list(colors), range=list(colors.values())))
             if colors else alt.value(SERIES))
    base = alt.Chart(df).encode(cat, val, tooltip=[alt.Tooltip(f"{category}:N", title=title),
                                                   alt.Tooltip(f"{value}:Q", format=",")])
    bars = base.mark_bar(cornerRadiusEnd=4, size=22 if horizontal else 28).encode(color=color)
    labels = base.mark_text(align="left" if horizontal else "center",
                            dx=6 if horizontal else 0, dy=0 if horizontal else -8,
                            color=INK_SECONDARY).encode(text=alt.Text(f"{value}:Q", format=","))
    height = 44 * len(df) if horizontal else 220
    return (bars + labels).properties(height=height)


def overview(b: pd.DataFrame) -> None:
    """The story on one screen: headline numbers, three charts, three takeaways."""
    st.markdown(
        "Seattle's large buildings must meet two climate laws: a **city limit on "
        "greenhouse-gas emissions** (from 2031) and a **state limit on energy use** "
        "(from 2026). Missing them can cost owners up to $10 per square foot. This "
        "dashboard shows who is off track, by how much, and what is likely to fix it.")

    action = b[b["status"] == "Action needed"]
    review = b[b["status"] == "Data needs review"]
    excess = b["beps_excess_tco2e_yr"].fillna(0).sum()
    over_city = b["beps_over_2031"].fillna(False).astype(bool)
    typical_cost = b.loc[over_city, "beps_acp_est"].median()

    c = st.columns(4)
    c[0].metric("Buildings that need action", f"{len(action):,}",
                help=STATUS_NOTES["Action needed"])
    c[1].metric("Emissions over the limit", f"{round_to(excess, 1000):,} t",
                help="Tonnes of CO2 a year above the buildings' 2031 city limits.")
    c[1].caption(f"Tonnes of CO2 a year, about {round_to(excess / TONNES_PER_CAR, 100):,} "
                 "cars' worth")
    c[2].metric("Typical cost if nothing changes",
                fmt_money(typical_cost) if over_city.any() else "-",
                help="Median estimated payment per building over the city limit for "
                     "2031-2035, if its performance stays the same. Owners can choose "
                     "this payment instead of meeting the limit. An estimate, not a "
                     "penalty notice.")
    c[3].metric("Data needs review", f"{len(review):,}",
                help=STATUS_NOTES["Data needs review"])

    left, mid, right = st.columns(3)
    with left:
        st.markdown("**Buildings by status**")
        counts = (b["status"].value_counts().reindex(STATUS_ORDER).fillna(0).astype(int)
                  .rename_axis("Status").reset_index(name="Buildings"))
        counts = counts[counts["Buildings"] > 0]
        st.altair_chart(bar_chart(counts, "Status", "Buildings", "Status",
                                  colors={s: STATUS_COLORS[s] for s in counts["Status"]},
                                  sort=STATUS_ORDER), width="stretch")
    with mid:
        st.markdown("**Recommended next step**")
        st.caption("Buildings that need action")
        steps = (action["next_step"].value_counts().rename_axis("Next step")
                 .reset_index(name="Buildings"))
        if len(steps):
            st.altair_chart(bar_chart(steps, "Next step", "Buildings", "Next step",
                                      sort="-x"), width="stretch")
    with right:
        st.markdown("**When the first deadline hits**")
        st.caption("Buildings that need action. 2026-2028: state energy limit; "
                   "2031-2035: city emissions limit.")
        years = (action["deadline"].dropna().astype(int).value_counts().sort_index()
                 .rename_axis("Year").reset_index(name="Buildings"))
        if len(years):
            st.altair_chart(bar_chart(years, "Year", "Buildings", "Year", horizontal=False),
                            width="stretch")

    st.markdown("#### Key takeaways")
    for line in takeaways(b, action, review, excess):
        st.markdown(f"- {line}")

    with st.expander("How to use this dashboard"):
        st.markdown(
            "- **Priority list:** buildings that need action, most urgent first, plus the "
            "buildings whose data should be checked first. Click a row to open its "
            "profile.\n"
            "- **Map:** every building, colored by status. Click a dot to open its "
            "profile.\n"
            "- **Building profile:** one building in plain language: how it compares, "
            "its deadline, the likely fix, and its history.\n"
            "- **Find buildings** (left panel): search and filter. Every view, including "
            "this one, shows only the buildings that match.")


def takeaways(b: pd.DataFrame, action: pd.DataFrame, review: pd.DataFrame,
              excess: float) -> list[str]:
    """Three plain-language sentences, computed from the buildings shown."""
    lines = []
    if len(b):
        lines.append(
            f"**{len(action):,} of {len(b):,} buildings** shown need action. Together, "
            f"the buildings shown emit about **{round_to(excess, 1000):,} tonnes of CO2 a "
            f"year** more than their limits allow, roughly "
            f"{round_to(excess / TONNES_PER_CAR, 100):,} cars' worth.")
    over_city = action[action["beps_over_2031"].fillna(False).astype(bool)]
    if len(over_city):
        gas = 100 * over_city["gas_emission_share"].median()
        enough = int((over_city["pathway"] == "efficiency_may_suffice").sum())
        lines.append(
            f"For a typical building over the city limit, **{gas:.0f}% of its emissions "
            f"come from burning natural gas**. Energy upgrades alone could close the gap "
            f"for {enough:,} of {len(over_city):,}; most will need to replace gas "
            f"equipment.")
    if len(review) and excess > 0:
        share = 100 * review["beps_excess_tco2e_yr"].fillna(0).sum() / excess
        lines.append(
            f"**{len(review):,} buildings account for {share:.0f}% of the emissions over "
            f"the limit**, but their reported numbers don't add up. Checking those records "
            f"first has the biggest payoff.")
    return lines


# --------------------------------------------------------------------------- #
# Priority list
# --------------------------------------------------------------------------- #

def priority_list(b: pd.DataFrame) -> None:
    """Ranked buildings that need action, then buildings whose data needs review."""
    nonce = st.session_state.get("click_nonce", 0)
    action = b[b["status"] == "Action needed"].sort_values("action_rank")
    st.subheader("Buildings that need action")
    st.caption(
        "Most urgent first. Priority (0-100) combines how far the building is over its "
        "limit, how soon its deadline is, how much more energy it uses than similar "
        "buildings, and how reliable its data is. **Click a row to open the building's "
        "profile.**")
    if action.empty:
        st.info("No buildings that need action match the current filters.")
    else:
        # Data reliability is always High here (others go to "Data needs review").
        # Decision columns first, sized to fit; type and neighborhood after (scroll).
        table = pd.DataFrame({
            "#": action["action_rank"],
            "Building": action["building_name"],
            "Priority": action["priority_score"],
            "Recommended next step": action["next_step"],
            "Deadline": action["deadline"],
            "Cut needed": action["required_reduction_pct"],
            "Est. cost if unchanged": action["beps_acp_est"].map(
                lambda x: fmt_money(x) if x > 0 else "-"),
            "Energy vs. similar": action["eui_ratio"],
            "Type": action["use1_type"],
            "Neighborhood": action["neighborhood_label"],
        })
        event = st.dataframe(
            table, hide_index=True, width="stretch", height=460,
            on_select="rerun", selection_mode="single-row", key=f"action_{nonce}",
            column_config={
                "#": st.column_config.NumberColumn(width=40),
                "Building": st.column_config.TextColumn(width=210),
                "Priority": st.column_config.ProgressColumn(
                    min_value=0, max_value=100, format="%.0f", width=85,
                    help="0-100: how far over the limit, how soon the deadline, energy "
                         "use vs. similar buildings, and data reliability."),
                "Recommended next step": st.column_config.TextColumn(width=240),
                "Deadline": st.column_config.NumberColumn(
                    format="%d", width=70, help="First year the building must meet a "
                                                "limit."),
                "Cut needed": st.column_config.NumberColumn(
                    format="%.0f%%", width=85, help="How much the building must cut its "
                                                    "emissions to meet its 2031 city limit."),
                "Energy vs. similar": st.column_config.NumberColumn(
                    format="%.1fx", width=120, help="Energy use compared with similar "
                                                   "buildings. 1.0x = the same; 2.0x = "
                                                   "twice as much."),
                "Est. cost if unchanged": st.column_config.TextColumn(
                    width=120, help="Estimated payment for 2031-2035 if nothing changes. "
                                    "An estimate, not a penalty notice."),
            },
        )
        if event and event.selection.rows:
            open_profile(action.iloc[event.selection.rows[0]]["building_id"])
        st.download_button("Download this list (CSV)", table.to_csv(index=False),
                           "buildings_that_need_action.csv", "text/csv")

    review = b[b["status"] == "Data needs review"].sort_values(
        "beps_excess_tco2e_yr", ascending=False)
    st.subheader("Data needs review")
    st.caption(
        "These buildings' reported numbers don't add up, so they are not ranked yet. "
        "Confirm the details below with the owner first: the numbers may hide a large "
        "problem, or none at all. Click a row to open the building's profile.")
    if review.empty:
        st.info("No buildings with data to review match the current filters.")
        return
    table = pd.DataFrame({
        "Building": review["building_name"],
        "What to check": review["verify_reason"].str.capitalize(),
        "Reported excess emissions": review["beps_excess_tco2e_yr"],
        "Neighborhood": review["neighborhood_label"],
    })
    event = st.dataframe(
        table, hide_index=True, width="stretch", height=320, on_select="rerun",
        selection_mode="single-row", key=f"review_{nonce}",
        column_config={
            "Building": st.column_config.TextColumn(width=250),
            "What to check": st.column_config.TextColumn(width=480),
            "Reported excess emissions": st.column_config.NumberColumn(
                format="%,.0f t/yr", width=120,
                help="Tonnes of CO2 a year above the 2031 limit, as reported. Large values "
                     "may be real or may be a reporting error."),
        })
    if event and event.selection.rows:
        open_profile(review.iloc[event.selection.rows[0]]["building_id"])


# --------------------------------------------------------------------------- #
# Map
# --------------------------------------------------------------------------- #

def map_view(b: pd.DataFrame) -> None:
    """Every building, colored by status and sized by emissions over the limit."""
    st.caption("Each dot is a building. **Color shows its status**; larger dots emit more "
               "over their limit. Hover for details, **click a dot to open its profile.**")
    pts = b.dropna(subset=["latitude", "longitude"]).copy()
    if pts.empty:
        st.info("No buildings match the current filters.")
        return
    # Draw on-track buildings first (underneath), smaller and fainter, so the buildings
    # that need attention are not buried under 2,000 gray dots.
    pts = pts.assign(_order=pts["status"].map({s: i for i, s in
                                               enumerate(reversed(STATUS_ORDER))}))
    pts = pts.sort_values("_order")
    on_track = pts["status"] == "On track"
    pts["color"] = [[*hex_to_rgb(STATUS_COLORS[s]), 90 if quiet else 220]
                    for s, quiet in zip(pts["status"], on_track, strict=True)]
    excess = pts["beps_excess_tco2e_yr"].fillna(0)
    pts["radius"] = np.where(on_track, 18, 25 + 6 * np.sqrt(excess.clip(upper=1500)))
    pts["over_txt"] = excess.map(lambda x: f"{x:,.0f} t CO2/yr over the limit" if x > 0
                                 else "Within the city limit")
    layer = pdk.Layer(
        "ScatterplotLayer", pts, id="buildings", get_position=["longitude", "latitude"],
        get_fill_color="color", get_radius="radius", radius_min_pixels=4,
        radius_max_pixels=40, pickable=True, stroked=True, get_line_color=[255, 255, 255],
        line_width_min_pixels=1,
    )
    tooltip = {"html": "<b>{building_name}</b><br/>{status}<br/>{next_step}<br/>{over_txt}",
               "style": {"fontSize": "13px"}}
    deck = pdk.Deck(layers=[layer], tooltip=tooltip, map_style=None,
                    initial_view_state=pdk.ViewState(latitude=47.62, longitude=-122.33,
                                                     zoom=11))
    nonce = st.session_state.get("click_nonce", 0)
    event = st.pydeck_chart(deck, width="stretch", on_select="rerun",
                            selection_mode="single-object", key=f"map_{nonce}")
    picked = (event.selection.get("objects", {}).get("buildings", [])
              if event and event.selection else [])
    if picked:
        open_profile(picked[0]["building_id"])
    legend = "".join(
        f"<span style='display:inline-flex;align-items:center;margin:0 18px 6px 0'>"
        f"<span style='width:11px;height:11px;border-radius:50%;background:{c};"
        f"margin-right:7px'></span><b>{s}</b>&nbsp;({int((pts['status'] == s).sum()):,})"
        f"<span style='color:{INK_SECONDARY}'>&nbsp;· {STATUS_NOTES[s]}</span></span>"
        for s, c in STATUS_COLORS.items() if (pts["status"] == s).any())
    st.markdown(legend, unsafe_allow_html=True)


# --------------------------------------------------------------------------- #
# Building profile
# --------------------------------------------------------------------------- #

def history_chart(h: pd.DataFrame, value: str, y_title: str, tooltip_title: str,
                  ref_value: float | None, ref_label: str) -> alt.Chart:
    """One series by year, with an optional labeled dashed reference line."""
    base = alt.Chart(h).encode(x=alt.X("data_year:O", title=None,
                                       axis=alt.Axis(labelAngle=0)))
    line = base.mark_line(color=SERIES, strokeWidth=2).encode(
        y=alt.Y(f"{value}:Q", title=y_title, scale=alt.Scale(zero=True)))
    points = base.mark_point(color=SERIES, filled=True, size=64).encode(
        y=f"{value}:Q",
        tooltip=[alt.Tooltip("data_year:O", title="Year"),
                 alt.Tooltip(f"{value}:Q", title=tooltip_title, format=".2f")])
    layers = [line, points]
    if ref_value is not None and not pd.isna(ref_value):
        ref = pd.DataFrame({"y": [ref_value], "label": [ref_label]})
        layers.append(alt.Chart(ref).mark_rule(color=REFERENCE, strokeDash=[4, 4],
                                               strokeWidth=2).encode(y="y:Q"))
        layers.append(alt.Chart(ref).mark_text(align="left", dx=4, dy=-7,
                                               color=INK_SECONDARY).encode(
            y="y:Q", x=alt.value(0), text="label:N"))
    return alt.layer(*layers).properties(height=250)


def building_profile(b_all: pd.DataFrame, history: pd.DataFrame) -> None:
    """One building in plain language: status, next step, deadline, numbers, history."""
    options = b_all.sort_values("building_name")
    labels = dict(zip(options["building_id"],
                      options["building_name"] + " - " + options["address"], strict=True))
    ids = list(labels)
    default = st.session_state.get("selected_id", int(
        b_all.sort_values("action_rank")["building_id"].iloc[0]))
    bid = st.selectbox("Choose a building (type to search)", ids,
                       index=ids.index(default) if default in ids else 0,
                       format_func=lambda i: labels[i])
    st.session_state["selected_id"] = bid
    r = b_all.set_index("building_id").loc[bid]

    st.markdown(f"## {r['building_name']}")
    built = f" · built {int(r['year_built'])}" if pd.notna(r["year_built"]) else ""
    st.caption(f"{r['address']} · {r['neighborhood_label']} · {r['use1_type']} · "
               f"{r['area_sf']:,.0f} sq ft{built} · latest data {r['data_year']}")
    rank = (f" &nbsp;·&nbsp; Priority **{r['priority_score']:.0f}/100**, "
            f"#{int(r['action_rank'])} of {int(b_all['action_rank'].max()):,}"
            if pd.notna(r["action_rank"]) else "")
    st.markdown(f"{status_badge(r['status'])} &nbsp; {r['status_note']}{rank}",
                unsafe_allow_html=True)
    st.write("")

    left, right = st.columns([2, 1])
    with left, st.container(border=True):
        st.markdown(f"**Recommended next step: {r['next_step']}**")
        st.write(r["next_step_detail"])
        if r["verify_reason"]:
            st.write(f"What to check: {r['verify_reason']}.")
    with right, st.container(border=True):
        st.markdown("**First deadline**")
        if pd.notna(r["deadline"]):
            st.markdown(f"### {int(r['deadline'])}")
            st.caption(years_until(r["deadline"]).capitalize())
        else:
            st.write("No deadline applies.")

    over_city = bool(r["beps_over_2031"])
    ratio = r["emissions_vs_limit"]
    cut = r["required_reduction_pct"]
    c = st.columns(4)
    c[0].metric("Emissions vs. its limit",
                "No limit" if pd.isna(ratio) else f"{ratio:.1f}x",
                help="Emissions per square foot compared with the building's 2031-2035 "
                     "city limit. 1.0x is exactly at the limit; above 1.0x is over it.")
    c[0].caption("over the limit" if over_city else "within the limit"
                 if pd.notna(ratio) else "")
    c[1].metric("Energy vs. similar buildings",
                "n/a" if pd.isna(r["eui_ratio"]) else f"{r['eui_ratio']:.1f}x",
                help="Compared with buildings with the same mix of uses and size. "
                     "1.0x is the same; 2.0x is twice as much.")
    c[1].caption(peer_words(r["eui_ratio"]))
    c[2].metric("Emissions cut needed",
                "None" if not over_city else "Under 1%" if cut < 1 else f"{cut:.0f}%",
                help="How much the building must cut its emissions to meet its 2031 "
                     "city limit.")
    c[3].metric("Cost if unchanged (est.)",
                fmt_money(r["beps_acp_est"]) if over_city else "$0",
                help="Estimated payment for 2031-2035 if performance stays the same. "
                     "Owners can choose this instead of meeting the limit. An estimate, "
                     "not a penalty notice.")

    st.markdown("**In plain words**")
    checked = ("Every number in this summary is automatically checked against the data."
               if r["explanation_checked"] else "This summary could not be fully checked.")
    st.info(f"{r['explanation']}\n\n*{checked}*")

    h = history[history["building_id"] == bid]
    left, right = st.columns(2)
    with left:
        st.markdown("**Energy use over time**")
        st.altair_chart(history_chart(
            h, "site_eui_wn", "Energy per sq ft (kBtu)", "Energy per sq ft (kBtu)",
            r["expected_site_eui_wn"], "Typical for similar buildings"), width="stretch")
        st.caption("Adjusted for weather, so a cold year does not look like waste.")
    with right:
        st.markdown("**Emissions over time**")
        st.altair_chart(history_chart(
            h, "ghgi_2031_factors", "Emissions per sq ft (kg CO2)",
            "Emissions per sq ft (kg CO2)", r["beps_target_2031"], "2031 limit"),
            width="stretch")
        st.caption("Calculated with the 2031 rules, so every year compares fairly "
                   "with the limit.")

    left, right = st.columns(2)
    with left:
        st.markdown("**Where its emissions come from**")
        shares = pd.DataFrame({
            "Source": ["Natural gas", "District steam", "Electricity"],
            "Share": [r["gas_emission_share"], r["steam_emission_share"],
                      r["electric_emission_share"]],
        })
        st.dataframe(shares, hide_index=True, width="stretch", column_config={
            "Share": st.column_config.ProgressColumn(min_value=0, max_value=1,
                                                     format="percent")})
    with right:
        st.markdown("**Data reliability**")
        st.markdown(f"### {r['reliability']}")
        st.caption("High means the reported numbers are consistent with each other and "
                   "with past years. Medium or Low means some numbers should be confirmed.")


# --------------------------------------------------------------------------- #
# Page
# --------------------------------------------------------------------------- #

def footer(run: dict) -> None:
    built = ""
    if run.get("finished_at"):
        built = f", updated {run['finished_at'][:10]}"
    years = f"2015-{run['latest_data_year']}" if run.get("latest_data_year") else ""
    method = (f" [How the numbers are made]({REPO_URL})." if REPO_URL
              else " How the numbers are made is described in the project README.")
    st.divider()
    st.caption(
        f"Data: City of Seattle building energy disclosures, {years}{built}. "
        "Dollar figures are estimates that assume performance stays the same; they are not "
        "official compliance determinations. Statuses mean \"worth a look\", not "
        f"wrongdoing.{method}")


def main() -> None:
    ensure_data()
    buildings, history, run = load(data_version())
    st.title(TITLE)
    st.markdown(SUBTITLE)
    shown = filters(buildings)
    view = navigation()
    if view == "Overview":
        overview(shown)
    elif view == "Priority list":
        priority_list(shown)
    elif view == "Map":
        map_view(shown)
    else:
        building_profile(buildings, history)
    footer(run)


main()

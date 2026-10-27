from __future__ import annotations

import pytest

from beps_prioritizer.config import APP_BUILDINGS_PARQUET, APP_HISTORY_PARQUET, PROJECT_ROOT

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = str(PROJECT_ROOT / "app" / "streamlit_app.py")

pytestmark = pytest.mark.skipif(
    not (APP_BUILDINGS_PARQUET.exists() and APP_HISTORY_PARQUET.exists()),
    reason="app data not built (run `make run`)",
)


def _app() -> AppTest:
    return AppTest.from_file(APP, default_timeout=120).run()


def _show(at: AppTest, view: str) -> AppTest:
    at.session_state["goto"] = view
    return at.run()


def test_overview_renders_plain_numbers() -> None:
    at = _app()
    assert not at.exception
    assert at.title[0].value == "Seattle Building Climate Compliance Dashboard"
    metrics = {m.label: m.value for m in at.metric}
    assert int(metrics["Buildings that need action"].replace(",", "")) > 0
    assert metrics["Emissions over the limit"].endswith(" t")  # tonnes; unit in caption


@pytest.mark.parametrize("view", ["Priority list", "Map", "Building profile"])
def test_every_view_renders(view: str) -> None:
    at = _show(_app(), view)
    assert not at.exception


def test_filters_apply_to_overview() -> None:
    at = _app()
    at.sidebar.multiselect[0].set_value(["On track"]).run()
    assert not at.exception
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Buildings that need action"] == "0"


def test_profile_for_selected_building() -> None:
    at = _app()
    at.session_state["selected_id"] = 1
    at = _show(at, "Building profile")
    assert not at.exception
    assert any("Recommended next step" in m.value for m in at.markdown)

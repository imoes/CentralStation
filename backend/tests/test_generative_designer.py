from app.services.dashboard.generative_designer import (
    _DEFAULT_SIZE,
    _ensure_forecast_widgets,
    _fallback_widgets,
    _validate_widgets,
    semantic_widget_key,
)
from app.services.dashboard.generative_persistence import _generation_is_obsolete


def _situation(*, incidents=False, forecasts=0):
    candidates = [
        {
            "host": f"db-{idx}.example.test",
            "service": "Filesystem /",
            "metric_id": "fs_used_percent",
            "label": "Disk",
        }
        for idx in range(forecasts)
    ]
    return {
        "open_incidents": ([{"id": "inc-1"}] if incidents else []),
        "forecast_candidates": candidates,
        "vitals": [dict(candidate) for candidate in candidates],
        "severity_summary": "critical" if incidents else "none",
    }


def test_incidents_are_a_supported_widget_type():
    widgets = _validate_widgets(
        [{"type": "incidents", "title": "Open incidents", "config": {"limit": 500}}],
        _situation(incidents=True),
    )

    assert len(widgets) == 1
    assert widgets[0]["widget_type"] == "incidents"
    assert widgets[0]["config"]["limit"] == 20


def test_final_validation_deduplicates_facts_and_respects_hard_budget():
    situation = _situation(incidents=True, forecasts=2)
    raw = [
        {"type": "incidents", "title": "Incidents", "config": {}},
        {"type": "incidents", "title": "Incidents again", "config": {}},
        {"type": "ai_summary", "title": "Second briefing", "config": {}},
        {"type": "war_room", "title": "Duplicate incident detail", "config": {}},
        {"type": "list", "title": "Incident members", "config": {"query_string": "NOT status:resolved"}},
        {"type": "stat", "title": "Critical", "config": {"query_string": "severity:critical"}},
        {"type": "stat", "title": "Critical duplicate", "config": {"query_string": "severity:critical"}},
    ]
    specs = _validate_widgets(raw, situation, enforce_budget=False)
    specs = _ensure_forecast_widgets(specs, situation, "en")
    widgets = _validate_widgets(specs, situation)

    keys = [semantic_widget_key(widget) for widget in widgets]
    assert len(keys) == len(set(keys))
    assert "ai_summary" not in {widget["widget_type"] for widget in widgets}
    assert "war_room" not in {widget["widget_type"] for widget in widgets}
    assert "list" not in {widget["widget_type"] for widget in widgets}
    assert sum(
        _DEFAULT_SIZE[widget["widget_type"]][0] * _DEFAULT_SIZE[widget["widget_type"]][1]
        for widget in widgets
    ) <= 110


def test_forecast_replaces_timeseries_for_same_metric():
    situation = _situation(forecasts=1)
    candidate = situation["forecast_candidates"][0]
    widgets = _validate_widgets([
        {"type": "timeseries", "title": "Disk history", "config": candidate},
        {"type": "forecast", "title": "Disk forecast", "config": candidate},
    ], situation)

    assert [widget["widget_type"] for widget in widgets] == ["forecast"]


def test_fallback_passes_the_same_final_validator():
    situation = _situation(incidents=True, forecasts=2)
    fallback, _ = _fallback_widgets(situation, "de")
    final = _validate_widgets(_ensure_forecast_widgets(fallback, situation, "de"), situation)

    assert len(final) > 0
    assert sum(
        _DEFAULT_SIZE[widget["widget_type"]][0] * _DEFAULT_SIZE[widget["widget_type"]][1]
        for widget in final
    ) <= 110


def test_generation_started_before_source_invalidation_is_rejected():
    current = {
        "result_state": "refresh_pending",
        "invalidated_at": "2026-09-17T11:39:00+00:00",
    }

    assert _generation_is_obsolete(current, {"as_of": "2026-09-17T11:38:00+00:00"}) is True
    assert _generation_is_obsolete(current, {"as_of": "2026-09-17T11:40:00+00:00"}) is False

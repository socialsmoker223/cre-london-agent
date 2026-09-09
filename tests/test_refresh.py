from london_monitor.models import Metric
from london_monitor.refresh import _metric_changes


def metric(source_id, value, *, unit="%", definition="vacancy"):
    return Metric(
        metric="vacancy", value=value, unit=unit, period="2026-Q1",
        submarket="City", source_id=source_id, definition=definition,
    )


def test_metric_revision_includes_both_source_ids():
    changes = _metric_changes([metric("old", 8)], [metric("new", 9)])
    assert len(changes) == 1
    assert "old -> new" in changes[0]


def test_metric_conflict_and_incompatible_definition():
    changes = _metric_changes(
        [metric("old", 8)],
        [metric("a", 9), metric("b", 10), metric("different", 7, definition="different")],
    )
    assert any(item.startswith("Conflict:") and "a, b" in item for item in changes)
    assert not any("old -> different" in item for item in changes)

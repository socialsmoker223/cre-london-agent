import sqlite3
from datetime import date

import pytest

from london_monitor.db import Database
from london_monitor.models import Metric, MetricQuery, Project, Source


def source(source_id: str) -> Source:
    return Source(
        id=source_id,
        title=source_id,
        publisher="test",
        published_at=date(2026, 1, 1),
        checksum=source_id,
    )


def metric(source_id: str, period: str, value: float, metric: str = "vacancy") -> Metric:
    return Metric(
        metric=metric,
        value=value,
        unit="%",
        period=period,
        submarket="City",
        source_id=source_id,
    )


def test_metrics_latest_period_preserves_conflicting_sources_and_explicit_history() -> None:
    db = Database()
    db.add_source(source("a"))
    db.add_source(source("b"))
    db.add_metrics(
        [
            metric("a", "2025-Q4", 9),
            metric("a", "2026-Q1", 8),
            metric("b", "2026-Q1", 7),
        ]
    )

    latest = db.query_metrics(MetricQuery(submarkets=["City"], metrics=["vacancy"]))
    assert {item.value for item in latest} == {8, 7}
    history = db.query_metrics(MetricQuery(period="2025-Q4", latest=False))
    assert [item.value for item in history] == [9]


def test_filters_projects_and_additions_are_idempotent() -> None:
    db = Database()
    db.add_source(source("a"))
    item = metric("a", "2026-Q1", 8)
    db.add_metrics([item, item])
    project = Project(
        name="House",
        submarket="City",
        status="planned",
        completion_date=date(2027, 1, 1),
        size_sq_ft=100,
        prelet_status="none",
        source_id="a",
    )
    db.add_projects([project, project])
    assert len(db.query_metrics(MetricQuery())) == 1
    assert db.get_projects(["West End"]) == []
    assert len(db.get_projects(["City"])) == 1


def test_source_provenance_is_retained() -> None:
    db = Database()
    item = source("a")
    assert db.add_source(item)
    assert not db.add_source(item)
    assert db.list_sources()[0].id == "a"


def test_metric_batch_rolls_back_on_foreign_key_error() -> None:
    db = Database()
    db.add_source(source("a"))
    with pytest.raises(sqlite3.IntegrityError):
        db.add_metrics([metric("a", "2026-Q1", 8), metric("missing", "2026-Q1", 7)])
    assert db.query_metrics(MetricQuery(latest=False)) == []

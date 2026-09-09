import sqlite3
from datetime import UTC, date, datetime

import pytest

from london_monitor.db import Database
from london_monitor.models import Metric, MetricQuery, Project, RefreshResult, Source


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


def test_documents_refresh_and_unknown_date_roundtrip(tmp_path) -> None:
    db = Database(tmp_path / "market.sqlite")
    undated = Source(id="undated", title="u", publisher="p", checksum="same")
    assert db.save_document(undated, "full text")
    assert db.get_document("undated").source.published_at is None
    result = RefreshResult(
        run_id="run", started_at=datetime.now(UTC), completed_at=datetime.now(UTC),
        status="complete", baseline=False, briefing="ok"
    )
    db.save_refresh(result)
    db.close()
    reopened = Database(tmp_path / "market.sqlite")
    assert reopened.latest_successful_refresh().run_id == "run"
    assert reopened.list_documents()[0].text == "full text"


def test_latest_metrics_prefer_newest_source_version() -> None:
    db = Database()
    first = Source(
        id="old", title="old", publisher="p", published_at=date(2026, 1, 1),
        checksum="a", canonical_url="https://x.test/a"
    )
    newer = Source(
        id="new", title="new", publisher="p", published_at=date(2026, 1, 1),
        checksum="b", canonical_url="https://x.test/a"
    )
    db.add_source(first)
    db.add_source(newer)
    db.add_metrics([metric("old", "2026-Q1", 1), metric("new", "2026-Q1", 2)])
    assert [item.value for item in db.query_metrics(MetricQuery())] == [2]


def test_latest_period_ignores_superseded_version() -> None:
    db = Database()
    old = Source(
        id="old-period", title="old", publisher="p", published_at=date(2026, 1, 1),
        checksum="old-period", canonical_url="https://x.test/period"
    )
    new = Source(
        id="new-period", title="new", publisher="p", published_at=date(2026, 1, 1),
        checksum="new-period", canonical_url="https://x.test/period"
    )
    db.add_source(old)
    db.add_source(new)
    db.add_metrics([metric(old.id, "2026-Q2", 9), metric(new.id, "2026-Q1", 8)])
    assert [item.period for item in db.query_metrics(MetricQuery())] == ["2026-Q1"]


def test_metric_reports_with_distinct_units_and_definitions_are_retained() -> None:
    db = Database()
    db.add_source(source("multi"))
    db.add_metrics([
        metric("multi", "2026-Q1", 8),
        Metric(metric="vacancy", value=8, unit="bps", period="2026-Q1", submarket="City",
               source_id="multi", definition="different"),
    ])
    assert len(db.query_metrics(MetricQuery(latest=False))) == 2


def test_legacy_schema_migrates_foreign_keys_and_nullable_dates(tmp_path) -> None:
    path = tmp_path / "legacy.sqlite"
    connection = sqlite3.connect(path)
    connection.executescript(
        """CREATE TABLE sources (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, publisher TEXT NOT NULL, url TEXT,
            published_at TEXT NOT NULL, retrieved_at TEXT NOT NULL, source_type TEXT NOT NULL,
            checksum TEXT NOT NULL UNIQUE, demo INTEGER NOT NULL);
        CREATE TABLE metrics (
            id INTEGER PRIMARY KEY, metric TEXT NOT NULL, value REAL NOT NULL, unit TEXT NOT NULL,
            period TEXT NOT NULL, submarket TEXT NOT NULL,
            source_id TEXT NOT NULL REFERENCES sources(id));
        CREATE TABLE projects (
            id INTEGER PRIMARY KEY, name TEXT, submarket TEXT, status TEXT, completion_date TEXT,
            size_sq_ft REAL, prelet_status TEXT, source_id TEXT REFERENCES sources(id));
        INSERT INTO sources VALUES ('old','Old','P',NULL,'2026-01-01',
            '2026-01-01T00:00:00','text','old',0);
        INSERT INTO metrics(metric,value,unit,period,submarket,source_id)
            VALUES ('vacancy',8,'%','2026-Q1','City','old');
        INSERT INTO projects(name,source_id) VALUES ('Project','old');"""
    )
    connection.commit()
    connection.close()
    db = Database(path)
    assert db.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert db.connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert db.add_source(Source(id="new", title="New", publisher="P", checksum="old"))
    assert db.get_document("missing") is None

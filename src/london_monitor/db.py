"""SQLite storage for the authoritative market data."""

import sqlite3
from pathlib import Path

from .models import Metric, MetricQuery, Project, Source, Store, Submarket


class Database(Store):
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.connection = sqlite3.connect(str(path), check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS sources (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, publisher TEXT NOT NULL,
                url TEXT, published_at TEXT NOT NULL, retrieved_at TEXT NOT NULL,
                source_type TEXT NOT NULL, checksum TEXT NOT NULL UNIQUE, demo INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS metrics (
                id INTEGER PRIMARY KEY, metric TEXT NOT NULL, value REAL NOT NULL,
                unit TEXT NOT NULL, period TEXT NOT NULL, submarket TEXT NOT NULL,
                source_id TEXT NOT NULL REFERENCES sources(id),
                UNIQUE(metric, period, submarket, source_id)
            );
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY, name TEXT NOT NULL, submarket TEXT NOT NULL,
                status TEXT NOT NULL, completion_date TEXT NOT NULL, size_sq_ft REAL NOT NULL,
                prelet_status TEXT NOT NULL, source_id TEXT NOT NULL REFERENCES sources(id),
                UNIQUE(name, submarket, completion_date, source_id)
            );
            """
        )
        self.connection.commit()

    def add_source(self, source: Source) -> bool:
        with self.connection:
            cursor = self.connection.execute(
                """INSERT OR IGNORE INTO sources
                (id, title, publisher, url, published_at, retrieved_at, source_type, checksum, demo)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    source.id,
                    source.title,
                    source.publisher,
                    str(source.url) if source.url else None,
                    source.published_at.isoformat(),
                    source.retrieved_at.isoformat(),
                    source.source_type,
                    source.checksum,
                    int(source.demo),
                ),
            )
        return cursor.rowcount == 1

    def list_sources(self) -> list[Source]:
        rows = self.connection.execute(
            "SELECT * FROM sources ORDER BY published_at DESC, id"
        ).fetchall()
        return [self._source(row) for row in rows]

    def add_metrics(self, metrics: list[Metric]) -> None:
        with self.connection:
            self.connection.executemany(
                """INSERT OR IGNORE INTO metrics
                (metric, value, unit, period, submarket, source_id) VALUES (?, ?, ?, ?, ?, ?)""",
                [
                    (
                        item.metric,
                        item.value,
                        item.unit,
                        item.period,
                        item.submarket,
                        item.source_id,
                    )
                    for item in metrics
                ],
            )

    def query_metrics(self, query: MetricQuery) -> list[Metric]:
        clauses: list[str] = []
        params: list[object] = []
        if query.submarkets:
            clauses.append(f"submarket IN ({','.join('?' for _ in query.submarkets)})")
            params.extend(query.submarkets)
        if query.metrics:
            clauses.append(f"metric IN ({','.join('?' for _ in query.metrics)})")
            params.extend(query.metrics)
        if query.period:
            clauses.append("period = ?")
            params.append(query.period)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        if query.latest and not query.period:
            where = (
                f"{where} {'AND' if where else 'WHERE'} period = "
                "(SELECT MAX(m2.period) FROM metrics m2 "
                "WHERE m2.metric = metrics.metric AND m2.submarket = metrics.submarket)"
            )
        rows = self.connection.execute(
            f"SELECT metric, value, unit, period, submarket, source_id FROM metrics {where} "
            "ORDER BY period DESC, metric, submarket, source_id LIMIT ?",
            [*params, query.limit],
        ).fetchall()
        return [Metric.model_validate(dict(row)) for row in rows]

    def add_projects(self, projects: list[Project]) -> None:
        with self.connection:
            self.connection.executemany(
                """INSERT OR IGNORE INTO projects
                (name, submarket, status, completion_date, size_sq_ft, prelet_status, source_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        item.name,
                        item.submarket,
                        item.status,
                        item.completion_date.isoformat(),
                        item.size_sq_ft,
                        item.prelet_status,
                        item.source_id,
                    )
                    for item in projects
                ],
            )

    def get_projects(self, submarkets: list[Submarket]) -> list[Project]:
        params: list[object] = []
        where = ""
        if submarkets:
            where = f"WHERE submarket IN ({','.join('?' for _ in submarkets)})"
            params.extend(submarkets)
        rows = self.connection.execute(
            f"SELECT name, submarket, status, completion_date, size_sq_ft, prelet_status, "
            f"source_id FROM projects {where} ORDER BY completion_date, name",
            params,
        ).fetchall()
        return [Project.model_validate(dict(row)) for row in rows]

    def close(self) -> None:
        self.connection.close()

    @staticmethod
    def _source(row: sqlite3.Row) -> Source:
        return Source.model_validate(dict(row) | {"demo": bool(row["demo"])})

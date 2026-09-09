"""SQLite storage for authoritative market data."""

import sqlite3
import threading
from pathlib import Path

from .models import Document, Metric, MetricQuery, Project, RefreshResult, Source, Store, Submarket


class Database(Store):
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.connection = sqlite3.connect(str(path), check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self.connection.execute("PRAGMA foreign_keys = OFF")
        with self._lock, self.connection:
            self.connection.executescript("""CREATE TABLE IF NOT EXISTS sources (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, publisher TEXT NOT NULL, url TEXT, published_at TEXT,
 retrieved_at TEXT NOT NULL, source_type TEXT NOT NULL, checksum TEXT NOT NULL,
 demo INTEGER NOT NULL, submarket TEXT NOT NULL DEFAULT 'London',
 category TEXT NOT NULL DEFAULT 'commentary', trusted INTEGER NOT NULL DEFAULT 0,
 canonical_url TEXT);
CREATE TABLE IF NOT EXISTS metrics (
 id INTEGER PRIMARY KEY, metric TEXT NOT NULL, value REAL NOT NULL, unit TEXT NOT NULL,
 period TEXT NOT NULL, submarket TEXT NOT NULL, source_id TEXT NOT NULL REFERENCES sources(id),
 definition TEXT NOT NULL DEFAULT '', quotation TEXT NOT NULL DEFAULT '',
 UNIQUE(metric,period,submarket,source_id,unit,definition,value));
CREATE TABLE IF NOT EXISTS projects (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL, submarket TEXT NOT NULL, status TEXT NOT NULL,
 completion_date TEXT NOT NULL, size_sq_ft REAL NOT NULL, prelet_status TEXT NOT NULL,
 source_id TEXT NOT NULL REFERENCES sources(id), UNIQUE(name,submarket,completion_date,source_id));
CREATE TABLE IF NOT EXISTS documents (
 source_id TEXT PRIMARY KEY REFERENCES sources(id), text TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS refreshes (run_id TEXT PRIMARY KEY, payload TEXT NOT NULL,
 completed_at TEXT NOT NULL, status TEXT NOT NULL);""")
            self._migrate_columns()
        self.connection.execute("PRAGMA foreign_keys = ON")

    def _migrate_columns(self) -> None:
        def cols(table):
            return {row[1] for row in self.connection.execute(f"PRAGMA table_info({table})")}

        source_info = list(self.connection.execute("PRAGMA table_info(sources)"))
        source_sql = self.connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='sources'"
        ).fetchone()[0]
        legacy_date = any(row[1] == "published_at" and row[3] for row in source_info)
        legacy_checksum = "checksum TEXT NOT NULL UNIQUE" in source_sql
        if legacy_date or legacy_checksum:
            target = {row[1] for row in source_info}
            self.connection.execute("""CREATE TABLE sources_new (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, publisher TEXT NOT NULL, url TEXT,
                published_at TEXT, retrieved_at TEXT NOT NULL, source_type TEXT NOT NULL,
                checksum TEXT NOT NULL, demo INTEGER NOT NULL,
                submarket TEXT NOT NULL DEFAULT 'London',
                category TEXT NOT NULL DEFAULT 'commentary', trusted INTEGER NOT NULL DEFAULT 0,
                canonical_url TEXT)""")
            columns = [name for name in (
                "id", "title", "publisher", "url", "published_at", "retrieved_at",
                "source_type", "checksum", "demo", "submarket", "category", "trusted",
                "canonical_url"
            ) if name in target]
            names = ",".join(columns)
            self.connection.execute(
                f"INSERT INTO sources_new ({names}) SELECT {names} FROM sources"
            )
            self.connection.execute("DROP TABLE sources")
            self.connection.execute("ALTER TABLE sources_new RENAME TO sources")
        for name, definition in {
            "submarket": "TEXT NOT NULL DEFAULT 'London'",
            "category": "TEXT NOT NULL DEFAULT 'commentary'",
            "trusted": "INTEGER NOT NULL DEFAULT 0",
            "canonical_url": "TEXT",
        }.items():
            if name not in cols("sources"):
                self.connection.execute(f"ALTER TABLE sources ADD COLUMN {name} {definition}")
        for name in ("definition", "quotation"):
            if name not in cols("metrics"):
                self.connection.execute(
                    f"ALTER TABLE metrics ADD COLUMN {name} TEXT NOT NULL DEFAULT ''"
                )
        metric_sql = self.connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='metrics'"
        ).fetchone()[0]
        if "UNIQUE(metric,period,submarket,source_id)" in metric_sql:
            self.connection.execute("ALTER TABLE metrics RENAME TO metrics_legacy")
            self.connection.execute("""CREATE TABLE metrics (
                id INTEGER PRIMARY KEY, metric TEXT NOT NULL, value REAL NOT NULL,
                unit TEXT NOT NULL, period TEXT NOT NULL, submarket TEXT NOT NULL,
                source_id TEXT NOT NULL REFERENCES sources(id), definition TEXT NOT NULL DEFAULT '',
                quotation TEXT NOT NULL DEFAULT '',
                UNIQUE(metric,period,submarket,source_id,unit,definition,value))""")
            self.connection.execute("""INSERT INTO metrics
                (id,metric,value,unit,period,submarket,source_id,definition,quotation)
                SELECT id,metric,value,unit,period,submarket,source_id,definition,quotation
                FROM metrics_legacy""")
            self.connection.execute("DROP TABLE metrics_legacy")

    def add_source(self, source: Source) -> bool:
        with self._lock, self.connection:
            cur = self.connection.execute(
                "INSERT OR IGNORE INTO sources (id,title,publisher,url,published_at,retrieved_at,"
                "source_type,checksum,demo,submarket,category,trusted,canonical_url) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    source.id,
                    source.title,
                    source.publisher,
                    str(source.url) if source.url else None,
                    source.published_at.isoformat() if source.published_at else None,
                    source.retrieved_at.isoformat(),
                    source.source_type,
                    source.checksum,
                    int(source.demo),
                    source.submarket,
                    source.category,
                    int(source.trusted),
                    source.canonical_url,
                ),
            )
            return cur.rowcount == 1

    def list_sources(self) -> list[Source]:
        with self._lock:
            return [
                self._source(r)
                for r in self.connection.execute(
                    "SELECT * FROM sources ORDER BY published_at DESC,id"
                )
            ]

    def add_metrics(self, metrics: list[Metric]) -> None:
        with self._lock, self.connection:
            self.connection.executemany(
                "INSERT OR IGNORE INTO metrics "
                "(metric,value,unit,period,submarket,source_id,definition,quotation) "
                "VALUES (?,?,?,?,?,?,?,?)",
                [
                    (
                        m.metric,
                        m.value,
                        m.unit,
                        m.period,
                        m.submarket,
                        m.source_id,
                        m.definition,
                        m.quotation,
                    )
                    for m in metrics
                ],
            )

    def query_metrics(self, query: MetricQuery) -> list[Metric]:
        with self._lock:
            clauses, params = [], []
            if query.submarkets:
                clauses.append(f"m.submarket IN ({','.join('?' * len(query.submarkets))})")
                params.extend(query.submarkets)
            if query.metrics:
                clauses.append(f"m.metric IN ({','.join('?' * len(query.metrics))})")
                params.extend(query.metrics)
            if query.period:
                clauses.append("m.period=?")
                params.append(query.period)
            clauses.append(
                "(s.canonical_url IS NULL OR s.id=(SELECT s2.id FROM sources s2 "
                "WHERE s2.canonical_url=s.canonical_url ORDER BY s2.retrieved_at DESC LIMIT 1))"
            )
            if query.latest and not query.period:
                clauses.append(
                    "m.period=(SELECT MAX(m2.period) FROM metrics m2 "
                    "JOIN sources s2 ON s2.id=m2.source_id "
                    "WHERE m2.metric=m.metric AND m2.submarket=m.submarket AND "
                    "(s2.canonical_url IS NULL OR s2.id=(SELECT s3.id FROM sources s3 "
                    "WHERE s3.canonical_url=s2.canonical_url ORDER BY s3.retrieved_at DESC "
                    "LIMIT 1)))"
                )
            rows = self.connection.execute(
                "SELECT m.metric,m.value,m.unit,m.period,m.submarket,m.source_id,"
                "m.definition,m.quotation FROM metrics m JOIN sources s ON s.id=m.source_id "
                f"WHERE {' AND '.join(clauses)} ORDER BY m.period DESC,m.metric,m.submarket,"
                "m.source_id LIMIT ?",
                [*params, query.limit],
            ).fetchall()
            return [Metric.model_validate(dict(r)) for r in rows]

    def has_metrics(self, source_id: str) -> bool:
        with self._lock:
            return self.connection.execute(
                "SELECT 1 FROM metrics WHERE source_id=? LIMIT 1", (source_id,)
            ).fetchone() is not None

    def add_projects(self, projects: list[Project]) -> None:
        with self._lock, self.connection:
            self.connection.executemany(
                "INSERT OR IGNORE INTO projects "
                "(name,submarket,status,completion_date,size_sq_ft,prelet_status,source_id) "
                "VALUES (?,?,?,?,?,?,?)",
                [
                    (
                        p.name,
                        p.submarket,
                        p.status,
                        p.completion_date.isoformat(),
                        p.size_sq_ft,
                        p.prelet_status,
                        p.source_id,
                    )
                    for p in projects
                ],
            )

    def get_projects(self, submarkets: list[Submarket]) -> list[Project]:
        with self._lock:
            params = list(submarkets)
            where = f"WHERE submarket IN ({','.join('?' * len(params))})" if params else ""
            return [
                Project.model_validate(dict(r))
                for r in self.connection.execute(
                    "SELECT name,submarket,status,completion_date,size_sq_ft,prelet_status,"
                    f"source_id FROM projects {where} ORDER BY completion_date,name",
                    params,
                )
            ]

    def save_document(self, source: Source, text: str) -> bool:
        with self._lock, self.connection:
            self.add_source(source)
            cur = self.connection.execute(
                "INSERT OR IGNORE INTO documents(source_id,text) VALUES (?,?)", (source.id, text)
            )
            return cur.rowcount == 1

    def get_document(self, source_id: str) -> Document | None:
        with self._lock:
            r = self.connection.execute(
                "SELECT s.*,d.text FROM documents d JOIN sources s ON s.id=d.source_id "
                "WHERE d.source_id=?",
                (source_id,),
            ).fetchone()
            return Document(source=self._source(r), text=r["text"]) if r else None

    def list_documents(self) -> list[Document]:
        with self._lock:
            return [
                Document(source=self._source(r), text=r["text"])
                for r in self.connection.execute(
                    "SELECT s.*,d.text FROM documents d JOIN sources s ON s.id=d.source_id "
                    "ORDER BY s.published_at DESC,s.id"
                )
            ]

    def save_refresh(self, result: RefreshResult) -> None:
        with self._lock, self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO refreshes(run_id,payload,completed_at,status) "
                "VALUES (?,?,?,?)",
                (
                    result.run_id,
                    result.model_dump_json(),
                    result.completed_at.isoformat(),
                    result.status,
                ),
            )

    def _refresh(self, where=""):
        r = self.connection.execute(
            f"SELECT payload FROM refreshes {where} ORDER BY completed_at DESC LIMIT 1"
        ).fetchone()
        return RefreshResult.model_validate_json(r[0]) if r else None

    def latest_refresh(self):
        with self._lock:
            return self._refresh()

    def latest_successful_refresh(self):
        with self._lock:
            return self._refresh("WHERE status='complete'")

    def close(self):
        with self._lock:
            self.connection.close()

    @staticmethod
    def _source(row):
        data = dict(row)
        data.pop("text", None)
        data["demo"] = bool(data["demo"])
        data["trusted"] = bool(data["trusted"])
        return Source.model_validate(data)

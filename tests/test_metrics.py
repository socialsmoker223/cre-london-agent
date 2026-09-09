from london_monitor.db import Database
from london_monitor.models import Metric, MetricQuery, Source
from london_monitor.service import metric_evidence


def test_database_metrics_keep_provenance_and_suppress_conflicting_comparisons(tmp_path):
    store = Database(tmp_path / "market.sqlite")
    try:
        for name in ("a", "b", "c"):
            store.add_source(Source(id=name, title=name, publisher="Unit test", checksum=name))
        assert not store.has_metrics("a")
        city = Metric(
            metric="vacancy", value=5, unit="%", period="2026-Q2", submarket="City",
            source_id="a", definition="office vacancy",
        )
        west = city.model_copy(update={"value": 8, "submarket": "West End", "source_id": "b"})
        store.add_metrics([city, west])
        assert store.has_metrics("a")
        _, evidence = metric_evidence(store.query_metrics(MetricQuery()), store.list_sources())
        calculation = next(item for item in evidence if item.category == "calculation")
        assert calculation.source_ids == ["a", "b"]
        assert "= +3 percentage points" in calculation.excerpt
        store.add_metrics([city.model_copy(update={"value": 6, "source_id": "c"})])
        _, evidence = metric_evidence(store.query_metrics(MetricQuery()), store.list_sources())
        assert not any(item.category == "calculation" for item in evidence)
        assert any(item.id.startswith("conflict:") for item in evidence)
    finally:
        store.close()

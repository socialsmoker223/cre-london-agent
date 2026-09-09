from datetime import date

import pytest
from pydantic import ValidationError

from london_monitor.models import ChatRequest, IngestRequest, Metric, MetricQuery


def test_metric_rejects_nonfinite_values():
    for value in [float("nan"), float("inf"), float("-inf")]:
        with pytest.raises(ValidationError):
            Metric(
                metric="vacancy",
                value=value,
                unit="%",
                period="2026-Q1",
                submarket="City",
                source_id="s",
            )


def test_queries_reject_malformed_periods_and_limits():
    with pytest.raises(ValidationError):
        MetricQuery(period="2026-1")
    with pytest.raises(ValidationError):
        MetricQuery(limit=0)


def test_ingest_and_chat_reject_whitespace_only_text():
    with pytest.raises(ValidationError):
        IngestRequest(
            title="Title",
            publisher="Publisher",
            text=" " * 20,
            published_at=date(2026, 1, 1),
        )
    with pytest.raises(ValidationError):
        ChatRequest(question="   ")

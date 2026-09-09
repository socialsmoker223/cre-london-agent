"""Load the clearly labelled, offline synthetic dataset."""

import json
from pathlib import Path
from typing import Any

from .models import IngestRequest, Metric, Project, Retriever, Store


def _data_path() -> Path:
    path = Path(__file__).resolve().parents[2] / "data" / "demo" / "demo.json"
    if path.exists():
        return path
    from importlib.resources import files

    return Path(str(files("london_monitor.demo_data").joinpath("demo.json")))


def seed(store: Store, retriever: Retriever) -> None:
    data: dict[str, Any] = json.loads(_data_path().read_text(encoding="utf-8"))
    source_ids: dict[str, str] = {}
    from .ingestion import ingest

    for item in data["sources"]:
        request = {key: value for key, value in item.items() if key != "id"}
        result = ingest(IngestRequest.model_validate(request), store, retriever)
        source_ids[item["id"]] = result.source.id

    metrics = [
        Metric.model_validate({**item, "source_id": source_ids[item["source_id"]]})
        for item in data["metrics"]
    ]
    projects = [
        Project.model_validate({**item, "source_id": source_ids[item["source_id"]]})
        for item in data["projects"]
    ]
    store.add_metrics(metrics)
    store.add_projects(projects)

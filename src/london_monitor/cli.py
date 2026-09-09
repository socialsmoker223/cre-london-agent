import argparse
import logging
import os
from pathlib import Path

from london_monitor.models import ChatRequest


def main():
    parser = argparse.ArgumentParser(description="London office market intelligence")
    parser.add_argument(
        "command", choices=["ask", "serve", "smoke", "refresh"]
    )
    parser.add_argument(
        "question", nargs="?", default="Give me the latest London office market pulse."
    )
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    if args.command == "serve":
        import uvicorn

        if args.data_dir:
            os.environ["LONDON_DATA_DIR"] = str(args.data_dir)
        uvicorn.run("london_monitor.api:create_app", factory=True, host="0.0.0.0", port=args.port)
        return
    from london_monitor.service import MarketService

    service = MarketService(args.data_dir)
    try:
        if args.command == "refresh":
            from london_monitor.models import RefreshRequest

            print(service.refresh(RefreshRequest()).model_dump_json(indent=2))
        else:
            response = service.chat(ChatRequest(question=args.question))
            if args.command == "smoke":
                assert response.answer and response.claims and response.citations
                assert not response.incomplete and not response.insufficient_evidence
                assert response.trace.nodes[-1] == "verify"
                ids = {c.source.id for c in response.citations}
                assert all(set(c.source_ids) <= ids for c in response.claims)
                print("Smoke passed: database, graph, evidence, citations, trace")
            else:
                print(response.model_dump_json(indent=2))
    finally:
        service.close()

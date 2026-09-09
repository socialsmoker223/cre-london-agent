import argparse
import json
import logging
import os
from pathlib import Path

from london_monitor.models import ChatRequest


def main():
    from london_monitor.config import Settings

    settings = Settings.from_env()
    parser = argparse.ArgumentParser(description="London office market intelligence")
    parser.add_argument(
        "command", choices=["init-demo", "ask", "serve", "eval", "smoke", "refresh"]
    )
    parser.add_argument(
        "question", nargs="?", default="Give me the latest London office market pulse."
    )
    parser.add_argument("--data-dir", type=Path, default=settings.data_dir)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--mode", choices=["live", "demo"])
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    if args.mode:
        os.environ["LONDON_MODE"] = args.mode
    if args.command == "serve":
        import uvicorn

        os.environ["LONDON_DATA_DIR"] = str(args.data_dir)
        uvicorn.run("london_monitor.api:create_app", factory=True, host="0.0.0.0", port=args.port)
        return
    from london_monitor.service import MarketService

    service = MarketService(
        args.data_dir, mode="demo" if args.command in {"init-demo", "eval", "smoke"} else args.mode
    )
    try:
        if args.command == "init-demo":
            print(f"DEMO DATA initialized: {len(service.sources())} sources")
        elif args.command == "refresh":
            from london_monitor.models import RefreshRequest

            print(service.refresh(RefreshRequest()).model_dump_json(indent=2))
        elif args.command == "eval":
            from london_monitor.evaluation import evaluate

            result = evaluate(service)
            print(json.dumps(result, indent=2))
            if result["passed"] != result["total"]:
                raise SystemExit(1)
        else:
            response = service.chat(ChatRequest(question=args.question))
            if args.command == "smoke":
                assert response.answer and response.claims and response.citations
                assert response.trace.nodes[-1] == "verify_grounding"
                ids = {c.source.id for c in response.citations}
                assert all(set(c.source_ids) <= ids for c in response.claims)
                print("Smoke passed: database, graph, evidence, citations, trace")
            else:
                print(response.model_dump_json(indent=2))
    finally:
        service.close()

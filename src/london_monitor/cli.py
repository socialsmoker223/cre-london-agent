import argparse
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

from london_monitor.models import ChatRequest


def main():
    parser = argparse.ArgumentParser(description="London office market intelligence")
    parser.add_argument(
        "command", choices=["ask", "serve", "smoke", "eval", "refresh"]
    )
    parser.add_argument(
        "question", nargs="?", default="Give me the latest London office market pulse."
    )
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--output", type=Path, help="Save live evaluation responses as JSON")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    if args.command in {"smoke", "eval"} and os.getenv("LLM_PROVIDER") == "offline":
        # Offline means contract verification only; never expose fixtures as live market research.
        tests = Path(__file__).resolve().parents[2] / "tests"
        target = str(tests / "test_business_workflows.py") if args.command == "eval" else str(
            tests / "test_changes.py"
        ) + "::test_change_tool_runs_through_graph_and_preserves_both_citations"
        print("Offline contract checks using test fixtures; not live model acceptance.", flush=True)
        raise SystemExit(subprocess.run([sys.executable, "-m", "pytest", "-q", target]).returncode)
    if args.command == "serve":
        import uvicorn

        if args.data_dir:
            os.environ["LONDON_DATA_DIR"] = str(args.data_dir)
        uvicorn.run("london_monitor.api:create_app", factory=True, host="0.0.0.0", port=args.port)
        return
    from london_monitor.service import MarketService

    service = MarketService(args.data_dir)
    try:
        if args.command == "eval":
            results = []
            for question in (
                "What changed versus the previous period?",
                "Is West End outperforming City?",
                "What evidence supports flight-to-quality?",
                "Where do the sources disagree?",
                "Give me the five most important developments this month.",
                "Forecast the exact City prime rent to the penny in Q4 2035.",
            ):
                response = service.chat(ChatRequest(question=question))
                results.append({"question": question, "response": response.model_dump(mode="json")})
                print(f"{question}: {response.verdict or response.conclusion}; "
                      f"{len(response.citations)} sources; incomplete={response.incomplete}")
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(results, indent=2))
            assert results[-1]["response"]["insufficient_evidence"], "Forecast must be unsupported"
            assert all(r["response"]["answer"] for r in results)
            print("Live scenarios completed; review claims and business usefulness in the output.")
        elif args.command == "refresh":
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

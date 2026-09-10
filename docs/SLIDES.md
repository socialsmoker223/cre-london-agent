# Executive decision brief

The six-slide presentation follows the executive narrative from problem to adoption.
The editable PowerPoint includes presenter notes; the PDF contains the presentation slides.

1. Problem: turn fragmented market evidence into a defensible meeting position.
2. Decision: use Monitor, Investigate and Prepare to focus the business question.
3. How it works: Sources → Retrieve/Filter → Compare/Reason → Verify → Cited Brief.
4. Output: an edited excerpt of the recorded agent response, with publisher citations.
5. Trust: comparability, conflicting evidence, uncertainty and traceability.
6. Adoption: a repeatable review routine, current capabilities and defined PoC boundaries.

Slide 4 uses `deliverables/live-run.json`, with its partial-brief status retained visibly.
The two source links point to Savills Q2 2026 and Knight Frank Q4 2025 reports; the slide
explains why unlike definitions and periods do not establish a publisher disagreement.
This is an edited excerpt, not a new live answer or independently validated market assertion.
The deck makes no measured time-saving claim. Compare preparation and verification time with
the team's current process during a pilot.

## Regenerate

From the repository root, with project dependencies installed:

```bash
uv run python scripts/build_slides.py
soffice --headless --convert-to pdf --outdir deliverables deliverables/london-office-market-agent.pptx
```

The generator uses the saved live-run record for the source links on Slide 4. Current dashboard
screenshots remain supporting artifacts; the executive deck uses native text and a workflow
graphic. LibreOffice is needed for PDF export. Render and inspect all six pages after regenerating.

See [browser acceptance](../deliverables/browser-acceptance.md) for what was actually checked.
Generating slides does not run the application or validate market claims.

import json
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt

OUT = Path("deliverables/london-office-market-agent.pptx")
NAVY = RGBColor(16, 35, 49)
CREAM = RGBColor(244, 241, 234)
TEAL = RGBColor(27, 128, 119)
MUTED = RGBColor(112, 128, 135)
WHITE = RGBColor(255, 253, 248)


def box(
    slide, x, y, w, h, text, size=20, color=NAVY, bold=False, font="Aptos", align=PP_ALIGN.LEFT
):
    shape = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    frame = shape.text_frame
    frame.clear()
    frame.word_wrap = True
    frame.margin_left = frame.margin_right = Inches(0.02)
    frame.vertical_anchor = MSO_ANCHOR.TOP
    para = frame.paragraphs[0]
    para.alignment = align
    run = para.add_run()
    run.text = text
    run.font.name = font
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color
    return shape


def base(prs, number, section, title, dark=False):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    bg = slide.background.fill
    bg.solid()
    bg.fore_color.rgb = NAVY if dark else CREAM
    box(
        slide,
        0.65,
        0.4,
        8,
        0.25,
        f"LONDON MARKET MONITOR  /  {section.upper()}  ·  LIVE RESEARCH",
        9,
        TEAL if not dark else RGBColor(164, 212, 205),
        True,
    )
    box(
        slide,
        12,
        0.4,
        0.7,
        0.25,
        f"0{number}",
        9,
        MUTED if not dark else RGBColor(160, 180, 183),
        True,
        align=PP_ALIGN.RIGHT,
    )
    box(slide, 0.65, 1.05, 11.8, 0.8, title, 30, WHITE if dark else NAVY, True, "Georgia")
    return slide


def card(slide, x, y, w, h, heading, body, accent=TEAL, dark=False):
    shape = slide.shapes.add_shape(1, Inches(x), Inches(y), Inches(w), Inches(h))
    shape.fill.solid()
    shape.fill.fore_color.rgb = RGBColor(25, 51, 64) if dark else WHITE
    shape.line.color.rgb = RGBColor(56, 84, 94) if dark else RGBColor(215, 213, 203)
    box(slide, x + 0.22, y + 0.2, w - 0.44, 0.3, heading.upper(), 9, accent, True)
    box(slide, x + 0.22, y + 0.68, w - 0.44, h - 0.85, body, 15, CREAM if dark else NAVY)


def build():
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    example_path = Path("deliverables/live-run.json")
    example = json.loads(example_path.read_text()) if example_path.exists() else None
    s = base(prs, 1, "the decision", "What changed — and what does it mean for a lease?")
    box(
        s,
        0.8,
        2.25,
        6.2,
        1.5,
        "Discover reports.\nCompare the evidence.",
        32,
        NAVY,
        True,
        "Georgia",
    )
    box(
        s,
        0.82,
        4.3,
        6.0,
        1.3,
        "Bring rents, demand, supply and macro context into one research conversation, "
        "with the source trail attached.",
        18,
        MUTED,
    )
    card(
        s,
        8.0,
        2.2,
        4.5,
        3.5,
        "Business question",
        "Where is occupier demand strengthening, and could constrained quality supply "
        "change the timing or terms of our next leasing decision?",
    )
    s = base(prs, 2, "workflow", "One bounded research loop", True)
    for x, heading, body in [
        (0.8, "DISCOVER", "Find broker, official and developer reports"),
        (3.35, "READ", "Extract public pages and text PDFs"),
        (5.9, "COMPARE", "Retrieve passages; align definitions and periods"),
        (8.45, "EXPLAIN", "Separate reported facts from implications"),
        (11.0, "CHECK", "Open citations and inspect missing evidence"),
    ]:
        card(s, x, 2.3, 2.0, 3.0, heading, body, RGBColor(164, 212, 205), True)
    box(
        s,
        0.85,
        5.85,
        11.5,
        0.65,
        "z.ai chooses tools • Firecrawl reads sources • FastEmbed + Qdrant retrieves • "
        "SQLite preserves observations",
        13,
        RGBColor(174, 200, 198),
    )
    s = base(prs, 3, "live check", "A recorded run, with its limits visible")
    if example:
        response = example["response"]
        claims = response.get("claims", [])
        chosen = claims[:1] + [c for c in claims if c["kind"] == "interpretation"][:1]
        summary = "\n\n".join(c["text"] for c in chosen)[:680]
        card(s, 0.8, 2.1, 7.0, 3.9, "Actual model answer", summary or response["answer"][:530])
        card(
            s,
            8.2,
            2.1,
            4.3,
            3.9,
            "Evidence trail",
            f"{len(response['citations'])} cited source(s) · incomplete\n"
            + "\n".join(dict.fromkeys(response["trace"]["tools"]))
            + "\n\nDirectly ingested public report. Local vector check. "
            "Firecrawl was not used in this run.",
        )
    else:
        card(
            s,
            0.8,
            2.1,
            5.6,
            3.8,
            "Verified independently",
            "Configured z.ai completed a two-step tool conversation.\n\n"
            "Real semantic embeddings retrieved a paraphrase and survived reopening.",
        )
        card(
            s,
            6.8,
            2.1,
            5.6,
            3.8,
            "Live example blocked",
            "Host disk exhaustion caused Docker storage errors.\n\n"
            "No end-to-end live report answer is presented as completed.",
        )
    if example:
        box(s, 0.85, 6.45, 11.6, 0.5, "Source: " + example["source"]["url"], 10, MUTED)
    s = base(prs, 4, "change briefing", "Separate a changed market from a changed source", True)
    for i, (heading, body) in enumerate(
        [
            (
                "New report",
                "Discoveries establish coverage; they do not alone prove market movement.",
            ),
            (
                "Revised content",
                "Same URL, different checksum: retain both versions and source IDs.",
            ),
            (
                "Comparable change",
                "Match units, periods and definitions; calculate differences in code.",
            ),
            (
                "Business implication",
                "Qualify timing and supply risks; expose disagreement and collection gaps.",
            ),
        ]
    ):
        card(
            s,
            0.8 + (i % 2) * 6.05,
            2.15 + (i // 2) * 1.95,
            5.35,
            1.8,
            heading,
            body,
            RGBColor(164, 212, 205),
            True,
        )
    s = base(prs, 5, "readiness", "Implemented; full live readiness still needs verification")
    card(
        s,
        0.8,
        2.1,
        3.65,
        3.8,
        "Delivered",
        "Tool-driven chat\nSemantic evidence\nManual refresh + briefing\n"
        "Source versions + citations\nExplicit demo isolation",
    )
    card(
        s,
        4.85,
        2.1,
        3.65,
        3.8,
        "Verified",
        "Automated grounding tests\nDeterministic evaluation\n"
        "Real model tool calls\nReal semantic retrieval\nCompose configuration",
    )
    card(
        s,
        8.9,
        2.1,
        3.65,
        3.8,
        "Remaining blocker",
        "Free host disk capacity and restore Docker health.\n\n"
        "Then verify Firecrawl search + scrape, full Compose startup and persistence.",
    )
    OUT.parent.mkdir(exist_ok=True)
    prs.save(OUT)


if __name__ == "__main__":
    build()

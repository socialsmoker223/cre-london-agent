import tempfile
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt

from london_monitor.models import MetricQuery
from london_monitor.service import MarketService

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
        f"LONDON MARKET MONITOR  /  {section.upper()}  ·  DEMO DATA",
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
    with tempfile.TemporaryDirectory() as temp_dir:
        service = MarketService(Path(temp_dir))
        metric = service.metrics(
            MetricQuery(submarkets=["City"], metrics=["prime_rent"], latest=True)
        )[0]
        service.close()
    metric_label = f"{metric.value:g} {metric.unit} ({metric.period})"
    s = base(prs, 1, "the brief", "London office decisions need a trusted starting point")
    box(s, 0.7, 2.25, 6.5, 1.4, "The question is rarely\njust a number.", 32, NAVY, True, "Georgia")
    box(
        s,
        0.72,
        4.1,
        5.8,
        1,
        "Teams need a current view of rents, vacancy, supply and macro context — "
        "with the evidence trail attached.",
        17,
        MUTED,
    )
    card(
        s,
        8,
        2.2,
        4.55,
        3.2,
        "Product",
        "A local-first monitor that turns a plain-language question into a concise answer, "
        "metrics, citations and a run trace.",
    )
    s = base(prs, 2, "the product", "One path from question to grounded answer", True)
    for x, h, b in [
        (0.8, "ASK", "Plain-language question"),
        (3.35, "ROUTE", "Skills + typed queries"),
        (5.9, "EVIDENCE", "SQLite + lexical retrieval"),
        (8.45, "VERIFY", "Source IDs + citations"),
        (11, "ANSWER", "UI, API or CLI"),
    ]:
        card(s, x, 2.25, 2.0, 2.5, h, b, RGBColor(164, 212, 205), True)
    box(
        s,
        0.85,
        5.5,
        11.5,
        0.65,
        "Question  →  intent and skill routing  →  market data + evidence  →  "
        "grounded answer with citations",
        13,
        RGBColor(174, 200, 198),
    )
    s = base(prs, 3, "example workflow", "“What is happening to prime rents in the City?”")
    card(
        s,
        0.8,
        2.1,
        3.6,
        3.55,
        "1  retrieve",
        "Metric query\nCity · prime_rent\nLatest stored period\n\nEvidence search\nCity + current",
        dark=False,
    )
    card(
        s,
        4.85,
        2.1,
        3.6,
        3.55,
        "2  combine",
        f"City prime rent: {metric_label}\n\nEvidence snapshot: stored demo data, not a live feed.",
        dark=False,
        accent=TEAL,
    )
    card(
        s,
        8.9,
        2.1,
        3.6,
        3.55,
        "3  return",
        "Answer with [1] citations\nPublisher + date + URL\n\nTrace: metrics · evidence · verify",
        dark=False,
        accent=TEAL,
    )
    s = base(prs, 4, "trust", "Reliability is part of the answer", True)
    for i, (h, b) in enumerate(
        [
            ("Typed", "Pydantic contracts at every boundary"),
            ("Bounded", "Parameterized, limited queries"),
            ("Grounded", "Unknown claims are rejected"),
            ("Transparent", "Warnings, trace and demo label"),
        ]
    ):
        card(
            s,
            0.8 + (i % 2) * 6.05,
            2.15 + (i // 2) * 1.85,
            5.35,
            1.55,
            h,
            b,
            RGBColor(164, 212, 205),
            True,
        )
    s = base(prs, 5, "next", "A focused PoC with a clear production path")
    card(
        s,
        0.8,
        2.1,
        3.65,
        3.5,
        "Today · PoC",
        "Offline by default\nSynthetic seed data\nHashed lexical vectors\nOne local worker\n"
        "Text ingestion",
        accent=TEAL,
    )
    card(
        s,
        4.85,
        2.1,
        3.65,
        3.5,
        "Next · harden",
        "Curated connectors\nFreshness SLAs\nStronger embeddings\nServer storage\nAccess control",
        accent=TEAL,
    )
    card(
        s,
        8.9,
        2.1,
        3.65,
        3.5,
        "Measure · prove",
        "Citation coverage\nRetrieval relevance\nLatency by route\nProvider fallback\n"
        "User feedback",
        accent=TEAL,
    )
    OUT.parent.mkdir(exist_ok=True)
    prs.save(OUT)


if __name__ == "__main__":
    build()

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
        f"LONDON MARKET MONITOR  /  {section.upper()}  ·  MARKET INTELLIGENCE",
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
    s = base(prs, 1, "the problem", "Too much reading. Too little time to interpret.")
    box(s, 0.8, 2.2, 6.1, 1.7, "Reports, headlines, macro data.\nOne decision to prepare for.",
        31, NAVY, True, "Georgia")
    box(s, 0.82, 4.35, 5.9, 1.3,
        "Teams repeatedly find reports, reconcile periods and rebuild the same evidence trail. "
        "The important change can be buried in a familiar market summary.", 18, MUTED)
    card(s, 7.5, 2.2, 5.0, 3.7, "The business outcome",
         "Move from raw information to important changes, plausible drivers and implications "
         "worth investigating.\n\nBring the evidence into the meeting, "
         "with the uncertainty intact.")
    s = base(prs, 2, "the workflow", "Ask what changed. Leave ready to discuss it.", True)
    for x, heading, body in [
        (0.8, "Monitor", "What happened since I last checked?\n\nMaterial movements, new "
         "evidence and changing risks, compared with an explicit baseline."),
        (4.85, "Investigate", "I heard West End is outperforming City. Is it true?\n\nTest "
         "the claim across rents, vacancy, demand, supply and commentary."),
        (8.9, "Prepare", "What belongs in my meeting brief?\n\nUp to five developments, "
         "their business implications and the sources to verify them."),
    ]:
        card(s, x, 2.2, 3.65, 3.6, heading, body, RGBColor(164, 212, 205), True)
    box(s, 0.85, 6.1, 11.7, 0.6,
        "Follow the thread: expand a point  →  inspect the evidence  →  compare last quarter  "
        "→  decide what to watch", 15, CREAM)
    s = base(prs, 3, "example investigation", "Is West End outperforming City?")
    card(s, 0.8, 2.15, 5.55, 3.95, "Test the hypothesis",
         "Rents: compare growth, not just rent levels.\n"
         "Vacancy: align coverage and Grade A definitions.\n"
         "Take-up: compare the same reporting periods.\n"
         "Supply: distinguish future space from pre-lets.\n"
         "Commentary + macro: look for drivers and counterevidence.")
    card(s, 6.8, 2.15, 5.7, 3.95, "A useful conclusion",
         "Supported · Partially supported\nNot supported · Insufficient evidence\n\n"
         "Explain which indicators support the claim, which challenge it and what is missing. "
         "Keep facts separate from interpretation.")
    box(s, 0.85, 6.35, 11.7, 0.55,
        "Investigation design shown here; this slide does not assert a current market verdict.",
        12, MUTED)
    s = base(prs, 4, "trust", "Every important claim has a trail back to evidence.", True)
    for i, (heading, body) in enumerate([
        ("Show the calculation", "Source values, units and reporting periods alongside "
         "derived deltas. Transparent screening thresholds."),
        ("Keep disagreement", "Different numbers, definitions or dates remain visible. "
         "Conflicting reports are never averaged into one answer."),
        ("Explain possible drivers", "Cited facts are separate from interpretation. "
         "Consistency and correlation are not proof of causation."),
        ("Make verification quick", "Open the publisher, title, date, URL and relevant passage. "
         "Missing evidence stays visible, including unsupported forecasts."),
    ]):
        card(s, 0.8 + i % 2 * 6.05, 2.15 + i // 2 * 2.0, 5.35, 1.85,
             heading, body, RGBColor(164, 212, 205), True)
    s = base(prs, 5, "from poc to practice", "Prove the research workflow. Then automate it.")
    card(s, 0.8, 2.15, 3.65, 3.9, "PoC today",
         "On-demand market briefs\nHypothesis investigations\nSource-backed watchlists\n"
         "Manual evidence refresh\n\nImplications to investigate, not investment recommendations.")
    card(s, 4.85, 2.15, 3.65, 3.9, "Measure the value",
         "Time to a usable meeting brief\nTime to verify a claim\n"
         "Material developments found\nMissing or disputed evidence exposed\n\n"
         "Compare with the team's manual research baseline; no savings are claimed yet.")
    card(s, 8.9, 2.15, 3.65, 3.9, "Next, after validation",
         "Automated monitoring\nMaterial-change alerts\nPrivate reports and leasing data\n"
         "Team-specific watchlists\n\nPrioritize additions using observed workflow needs.")
    OUT.parent.mkdir(exist_ok=True)
    prs.save(OUT)


if __name__ == "__main__":
    build()

from london_monitor.agent.graph import numbers, validate_answer
from london_monitor.models import AnswerClaim, Evidence, ResearchAnswer
from london_monitor.service import validated_metrics

# Real passage from CBRE's UK Office Market Outlook 2026: Midyear Review.
# https://www.cbre.co.uk/insights/books/uk-real-estate-market-outlook-midyear-review-2026/office
PASSAGE = (
    "Rental growth continued in the first half of 2026 across Central London. "
    "The City saw prime rents increase by £5.00 psf, reaching £95.00 psf in Q2. "
    "This reflects a 12% year-on-year increase."
)


def test_metric_validation_preserves_quoted_basis_and_rejects_invented_observations():
    row = dict(
        metric="prime_rent", value=95, unit="GBP/sq ft", period="2026-Q2", submarket="City",
        definition="prime headline rent", quotation=PASSAGE,
    )
    assert len(validated_metrics([row], "cbre", PASSAGE)) == 1
    short_quote = "The City saw prime rents increase by £5.00 psf, reaching £95.00 psf in Q2."
    recovered = validated_metrics([{**row, "quotation": short_quote}], "cbre", PASSAGE)
    assert recovered[0].quotation == PASSAGE
    invalid = [
        {**row, "unit": "GBP/sq ft/year"},
        {**row, "period": "2026-Q3"},
        {**row, "value": 999},
        {**row, "submarket": "Canary Wharf"},
        {**row, "quotation": "Made up source quotation"},
    ]
    assert validated_metrics(invalid, "cbre", PASSAGE) == []
    half_year = PASSAGE + (
        " Mayfair and St James’s also saw prime rental growth in the first half of the year, "
        "reaching £200.00 psf, reflecting an 18% increase year-on-year."
    )
    assert validated_metrics([
        {**row, "value": 200, "submarket": "West End", "quotation": half_year}
    ], "cbre", half_year) == []


def test_answer_grounding_rejects_unknown_ids_numbers_and_uncomputed_differences():
    assert numbers("06 August 2026, £95.00") == numbers("6 August 2026, £95")
    assert numbers("first half of 2026") == numbers("H1 2026")
    evidence = {"cbre:1": Evidence(
        id="cbre:1", source_id="cbre", excerpt=PASSAGE, category="rents",
        submarket="City", published_at=None,
    )}
    claim = AnswerClaim(text="City prime rent reached £95 psf in Q2 2026.", evidence_ids=["cbre:1"])
    assert not validate_answer(ResearchAnswer(conclusion="Prime rents", claims=[claim]), evidence)
    for invalid in (
        claim.model_copy(update={"text": "City rents reached £999 psf."}),
        claim.model_copy(update={"evidence_ids": ["missing"]}),
        claim.model_copy(update={"kind": "calculation"}),
    ):
        assert validate_answer(ResearchAnswer(claims=[invalid]), evidence)
    assert validate_answer(ResearchAnswer(), evidence)
    assert not validate_answer(ResearchAnswer(insufficient_evidence=True), {})
    errors = validate_answer(ResearchAnswer(claims=[claim.model_copy(update={
        "kind": "calculation",
    })]), evidence)
    assert any(claim.text in error and "source-reported" in error for error in errors)


def test_publication_metadata_can_ground_a_publication_date():
    from datetime import date

    ref = Evidence(id='dated', source_id='report', excerpt='Office research.',
                   category='commentary', submarket='London', published_at=date(2026, 8, 6))
    claim = AnswerClaim(text='Published on 6 August 2026.', evidence_ids=['dated'])
    assert not validate_answer(ResearchAnswer(claims=[claim]), {'dated': ref})
    assert validate_answer(ResearchAnswer(claims=[claim.model_copy(update={
        'text': 'Published on 7 August 2026.',
    })]), {'dated': ref})


def test_numbers_do_not_validate_a_misattributed_publisher():
    ref = Evidence(id='cbre', source_id='report', publisher='www.cbre.co.uk',
                   excerpt=PASSAGE, category='rents', submarket='City')
    claim = AnswerClaim(text='Knight Frank reports City prime rent at £95 psf in Q2 2026.',
                        evidence_ids=['cbre'])
    assert any('Publisher Knight Frank' in error for error in validate_answer(
        ResearchAnswer(claims=[claim]), {'cbre': ref},
    ))
    assert not validate_answer(ResearchAnswer(claims=[claim]), {
        'cbre': ref.model_copy(update={'publisher': 'www.knightfrank.co.uk'}),
    })
    claim.text = claim.text.replace('Knight Frank', 'CBRE')
    assert not validate_answer(ResearchAnswer(claims=[claim]), {'cbre': ref})

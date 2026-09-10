"""Business contract checks; fixture providers do not measure live model quality."""

from collections import OrderedDict
from datetime import date, timedelta
from threading import RLock
from types import SimpleNamespace

from london_monitor.agent.graph import build_graph, validate_answer
from london_monitor.changes import change_evidence
from london_monitor.db import Database
from london_monitor.models import (
    AnswerClaim,
    ChangeQuery,
    ChatRequest,
    Evidence,
    Metric,
    ModelTurn,
    ResearchAnswer,
    Source,
)
from london_monitor.service import MarketService, metric_evidence, validated_metrics


def test_outperformance_compares_growth_and_blocks_ambiguous_baselines():
    sources = [Source(id=s, title=s, publisher=s, checksum=s) for s in ('a', 'b', 'c')]
    rows = [Metric(
        metric='prime_rent', value=value, unit='GBP/sq ft', period=period,
        submarket=market, source_id=source, definition='prime headline rent',
    ) for market, source, period, value in [
        ('City', 'a', '2026-Q1', 100), ('City', 'a', '2026-Q2', 110),
        ('West End', 'b', '2026-Q1', 200), ('West End', 'b', '2026-Q2', 210),
    ]]
    _, refs = metric_evidence(rows, sources)
    growth = next(e for e in refs if e.id.startswith('calc:growth:'))
    assert 'City 100 -> 110 GBP/sq ft, change +10.00%' in growth.excerpt
    assert 'West End 200 -> 210 GBP/sq ft, change +5.00%' in growth.excerpt
    assert 'movement = -5.00 percentage points' in growth.excerpt
    assert len(growth.observations) == 4 and growth.source_ids == ['a', 'b']
    conflict = rows[0].model_copy(update={'source_id': 'c', 'value': 99})
    _, refs = metric_evidence([*rows, conflict], sources)
    assert not any(e.id.startswith('calc:growth:') for e in refs)
    assert any(e.id.startswith('conflict:') for e in refs)
    _, refs = metric_evidence([*rows, rows[0].model_copy(update={
        'source_id': 'c', 'definition': 'average rent', 'value': 80,
    })], sources)
    mismatch = next(e for e in refs if e.id.startswith('mismatch:'))
    assert 'not interchangeable' in mismatch.excerpt
    assert set(mismatch.source_ids) == {'a', 'c'}


def test_month_brief_uses_publication_not_ingestion_and_keeps_reporting_period(tmp_path):
    service = MarketService.__new__(MarketService)
    service.store = Database(tmp_path / 'monthly.sqlite')
    try:
        for name, published in [('old', date(2025, 12, 10)), ('new', date(2026, 1, 10)),
                                ('undated', None), ('late', date(2024, 1, 1))]:
            source = Source(id=name, title=name, publisher=name, checksum=name,
                            published_at=published, submarket='City')
            service.store.save_document(source, 'Fixture: quality space supply is tightening.')
        service.store.add_metrics([Metric(
            metric='grade_a_vacancy', value=value, unit='%', period=period,
            submarket='City', source_id=source, definition='Grade A office vacancy',
        ) for source, period, value in [('old', '2025-Q3', 4), ('new', '2025-Q4', 3.5)]])
        report, refs = change_evidence(service, ChangeQuery(
            basis='publication_month', month='2026-01',
        ))
        assert report['previous_boundary'] == '2025-12'
        assert report['new_source_ids'] == ['new']
        assert {e.source_id for e in refs if e.comparison_role} == {'old', 'new'}
        calc = next(e for e in refs if e.material)
        assert calc.reporting_period == '2025-Q4'
        assert 'difference -0.5 percentage points' in calc.excerpt
        assert report['priority_evidence_ids'][0] == calc.id
        assert '0.5 percentage points' in report['materiality_rule']
        assert 'Publication is not event date' in ' '.join(report['warnings'])
    finally:
        service.store.close()


def test_supply_and_quality_metrics_preserve_explicit_quoted_units():
    for metric, label, value, unit in [
        ('grade_a_vacancy', 'Grade A vacancy', 3, '%'),
        ('secondary_vacancy', 'secondary vacancy', 12, '%'),
        ('availability', 'office availability', 8, '%'),
        ('prelet_share', 'prelet share', 70, '%'),
        ('completions', 'office completions', 500000, 'sq ft'),
        ('pipeline', 'office pipeline for delivery next year', 900000, 'sq ft'),
    ]:
        quote = f'City {label} in Q2 2026 was {value} {unit}.'
        row = dict(metric=metric, value=value, unit=unit, period='2026-Q2',
                   submarket='City', definition=label, quotation=quote)
        assert len(validated_metrics([row], 'fixture', quote)) == 1
        assert not validated_metrics([{**row, 'value': value + 1}], 'fixture', quote)


def test_insufficient_forecast_survives_citations_and_followups(tmp_path):
    source = Source(id='fixture', title='Historical fixture', publisher='Test', checksum='abc')
    evidence = Evidence(id='fixture:1', source_id=source.id,
                        excerpt='City prime rent was £95 psf in Q2 2026.',
                        category='rents', submarket='City')
    answer = ResearchAnswer(
        conclusion='Historical context cannot establish an exact forecast',
        verdict='Insufficient evidence', insufficient_evidence=True, workflow='investigate',
        gaps=['No sourced forecast with the requested horizon and assumptions.'],
        claims=[AnswerClaim(text=evidence.excerpt, evidence_ids=[evidence.id])],
    )
    service = MarketService.__new__(MarketService)
    service.store = Database(tmp_path / 'forecast.sqlite')
    service.store.save_document(source, evidence.excerpt)
    service.lock = RLock()
    service.conversations = OrderedDict()
    service.retriever = SimpleNamespace(search=lambda query: [evidence])
    messages_seen = []

    def complete(messages, tools, timeout):
        messages_seen.append(messages)
        return ModelTurn(content=answer.model_dump_json())

    service.graph = build_graph(service, SimpleNamespace(complete=complete))
    try:
        response = service.chat(ChatRequest(question='Forecast the exact City rent next year'))
        assert response.insufficient_evidence and response.citations and not response.incomplete
        assert response.verdict == 'Insufficient evidence' and response.gaps
        assert response.claims[0].evidence_ids == [evidence.id]
        assert response.evidence[0].excerpt == evidence.excerpt
        for question in ['What evidence supports that?', 'What should we watch next?']:
            service.chat(ChatRequest(question=question, conversation_id=response.conversation_id))
        assert 'Forecast the exact City rent next year' in str(messages_seen[-1])
        assert evidence.excerpt in str(messages_seen[-1])
        assert response.conversation_id in service.conversations
    finally:
        service.store.close()


def test_business_implications_and_verdicts_cannot_bypass_evidence_checks():
    ref = Evidence(id='a', source_id='a', excerpt='Quality supply is constrained.',
                   category='supply', submarket='City')
    claim = AnswerClaim(text='Quality supply may warrant monitoring.', evidence_ids=['a'],
                        section='watchlist', kind='interpretation')
    assert not validate_answer(ResearchAnswer(claims=[claim]), {'a': ref})
    for invalid in [claim.model_copy(update={'kind': 'fact'}),
                    claim.model_copy(update={'section': 'disagreements'})]:
        assert validate_answer(ResearchAnswer(claims=[invalid]), {'a': ref})
    assert validate_answer(ResearchAnswer(verdict='Supported', insufficient_evidence=True), {})


def test_prepare_cannot_relabel_historical_evidence_as_this_month(tmp_path):
    from london_monitor.agent.graph import briefing_query, run_graph

    service = MarketService.__new__(MarketService)
    service.store = Database(tmp_path / 'prepare.sqlite')
    source = Source(id='old', title='Old report', publisher='Test', checksum='old',
                    published_at=date(2025, 1, 1))
    ref = Evidence(id='old:1', source_id='old', excerpt='Quality space is scarce.',
                   category='supply', submarket='City', published_at=source.published_at)
    service.store.save_document(source, ref.excerpt)
    def search(query):
        assert query.current  # Apply the version filter before the retrieval limit.
        return [ref]

    service.retriever = SimpleNamespace(search=search)
    claim = AnswerClaim(text=ref.excerpt, evidence_ids=[ref.id], section='what_changed')
    turns = iter([
        ModelTurn(content=ResearchAnswer(claims=[claim]).model_dump_json()),
        ModelTurn(content=ResearchAnswer(claims=[claim.model_copy(update={
            'section': 'key_metrics',
        })]).model_dump_json()),
    ])
    provider = SimpleNamespace(complete=lambda *args, **kwargs: next(turns))
    request = ChatRequest(question='Give me a meeting brief for June 2026', workflow='prepare')
    assert briefing_query(request).month == '2026-06'
    assert briefing_query(request.model_copy(update={
        'question': 'What changed in Q2 2026?',
    })).period == '2026-Q2'
    try:
        response = run_graph(build_graph(service, provider), request)
        assert response.insufficient_evidence and not response.incomplete
        assert all(c.section != 'what_changed' for c in response.claims)
        assert 'No dated developments verified for 2026-06.' in response.gaps
    finally:
        service.store.close()


def test_publication_dates_come_from_article_labels_not_events_or_related_links(tmp_path):
    from london_monitor.ingestion import ingest, publication_date
    from london_monitor.models import IngestRequest

    article = 'Central London Office Market Q2 2026\n====================================\n\n'
    article += '06 August 2026\n\nQuality space remains constrained.'
    assert publication_date(article) == date(2026, 8, 6)
    assert publication_date('## Research article\n# Office Market\n06 August 2026') == date(
        2026, 8, 6
    )
    assert publication_date('## Publication\n# Report\n30 June 2026') == date(2026, 6, 30)
    assert publication_date('Published on 30 July 2026\nNext due: 17 September 2026') == date(
        2026, 7, 30
    )
    assert publication_date('menu Insight 04 August 2026 # Office report') == date(2026, 8, 4)
    assert publication_date('Next due: 17 September 2026') is None
    assert publication_date('# Event\n17 September 2026') is None
    assert publication_date('Commercial Leasing 28 August 2026') is None
    assert publication_date(article + '\nPublished on 07 August 2026') is None
    db = Database(tmp_path / 'dates.sqlite')
    indexed = []
    retriever = SimpleNamespace(index=lambda source, *args: indexed.append(source) or 1)
    request = IngestRequest(title='Report', publisher='Test', text=article,
                            url='https://example.test/report')
    try:
        first = ingest(request, db, retriever)
        db.connection.execute('UPDATE sources SET published_at=NULL')
        duplicate = ingest(request, db, retriever)
        assert duplicate.duplicate and duplicate.source.id == first.source.id
        assert indexed[-1].published_at == date(2026, 8, 6)
        assert db.get_document(first.source.id).source.published_at == date(2026, 8, 6)
        newer = first.source.model_copy(update={
            'id': 'newer', 'checksum': 'newer',
            'retrieved_at': first.source.retrieved_at + timedelta(seconds=1),
        })
        db.save_document(newer, 'Later version of the report.')
        db.connection.execute('UPDATE sources SET published_at=NULL WHERE id=?', (first.source.id,))
        count = len(indexed)
        old = ingest(request, db, retriever)
        assert old.source.published_at == date(2026, 8, 6)
        assert old.chunks == 0 and len(indexed) == count
    finally:
        db.close()


def test_publisher_aliases_do_not_create_independent_corroboration():
    refs = {name: Evidence(id=name, source_id=name, excerpt='Quality supply is tightening.',
                          category='supply', submarket='City', comparison_role='current',
                          publisher=publisher)
            for name, publisher in [('a', 'Savills UK'), ('b', 'www.savills.com')]}
    claim = AnswerClaim(text='A quality supply theme is emerging.', kind='interpretation',
                        evidence_ids=['a', 'b'], change_status='emerging')
    assert validate_answer(ResearchAnswer(claims=[claim]), refs)


def test_failed_claim_is_omitted_without_losing_verified_business_evidence(tmp_path):
    from london_monitor.agent.graph import run_graph

    service = MarketService.__new__(MarketService)
    service.store = Database(tmp_path / 'partial.sqlite')
    source = Source(id='report', title='Report', publisher='Test', checksum='partial')
    ref = Evidence(id='report:1', source_id=source.id, excerpt='City rent is £95 psf.',
                   category='rents', submarket='City')
    service.store.save_document(source, ref.excerpt)
    service.retriever = SimpleNamespace(search=lambda query: [ref])
    candidate = ResearchAnswer(verdict='Supported', claims=[
        AnswerClaim(text='1. City rent is £95 psf. This could signal pressure.',
                    evidence_ids=[ref.id]),
        AnswerClaim(text='City rent will reach £999 psf.', evidence_ids=[ref.id]),
        AnswerClaim(text='Verdict: Supported.', evidence_ids=[ref.id]),
    ])
    provider = SimpleNamespace(complete=lambda *args, **kwargs:
                               ModelTurn(content=candidate.model_dump_json()))
    try:
        response = run_graph(build_graph(service, provider), ChatRequest(
            question='Investigate City rents', workflow='investigate',
        ))
        assert response.incomplete and response.insufficient_evidence
        assert response.verdict == 'Insufficient evidence'
        assert len(response.claims) == len(response.citations) == 1
        assert response.claims[0].text == 'City rent is £95 psf. This could signal pressure.'
        assert response.claims[0].kind == 'interpretation'
        assert '999' not in response.answer
        assert response.trace.failures == ['verification:partial_answer']
    finally:
        service.store.close()


def test_article_body_reaches_metrics_and_comparison_beyond_navigation(tmp_path):
    import json

    from london_monitor.ingestion import article_text
    from london_monitor.models import ScrapedPage, ScrapeQuery

    body = '## Research article\n# Office Market Q2 2026\n06 August 2026\n'
    body += 'Leasing commentary. ' * 300
    quote = 'City prime rent in Q2 2026 was £95 psf.'
    body += '\n\n' + quote
    document = 'Navigation link\n' * 2500 + body + '\n\nAuthors\nContact details'
    assert article_text(document) == body
    service = MarketService.__new__(MarketService)
    service.store = Database(tmp_path / 'article.sqlite')
    source = Source(id='report', title='Office Market Q2 2026', publisher='Broker',
                    checksum='article', published_at=date(2026, 8, 6))
    requests = []

    def complete(messages, tools, timeout):
        requests.append(messages)
        return ModelTurn(content='{"metrics": []}')

    service.provider = SimpleNamespace(complete=complete)
    try:
        import time

        service.store.save_document(source, document)
        service.extract_metrics(source, document, time.monotonic() + 10)
        assert quote in json.loads(requests[0][1]['content'])['text']
        assert 'Navigation link' not in requests[0][1]['content']
        report, refs = service.market_changes(ChangeQuery(
            basis='publication_month', month='2026-08',
        ))
        assert quote in refs[0].excerpt
        assert 'Contact details' not in refs[0].excerpt
        assert not any('part of' in warning for warning in report['warnings'])
        service.web = SimpleNamespace(scrape=lambda *args, **kwargs: ScrapedPage(
            title=source.title, publisher=source.publisher, text=document,
            url='https://example.com/report',
        ))
        service.retriever = SimpleNamespace(index=lambda *args: 1)
        _, scraped = service.collect(ScrapeQuery(url='https://example.com/report'),
                                     time.monotonic() + 10, with_metrics=False)
        assert quote in ' '.join(e.excerpt for e in scraped)
        assert all('Navigation link' not in e.excerpt for e in scraped)
    finally:
        service.store.close()

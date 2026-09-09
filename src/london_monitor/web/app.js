const $ = (selector) => document.querySelector(selector);
const form = $('#chat-form');
const question = $('#question');
const answer = $('#answer');
const status = $('#status');
const context = $('#use-context');
let previousQuestion = null;
let conversationId = null;

document.querySelectorAll('.examples button').forEach((button) => button.addEventListener('click', () => {
  question.value = button.textContent;
  question.focus();
}));

function text(parent, value, tag = 'span') {
  const node = document.createElement(tag);
  node.textContent = value ?? '';
  parent.append(node);
  return node;
}

function cell(row, value, tag = 'td') {
  const node = document.createElement(tag);
  node.textContent = value ?? '';
  row.append(node);
  return node;
}

function renderAnswer(data) {
  answer.replaceChildren();
  answer.classList.remove('hidden');
  text(answer, data.demo ? 'DEMO DATA · ANSWER' : 'MARKET ANSWER', 'div').className = 'answer-meta';
  const fallback = data.incomplete && data.answer.startsWith('Evidence-only fallback');
  text(answer, fallback ? 'Source evidence' : 'Market answer', 'h2');
  text(answer, fallback ? 'A verified synthesis is unavailable. Review the cited source excerpts below.' : data.answer, 'p').className = 'answer-body';
  if (data.incomplete) text(answer, 'This answer is incomplete; refresh or try again.', 'p');
  if (data.freshness) text(answer, `Freshness · ${data.freshness}`, 'p').className = 'answer-meta';
  (data.warnings || []).forEach((warning) => text(answer, warning, 'p'));
  const citations = document.createElement('div');
  citations.className = 'citations';
  (data.citations || []).forEach((citation) => {
    const item = document.createElement('details');
    item.className = 'citation';
    const summary = document.createElement('summary');
    text(summary, `${citation.number}. ${citation.source.title} · ${citation.source.publisher} · ${citation.source.published_at || "date unknown"}`);
    item.append(summary);
    text(item, citation.excerpt, 'p');
    if (citation.source.url) {
      const link = document.createElement('a');
      link.href = citation.source.url;
      link.target = '_blank';
      link.rel = 'noreferrer';
      text(link, citation.source.url);
      item.append(link);
    }
    citations.append(item);
  });
  answer.append(citations);
  if (data.trace) {
    const trace = document.createElement('div');
    trace.className = 'trace';
    text(trace, `Trace · ${[...(data.trace.skills || []), ...(data.trace.tools || [])].join(' · ')}`);
    text(trace, ` · ${data.trace.source_count || data.citations.length} sources · ${(data.trace.duration_ms / 1000).toFixed(1)}s · ${data.trace.failures.length} failures`);
    if (Object.keys(data.trace.usage || {}).length) text(trace, ` · tokens ${JSON.stringify(data.trace.usage)}`);
    answer.append(trace);
  }
  if (data.evidence?.length) {
    const evidence = document.createElement('details');
    evidence.className = 'citations';
    text(evidence, `Supporting excerpts (${data.evidence.length})`, 'summary');
    data.evidence.forEach((item) => text(evidence, `${item.location || 'Source excerpt'} · ${item.excerpt}`, 'p'));
    answer.append(evidence);
  }
  status.dataset.state = data.incomplete ? 'error' : 'success';
  status.textContent = data.incomplete ? 'Answer ready with evidence gaps.' : 'Answer ready.';
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const value = question.value.trim();
  if (value.length < 3) {
    status.dataset.state = 'error';
    question.setAttribute('aria-invalid', 'true');
    question.focus();
    status.textContent = 'Please enter a question with at least three characters.';
    return;
  }
  const ask = $('#ask');
  question.removeAttribute('aria-invalid');
  status.dataset.state = 'loading';
  form.setAttribute('aria-busy', 'true');
  answer.classList.add('hidden');
  ask.disabled = true;
  status.textContent = 'Researching sources and checking evidence (up to three minutes)…';
  try {
    const payload = { question: value };
    if (context.checked && previousQuestion) payload.previous_question = previousQuestion;
    if (context.checked && conversationId) payload.conversation_id = conversationId;
    const response = await fetch('/api/chat', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload),
    });
    if (!response.ok) throw new Error('Request failed');
    const result = await response.json();
    renderAnswer(result);
    previousQuestion = value;
    if (result?.conversation_id) conversationId = result.conversation_id;
  } catch {
    status.dataset.state = 'error';
    status.textContent = 'The monitor is temporarily unavailable. Please try again.';
  } finally {
    ask.disabled = false;
    form.removeAttribute('aria-busy');
  }
});

async function loadMetrics() {
  try {
    const [metricsResponse, sourcesResponse] = await Promise.all([fetch('/api/metrics'), fetch('/api/sources')]);
    if (!metricsResponse.ok || !sourcesResponse.ok) throw new Error();
    const metrics = await metricsResponse.json();
    const sources = Object.fromEntries((await sourcesResponse.json()).map((source) => [source.id, source]));
    const groups = {};
    metrics.forEach((metric) => {
      const key = `${metric.metric}|${metric.submarket}|${metric.period}|${metric.unit}|${metric.definition}`;
      (groups[key] ||= []).push(metric);
    });
    const table = document.createElement('table');
    table.className = 'metrics-table';
    table.innerHTML = '<caption class="sr-only">Latest market indicators</caption><thead><tr><th>Metric</th><th>Submarket</th><th>Value</th><th>Period</th><th>Source</th></tr></thead>';
    const body = document.createElement('tbody');
    Object.values(groups).flat().forEach((metric) => {
      const row = document.createElement('tr');
      const source = sources[metric.source_id];
      const values = groups[`${metric.metric}|${metric.submarket}|${metric.period}|${metric.unit}|${metric.definition}`];
      cell(row, metric.metric.replaceAll('_', ' '), 'th');
      cell(row, metric.submarket);
      cell(row, `${metric.value} ${metric.unit}`);
      cell(row, metric.period);
      const sourceCell = cell(row, '');
      text(sourceCell, source ? `${source.demo ? 'Demo · ' : ''}${source.publisher}` : metric.source_id);
      if (new Set(values.map(item => item.value)).size > 1) text(sourceCell, 'Conflicting estimate', 'small').className = 'conflict';
      row.append(sourceCell);
      body.append(row);
    });
    table.append(body);
    const container = $('#metrics');
    container.replaceChildren();
    container.append(metrics.length ? table : text(container, 'No metrics available yet.'));
  } catch {
    $('#metrics').replaceChildren();
    text($('#metrics'), 'Metrics are unavailable right now.');
  }
}

async function loadStatus() {
  try {
    const response = await fetch('/api/status');
    if (!response.ok) throw new Error();
    const data = await response.json();
    const live = data.mode === 'live' || data.live === true;
    $('#mode-badge').textContent = live ? 'LIVE MODE' : 'DEMO DATA';
    $('#mode-badge').classList.toggle('live-pill', live);
    $('#refresh').disabled = !live;
    if (!live) $('#refresh-status').textContent = 'Refresh is available in live mode.';
  } catch {
    $('#mode-badge').textContent = 'STATUS UNAVAILABLE';
  }
}

async function loadBriefing() {
  try {
    const response = await fetch('/api/refresh');
    if (!response.ok) throw new Error();
    const data = await response.json();
    const briefing = $('#briefing');
    briefing.replaceChildren();
    if (data) {
      briefing.classList.remove('hidden');
      text(briefing, `Last refresh · ${data.status} · ${new Date(data.completed_at).toLocaleString()}`, 'h3');
      text(briefing, data.briefing || 'No briefing available.', 'p');
    }
  } catch { /* briefing is optional */ }
}

$('#refresh').addEventListener('click', async () => {
  const button = $('#refresh');
  const refreshStatus = $('#refresh-status');
  button.disabled = true;
  refreshStatus.dataset.state = 'loading';
  refreshStatus.textContent = 'Refreshing evidence (up to five minutes)…';
  try {
    const response = await fetch('/api/refresh', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}',
    });
    if (!response.ok) throw new Error();
    const data = await response.json();
    refreshStatus.dataset.state = data.status === 'complete' ? 'success' : 'error';
    refreshStatus.textContent = `Refresh ${data.status}.`;
    await Promise.all([loadBriefing(), loadMetrics()]);
  } catch {
    refreshStatus.dataset.state = 'error';
    refreshStatus.textContent = 'Refresh failed. Please try again.';
  } finally {
    button.disabled = false;
  }
});
loadMetrics();
loadStatus();
loadBriefing();

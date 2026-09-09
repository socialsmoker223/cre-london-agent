const $ = (selector) => document.querySelector(selector);
const form = $('#chat-form');
const question = $('#question');
const answer = $('#answer');
const status = $('#status');
const context = $('#use-context');
let previousQuestion = null;

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
  text(answer, 'Grounded market answer', 'h2');
  text(answer, data.answer, 'p').className = 'answer-body';
  (data.warnings || []).forEach((warning) => text(answer, warning, 'p'));
  const citations = document.createElement('div');
  citations.className = 'citations';
  (data.citations || []).forEach((citation) => {
    const item = document.createElement('details');
    item.className = 'citation';
    const summary = document.createElement('summary');
    text(summary, `${citation.number}. ${citation.source.title} · ${citation.source.publisher} · ${citation.source.published_at}`);
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
    answer.append(trace);
  }
  status.textContent = 'Answer ready.';
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const value = question.value.trim();
  if (value.length < 3) {
    status.textContent = 'Please enter a question with at least three characters.';
    return;
  }
  const ask = $('#ask');
  ask.disabled = true;
  status.textContent = 'Finding grounded evidence…';
  try {
    const payload = { question: value };
    if (context.checked && previousQuestion) payload.previous_question = previousQuestion;
    const response = await fetch('/api/chat', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload),
    });
    if (!response.ok) throw new Error('Request failed');
    renderAnswer(await response.json());
    previousQuestion = value;
  } catch {
    status.textContent = 'The monitor is temporarily unavailable. Please try again.';
  } finally {
    ask.disabled = false;
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
      const key = `${metric.metric}|${metric.submarket}|${metric.period}`;
      (groups[key] ||= []).push(metric);
    });
    const table = document.createElement('table');
    table.className = 'metrics-table';
    table.innerHTML = '<caption class="sr-only">Latest market indicators</caption><thead><tr><th>Metric</th><th>Submarket</th><th>Value</th><th>Period</th><th>Source</th></tr></thead>';
    const body = document.createElement('tbody');
    Object.values(groups).flat().forEach((metric) => {
      const row = document.createElement('tr');
      const source = sources[metric.source_id];
      const values = groups[`${metric.metric}|${metric.submarket}|${metric.period}`];
      cell(row, metric.metric.replaceAll('_', ' '), 'th');
      cell(row, metric.submarket);
      cell(row, `${metric.value} ${metric.unit}`);
      cell(row, metric.period);
      const sourceCell = cell(row, '');
      text(sourceCell, source ? `${source.demo ? 'Demo · ' : ''}${source.publisher}` : metric.source_id);
      if (values.length > 1) text(sourceCell, 'Conflicting estimate', 'small').className = 'conflict';
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
loadMetrics();

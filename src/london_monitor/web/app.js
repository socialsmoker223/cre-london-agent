const $ = (selector) => document.querySelector(selector);
const form = $('#chat-form');
const question = $('#question');
const answer = $('#answer');
const status = $('#status');
const context = $('#use-context');
let previousQuestion = null;
let conversationId = null;
let refreshTimer = null;
let refreshPending = false;
let chatController = null;
context.disabled = true;

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

function sourceLink(parent, source) {
  if (!source?.url) return text(parent, source?.publisher || 'Unknown source');
  const link = text(parent, source.title, 'a');
  link.href = source.url;
  link.target = '_blank';
  link.rel = 'noopener noreferrer';
  return link;
}

async function requestJson(url, options) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Please check your request and retry.');
  return data;
}

function renderAnswer(data, askedQuestion) {
  answer.replaceChildren();
  answer.classList.remove('hidden');
  text(answer, `Question · ${askedQuestion}`, 'p').className = 'answer-meta';
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
    const trace = document.createElement('details');
    trace.className = 'trace';
    text(trace, `Research activity · ${data.citations.length} sources · ${(data.trace.duration_ms / 1000).toFixed(1)}s`, 'summary');
    text(trace, [...new Set(data.trace.tools || [])].map(name => name.replaceAll('_', ' ')).join(' → '), 'p');
    (data.trace.failures || []).forEach(failure => text(trace, failure, 'p'));
    answer.append(trace);
  }
  if (data.evidence?.length) {
    const evidence = document.createElement('details');
    evidence.className = 'citations';
    text(evidence, `Supporting excerpts (${data.evidence.length})`, 'summary');
    data.evidence.forEach((item) => {
      const source = data.citations.find(citation => citation.source.id === item.source_id)?.source;
      text(evidence, `${source?.title || 'Source'} · ${item.location || 'Source excerpt'}`, 'h3');
      text(evidence, item.excerpt, 'p');
    });
    answer.append(evidence);
  }
  status.dataset.state = data.incomplete || data.insufficient_evidence ? 'error' : 'success';
  status.textContent = data.insufficient_evidence ? 'See the scope or evidence limitation below.' : data.incomplete ? 'Answer ready with evidence gaps.' : 'Answer ready.';
}

async function streamChat(payload, signal, onActivity) {
  const response = await fetch('/api/chat/stream', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload), signal,
  });
  if (!response.ok) {
    const data = await response.json();
    throw new Error(typeof data.detail === 'string' ? data.detail : 'Please check your request and retry.');
  }
  if (!response.body) throw new Error('Streaming is unavailable in this browser.');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  try {
    while (true) {
      const {value, done} = await reader.read();
      buffer += decoder.decode(value, {stream: !done});
      let boundary;
      while ((boundary = buffer.indexOf('\n\n')) !== -1) {
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const data = frame.split('\n').filter(line => line.startsWith('data:'))
          .map(line => line.slice(5).trimStart()).join('\n');
        if (!data) continue;
        const event = JSON.parse(data);
        if (event.type === 'activity') onActivity(event.message);
        if (event.type === 'error') throw new Error(event.message);
        if (event.type === 'result') return event.data;
      }
      if (done) throw new Error('The research stream ended before an answer arrived. Please retry.');
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

$('#stop').addEventListener('click', () => chatController?.abort());

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (chatController) return;
  const value = question.value.trim();
  if (value.length < 3) {
    status.dataset.state = 'error';
    question.setAttribute('aria-invalid', 'true');
    question.focus();
    status.textContent = 'Please enter a question with at least three characters.';
    return;
  }
  const ask = $('#ask');
  const stop = $('#stop');
  const activity = $('#activity');
  const steps = $('#activity-steps');
  const summary = $('#activity-summary');
  question.removeAttribute('aria-invalid');
  status.dataset.state = 'loading';
  form.setAttribute('aria-busy', 'true');
  ask.disabled = true;
  stop.classList.remove('hidden');
  activity.classList.remove('hidden');
  activity.open = true;
  activity.dataset.state = 'loading';
  steps.replaceChildren();
  $('#activity-question').textContent = value;
  chatController = new AbortController();
  const started = Date.now();
  let stage = 'Connecting to research service';
  const updateStatus = () => {
    const elapsed = Math.floor((Date.now() - started) / 1000);
    summary.textContent = `Research activity · ${elapsed}s`;
    status.textContent = `${stage}…`;
  };
  updateStatus();
  activity.scrollIntoView({block: 'nearest'});
  const timer = setInterval(updateStatus, 1000);
  try {
    const payload = { question: value };
    if (context.checked && previousQuestion) payload.previous_question = previousQuestion;
    if (context.checked && conversationId) payload.conversation_id = conversationId;
    const result = await streamChat(payload, chatController.signal, message => {
      stage = message;
      const follow = steps.scrollHeight - steps.scrollTop - steps.clientHeight < 40;
      const item = text(steps, message, 'li');
      text(item, `${Math.floor((Date.now() - started) / 1000)}s`, 'time');
      if (follow) steps.scrollTop = steps.scrollHeight;
      updateStatus();
    });
    clearInterval(timer);
    renderAnswer(result, value);
    activity.dataset.state = result.incomplete ? 'error' : 'success';
    summary.textContent = `Research ${result.incomplete ? 'completed with gaps' : 'complete'} · ${Math.floor((Date.now() - started) / 1000)}s`;
    activity.open = false;
    previousQuestion = value;
    if (result?.conversation_id) conversationId = result.conversation_id;
    context.disabled = false;
    await loadMetrics();
  } catch (error) {
    clearInterval(timer);
    const stopped = error.name === 'AbortError';
    activity.dataset.state = stopped ? 'stopped' : 'error';
    summary.textContent = `Research ${stopped ? 'stopped' : 'interrupted'} · ${Math.floor((Date.now() - started) / 1000)}s`;
    status.dataset.state = 'error';
    status.textContent = `${stopped ? 'Research stopped.' : error.message === 'Failed to fetch' ? 'The monitor is unavailable. Please retry.' : error.message} Your last answer has been kept.`;
  } finally {
    clearInterval(timer);
    chatController = null;
    ask.disabled = false;
    if (document.activeElement === stop) ask.focus();
    stop.classList.add('hidden');
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
      const details = document.createElement('details');
      text(details, source?.publisher || metric.source_id, 'summary');
      sourceLink(details, source);
      text(details, `Definition · ${metric.definition || 'Not specified'}`, 'p');
      text(details, metric.quotation || 'No supporting quotation retained.', 'blockquote');
      text(details, `Published · ${source?.published_at || 'Unknown'} · Retrieved · ${source?.retrieved_at ? new Date(source.retrieved_at).toLocaleDateString() : 'Unknown'}`, 'p');
      sourceCell.append(details);
      if (new Set(values.map(item => item.value)).size > 1) text(sourceCell, 'Conflicting estimate', 'small').className = 'conflict';
      row.append(sourceCell);
      body.append(row);
    });
    table.append(body);
    const container = $('#metrics');
    container.replaceChildren();
    if (metrics.length) container.append(table);
    else text(container, 'No validated numerical indicators yet. Refresh data to collect evidence; available reports are listed below.');
    const library = $('#sources');
    library.replaceChildren();
    const list = Object.values(sources);
    text(library, `Collected sources (${list.length})`, 'summary');
    list.forEach(source => {
      const item = document.createElement('p');
      sourceLink(item, source);
      text(item, ` · ${source.publisher} · Published ${source.published_at || 'date unknown'}`);
      library.append(item);
    });
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
    $('#mode-badge').textContent = 'LIVE RESEARCH';
    $('#provider-status').textContent = `${data.provider} · ${data.model} · ${data.sources} collected sources`;
    $('#ask').disabled = form.getAttribute('aria-busy') === 'true';
    $('#refresh').disabled = refreshPending || Boolean(data.refresh_progress);
    if (data.refresh_progress) {
      $('#refresh-status').dataset.state = 'loading';
      $('#refresh-status').textContent = `${data.refresh_progress.stage} · ${data.refresh_progress.documents} documents collected…`;
      watchRefresh();
    } else if (refreshTimer && !refreshPending) {
      clearInterval(refreshTimer);
      refreshTimer = null;
      $('#refresh-status').textContent = 'Refresh finished. See the saved briefing below.';
      await Promise.all([loadBriefing(), loadMetrics()]);
    }
  } catch {
    $('#mode-badge').textContent = 'STATUS UNAVAILABLE';
    $('#provider-status').textContent = 'Cannot reach the configured research service.';
    $('#refresh').disabled = true;
  }
}

function watchRefresh() {
  if (!refreshTimer) refreshTimer = setInterval(loadStatus, 3000);
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
      if (data.failures?.length) {
        const failures = document.createElement('details');
        text(failures, `Collection issues (${data.failures.length}) and next steps`, 'summary');
        const list = document.createElement('ul');
        data.failures.forEach(failure => text(list, failure, 'li'));
        failures.append(list);
        text(failures, 'Retry refresh to recheck searches and extraction. If all topics return no results, check DuckDuckGo search errors or Crawl4AI logs before retrying. Existing evidence is retained.', 'p');
        briefing.append(failures);
      }
    }
  } catch { /* briefing is optional */ }
}

$('#refresh').addEventListener('click', async () => {
  const button = $('#refresh');
  const refreshStatus = $('#refresh-status');
  button.disabled = true;
  refreshPending = true;
  watchRefresh();
  refreshStatus.dataset.state = 'loading';
  refreshStatus.textContent = 'Refreshing evidence (up to five minutes)…';
  try {
    const data = await requestJson('/api/refresh', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}',
    });
    refreshStatus.dataset.state = data.status === 'complete' ? 'success' : 'error';
    refreshStatus.textContent = `Refresh ${data.status}.`;
    await Promise.all([loadBriefing(), loadMetrics()]);
  } catch (error) {
    refreshStatus.dataset.state = 'error';
    refreshStatus.textContent = `Refresh failed: ${error.message}. Existing evidence has been kept.`;
  } finally {
    refreshPending = false;
    await loadStatus();
  }
});
loadMetrics();
loadStatus();
loadBriefing();

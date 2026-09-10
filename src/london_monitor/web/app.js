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
let workflow = 'auto';
let providerOptions = [];
let removingSource = null;
context.disabled = true;

document.querySelectorAll('.examples button:not(#new-research), .workflows button').forEach((button) => button.addEventListener('click', () => {
  question.value = button.dataset.question || button.textContent;
  workflow = button.dataset.workflow || 'auto';
  context.checked = false;
  question.focus();
}));
question.addEventListener('input', () => { workflow = 'auto'; });
$('#new-research').addEventListener('click', () => {
  conversationId = null;
  previousQuestion = null;
  context.checked = false;
  context.disabled = true;
  workflow = 'auto';
  question.value = '';
  question.focus();
});

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
  if (!source?.url) return text(parent, source?.title || source?.publisher || 'Unknown source');
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
  const verificationFailed = (data.trace?.failures || []).some(failure => failure.startsWith('verification:'));
  text(answer, fallback ? 'Source evidence' : data.conclusion || 'Market brief', 'h2');
  if (data.verdict) text(answer, verificationFailed ? 'Overall conclusion unavailable' : data.verdict, 'p').className = 'verdict';
  if (verificationFailed && !fallback) text(answer, 'Some findings were verified, but an overall conclusion is unavailable. Review the supported points and evidence gaps below.', 'p').className = 'summary-limitation';
  if (fallback) text(answer, 'A verified synthesis is unavailable. Review the cited source excerpts below.', 'p');
  if (!data.claims.length) text(answer, data.answer, 'p').className = 'answer-body';
  const sections = {
    summary: 'In brief', what_changed: 'What changed', key_metrics: 'Key metrics', emerging_signals: 'Emerging signals',
    risks: 'Risks to investigate', opportunities: 'Opportunities to investigate',
    watchlist: 'Watchlist', disagreements: 'Where sources disagree',
  };
  Object.entries(sections).forEach(([key, title]) => {
    const claims = key === 'summary' ? (data.summary || [])
      : data.claims.filter(claim => (claim.section || 'key_metrics') === key);
    if (!claims.length) return;
    const isSummary = key === 'summary';
    const expanded = isSummary || key === 'what_changed';
    const section = document.createElement(expanded ? 'section' : 'details');
    section.className = isSummary ? 'brief-section executive-summary' : 'brief-section';
    if (key === 'key_metrics' || key === 'disagreements') section.open = true;
    text(section, title, expanded ? 'h3' : 'summary');
    const list = document.createElement(isSummary ? 'div' : 'ol');
    claims.forEach((claim) => {
      const item = document.createElement(isSummary ? 'div' : 'li');
      text(item, claim.kind === 'calculation' ? 'Fact · calculated' : claim.kind === 'interpretation' ? 'Interpretation' : 'Fact', 'small').className = 'claim-kind';
      text(item, claim.text, 'p');
      const evidence = document.createElement('details');
      evidence.className = 'claim-evidence';
      text(evidence, 'Verify evidence', 'summary');
      const refs = (data.evidence || []).filter(ref => (claim.evidence_ids || []).includes(ref.id));
      refs.forEach(ref => {
        text(evidence, `${ref.reporting_period ? `Reporting period · ${ref.reporting_period} · ` : ''}${ref.location || 'Source excerpt'}`, 'p');
        text(evidence, ref.excerpt, 'blockquote');
        if (ref.materiality_reason) text(evidence, `Screening rule · ${ref.materiality_reason}`, 'p');
        (ref.observations || []).forEach(metric => {
          const source = data.citations.find(c => c.source.id === metric.source_id)?.source;
          text(evidence, `Source value · ${metric.submarket} · ${metric.metric.replaceAll('_', ' ')} · ${metric.value} ${metric.unit} · ${metric.period} · ${metric.definition}`, 'p');
          text(evidence, metric.quotation || 'No verbatim quotation retained.', 'blockquote');
          sourceLink(evidence, source);
        });
      });
      claim.source_ids.forEach(id => {
        const citation = data.citations.find(c => c.source.id === id);
        if (!citation) return;
        const link = text(item, ` [${citation.number}]`, 'a');
        link.href = `#source-${citation.number}`;
        link.addEventListener('click', () => { $(`#source-${citation.number}`).open = true; });
      });
      item.append(evidence);
      list.append(item);
    });
    section.append(list);
    answer.append(section);
  });
  if (data.gaps?.length) {
    text(answer, 'Evidence gaps', 'h3');
    const gaps = document.createElement('ul');
    data.gaps.forEach(gap => text(gaps, gap, 'li'));
    answer.append(gaps);
  }
  if (data.incomplete) text(answer, verificationFailed
    ? 'Some draft claims could not be matched to their citations. Review the verified claims below or ask a narrower question; refreshing data alone will not fix a citation error.'
    : 'Research could not finish all checks. See the limitations below for what remains unresolved.', 'p');
  if (data.freshness) text(answer, `Freshness · ${data.freshness}`, 'p').className = 'answer-meta';
  if (data.warnings?.length) {
    const limitations = document.createElement('details');
    text(limitations, 'Comparison scope and limitations', 'summary');
    data.warnings.forEach(warning => text(limitations, warning, 'p'));
    answer.append(limitations);
  }
  const citations = document.createElement('div');
  citations.className = 'citations';
  text(citations, 'Sources', 'h3');
  (data.citations || []).forEach((citation) => {
    const item = document.createElement('details');
    item.className = 'citation';
    item.id = `source-${citation.number}`;
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
  const followups = document.createElement('div');
  followups.className = 'examples followups';
  ['Expand point 3', 'Compare with last quarter', 'What evidence supports that?', 'Where do sources disagree?', 'What should we watch next?'].forEach(prompt => {
    const button = text(followups, prompt, 'button');
    button.type = 'button';
    button.addEventListener('click', () => {
      question.value = prompt;
      workflow = 'auto';
      context.checked = true;
      question.focus();
      question.scrollIntoView({block: 'center'});
    });
  });
  const copy = text(followups, 'Copy meeting brief', 'button');
  copy.type = 'button';
  copy.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(data.answer + '\n\nSources\n' + data.citations.map(c =>
        `[${c.number}] ${c.source.title} · ${c.source.publisher} · ${c.source.published_at || 'Date unknown'} · ${c.source.url || 'Stored source'}`
      ).join('\n'));
      copy.textContent = 'Brief copied';
    } catch { copy.textContent = 'Copy unavailable — select the answer text'; }
  });
  answer.append(followups);
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
      const excerpt = document.createElement('details');
      excerpt.className = 'citation';
      text(excerpt, `${source?.title || 'Source'} · ${item.location || 'Source excerpt'}`, 'summary');
      text(excerpt, item.excerpt, 'p');
      evidence.append(excerpt);
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
    const payload = { question: value, workflow };
    if (providerOptions.length) {
      payload.provider = $('#llm-provider').value;
      payload.model = $('#llm-model').value.trim();
    }
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
    context.checked = true;
    answer.scrollIntoView({block: 'start'});
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
    else text(container, 'No structured observations are available for the current source versions. Reports may contain figures that could not be validated with an explicit period, geography and unit; inspect the source library.');
    const library = $('#sources');
    library.replaceChildren();
    const list = Object.values(sources);
    $('#source-count').textContent = list.length;
    if (!list.length) text(library, 'No sources yet. Ask a question or refresh data to collect research.', 'p');
    list.forEach(source => {
      const item = document.createElement('article');
      item.className = 'source-item';
      const info = document.createElement('div');
      sourceLink(info, source);
      text(info, `${source.publisher} · ${source.published_at || 'Date unknown'}`, 'small');
      const remove = text(item, '×', 'button');
      remove.type = 'button';
      remove.className = 'remove-source';
      remove.setAttribute('aria-label', `Remove ${source.title}`);
      remove.title = 'Remove source';
      remove.addEventListener('click', () => {
        removingSource = source;
        $('#remove-description').textContent = source.title;
        $('#remove-dialog').showModal();
      });
      item.prepend(info);
      library.append(item);
    });
  } catch {
    $('#metrics').replaceChildren();
    text($('#metrics'), 'Metrics are unavailable right now.');
    $('#source-status').textContent = 'Could not update the source library. Please reload to retry.';
  }
}

async function loadStatus() {
  try {
    const response = await fetch('/api/status');
    if (!response.ok) throw new Error();
    const data = await response.json();
    $('#mode-badge').textContent = 'LIVE RESEARCH';
    $('#provider-status').textContent = `${data.sources} sources · Evidence-led market intelligence`;
    if (!providerOptions.length && data.providers?.length) {
      providerOptions = data.providers;
      const select = $('#llm-provider');
      select.replaceChildren();
      providerOptions.forEach(provider => {
        const option = text(select, provider.id === 'openai' ? 'OpenAI compatible' : 'z.ai', 'option');
        option.value = provider.id;
      });
      select.value = data.provider;
      $('#llm-model').value = data.model;
      select.disabled = false;
      $('#llm-model').disabled = false;
    }
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
    briefing.classList.toggle('hidden', !data);
    if (data) {
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
$('#llm-provider').addEventListener('change', () => {
  $('#llm-model').value = providerOptions.find(provider => provider.id === $('#llm-provider').value)?.model || '';
});

$('#remove-dialog').addEventListener('close', async () => {
  if ($('#remove-dialog').returnValue !== 'remove' || !removingSource) return;
  const source = removingSource;
  removingSource = null;
  const feedback = $('#source-status');
  feedback.dataset.state = 'loading';
  feedback.textContent = 'Removing source…';
  try {
    await requestJson(`/api/sources/${encodeURIComponent(source.id)}`, {method: 'DELETE'});
    chatController?.abort();
    conversationId = null;
    previousQuestion = null;
    context.checked = false;
    context.disabled = true;
    answer.replaceChildren();
    answer.classList.add('hidden');
    await Promise.all([loadMetrics(), loadStatus(), loadBriefing()]);
    feedback.dataset.state = 'success';
    feedback.textContent = 'Source removed.';
  } catch (error) {
    feedback.dataset.state = 'error';
    feedback.textContent = `Could not remove source: ${error.message}`;
  }
});

loadMetrics();
loadStatus();
loadBriefing();

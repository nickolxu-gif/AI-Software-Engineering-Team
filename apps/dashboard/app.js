'use strict';

const REFRESH_INTERVAL_MS = 15000;
const STALE_AFTER_MS = 45000;
const SECONDARY_REQUEST_LIMIT = 4;
const state = {
  view: 'overview', startedAt: Date.now(), lastSuccessAt: 0,
  sourceHeadSha: null, data: null, error: null, selectedTask: null,
  selectedDetail: null, selectedDetailSourceHead: null,
  selectedEvents: null, selectedEventsTaskId: null, selectedEventsSourceHead: null,
  evidence: null, evidenceTaskId: null, evidenceSourceHead: null,
  taskGeneration: 0, evidenceGeneration: 0, refreshing: false,
  taskQuery: '', riskFilter: ''
};
const content = document.querySelector('#content');
const statusRegion = document.querySelector('#status-region');
const sourceMeta = document.querySelector('#source-meta');
let secondaryActive = 0;
const secondaryQueue = [];

async function getJson(url) {
  const response = await fetch(url, { method: 'GET', headers: { Accept: 'application/json' } });
  const payload = await response.json();
  if (!response.ok) {
    const error = new Error(payload.error?.message || '本地工作台请求失败');
    error.code = payload.error?.code || 'REQUEST_FAILED';
    throw error;
  }
  return payload;
}

async function submitIntent(request) {
  const session = assertSourceHead(await getJson('/api/session'), state.sourceHeadSha);
  const response = await fetch('/api/intents', {
    method: 'POST',
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/json',
      'X-Team-Intent-Token': session.data.intent_token
    },
    body: JSON.stringify(request)
  });
  const payload = await response.json();
  if (!response.ok) {
    const error = new Error(payload.error?.message || '受控意图提交失败');
    error.code = payload.error?.code || 'INTENT_SUBMIT_FAILED';
    throw error;
  }
  return assertSourceHead(payload, state.sourceHeadSha);
}

function runSecondary(operation) {
  return new Promise((resolve, reject) => {
    const run = async () => {
      secondaryActive += 1;
      try { resolve(await operation()); } catch (error) { reject(error); }
      finally {
        secondaryActive -= 1;
        const next = secondaryQueue.shift();
        if (next) next();
      }
    };
    if (secondaryActive < SECONDARY_REQUEST_LIMIT) run(); else secondaryQueue.push(run);
  });
}

function assertSourceHead(payload, expectedHead) {
  if (!expectedHead || payload.source_head_sha !== expectedHead) {
    const error = new Error('只读响应来自不同 Git HEAD，已拒绝混合显示');
    error.code = 'SOURCE_HEAD_MISMATCH';
    throw error;
  }
  return payload;
}

function escapeHtml(value) {
  return String(value ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#039;');
}

function badge(value, ok = false) { return `<span class="badge${ok ? ' ok' : ''}">${escapeHtml(value)}</span>`; }
const STATUS_ZH = {
  PLANNED: '已规划', DISPATCHED: '已派发', IN_PROGRESS: '进行中', REVIEWING: '审查中',
  BLOCKED: '阻塞', NEEDS_CLARIFICATION: '需要澄清', NEEDS_DIRECTION: '需要决策',
  NEEDS_HUMAN_APPROVAL: '等待人工批准', PAUSE_REQUESTED: '申请暂停', PAUSED: '已暂停',
  ACCEPTED: '已验收', INTEGRATED: '已集成', RELEASED: '已发布', CLOSED: '已关闭',
  COMPLETED: '已完成', RUNNING: '运行中', FAILED: '失败', OPEN: '未解决',
  RESOLVED: '已解决', PENDING: '待处理', CONSUMED: '已使用', EXPIRED: '已过期',
  REJECTED: '已拒绝', ACCEPT: '通过', MODIFY: '需修改', BLOCK: '不通过', ESCALATE: '升级处理',
  CURRENT: '当前有效', STALE: '已过期', ATTENTION: '需要关注', HEALTHY: '正常',
  UNKNOWN: '状态无法确认'
};
const REASON_ZH = {
  PENDING_APPROVAL: '等待人工批准', OPEN_BLOCKER: '存在未解决阻塞', STALE_REVIEW: '审查已过期',
  STALE_EVIDENCE: '证据已过期', HEAD_DRIFT: 'HEAD 漂移', WORKTREE_UNAVAILABLE: 'Worktree 不可用'
};
function statusText(value) { return `${value} · ${STATUS_ZH[value] || '未定义状态'}`; }
function statusBadge(value, ok = false) { return badge(statusText(value), ok); }
function reasonBadge(value) {
  const stateReason = value.startsWith('STATE_') ? STATUS_ZH[value.slice(6)] : null;
  return badge(`${value} · ${REASON_ZH[value] || stateReason || '需要 Codex 检查'}`);
}
function shown(value, fallback = '当前控制库未记录') { return value === null || value === undefined || value === '' ? fallback : value; }
function shortSha(value) { return value ? String(value).slice(0, 12) : '未记录'; }
function renderLoading() { content.innerHTML = '<div class="empty">正在读取本地控制平面…</div>'; }
function renderEmpty(message) { return `<div class="empty">${escapeHtml(message)}</div>`; }
function renderError(error) { content.innerHTML = `<section class="panel error"><h1>工作台暂不可用</h1><p>${escapeHtml(error.code || 'REQUEST_FAILED')} · ${escapeHtml(error.message)}</p><p>请回到 Codex 处理。</p></section>`; }
function renderStale() {
  const base = state.lastSuccessAt || state.startedAt;
  if (Date.now() - base > STALE_AFTER_MS) statusRegion.textContent = '数据已超过 45 秒未刷新，请回到 Codex 检查本地服务。';
}
function taskRow(task) {
  const reasons = (task.attention_reasons || []).map(reasonBadge).join('');
  return `<article class="row"><div class="row-head"><button class="task-link" data-task="${escapeHtml(task.dispatch_id)}">${escapeHtml(task.title)}</button>${statusBadge(task.effective_state, !reasons)}</div><div class="meta">${escapeHtml(task.dispatch_id)} · 风险 ${escapeHtml(task.risk_level)} · Owner ${escapeHtml(shown(task.owner))}</div><p>${escapeHtml(task.objective)}</p><div>${reasons}</div></article>`;
}
function bindTaskLinks() {
  document.querySelectorAll('[data-task]').forEach(button => button.addEventListener('click', async () => {
    state.selectedTask = button.dataset.task;
    state.selectedDetail = state.data.details.find(detail => detail.task.dispatch_id === state.selectedTask) || null;
    state.selectedDetailSourceHead = state.selectedDetail ? state.sourceHeadSha : null;
    state.selectedEvents = null; state.selectedEventsTaskId = null; state.selectedEventsSourceHead = null;
    if (state.evidenceTaskId !== state.selectedTask) {
      state.evidence = null; state.evidenceTaskId = null; state.evidenceSourceHead = null;
    }
    state.view = 'tasks'; updateNav(); renderTasks(); content.focus();
    await loadTaskDetail(state.selectedTask);
  }));
  document.querySelectorAll('[data-evidence]').forEach(button => button.addEventListener('click', async () => {
    state.selectedTask = button.dataset.evidence; state.view = 'evidence'; updateNav();
    await loadEvidence(); content.focus();
  }));
}
function renderOverview() {
  const project = state.data.project; const counts = project.counts; const queue = project.attention_items || [];
  content.innerHTML = `<h1>工程总览</h1><p class="subhead">异常优先 · 所有工程动作仍由 Codex 执行</p>
    <section class="banner"><div><strong>${project.health === 'ATTENTION' ? `项目状态 ATTENTION · ${escapeHtml(STATUS_ZH.ATTENTION)}${queue.length ? ` · ${escapeHtml(queue.length)} 项异常` : ''}` : `项目状态 HEALTHY · ${escapeHtml(STATUS_ZH.HEALTHY)}`}</strong><div class="meta">来源 HEAD ${escapeHtml(project.head_sha)}</div></div><div>请回到 Codex 处理</div></section>
    <section class="cards"><div class="card">活跃任务<div class="metric">${escapeHtml(counts.active_tasks)}</div></div><div class="card">阻塞任务<div class="metric">${escapeHtml(counts.blocked_tasks)}</div></div><div class="card">待审批<div class="metric">${escapeHtml(counts.pending_approvals)}</div></div><div class="card">过期证据<div class="metric">${escapeHtml(counts.stale_reviews + counts.stale_evidence)}</div></div></section>
    <div class="grid"><section class="panel"><h2>优先队列</h2>${queue.length ? queue.map(taskRow).join('') : renderEmpty('暂无异常任务')}</section><aside class="panel"><h2>Codex 建议</h2><p>优先处理审批、阻塞和方向问题。此页面不会执行修改。</p><p class="meta">分支 ${escapeHtml(project.branch)} · Worktree ${escapeHtml(project.worktree_count)}</p></aside></div>`;
  bindTaskLinks();
}
function renderTimeline(page) {
  if (page === null) return renderEmpty('正在读取生命周期事件…');
  const events = page.items || [];
  if (!events.length) return renderEmpty('当前没有生命周期事件');
  const items = events.map(item => {
    const details = item.details || {};
    const transition = details.from || details.to ? ` · ${shown(details.from, '未知')} → ${shown(details.to, '未知')}` : '';
    const reason = details.reason ? `<div>${escapeHtml(details.reason)}</div>` : '';
    return `<li><strong>${escapeHtml(item.summary)}</strong><div class="meta">${escapeHtml(item.event_type)}${escapeHtml(transition)} · ${escapeHtml(item.created_at)}</div>${reason}</li>`;
  }).join('');
  const bounded = page.has_more ? '<p class="meta">仅显示最新 100 条；更早事件请回到 Codex 查询。</p>' : '';
  return `<ol class="timeline">${items}</ol>${bounded}`;
}
function renderTaskDetail(detail) {
  if (!state.selectedTask) return '';
  if (!detail) return `<section class="panel"><h2>${escapeHtml(state.selectedTask)}</h2>${renderEmpty('正在读取任务详情…')}</section>`;
  const task = detail.task;
  const nextStep = (detail.blockers || []).some(item => item.status === 'OPEN') ? '先处理未解决阻塞' : detail.pending_approval_count ? '等待 Human 审批' : !detail.valid_acceptance ? '完成独立验收' : '回到 Codex 确认下一步';
  const agents = (detail.agents || []).map(item => `<div class="row"><div class="row-head"><strong>${escapeHtml(item.agent_id)}</strong>${statusBadge(item.state, item.state === 'COMPLETED')}</div><div class="meta">${escapeHtml(item.role)} · 进度 ${escapeHtml(item.progress)}% · ${escapeHtml(item.updated_at)}</div></div>`).join('') || renderEmpty('无 Agent 汇报');
  const blockers = (detail.blockers || []).map(item => `<div class="row"><div class="row-head"><strong>${escapeHtml(item.reason)}</strong>${statusBadge(item.status, item.status === 'RESOLVED')}</div><div class="meta">Owner ${escapeHtml(shown(item.owner))} · ${escapeHtml(item.created_at)}</div><p>${escapeHtml(shown(item.resolution_condition, '未记录解除条件'))}</p></div>`).join('') || renderEmpty('无阻塞');
  const reviews = (detail.reviews || []).map(item => `<div class="row"><div class="row-head"><strong>${escapeHtml(item.reviewer)}</strong>${statusBadge(item.disposition, item.disposition === 'ACCEPT' && !item.stale)}</div><div class="meta">${item.stale ? escapeHtml(statusText('STALE')) : escapeHtml(statusText('CURRENT'))} · ${escapeHtml(item.created_at)}</div></div>`).join('') || renderEmpty('无审查记录');
  const intents = (detail.intents || []).map(item => `<div class="row"><div class="row-head"><strong>${escapeHtml(item.action)}</strong>${statusBadge(item.status, item.status === 'APPLIED')}</div><div class="meta">${escapeHtml(shown(item.result_code, '尚未由 Codex 处理'))} · ${escapeHtml(item.created_at)}</div></div>`).join('') || renderEmpty('尚未提交受控意图');
  const terminal = ['CLOSED', 'RELEASED'].includes(task.state);
  const controls = `<section class="intent-panel"><h2>提交给 Codex</h2><p class="meta">仅创建待处理意图，不会在浏览器执行 Git、合并、发布或审批。</p><div class="intent-controls"><button data-intent-action="PAUSE_REQUEST"${terminal ? ' disabled' : ''}>申请暂停</button><button data-intent-action="RESUME_REQUEST"${task.state === 'PAUSED' && !terminal ? '' : ' disabled'}>申请恢复</button><button data-intent-action="APPROVAL_REQUEST"${terminal ? ' disabled' : ''}>请求审批准备</button></div></section>`;
  return `<section class="panel task-detail"><div class="row-head"><div><h2>${escapeHtml(task.title)}</h2><div class="meta">${escapeHtml(task.dispatch_id)}</div></div><button class="task-link" data-evidence="${escapeHtml(task.dispatch_id)}">查看证据索引</button></div>
    <p>${escapeHtml(task.objective)}</p><div>${statusBadge(task.effective_state)}${badge(`风险 ${task.risk_level}`)}${badge(detail.head_drift ? 'HEAD 漂移' : 'HEAD 一致', !detail.head_drift)}${badge(detail.valid_acceptance ? '有效验收' : '尚无有效验收', detail.valid_acceptance)}</div>
    <dl class="facts"><div><dt>Owner</dt><dd>${escapeHtml(shown(task.owner))}</dd></div><div><dt>Executor</dt><dd>${escapeHtml(shown(task.agent))}</dd></div><div><dt>生命周期状态</dt><dd>${escapeHtml(statusText(task.state))}</dd></div><div><dt>最近更新</dt><dd>${escapeHtml(task.updated_at)}</dd></div><div><dt>Task Base SHA</dt><dd>${escapeHtml(shortSha(task.task_base_sha))}</dd></div><div><dt>Current HEAD</dt><dd>${escapeHtml(shortSha(task.current_head_sha))}</dd></div><div><dt>Actual HEAD</dt><dd>${escapeHtml(shortSha(detail.actual_head_sha))}</dd></div><div><dt>分支</dt><dd>${escapeHtml(shown(task.branch))}</dd></div><div class="wide"><dt>Worktree</dt><dd>${escapeHtml(shown(task.worktree_path))}</dd></div></dl>
    <div class="cards compact"><div class="card">Agent<div class="metric">${escapeHtml((detail.agents || []).length)}</div></div><div class="card">未解决阻塞<div class="metric">${escapeHtml((detail.blockers || []).filter(item => item.status === 'OPEN').length)}</div></div><div class="card">待审批<div class="metric">${escapeHtml(detail.pending_approval_count)}</div></div><div class="card">证据<div class="metric">${escapeHtml(detail.evidence_count)}</div></div></div>
    <h2>当前下一步</h2><p>${escapeHtml(nextStep)}；所有工程动作请回到 Codex 处理。</p>
    ${controls}<h2>受控意图</h2>${intents}<h2>Agent 汇报</h2>${agents}<h2>阻塞</h2>${blockers}<h2>审查</h2>${reviews}<h2>生命周期事件</h2>${renderTimeline(state.selectedEvents)}</section>`;
}

function newIntentId() {
  if (!globalThis.crypto?.randomUUID) throw Object.assign(new Error('当前浏览器不支持安全意图标识'), { code: 'BROWSER_CRYPTO_UNAVAILABLE' });
  return globalThis.crypto.randomUUID();
}

function bindIntentControls() {
  document.querySelectorAll('[data-intent-action]').forEach(button => button.addEventListener('click', async () => {
    const detail = state.selectedDetail;
    if (!detail || button.disabled) return;
    const action = button.dataset.intentAction;
    let parameters = {};
    if (action === 'APPROVAL_REQUEST') {
      const confirmation = window.prompt('请说明需要 Codex 准备的审批事项（最多 256 字）：');
      if (!confirmation) return;
      parameters = { requested_action: 'HUMAN_APPROVAL', requested_parameters: {}, confirmation };
    }
    button.disabled = true;
    statusRegion.textContent = '正在提交给 Codex…';
    try {
      await submitIntent({
        dispatch_id: detail.task.dispatch_id,
        action,
        target_sha: detail.task.current_head_sha,
        idempotency_key: newIntentId(),
        parameters
      });
      statusRegion.textContent = '已提交给 Codex，尚未执行';
      await loadTaskDetail(detail.task.dispatch_id);
    } catch (error) {
      statusRegion.textContent = `提交失败：${error.code || 'INTENT_SUBMIT_FAILED'}；请回到 Codex 处理`;
      button.disabled = false;
    }
  }));
}
function renderTasks() {
  const tasks = (state.data.tasks || []).filter(task => (!state.riskFilter || task.risk_level === state.riskFilter) && (!state.taskQuery || `${task.dispatch_id} ${task.title} ${task.objective}`.toLowerCase().includes(state.taskQuery.toLowerCase())));
  const detailIsCurrent = state.selectedDetail && state.selectedDetailSourceHead === state.sourceHeadSha && state.selectedDetail.task.dispatch_id === state.selectedTask;
  const eventsAreCurrent = state.selectedEvents && state.selectedEventsTaskId === state.selectedTask && state.selectedEventsSourceHead === state.sourceHeadSha;
  if (!eventsAreCurrent) { state.selectedEvents = null; state.selectedEventsTaskId = null; state.selectedEventsSourceHead = null; }
  content.innerHTML = `<h1>任务</h1><p class="subhead">当前首屏最多 100 项；点击任意任务读取完整详情</p><div class="filters"><label>搜索 <input id="task-search" value="${escapeHtml(state.taskQuery)}"></label><label>风险 <select id="risk-filter"><option value="">全部</option>${['L1','L2','L3'].map(value => `<option${state.riskFilter === value ? ' selected' : ''}>${value}</option>`).join('')}</select></label></div><div class="grid"><section>${tasks.length ? tasks.map(taskRow).join('') : renderEmpty('没有匹配任务')}</section>${renderTaskDetail(detailIsCurrent ? state.selectedDetail : null)}</div>`;
  bindTaskLinks();
  bindIntentControls();
  document.querySelector('#task-search').addEventListener('input', event => { state.taskQuery = event.target.value; renderTasks(); const input = document.querySelector('#task-search'); input.focus(); input.setSelectionRange(input.value.length, input.value.length); });
  document.querySelector('#risk-filter').addEventListener('change', event => { state.riskFilter = event.target.value; renderTasks(); document.querySelector('#risk-filter').focus(); });
}
function renderAgents() {
  const agents = state.data.details.flatMap(detail => (detail.agents || []).map(agent => ({ ...agent, task: detail.task.dispatch_id })));
  content.innerHTML = `<h1>Agents</h1><p class="subhead">首屏活跃任务的最近 Agent 汇报</p>${agents.length ? agents.map(agent => `<article class="row"><div class="row-head"><strong>${escapeHtml(agent.agent_id)}</strong>${statusBadge(agent.state, agent.state === 'COMPLETED')}</div><div class="meta">${escapeHtml(agent.role)} · 任务 ${escapeHtml(agent.task)} · 进度 ${escapeHtml(agent.progress)}%</div></article>`).join('') : renderEmpty('当前没有 Agent 汇报')}`;
}
function renderApprovals() {
  const approvals = state.data.approvals || [];
  content.innerHTML = `<h1>审批</h1><p class="subhead">仅显示审批索引，不在浏览器中操作</p>${approvals.length ? approvals.map(item => `<article class="row"><div class="row-head"><strong>${escapeHtml(item.action)}</strong>${statusBadge(item.status, item.status === 'CONSUMED')}</div><div class="meta">任务 ${escapeHtml(item.dispatch_id)} · 到期 ${escapeHtml(item.expires_at)}</div><p>请回到 Codex 处理</p></article>`).join('') : renderEmpty('当前没有审批记录')}`;
}
function renderEvidence() {
  if (!state.selectedTask) { content.innerHTML = `<h1>证据</h1><p class="subhead">请先从任务列表选择任务</p>${renderEmpty('未选择任务')}`; return; }
  const cacheMatches = state.evidenceTaskId === state.selectedTask && state.evidenceSourceHead === state.sourceHeadSha;
  if (!cacheMatches) { content.innerHTML = `<h1>证据</h1><p class="subhead">任务 ${escapeHtml(state.selectedTask)} 的只读证据索引</p>${renderEmpty('正在读取证据索引…')}`; return; }
  const items = state.evidence || [];
  content.innerHTML = `<h1>证据</h1><p class="subhead">任务 ${escapeHtml(state.selectedTask)} 的只读证据索引</p>${items.length ? items.map(item => `<article class="row"><div class="row-head"><strong>${escapeHtml(item.kind)}</strong>${statusBadge(item.stale ? 'STALE' : 'CURRENT', !item.stale)}</div><div class="meta">${escapeHtml(item.relative_path)} · ${escapeHtml(item.source_sha)}</div></article>`).join('') : renderEmpty('该任务尚无证据')}`;
}
function renderCurrent() {
  if (state.error && !state.data) return renderError(state.error);
  ({ overview: renderOverview, tasks: renderTasks, agents: renderAgents, approvals: renderApprovals, evidence: renderEvidence }[state.view] || renderOverview)();
}
function updateNav() { document.querySelectorAll('nav [data-view]').forEach(button => { const active = button.dataset.view === state.view; if (active) button.setAttribute('aria-current', 'page'); else button.removeAttribute('aria-current'); }); }
async function mapLimit(values, limit, mapper) {
  const results = []; let index = 0;
  async function worker() { while (index < values.length) { const current = index++; results[current] = await mapper(values[current]); } }
  await Promise.all(Array.from({ length: Math.min(limit, values.length) }, worker)); return results;
}
async function getTaskPayload(taskId) { return runSecondary(() => getJson(`/api/tasks/${encodeURIComponent(taskId)}`)); }
async function getEventPayload(taskId) { return runSecondary(() => getJson(`/api/tasks/${encodeURIComponent(taskId)}/events?limit=100&offset=0`)); }
async function loadTaskDetail(taskId) {
  const generation = ++state.taskGeneration; const expectedHead = state.sourceHeadSha;
  try {
    const [detailPayload, eventPayload] = await Promise.all([getTaskPayload(taskId), getEventPayload(taskId)]);
    assertSourceHead(detailPayload, expectedHead); assertSourceHead(eventPayload, expectedHead);
    if (generation !== state.taskGeneration || taskId !== state.selectedTask || expectedHead !== state.sourceHeadSha) return;
    state.selectedDetail = detailPayload.data; state.selectedDetailSourceHead = expectedHead;
    state.selectedEvents = eventPayload.data; state.selectedEventsTaskId = taskId;
    state.selectedEventsSourceHead = expectedHead;
    state.error = null; renderTasks();
  } catch (error) {
    if (generation !== state.taskGeneration || taskId !== state.selectedTask || expectedHead !== state.sourceHeadSha) return;
    state.error = error; renderError(error);
  }
}
async function loadEvidence() {
  const taskId = state.selectedTask; const generation = ++state.evidenceGeneration; const expectedHead = state.sourceHeadSha;
  content.innerHTML = renderEmpty('正在读取证据索引…');
  try {
    const payload = await runSecondary(() => getJson(`/api/tasks/${encodeURIComponent(taskId)}/evidence?limit=100&offset=0`));
    assertSourceHead(payload, expectedHead);
    if (generation !== state.evidenceGeneration || taskId !== state.selectedTask || expectedHead !== state.sourceHeadSha) return;
    state.evidence = payload.data.items; state.evidenceTaskId = taskId;
    state.evidenceSourceHead = expectedHead; state.error = null;
  } catch (error) {
    if (generation !== state.evidenceGeneration || taskId !== state.selectedTask || expectedHead !== state.sourceHeadSha) return;
    state.error = error; return renderError(error);
  }
  renderEvidence();
}
async function refresh() {
  if (state.refreshing) return;
  state.refreshing = true;
  let reloadSelectedTask = null;
  let reloadEvidenceTask = null;
  if (!state.data) renderLoading();
  try {
    const focusedTask = document.activeElement?.dataset?.task || null;
    const selectedTaskAtStart = state.selectedTask;
    const [project, tasks, approvals] = await Promise.all([getJson('/api/project'), getJson('/api/tasks?limit=100&offset=0'), getJson('/api/approvals?limit=100&offset=0')]);
    const sourceHeads = new Set([project.source_head_sha, tasks.source_head_sha, approvals.source_head_sha]);
    if (sourceHeads.size !== 1) throw Object.assign(new Error('并行快照来源 HEAD 不一致'), { code: 'SOURCE_HEAD_MISMATCH' });
    const expectedHead = project.source_head_sha;
    const active = tasks.data.items.filter(task => !['CLOSED', 'RELEASED'].includes(task.state));
    const detailTargets = [...active];
    if (selectedTaskAtStart && !detailTargets.some(task => task.dispatch_id === selectedTaskAtStart)) detailTargets.push({ dispatch_id: selectedTaskAtStart });
    const detailPayloads = await mapLimit(detailTargets, SECONDARY_REQUEST_LIMIT, task => getTaskPayload(task.dispatch_id));
    detailPayloads.forEach(payload => assertSourceHead(payload, expectedHead));
    let eventPayload = null;
    if (selectedTaskAtStart) {
      eventPayload = await getEventPayload(selectedTaskAtStart);
      assertSourceHead(eventPayload, expectedHead);
    }
    const details = detailPayloads.map(payload => payload.data);
    const selectedDetail = details.find(detail => detail.task.dispatch_id === selectedTaskAtStart) || null;
    state.data = { project: project.data, tasks: tasks.data.items, approvals: approvals.data.items, details };
    state.sourceHeadSha = expectedHead;
    if (state.selectedTask === selectedTaskAtStart) {
      state.selectedDetail = selectedDetail;
      state.selectedDetailSourceHead = selectedDetail ? expectedHead : null;
      state.selectedEvents = eventPayload ? eventPayload.data : null;
      state.selectedEventsTaskId = eventPayload ? selectedTaskAtStart : null;
      state.selectedEventsSourceHead = eventPayload ? expectedHead : null;
    } else if (
      state.selectedTask &&
      (
        state.selectedDetailSourceHead !== expectedHead ||
        state.selectedDetail?.task?.dispatch_id !== state.selectedTask ||
        state.selectedEventsSourceHead !== expectedHead ||
        state.selectedEventsTaskId !== state.selectedTask
      )
    ) {
      state.selectedDetail = null; state.selectedDetailSourceHead = null;
      state.selectedEvents = null; state.selectedEventsTaskId = null; state.selectedEventsSourceHead = null;
      reloadSelectedTask = state.selectedTask;
    }
    if (state.evidenceSourceHead !== expectedHead) {
      state.evidence = null; state.evidenceTaskId = null; state.evidenceSourceHead = null;
      if (state.view === 'evidence' && state.selectedTask) reloadEvidenceTask = state.selectedTask;
    }
    state.lastSuccessAt = Date.now(); state.error = null;
    sourceMeta.textContent = `HEAD ${state.sourceHeadSha.slice(0, 10)} · 刷新于 ${new Date().toLocaleTimeString('zh-CN')}`;
    statusRegion.textContent = project.data.health === 'ATTENTION' ? '项目需要关注，请回到 Codex 处理' : '本地控制平面已同步';
    renderCurrent();
    if (focusedTask) Array.from(document.querySelectorAll('[data-task]')).find(button => button.dataset.task === focusedTask)?.focus();
  } catch (error) {
    state.error = error;
    if (!state.data) renderError(error); else { statusRegion.textContent = `刷新失败：${error.code || 'REQUEST_FAILED'}；保留上次数据`; renderStale(); }
  } finally {
    state.refreshing = false;
    if (reloadSelectedTask && state.selectedTask === reloadSelectedTask) loadTaskDetail(reloadSelectedTask);
    if (reloadEvidenceTask && state.selectedTask === reloadEvidenceTask) loadEvidence();
  }
}
document.querySelectorAll('nav [data-view]').forEach(button => button.addEventListener('click', async () => {
  state.view = button.dataset.view; updateNav();
  const evidenceIsCurrent = state.evidenceTaskId === state.selectedTask && state.evidenceSourceHead === state.sourceHeadSha;
  if (state.view === 'evidence' && state.selectedTask && !evidenceIsCurrent) await loadEvidence(); else renderCurrent();
  content.focus();
}));
refresh(); setInterval(refresh, REFRESH_INTERVAL_MS); setInterval(renderStale, 5000);

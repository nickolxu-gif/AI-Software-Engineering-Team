'use strict';

const REFRESH_INTERVAL_MS = 15000;
const STALE_AFTER_MS = 45000;
const state = { view: 'overview', startedAt: Date.now(), lastSuccessAt: 0, sourceHeadSha: null, data: null, error: null, selectedTask: null, selectedDetail: null, evidence: null, evidenceGeneration: 0, refreshing: false, taskQuery: '', riskFilter: '' };
const content = document.querySelector('#content');
const statusRegion = document.querySelector('#status-region');
const sourceMeta = document.querySelector('#source-meta');

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

function escapeHtml(value) {
  return String(value ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#039;');
}
function badge(value, ok = false) { return `<span class="badge${ok ? ' ok' : ''}">${escapeHtml(value)}</span>`; }
const STATUS_ZH = { PLANNED: '已规划', DISPATCHED: '已派发', IN_PROGRESS: '进行中', REVIEWING: '审查中', BLOCKED: '阻塞', NEEDS_DIRECTION: '需要决策', NEEDS_HUMAN_APPROVAL: '等待人工批准', ACCEPTED: '已验收', CLOSED: '已关闭', COMPLETED: '已完成' };
function statusBadge(value, ok = false) { return badge(`${value} · ${STATUS_ZH[value] || '状态'}`, ok); }
function renderLoading() { content.innerHTML = '<div class="empty">正在读取本地控制平面…</div>'; }
function renderEmpty(message) { return `<div class="empty">${escapeHtml(message)}</div>`; }
function renderError(error) { content.innerHTML = `<section class="panel error"><h1>工作台暂不可用</h1><p>${escapeHtml(error.code || 'REQUEST_FAILED')} · ${escapeHtml(error.message)}</p><p>请回到 Codex 处理。</p></section>`; }
function renderStale() { const base = state.lastSuccessAt || state.startedAt; if (Date.now() - base > STALE_AFTER_MS) statusRegion.textContent = '数据已超过 45 秒未刷新，请回到 Codex 检查本地服务。'; }
function taskRow(task) {
  const reasons = (task.attention_reasons || []).map(value => badge(value)).join('');
  return `<article class="row"><div class="row-head"><button class="task-link" data-task="${escapeHtml(task.dispatch_id)}">${escapeHtml(task.title)}</button>${statusBadge(task.effective_state, !reasons)}</div><div class="meta">${escapeHtml(task.dispatch_id)} · ${escapeHtml(task.risk_level)} · ${escapeHtml(task.owner)}</div><p>${escapeHtml(task.objective)}</p><div>${reasons}</div></article>`;
}
function bindTaskLinks() {
  document.querySelectorAll('[data-task]').forEach(button => button.addEventListener('click', () => {
    state.selectedTask = button.dataset.task; state.selectedDetail = state.data.details.find(detail => detail.task.dispatch_id === state.selectedTask) || null; state.view = 'tasks'; updateNav(); renderTasks(); content.focus();
  }));
  document.querySelectorAll('[data-evidence]').forEach(button => button.addEventListener('click', async () => { state.selectedTask = button.dataset.evidence; state.view = 'evidence'; updateNav(); await loadEvidence(); content.focus(); }));
}
function renderOverview() {
  const project = state.data.project; const counts = project.counts; const queue = project.attention_items || [];
  content.innerHTML = `<h1>工程总览</h1><p class="subhead">异常优先 · 所有工程动作仍由 Codex 执行</p>
    <section class="banner"><div><strong>${project.health === 'ATTENTION' ? `项目状态 ATTENTION${queue.length ? ` · ${escapeHtml(queue.length)} 项异常` : ''}` : '项目状态 HEALTHY · 当前没有待处理异常'}</strong><div class="meta">来源 HEAD ${escapeHtml(project.head_sha)}</div></div><div>请回到 Codex 处理</div></section>
    <section class="cards"><div class="card">活跃任务<div class="metric">${escapeHtml(counts.active_tasks)}</div></div><div class="card">阻塞任务<div class="metric">${escapeHtml(counts.blocked_tasks)}</div></div><div class="card">待审批<div class="metric">${escapeHtml(counts.pending_approvals)}</div></div><div class="card">过期证据<div class="metric">${escapeHtml(counts.stale_reviews + counts.stale_evidence)}</div></div></section>
    <div class="grid"><section class="panel"><h2>优先队列</h2>${queue.length ? queue.map(taskRow).join('') : renderEmpty('暂无异常任务')}</section><aside class="panel"><h2>Codex 建议</h2><p>优先处理审批、阻塞和方向问题。此页面不会执行修改。</p><p class="meta">分支 ${escapeHtml(project.branch)} · Worktree ${escapeHtml(project.worktree_count)}</p></aside></div>`;
  bindTaskLinks();
}
function renderTasks() {
  const tasks = (state.data.tasks || []).filter(task => (!state.riskFilter || task.risk_level === state.riskFilter) && (!state.taskQuery || `${task.dispatch_id} ${task.title} ${task.objective}`.toLowerCase().includes(state.taskQuery.toLowerCase())));
  const detail = state.selectedDetail;
  const detailHtml = detail ? `<section class="panel"><div class="row-head"><h2>${escapeHtml(detail.task.title)}</h2><button class="task-link" data-evidence="${escapeHtml(detail.task.dispatch_id)}">查看证据</button></div><p>${escapeHtml(detail.task.objective)}</p><div>${badge(detail.head_drift ? 'HEAD 漂移' : 'HEAD 一致', !detail.head_drift)}${badge(detail.valid_acceptance ? '有效验收' : '尚无有效验收', detail.valid_acceptance)}</div><h2>阻塞与审查</h2>${(detail.blockers || []).map(item => `<div class="row">${escapeHtml(item.reason)} · ${statusBadge(item.status)}</div>`).join('') || renderEmpty('无阻塞')}${(detail.reviews || []).map(item => `<div class="row">${escapeHtml(item.reviewer)} · ${statusBadge(item.disposition, !item.stale)}</div>`).join('') || renderEmpty('无审查记录')}<p class="meta">最近事件：${escapeHtml(detail.latest_event?.summary || '无')} · 下一步请回到 Codex 处理</p></section>` : '';
  content.innerHTML = `<h1>任务</h1><p class="subhead">按异常、风险与更新时间排序；点击任务查看详情</p><div class="filters"><label>搜索 <input id="task-search" value="${escapeHtml(state.taskQuery)}"></label><label>风险 <select id="risk-filter"><option value="">全部</option>${['L1','L2','L3'].map(value => `<option${state.riskFilter === value ? ' selected' : ''}>${value}</option>`).join('')}</select></label></div><div class="grid"><section>${tasks.length ? tasks.map(taskRow).join('') : renderEmpty('没有匹配任务')}</section>${detailHtml}</div>`;
  bindTaskLinks();
  document.querySelector('#task-search').addEventListener('input', event => { state.taskQuery = event.target.value; renderTasks(); document.querySelector('#task-search').focus(); });
  document.querySelector('#risk-filter').addEventListener('change', event => { state.riskFilter = event.target.value; renderTasks(); document.querySelector('#risk-filter').focus(); });
}
function renderAgents() {
  const agents = state.data.details.flatMap(detail => (detail.agents || []).map(agent => ({ ...agent, task: detail.task.dispatch_id })));
  content.innerHTML = `<h1>Agents</h1><p class="subhead">首屏活跃任务的最近 Agent 汇报</p>${agents.length ? agents.map(agent => `<article class="row"><div class="row-head"><strong>${escapeHtml(agent.agent_id)}</strong>${statusBadge(agent.state, agent.state === 'COMPLETED')}</div><div class="meta">${escapeHtml(agent.role)} · 任务 ${escapeHtml(agent.task)} · 进度 ${escapeHtml(agent.progress)}%</div></article>`).join('') : renderEmpty('当前没有 Agent 汇报')}`;
}
function renderApprovals() {
  const approvals = state.data.approvals || [];
  content.innerHTML = `<h1>审批</h1><p class="subhead">仅显示审批索引，不在浏览器中操作</p>${approvals.length ? approvals.map(item => `<article class="row"><div class="row-head"><strong>${escapeHtml(item.action)}</strong>${badge(item.status, item.status === 'CONSUMED')}</div><div class="meta">任务 ${escapeHtml(item.dispatch_id)} · 到期 ${escapeHtml(item.expires_at)}</div><p>请回到 Codex 处理</p></article>`).join('') : renderEmpty('当前没有审批记录')}`;
}
function renderEvidence() {
  if (!state.selectedTask) { content.innerHTML = `<h1>证据</h1><p class="subhead">请先从任务列表选择任务</p>${renderEmpty('未选择任务')}`; return; }
  const items = state.evidence || [];
  content.innerHTML = `<h1>证据</h1><p class="subhead">任务 ${escapeHtml(state.selectedTask)} 的只读证据索引</p>${items.length ? items.map(item => `<article class="row"><div class="row-head"><strong>${escapeHtml(item.kind)}</strong>${badge(item.stale ? 'STALE' : 'CURRENT', !item.stale)}</div><div class="meta">${escapeHtml(item.relative_path)} · ${escapeHtml(item.source_sha)}</div></article>`).join('') : renderEmpty('该任务尚无证据')}`;
}
function renderCurrent() { if (state.error && !state.data) return renderError(state.error); ({ overview: renderOverview, tasks: renderTasks, agents: renderAgents, approvals: renderApprovals, evidence: renderEvidence }[state.view] || renderOverview)(); }
function updateNav() { document.querySelectorAll('nav [data-view]').forEach(button => { const active = button.dataset.view === state.view; if (active) button.setAttribute('aria-current', 'page'); else button.removeAttribute('aria-current'); }); }
async function mapLimit(values, limit, mapper) { const results = []; let index = 0; async function worker() { while (index < values.length) { const current = index++; results[current] = await mapper(values[current]); } } await Promise.all(Array.from({ length: Math.min(limit, values.length) }, worker)); return results; }
async function loadEvidence() { const taskId = state.selectedTask; const generation = ++state.evidenceGeneration; content.innerHTML = renderEmpty('正在读取证据索引…'); try { const payload = await getJson(`/api/tasks/${encodeURIComponent(taskId)}/evidence?limit=100&offset=0`); if (generation !== state.evidenceGeneration || taskId !== state.selectedTask) return; state.evidence = payload.data.items; state.error = null; } catch (error) { if (generation !== state.evidenceGeneration) return; state.error = error; return renderError(error); } renderEvidence(); }
async function refresh() {
  if (state.refreshing) return;
  state.refreshing = true;
  if (!state.data) renderLoading();
  try {
    const focusedTask = document.activeElement?.dataset?.task || null;
    const [project, tasks, approvals] = await Promise.all([getJson('/api/project'), getJson('/api/tasks?limit=100&offset=0'), getJson('/api/approvals?limit=100&offset=0')]);
    if (new Set([project.source_head_sha, tasks.source_head_sha, approvals.source_head_sha]).size !== 1) throw Object.assign(new Error('并行快照来源 HEAD 不一致'), { code: 'SOURCE_HEAD_MISMATCH' });
    const active = tasks.data.items.filter(task => !['CLOSED', 'RELEASED'].includes(task.state));
    const details = await mapLimit(active, 4, task => getJson(`/api/tasks/${encodeURIComponent(task.dispatch_id)}`).then(payload => payload.data));
    state.data = { project: project.data, tasks: tasks.data.items, approvals: approvals.data.items, details };
    state.sourceHeadSha = project.source_head_sha; state.lastSuccessAt = Date.now(); state.error = null;
    sourceMeta.textContent = `HEAD ${state.sourceHeadSha.slice(0, 10)} · 刷新于 ${new Date().toLocaleTimeString('zh-CN')}`;
    statusRegion.textContent = project.data.health === 'ATTENTION' ? '项目需要关注，请回到 Codex 处理' : '本地控制平面已同步'; renderCurrent();
    if (focusedTask) Array.from(document.querySelectorAll('[data-task]')).find(button => button.dataset.task === focusedTask)?.focus();
  } catch (error) { state.error = error; if (!state.data) renderError(error); else { statusRegion.textContent = `刷新失败：${error.code || 'REQUEST_FAILED'}；保留上次数据`; renderStale(); } } finally { state.refreshing = false; }
}
document.querySelectorAll('nav [data-view]').forEach(button => button.addEventListener('click', () => { state.view = button.dataset.view; updateNav(); renderCurrent(); content.focus(); }));
refresh(); setInterval(refresh, REFRESH_INTERVAL_MS); setInterval(renderStale, 5000);

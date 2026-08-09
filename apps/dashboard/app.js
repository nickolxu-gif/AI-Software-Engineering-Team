'use strict';

const REFRESH_INTERVAL_MS = 15000;
const STALE_AFTER_MS = 45000;
const state = { view: 'overview', lastSuccessAt: 0, sourceHeadSha: null, data: null, error: null, selectedTask: null, evidence: null };
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
function renderLoading() { content.innerHTML = '<div class="empty">正在读取本地控制平面…</div>'; }
function renderEmpty(message) { return `<div class="empty">${escapeHtml(message)}</div>`; }
function renderError(error) { content.innerHTML = `<section class="panel error"><h1>工作台暂不可用</h1><p>${escapeHtml(error.code || 'REQUEST_FAILED')} · ${escapeHtml(error.message)}</p><p>请回到 Codex 处理。</p></section>`; }
function renderStale() { if (state.lastSuccessAt && Date.now() - state.lastSuccessAt > STALE_AFTER_MS) statusRegion.textContent = '数据已超过 45 秒未刷新，请回到 Codex 检查本地服务。'; }
function taskRow(task) {
  const reasons = (task.attention_reasons || []).map(value => badge(value)).join('');
  return `<article class="row"><div class="row-head"><button class="task-link" data-task="${escapeHtml(task.dispatch_id)}">${escapeHtml(task.title)}</button>${badge(task.effective_state, !reasons)}</div><div class="meta">${escapeHtml(task.dispatch_id)} · ${escapeHtml(task.risk_level)} · ${escapeHtml(task.owner)}</div><p>${escapeHtml(task.objective)}</p><div>${reasons}</div></article>`;
}
function bindTaskLinks() {
  document.querySelectorAll('[data-task]').forEach(button => button.addEventListener('click', async () => {
    state.selectedTask = button.dataset.task;
    state.view = 'evidence';
    updateNav();
    await loadEvidence();
  }));
}
function renderOverview() {
  const project = state.data.project; const counts = project.counts; const queue = project.attention_items || [];
  content.innerHTML = `<h1>工程总览</h1><p class="subhead">异常优先 · 所有工程动作仍由 Codex 执行</p>
    <section class="banner"><div><strong>${queue.length ? `发现 ${escapeHtml(queue.length)} 项需要关注` : '当前没有待处理异常'}</strong><div class="meta">来源 HEAD ${escapeHtml(project.head_sha)}</div></div><div>请回到 Codex 处理</div></section>
    <section class="cards"><div class="card">活跃任务<div class="metric">${escapeHtml(counts.active_tasks)}</div></div><div class="card">阻塞任务<div class="metric">${escapeHtml(counts.blocked_tasks)}</div></div><div class="card">待审批<div class="metric">${escapeHtml(counts.pending_approvals)}</div></div><div class="card">过期证据<div class="metric">${escapeHtml(counts.stale_reviews + counts.stale_evidence)}</div></div></section>
    <div class="grid"><section class="panel"><h2>优先队列</h2>${queue.length ? queue.map(taskRow).join('') : renderEmpty('暂无异常任务')}</section><aside class="panel"><h2>Codex 建议</h2><p>优先处理审批、阻塞和方向问题。此页面不会执行修改。</p><p class="meta">分支 ${escapeHtml(project.branch)} · Worktree ${escapeHtml(project.worktree_count)}</p></aside></div>`;
  bindTaskLinks();
}
function renderTasks() {
  const tasks = state.data.tasks || [];
  content.innerHTML = `<h1>任务</h1><p class="subhead">按异常、风险与更新时间排序</p>${tasks.length ? tasks.map(taskRow).join('') : renderEmpty('控制库中还没有任务')}`;
  bindTaskLinks();
}
function renderAgents() {
  const agents = state.data.details.flatMap(detail => (detail.agents || []).map(agent => ({ ...agent, task: detail.task.dispatch_id })));
  content.innerHTML = `<h1>Agents</h1><p class="subhead">首屏活跃任务的最近 Agent 汇报</p>${agents.length ? agents.map(agent => `<article class="row"><div class="row-head"><strong>${escapeHtml(agent.agent_id)}</strong>${badge(agent.state, agent.state === 'COMPLETED')}</div><div class="meta">${escapeHtml(agent.role)} · 任务 ${escapeHtml(agent.task)} · 进度 ${escapeHtml(agent.progress)}%</div></article>`).join('') : renderEmpty('当前没有 Agent 汇报')}`;
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
async function mapLimit(values, limit, mapper) { const results = []; let index = 0; async function worker() { while (index < values.length) { const current = index++; try { results[current] = await mapper(values[current]); } catch { results[current] = null; } } } await Promise.all(Array.from({ length: Math.min(limit, values.length) }, worker)); return results.filter(Boolean); }
async function loadEvidence() { content.innerHTML = renderEmpty('正在读取证据索引…'); try { const payload = await getJson(`/api/tasks/${encodeURIComponent(state.selectedTask)}/evidence?limit=100&offset=0`); state.evidence = payload.data.items; state.error = null; } catch (error) { state.error = error; return renderError(error); } renderEvidence(); }
async function refresh() {
  if (!state.data) renderLoading();
  try {
    const [project, tasks, approvals] = await Promise.all([getJson('/api/project'), getJson('/api/tasks?limit=100&offset=0'), getJson('/api/approvals?limit=100&offset=0')]);
    const active = tasks.data.items.filter(task => !['CLOSED', 'RELEASED'].includes(task.state));
    const details = await mapLimit(active, 4, task => getJson(`/api/tasks/${encodeURIComponent(task.dispatch_id)}`).then(payload => payload.data));
    state.data = { project: project.data, tasks: tasks.data.items, approvals: approvals.data.items, details };
    state.sourceHeadSha = project.source_head_sha; state.lastSuccessAt = Date.now(); state.error = null;
    sourceMeta.textContent = `HEAD ${state.sourceHeadSha.slice(0, 10)} · 刷新于 ${new Date().toLocaleTimeString('zh-CN')}`;
    statusRegion.textContent = '本地控制平面已同步'; renderCurrent();
  } catch (error) { state.error = error; if (!state.data) renderError(error); else { statusRegion.textContent = `刷新失败：${error.code || 'REQUEST_FAILED'}；保留上次数据`; renderStale(); } }
}
document.querySelectorAll('nav [data-view]').forEach(button => button.addEventListener('click', () => { state.view = button.dataset.view; updateNav(); renderCurrent(); content.focus(); }));
refresh(); setInterval(refresh, REFRESH_INTERVAL_MS); setInterval(renderStale, 5000);

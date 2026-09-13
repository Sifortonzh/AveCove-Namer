const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const titles = { tmdb: 'TMDb 查询', namer: 'Namer', detective: '识别记录', emby: 'Emby 刷新' };
let mediaKind = 'tv';
let lastSourceData = null;
const basePath = location.pathname.startsWith('/media-tools') ? '/media-tools' : '';

function applyScene() {
  const hour = new Date().getHours();
  const scene = hour < 5 || hour >= 21 ? 'night' : hour < 11 ? 'morning' : hour < 17 ? 'day' : 'evening';
  document.documentElement.dataset.scene = scene;
  const colors = { morning:'#fff5e9', day:'#edf5fb', evening:'#f7eef4', night:'#090b10' };
  document.querySelector('meta[name="theme-color"]').content = colors[scene];
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
}

function toast(message, error = false) {
  const el = $('#toast');
  el.textContent = message;
  el.className = `toast show${error ? ' error' : ''}`;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => el.className = 'toast', 3300);
}

async function api(path, payload) {
  const options = payload ? { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(payload) } : {};
  const response = await fetch(`${basePath}${path}`, options);
  let data;
  try { data = await response.json(); } catch { data = {error: `服务返回 ${response.status}`}; }
  if (!response.ok) throw new Error(data.error || `请求失败 (${response.status})`);
  return data;
}

function loading(target, text = '正在查询') {
  target.className = 'result-space empty-state loading';
  target.innerHTML = `<p>${escapeHtml(text)}</p>`;
}

function fail(target, error) {
  target.className = 'result-space empty-state';
  target.innerHTML = `<span class="empty-icon">!</span><p>${escapeHtml(error.message || error)}</p>`;
  toast(error.message || String(error), true);
}

function switchView(name) {
  $$('.nav-item').forEach(item => item.classList.toggle('active', item.dataset.view === name));
  $$('.view').forEach(view => view.classList.toggle('active', view.id === `view-${name}`));
  $('#page-title').textContent = titles[name];
  $('.sidebar').classList.remove('open');
  location.hash = name;
  if (name === 'detective') loadDetective();
}

$$('.nav-item').forEach(item => item.addEventListener('click', () => switchView(item.dataset.view)));
$('#mobile-menu').addEventListener('click', () => $('.sidebar').classList.toggle('open'));
$$('.segmented button').forEach(button => button.addEventListener('click', () => {
  $$('.segmented button').forEach(item => item.classList.remove('active'));
  button.classList.add('active');
  mediaKind = button.dataset.kind;
  $('.notice').textContent = mediaKind === 'tv' ? '输入剧集 ID 会直接读取源语言单集信息；输入名称会先列出匹配结果。' : '电影查询返回片名、原名、年份和原始语言。';
}));

$('#tmdb-form').addEventListener('submit', async event => {
  event.preventDefault();
  const query = $('#tmdb-query').value.trim();
  const year = $('#tmdb-year').value.trim();
  if (!query) return;
  if (mediaKind === 'tv' && /^\d+$/.test(query)) return loadSource(query);
  const target = $('#tmdb-result');
  loading(target, '正在搜索 TMDb');
  try {
    const data = await api('/api/tmdb/search', {query, year, kind: mediaKind});
    renderSearch(data.results, mediaKind);
  } catch (error) { fail(target, error); }
});

function renderSearch(results, kind) {
  const target = $('#tmdb-result');
  if (!results.length) return fail(target, new Error('没有找到匹配结果'));
  target.className = 'result-space search-list';
  target.innerHTML = results.map(item => `<article class="search-item">
    <div><h3>${escapeHtml(item.title || item.original_title || '未命名')}</h3><p>${escapeHtml(item.original_title || '')}${item.year ? ` · ${item.year}` : ''} · ${escapeHtml(item.language || '未知语言')} · <span class="id-tag">TMDb ${item.id}</span></p></div>
    ${kind === 'tv' ? `<button class="mini-button source-button" data-id="${item.id}">源语言单集</button>` : ''}
  </article>`).join('');
  $$('.source-button').forEach(button => button.addEventListener('click', () => loadSource(button.dataset.id)));
}

async function loadSource(id) {
  const target = $('#tmdb-result');
  loading(target, '正在读取全部季与单集');
  try {
    const data = await api('/api/tmdb/source', {tmdb_id: id});
    lastSourceData = data;
    target.className = 'result-space result-card';
    target.innerHTML = `<div class="result-toolbar"><div><strong>${escapeHtml(data.original_name)}</strong><div class="meta">TMDb ${data.tmdb_id} · ${escapeHtml(data.original_language)} · ${data.episodes.length} 集</div></div><div class="actions"><button class="mini-button" id="copy-json">复制 JSON</button><button class="mini-button" id="download-json">下载 JSON</button></div></div>
      <div class="table-wrap"><table><thead><tr><th>季</th><th>集</th><th>源语言标题</th><th>源语言简介</th></tr></thead><tbody>${data.episodes.map(ep => `<tr><td class="num">S${String(ep.season).padStart(2,'0')}</td><td class="num">E${String(ep.episode).padStart(2,'0')}</td><td>${escapeHtml(ep.name) || '—'}</td><td class="overview">${escapeHtml(ep.overview) || '—'}</td></tr>`).join('')}</tbody></table></div>`;
    $('#copy-json').addEventListener('click', async () => { await navigator.clipboard.writeText(JSON.stringify(lastSourceData, null, 2)); toast('JSON 已复制'); });
    $('#download-json').addEventListener('click', downloadSource);
  } catch (error) { fail(target, error); }
}

function downloadSource() {
  const blob = new Blob([JSON.stringify(lastSourceData, null, 2)], {type:'application/json'});
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = `tmdb-${lastSourceData.tmdb_id}-${lastSourceData.original_language}.json`;
  link.click();
  URL.revokeObjectURL(link.href);
}

$('#namer-form').addEventListener('submit', async event => {
  event.preventDefault();
  const target = $('#namer-result');
  loading(target, '正在扫描目录并生成只读计划');
  try {
    const data = await api('/api/namer/plan', {path:$('#namer-path').value, tmdb_id:$('#namer-id').value, kind:$('#namer-kind').value, title_style:$('#namer-style').value});
    renderPlan(data);
  } catch (error) { fail(target, error); }
});

function renderPlan(plan) {
  const target = $('#namer-result');
  const blocked = plan.conflicts.length > 0;
  target.className = 'result-space result-card';
  target.innerHTML = `<div class="result-toolbar"><div><strong>${escapeHtml(plan.resolved.title)}</strong><div class="meta">${escapeHtml(plan.root)} · ${plan.operations.length} 项修改 · ${plan.skipped.length} 项跳过</div></div><span class="status ${blocked ? 'failed' : (plan.operations.length ? 'planned' : 'compliant')}">${blocked ? '有冲突' : (plan.operations.length ? '等待确认' : '已规范')}</span></div>
    ${blocked ? `<div class="conflict-box">${plan.conflicts.map(escapeHtml).join('<br>')}</div>` : ''}
    <div class="table-wrap"><table><thead><tr><th>类型</th><th>改名前 → 改名后</th></tr></thead><tbody>${plan.operations.map(op => `<tr><td>${escapeHtml(op.kind)}</td><td><div class="operation"><span class="from">${escapeHtml(op.source)}</span><span class="arrow">↓</span><span class="to">${escapeHtml(op.target)}</span></div></td></tr>`).join('') || '<tr><td colspan="2">无需修改</td></tr>'}</tbody></table></div>
    ${!blocked && plan.operations.length ? `<div class="apply-panel"><label><span>输入“执行 ${plan.operations.length} 项”</span><input id="apply-confirm" autocomplete="off" placeholder="执行 ${plan.operations.length} 项"></label><button class="primary danger" id="apply-plan">确认执行</button></div>` : ''}`;
  const button = $('#apply-plan');
  if (button) button.addEventListener('click', () => applyPlan(plan.plan_id, plan.operations.length));
}

async function applyPlan(planId, count) {
  const button = $('#apply-plan');
  button.disabled = true;
  button.textContent = '正在执行';
  try {
    const data = await api('/api/namer/apply', {plan_id: planId, confirmation: $('#apply-confirm').value});
    toast('改名任务已开始');
    pollNamerJob(data.job_id);
  } catch (error) { toast(error.message, true); button.disabled = false; button.textContent = '确认执行'; }
}

async function pollNamerJob(jobId) {
  const target = $('#namer-result');
  try {
    const job = await api(`/api/jobs/${jobId}`);
    target.className = 'result-space job-card';
    target.innerHTML = `<span class="status ${escapeHtml(job.status)}">${job.status === 'running' ? '执行中' : (job.status === 'completed' ? '已完成' : '失败')}</span><h3>${job.status === 'running' ? `正在执行 ${job.operations} 项改名` : (job.status === 'completed' ? `${job.operations} 项改名已完成` : '改名任务失败')}</h3><p>${escapeHtml(job.path)}</p>${job.output ? `<pre>${escapeHtml(job.output)}</pre>` : '<p>操作正在服务器后台安全执行，并逐项写入回滚记录。</p>'}${job.status === 'completed' ? '<button class="secondary" id="view-refresh-job">查看 Emby 刷新进度</button>' : ''}`;
    if (job.status === 'running') setTimeout(() => pollNamerJob(jobId), 2500);
    else if (job.status === 'completed') {
      toast('改名完成，Emby 刷新已开始');
      $('#view-refresh-job').addEventListener('click', () => { switchView('emby'); pollJob(job.refresh_job); });
    } else toast('改名任务失败，请查看错误信息', true);
  } catch (error) { fail(target, error); }
}

async function loadDetective() {
  const target = $('#detective-result');
  loading(target, '正在读取最近记录');
  try {
    const data = await api('/api/detective');
    const keys = [['applied','已处理'],['planned','待执行'],['review','待审核'],['scan_error','异常']];
    $('#detective-summary').innerHTML = keys.map(([key,label]) => `<div class="stat"><b>${data.counts[key] || 0}</b><span>${label}</span></div>`).join('');
    if (!data.events.length) { target.className='result-space empty-state'; target.innerHTML='<span class="empty-icon">◉</span><p>还没有检测记录</p>'; return; }
    target.className = 'result-space event-list';
    target.innerHTML = [...data.events].reverse().map(event => `<article class="event-item"><div><h3>${escapeHtml(event.path)}</h3><p>${escapeHtml(event.reason || (event.tmdb_id ? `TMDb ${event.tmdb_id}` : ''))}</p></div><span class="status ${escapeHtml(event.status)}">${statusLabel(event.status)}</span></article>`).join('');
  } catch (error) { fail(target, error); }
}

function statusLabel(status) {
  return ({applied:'已处理',applied_partial:'部分处理',planned:'待执行',review:'待审核',compliant:'已规范',unchanged:'无变化',baseline:'基线',scan_error:'异常',no_media:'无媒体'})[status] || status;
}

$('#reload-detective').addEventListener('click', loadDetective);
$('#emby-form').addEventListener('submit', async event => {
  event.preventDefault();
  const target = $('#emby-result');
  loading(target, '正在创建刷新任务');
  try { const data = await api('/api/emby/refresh', {path:$('#emby-path').value}); pollJob(data.job_id); }
  catch (error) { fail(target, error); }
});

async function pollJob(jobId) {
  const target = $('#emby-result');
  try {
    const job = await api(`/api/jobs/${jobId}`);
    target.className = 'result-space job-card';
    target.innerHTML = `<span class="status ${escapeHtml(job.status)}">${job.status === 'running' ? '执行中' : (job.status === 'completed' ? '已完成' : '失败')}</span><h3>${job.status === 'running' ? '正在同步并刷新 Emby' : (job.status === 'completed' ? '刷新完成' : '刷新失败')}</h3><p>${escapeHtml(job.path)}</p>${job.output ? `<pre>${escapeHtml(job.output)}</pre>` : '<p>任务在服务器后台低负载运行。</p>'}`;
    if (job.status === 'running') setTimeout(() => pollJob(jobId), 2500);
    else toast(job.status === 'completed' ? 'Emby 刷新完成' : 'Emby 刷新失败', job.status !== 'completed');
  } catch (error) { fail(target, error); }
}

async function checkHealth() {
  try {
    await api('/api/health');
    $('.status-dot').classList.add('ok');
    $('#service-status').textContent = '服务运行正常';
  } catch {
    $('#service-status').textContent = '服务连接异常';
  }
}

$('#today').textContent = new Intl.DateTimeFormat('zh-CN', {month:'long', day:'numeric', weekday:'short'}).format(new Date());
applyScene();
setInterval(applyScene, 300000);
const initial = location.hash.slice(1);
if (titles[initial]) switchView(initial);
checkHealth();

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const titles = { tmdb: 'TMDb 查询', namer: 'Namer', detective: '识别记录', emby: 'Emby 刷新' };
let mediaKind = 'tv';
let lastSourceData = null;
let embyPendingLoaded = false;
let namerInboxLoaded = false;
const embyQueue = new Map();
const basePath = location.pathname.startsWith('/media-tools') ? '/media-tools' : '';
const mediaPathGroups = [
  {label:'115', root:'115'},
  {label:'光鸭', root:'GuangYa'},
  {label:'123', root:'123'},
  {label:'百度', root:'Baidu'},
];
const tvDirectories = ['01美', '01中', '01韩', '01日', '01台', '01英'];
const movieDirectories = ['01国', '01外'];

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

function presetMarkup() {
  return mediaPathGroups.map(provider => `<section class="preset-provider">
    <strong>${provider.label}</strong>
    <div class="preset-directory-list">
      <span class="preset-type">剧集</span>
      ${tvDirectories.map(name => `<button type="button" data-kind="tv" data-path="/${provider.root}/00剧/${name}/">${name}</button>`).join('')}
      <span class="preset-type">电影</span>
      ${movieDirectories.map(name => `<button type="button" data-kind="movie" data-path="/${provider.root}/00影/${name}/">${name}</button>`).join('')}
    </div>
  </section>`).join('');
}

$('#namer-presets').innerHTML = presetMarkup();

function switchView(name) {
  $$('.nav-item').forEach(item => item.classList.toggle('active', item.dataset.view === name));
  $$('.view').forEach(view => view.classList.toggle('active', view.id === `view-${name}`));
  $('#page-title').textContent = titles[name];
  $('.sidebar').classList.remove('open');
  location.hash = name;
  if (name === 'detective') { loadDetective(); loadAutomationStatus(); }
  if (name === 'namer' && !namerInboxLoaded) { namerInboxLoaded = true; loadNamerInbox(); }
  if (name === 'emby' && !embyPendingLoaded) { embyPendingLoaded = true; loadEmbyCenter(); }
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
  checkLibrary(query);
  if (mediaKind === 'tv' && /^\d+$/.test(query)) return loadSource(query);
  const target = $('#tmdb-result');
  loading(target, '正在搜索 TMDb');
  try {
    const data = await api('/api/tmdb/search', {query, year, kind: mediaKind});
    renderSearch(data.results, data.resolved_kind || mediaKind);
  } catch (error) { fail(target, error); }
});

$('#library-search-button').addEventListener('click', () => checkLibrary($('#tmdb-query').value.trim()));

async function checkLibrary(query) {
  const target = $('#library-search-result');
  if (!query) {
    target.className = 'availability-card missing';
    target.innerHTML = '<span>收藏检查</span><strong>请先输入资源名称</strong>';
    $('#tmdb-query').focus();
    return;
  }
  target.className = 'availability-card checking';
  target.innerHTML = '<span>正在查询</span><strong>正在轻量检查各网盘目录…</strong>';
  try {
    const data = await api('/api/library/search', {query, kind: mediaKind});
    target.className = `availability-card ${data.found ? 'found' : 'missing'}`;
    target.innerHTML = `<div><span>${data.found ? '已收藏' : '暂未收藏'}</span><strong>${escapeHtml(data.message)}</strong></div>${data.matches.length ? `<div class="availability-paths">${data.matches.map(item => `<button class="availability-path" type="button" data-path="${escapeHtml(item.path)}"><b>${escapeHtml(item.provider)}</b><small>${escapeHtml(item.path)}</small></button>`).join('')}</div>` : '<p>目前在已接入的网盘目录中没有找到匹配资源。</p>'}`;
    $$('.availability-path').forEach(button => button.addEventListener('click', async () => {
      await navigator.clipboard.writeText(button.dataset.path);
      toast('网盘路径已复制');
    }));
  } catch (error) {
    target.className = 'availability-card missing';
    target.innerHTML = `<span>查询失败</span><strong>${escapeHtml(error.message || error)}</strong>`;
  }
}

function renderSearch(results, kind) {
  const target = $('#tmdb-result');
  if (!results.length) return fail(target, new Error('没有找到匹配结果'));
  target.className = 'result-space search-list';
  target.innerHTML = results.map(item => `<article class="search-item">
    <div><h3>${escapeHtml(item.title || item.original_title || '未命名')}</h3><p>${item.kind === 'movie' ? '电影' : '剧集'} · ${escapeHtml(item.original_title || '')}${item.year ? ` · ${item.year}` : ''} · ${escapeHtml(item.language || '未知语言')} · <span class="id-tag">TMDb ${item.id}</span></p>${item.recommended_name ? `<div class="copy-name"><code>${escapeHtml(item.recommended_name)}</code></div>` : ''}</div>
    <div class="actions"><button class="mini-button copy-title-button" data-title="${escapeHtml(item.title || item.original_title || '')}">仅复制剧名</button><button class="mini-button copy-folder-button" data-name="${escapeHtml(item.recommended_name || '')}">复制文件夹名</button><button class="mini-button copy-id-button" data-id="${item.id}">复制 TMDb 号</button>${kind === 'tv' ? `<button class="mini-button source-button" data-id="${item.id}">源语言单集</button>` : ''}</div>
  </article>`).join('');
  $$('.source-button').forEach(button => button.addEventListener('click', () => loadSource(button.dataset.id)));
  $$('.copy-id-button').forEach(button => button.addEventListener('click', async () => {
    await navigator.clipboard.writeText(button.dataset.id);
    toast('TMDb ID 已复制');
  }));
  $$('.copy-title-button').forEach(button => button.addEventListener('click', async () => {
    await navigator.clipboard.writeText(button.dataset.title);
    toast('剧名已复制');
  }));
  $$('.copy-folder-button').forEach(button => button.addEventListener('click', async () => {
    await navigator.clipboard.writeText(button.dataset.name);
    toast('文件夹名已复制');
  }));
}

async function loadSource(id) {
  const target = $('#tmdb-result');
  loading(target, '正在读取全部季与单集');
  try {
    const data = await api('/api/tmdb/source', {tmdb_id: id});
    lastSourceData = data;
    const seasons = data.episodes.reduce((groups, episode) => {
      (groups[episode.season] ||= []).push(episode);
      return groups;
    }, {});
    target.className = 'result-space result-card';
    target.innerHTML = `<div class="result-toolbar"><div><strong>${escapeHtml(data.title || data.original_name)}</strong><div class="meta">${data.year || '年份未知'} · TMDb ${data.tmdb_id} · ${escapeHtml(data.original_language)} · ${data.episodes.length} 集</div></div><div class="actions"><button class="mini-button" id="copy-source-title">仅复制剧名</button><button class="mini-button" id="copy-source-folder">复制文件夹名</button><button class="mini-button" id="copy-tmdb-id">复制 TMDb 号</button><button class="mini-button" id="copy-json">复制源数据</button><button class="mini-button" id="download-json">下载</button></div></div>
      <div class="source-name-bar"><code>${escapeHtml(data.recommended_name || '')}</code></div>
      <div class="season-groups">${Object.entries(seasons).sort((a,b) => Number(a[0]) - Number(b[0])).map(([season, episodes], index) => `<details class="season-group" ${index === 0 ? 'open' : ''}><summary><span>Season ${String(season).padStart(2,'0')}</span><small>${episodes.length} 集</small></summary><div class="table-wrap"><table><thead><tr><th>集</th><th>源语言标题</th><th>源语言简介</th></tr></thead><tbody>${episodes.map(ep => `<tr><td class="num">E${String(ep.episode).padStart(2,'0')}</td><td><div class="episode-copy"><span>${escapeHtml(ep.name) || '—'}</span><button class="mini-button copy-episode-name" data-copy="${escapeHtml(ep.name)}">复制单集名</button></div></td><td class="overview"><div class="episode-copy"><span>${escapeHtml(ep.overview) || '—'}</span><button class="mini-button copy-episode-overview" data-copy="${escapeHtml(ep.overview)}">复制简介</button></div></td></tr>`).join('')}</tbody></table></div></details>`).join('')}</div>`;
    $('#copy-tmdb-id').addEventListener('click', async () => { await navigator.clipboard.writeText(String(data.tmdb_id)); toast('TMDb ID 已复制'); });
    $('#copy-source-title').addEventListener('click', async () => { await navigator.clipboard.writeText(data.title || data.original_name); toast('剧名已复制'); });
    $('#copy-source-folder').addEventListener('click', async () => { await navigator.clipboard.writeText(data.recommended_name || data.original_name); toast('文件夹名已复制'); });
    $('#copy-json').addEventListener('click', async () => { await navigator.clipboard.writeText(JSON.stringify(lastSourceData, null, 2)); toast('JSON 已复制'); });
    $('#download-json').addEventListener('click', downloadSource);
    $$('.copy-episode-name').forEach(button => button.addEventListener('click', async () => { await navigator.clipboard.writeText(button.dataset.copy); toast('单集名已复制'); }));
    $$('.copy-episode-overview').forEach(button => button.addEventListener('click', async () => { await navigator.clipboard.writeText(button.dataset.copy); toast('单集简介已复制'); }));
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
  loading(target, '正在读取目录并调用 TMDb 识别');
  try {
    const data = await api('/api/namer/identify', {path:$('#namer-path').value, tmdb_id:$('#namer-id').value, kind:$('#namer-kind').value, title_style:$('#namer-style').value, source_parent:$('#namer-source-parent').checked});
    renderIdentification(data);
  } catch (error) { fail(target, error); }
});

$$('.namer-presets button').forEach(button => button.addEventListener('click', () => {
  const input = $('#namer-path');
  input.value = button.dataset.path;
  $('#namer-kind').value = button.dataset.kind || 'tv';
  input.focus();
  input.setSelectionRange(input.value.length, input.value.length);
}));

function renderIdentification(data) {
  const target = $('#namer-result');
  const confidence = Math.round((data.score || 0) * 100);
  target.className = 'result-space match-card';
  target.innerHTML = `<div class="match-mark">✓</div><div class="match-copy"><span class="eyebrow">TMDb MATCH</span><h3>${escapeHtml(data.resolved.title)}</h3><p>${data.resolved.year || '年份未知'} · TMDb ${data.tmdb_id} · 源语言 ${escapeHtml(data.resolved.original_language || '未知')}</p><div class="copy-name"><code>${escapeHtml(data.recommended_name)}</code><button class="mini-button" id="copy-match-name">复制名称</button></div>${data.warning ? `<p class="namer-warning">${escapeHtml(data.warning)}</p>` : ''}<small>${data.source_parent ? `父目录：${escapeHtml(data.parent_title)} · 子文件：${escapeHtml(data.child_title)} · ` : ''}${escapeHtml(data.reason)}${confidence ? ` · 置信度 ${confidence}%` : ''}</small></div><div class="match-actions"><button class="secondary" id="retry-identify">重新识别</button><button class="primary" id="confirm-match">信息正确，生成预览</button></div>`;
  $('#copy-match-name').addEventListener('click', async () => { await navigator.clipboard.writeText(data.recommended_name); toast('规范名称已复制'); });
  $('#retry-identify').addEventListener('click', () => $('#namer-path').focus());
  $('#confirm-match').addEventListener('click', () => createNamerPlan(data.tmdb_id));
}

async function createNamerPlan(tmdbId) {
  const target = $('#namer-result');
  loading(target, '正在生成只读改名预览');
  try {
    const data = await api('/api/namer/plan', {path:$('#namer-path').value, tmdb_id:tmdbId, kind:$('#namer-kind').value, title_style:$('#namer-style').value, source_parent:$('#namer-source-parent').checked});
    renderPlan(data);
  } catch (error) { fail(target, error); }
}

function renderPlan(plan) {
  const target = $('#namer-result');
  const blocked = plan.conflicts.length > 0;
  target.className = 'result-space result-card';
  target.innerHTML = `<div class="result-toolbar"><div><strong>${escapeHtml(plan.resolved.title)}</strong><div class="meta">${escapeHtml(plan.root)} · ${plan.operations.length} 项修改 · ${plan.skipped.length} 项跳过</div></div><span class="status ${blocked ? 'failed' : (plan.operations.length ? 'planned' : 'compliant')}">${blocked ? '有冲突' : (plan.operations.length ? '等待确认' : '已规范')}</span></div>
    ${blocked ? `<div class="conflict-box">${plan.conflicts.map(escapeHtml).join('<br>')}</div>` : ''}
    <div class="table-wrap"><table><thead><tr><th>类型</th><th>改名前 → 改名后</th></tr></thead><tbody>${plan.operations.map(op => `<tr><td>${escapeHtml(op.kind)}</td><td><div class="operation"><span class="from">${escapeHtml(op.source)}</span><span class="arrow">↓</span><span class="to">${escapeHtml(op.target)}</span></div></td></tr>`).join('') || '<tr><td colspan="2">无需修改</td></tr>'}</tbody></table></div>
    ${!blocked && plan.operations.length ? `<div class="apply-panel"><label><span>执行确认</span><div class="confirm-row"><input id="apply-confirm" autocomplete="off" placeholder="执行 ${plan.operations.length} 项"><button class="secondary" type="button" id="fill-confirm">一键填入</button></div></label><button class="primary danger" id="apply-plan">确认执行</button></div>` : ''}`;
  const button = $('#apply-plan');
  if (button) button.addEventListener('click', () => applyPlan(plan.plan_id, plan.operations.length));
  const fillButton = $('#fill-confirm');
  if (fillButton) fillButton.addEventListener('click', () => { $('#apply-confirm').value = `执行 ${plan.operations.length} 项`; toast('确认文字已填入'); });
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
    const manual = $('#manual-history');
    manual.innerHTML = `<div class="history-heading"><strong>手动识别</strong><span>最近 ${data.manual_events.length} / 20 条</span></div>${data.manual_events.length ? `<div class="event-list">${data.manual_events.map(event => `<article class="event-item"><div><h3>${escapeHtml(event.path)}</h3><p><b class="provider-tag">手动 · ${escapeHtml(event.provider || 'Namer')}</b>${escapeHtml(event.reason || (event.tmdb_id ? `TMDb ${event.tmdb_id}` : ''))}</p></div><span class="status identified">${statusLabel(event.status)}</span></article>`).join('')}</div>` : '<div class="history-empty">还没有手动识别记录</div>'}`;
    if (!data.events.length) { target.className='result-space empty-state'; target.innerHTML='<span class="empty-icon">◉</span><p>还没有自动检测记录</p>'; return; }
    target.className = 'result-space event-list';
    target.innerHTML = [...data.events].reverse().map(event => `<article class="event-item"><div><h3>${escapeHtml(event.path)}</h3><p><b class="provider-tag">${escapeHtml(event.provider || 'Namer')}</b>${escapeHtml(event.reason || (event.tmdb_id ? `TMDb ${event.tmdb_id}` : ''))}</p></div><span class="status ${escapeHtml(event.status)}">${statusLabel(event.status)}</span></article>`).join('');
  } catch (error) { fail(target, error); }
}

function statusLabel(status) {
  return ({identified:'已识别',applied:'已处理',applied_partial:'部分处理',planned:'待执行',review:'待审核',compliant:'已规范',unchanged:'无变化',baseline:'基线',scan_error:'异常',no_media:'无媒体'})[status] || status;
}

$('#reload-detective').addEventListener('click', loadDetective);
$('#automation-toggle').addEventListener('click', toggleAutomation);

async function loadAutomationStatus() {
  const button = $('#automation-toggle');
  button.disabled = true;
  try {
    const data = await api('/api/automation');
    button.dataset.enabled = String(data.enabled);
    button.textContent = data.enabled ? '自动扫描：已开启（点击关闭）' : '自动扫描：已关闭（点击开启）';
    button.classList.toggle('automation-on', data.enabled);
  } catch (error) {
    button.textContent = '自动扫描：状态读取失败';
    toast(error.message || String(error), true);
  } finally {
    button.disabled = false;
  }
}

async function toggleAutomation() {
  const button = $('#automation-toggle');
  const enabled = button.dataset.enabled === 'true';
  button.disabled = true;
  button.textContent = enabled ? '正在关闭自动扫描…' : '正在开启自动扫描…';
  try {
    const data = await api('/api/automation', {enabled: !enabled});
    button.dataset.enabled = String(data.enabled);
    button.textContent = data.enabled ? '自动扫描：已开启（点击关闭）' : '自动扫描：已关闭（点击开启）';
    button.classList.toggle('automation-on', data.enabled);
    toast(data.enabled ? '自动扫描已开启，将按计划执行' : '自动扫描已关闭');
  } catch (error) {
    toast(error.message || String(error), true);
    await loadAutomationStatus();
  } finally {
    button.disabled = false;
  }
}

function providerLabel(provider) {
  return ({GuangYa:'光鸭', Baidu:'百度'})[provider] || provider;
}

function groupByProvider(items) {
  return items.reduce((groups, item) => {
    (groups[item.provider] ||= []).push(item);
    return groups;
  }, {});
}

async function fetchPending() {
  return api('/api/emby/pending');
}

function beginNamerForPath(path, kind) {
  $('#namer-path').value = path;
  $('#namer-kind').value = kind === 'movie' ? 'movie' : 'tv';
  $('#namer-form').requestSubmit();
  $('#namer-result').scrollIntoView({behavior:'smooth', block:'center'});
}

async function loadNamerInbox() {
  const target = $('#namer-inbox');
  const button = $('#namer-inbox-reload');
  button.disabled = true;
  target.className = 'inbox-loading-wrap';
  target.innerHTML = '<div class="inbox-loading-card"><span></span><div><strong>正在检测新资源</strong><small>只读浅层检查，不读取视频</small></div></div>';
  try {
    const data = await fetchPending();
    const grouped = groupByProvider(data.pending);
    target.className = 'inbox-panel';
    const providerSummary = Object.entries(grouped).map(([provider, items]) => `<span><b>${escapeHtml(providerLabel(provider))}</b>${items.length}</span>`).join('');
    target.innerHTML = `<div class="inbox-heading"><div class="inbox-title"><span class="inbox-orb">✦</span><div><strong>新增与订阅更新</strong><span>按网盘更新时间排序，点击后只整理发生变化的作品</span></div></div><div class="inbox-total"><b>${data.pending_count}</b><span>待处理</span></div></div><div class="provider-summary">${providerSummary || '<span><b>已清空</b>0</span>'}</div>${Object.entries(grouped).length ? `<div class="inbox-body">${Object.entries(grouped).map(([provider, items]) => `<section class="provider-task-group"><div class="provider-task-head"><div><strong>${escapeHtml(providerLabel(provider))}</strong><span>${items.length} 项待处理</span></div><span class="provider-index">${String(items.length).padStart(2,'0')}</span></div><div class="compact-task-list">${items.map(item => `<button class="compact-task namer-inbox-item" type="button" data-path="${escapeHtml(item.path)}" data-kind="${escapeHtml(item.kind)}"><span>${escapeHtml(item.name)}</span><small>${item.reason === 'subscription_update' ? '订阅更新' : '新入库'} · ${item.kind === 'movie' ? '电影' : '剧集'} · ${escapeHtml(item.category)}<i>开始识别 →</i></small></button>`).join('')}</div></section>`).join('')}</div>` : '<div class="history-empty inbox-empty">没有发现新入库或订阅更新。</div>'}`;
    $$('.namer-inbox-item').forEach(item => item.addEventListener('click', () => beginNamerForPath(item.dataset.path, item.dataset.kind)));
  } catch (error) {
    target.className = 'inbox-loading-wrap';
    target.innerHTML = `<div class="history-empty">检测失败：${escapeHtml(error.message || error)}</div>`;
    toast(error.message || String(error), true);
  } finally { button.disabled = false; }
}

$('#namer-inbox-reload').addEventListener('click', loadNamerInbox);
$('#emby-reload-all').addEventListener('click', loadEmbyCenter);
$('#emby-run-queue').addEventListener('click', runEmbyQueue);

function updateEmbyQueue() {
  const button = $('#emby-run-queue');
  const count = embyQueue.size;
  $('#emby-queue-count').textContent = count ? `已收集 ${count} 个项目` : '尚未选择项目';
  button.disabled = !count;
  $$('.queue-item').forEach(item => {
    const selected = embyQueue.has(item.dataset.path);
    item.classList.toggle('selected', selected);
    item.textContent = selected ? '移出集合' : '加入集合';
  });
}

function addProviderToQueue(items) {
  items.forEach(item => embyQueue.set(item.path, item));
  updateEmbyQueue();
}

async function loadEmbyCenter() {
  const button = $('#emby-reload-all');
  button.disabled = true;
  button.textContent = '正在检测…';
  await loadEmbyPending();
  await loadEmbyResiduals();
  await loadEmbyDuplicates();
  button.disabled = false;
  button.textContent = '重新检测';
}

async function loadEmbyResiduals() {
  const target = $('#emby-residuals');
  loading(target, '正在核对 Emby 条目与本地 STRM 索引');
  try {
    const data = await api('/api/emby/residuals');
    const grouped = groupByProvider(data.items);
    target.className = 'result-space result-card';
    target.innerHTML = `<div class="result-toolbar"><div><strong>${data.count ? `${data.count} 个 Emby 残留项目` : '没有发现 Emby 残留'}</strong><div class="meta">云盘或 STRM 已不存在，但 Emby 数据库仍保留的作品</div></div><span class="status ${data.count ? 'failed' : 'compliant'}">${data.count ? '建议删除' : '正常'}</span></div><div class="provider-groups">${Object.entries(grouped).map(([provider, items]) => `<section class="provider-task-group"><div class="provider-task-head"><div><strong>${escapeHtml(providerLabel(provider))}</strong><span>${items.length} 项残留</span></div></div><div class="compact-task-list">${items.map(item => `<article class="compact-task-row"><div><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.type === 'Series' ? '剧集' : '电影')} · Emby ID ${escapeHtml(item.id)}</small></div><span class="status failed">待删除</span></article>`).join('')}</div></section>`).join('') || '<div class="history-empty">Emby 数据库与当前 STRM 索引一致。</div>'}</div>`;
  } catch (error) { fail(target, error); }
}

async function loadEmbyPending() {
  const target = $('#emby-pending');
  loading(target, '正在按网盘收集待刷新项目');
  try {
    const data = await fetchPending();
    const grouped = groupByProvider(data.pending);
    target.className = 'result-space result-card';
    target.innerHTML = `<div class="result-toolbar"><div><strong>${data.pending_count ? `${data.pending_count} 个项目尚未进入 Emby` : '当前没有待刷新项目'}</strong><div class="meta">已检查 ${data.scanned_roots} 个分类 · 云盘 ${data.cloud_titles} 项 · 已入库 ${data.present_titles} 项</div></div><span class="status ${data.pending_count ? 'planned' : 'compliant'}">${data.pending_count ? '待收集' : '已同步'}</span></div><div class="provider-groups">${Object.entries(grouped).map(([provider, items]) => `<section class="provider-task-group"><div class="provider-task-head"><div><strong>${escapeHtml(providerLabel(provider))}</strong><span>${items.length} 项待刷新</span></div><button class="mini-button queue-provider" type="button" data-provider="${escapeHtml(provider)}">加入本盘全部</button></div><div class="compact-task-list">${items.map(item => `<article class="compact-task-row"><div><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.category)}</small></div><button class="mini-button queue-item" type="button" data-path="${escapeHtml(item.path)}">加入集合</button></article>`).join('')}</div></section>`).join('') || '<div class="history-empty">云盘作品目录均已存在于 Emby STRM 索引。</div>'}</div>`;
    $$('.queue-item').forEach(item => item.addEventListener('click', () => {
      const found = data.pending.find(entry => entry.path === item.dataset.path);
      if (embyQueue.has(item.dataset.path)) embyQueue.delete(item.dataset.path);
      else if (found) embyQueue.set(found.path, found);
      updateEmbyQueue();
    }));
    $$('.queue-provider').forEach(item => item.addEventListener('click', () => addProviderToQueue(grouped[item.dataset.provider] || [])));
    updateEmbyQueue();
  } catch (error) { fail(target, error); }
}

async function cleanDuplicateSelection(planId, groupIds, count, label, button) {
  if (!confirm(`${label}：将保留每集最合理的一份，其余 ${count} 项移入可恢复隔离区。是否继续？`)) return;
  button.disabled = true;
  try {
    const result = await api('/api/emby/duplicates/clean', {plan_id:planId, group_ids:groupIds, confirmation:`清理 ${count} 项`});
    toast(`已隔离 ${result.moved} 个重复 STRM`);
    await loadEmbyDuplicates();
  } catch (error) { toast(error.message || String(error), true); button.disabled = false; }
}

async function loadEmbyDuplicates() {
  const target = $('#emby-duplicates');
  loading(target, '正在按网盘和剧集汇总重复 STRM');
  try {
    const data = await api('/api/emby/duplicates');
    const providers = {};
    data.groups.forEach(group => {
      const provider = providers[group.provider] ||= {};
      const series = provider[group.series] ||= {series:group.series, category:group.category, ids:[], groups:0, count:0};
      series.ids.push(group.id); series.groups += 1; series.count += group.remove.length;
    });
    target.className = 'result-space result-card';
    target.innerHTML = `<div class="result-toolbar"><div><strong>${data.group_count} 组重复 STRM，涉及 ${Object.values(providers).reduce((sum, provider) => sum + Object.keys(provider).length, 0)} 部作品</strong><div class="meta">不显示冗长文件路径；预计隔离 ${data.remove_count} 项</div></div>${data.remove_count ? `<button class="mini-button danger-action clean-all-duplicates" type="button">一键删除全部重复项</button>` : '<span class="status compliant">无重复</span>'}</div><div class="provider-groups">${Object.entries(providers).map(([provider, seriesMap]) => { const series = Object.values(seriesMap); const ids = series.flatMap(item => item.ids); const count = series.reduce((sum,item) => sum + item.count, 0); return `<section class="provider-task-group"><div class="provider-task-head"><div><strong>${escapeHtml(providerLabel(provider))}</strong><span>${series.length} 部作品 · ${count} 个重复项</span></div><button class="mini-button danger-action clean-provider-duplicates" data-provider="${escapeHtml(provider)}" type="button">删除本盘重复</button></div><div class="compact-task-list">${series.map(item => `<article class="compact-task-row"><div><strong>${escapeHtml(item.series)}</strong><small>${escapeHtml(item.category)} · ${item.groups} 集重复 · 删除 ${item.count} 项</small></div><button class="mini-button danger-action clean-series-duplicates" data-provider="${escapeHtml(provider)}" data-series="${escapeHtml(item.series)}" type="button">删除本剧重复</button></article>`).join('')}</div></section>`; }).join('') || '<div class="history-empty">没有发现可确定的重复 STRM。</div>'}</div>`;
    const allButton = $('.clean-all-duplicates');
    if (allButton) allButton.addEventListener('click', () => cleanDuplicateSelection(data.plan_id, 'all', data.remove_count, '全部网盘', allButton));
    $$('.clean-provider-duplicates').forEach(button => button.addEventListener('click', () => {
      const series = Object.values(providers[button.dataset.provider]);
      cleanDuplicateSelection(data.plan_id, series.flatMap(item => item.ids), series.reduce((sum,item) => sum + item.count, 0), providerLabel(button.dataset.provider), button);
    }));
    $$('.clean-series-duplicates').forEach(button => button.addEventListener('click', () => {
      const item = providers[button.dataset.provider][button.dataset.series];
      cleanDuplicateSelection(data.plan_id, item.ids, item.count, item.series, button);
    }));
  } catch (error) { fail(target, error); }
}

async function runEmbyQueue() {
  const button = $('#emby-run-queue');
  button.disabled = true;
  try {
    const data = await api('/api/emby/refresh-queue', {paths:[...embyQueue.keys()]});
    embyQueue.clear();
    updateEmbyQueue();
    toast('刷新集合已在后台严格串行启动');
    pollJob(data.job_id);
  } catch (error) { toast(error.message || String(error), true); updateEmbyQueue(); }
}

async function pollJob(jobId) {
  const target = $('#emby-result');
  try {
    const job = await api(`/api/jobs/${jobId}`);
    const queued = job.type === 'emby_refresh_queue';
    const progress = queued ? `${job.completed || 0}/${job.total || 0} 已完成${job.failed ? ` · ${job.failed} 失败` : ''}` : (job.path || '');
    const current = queued && job.current ? `<p>当前：${escapeHtml(job.current)}</p>` : '';
    target.className = 'result-space job-card';
    target.innerHTML = `<span class="status ${escapeHtml(job.status)}">${job.status === 'running' ? '执行中' : (job.status === 'completed' ? '已完成' : '失败')}</span><h3>${job.status === 'running' ? '正在串行同步并刷新 Emby' : (job.status === 'completed' ? '刷新完成' : '刷新已结束，部分项目失败')}</h3><p>${escapeHtml(progress)}</p>${current}${job.output ? `<pre>${escapeHtml(job.output)}</pre>` : '<p>任务在服务器后台低负载运行，页面可以关闭。</p>'}`;
    if (job.status === 'running') setTimeout(() => pollJob(jobId), 2500);
    else {
      toast(job.status === 'completed' ? 'Emby 刷新完成' : 'Emby 刷新完成，但有失败项', job.status !== 'completed');
      loadEmbyPending();
    }
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
else { namerInboxLoaded = true; loadNamerInbox(); }
checkHealth();

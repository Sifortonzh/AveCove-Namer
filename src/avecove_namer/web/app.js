const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const titles = { tmdb: 'TMDb 查询', namer: 'AveCove Namer', detective: '识别记录' };
let mediaKind = 'tv';
let lastSourceData = null;
let savedSeries = new Map();
let shelfFilter = 'all';
let shelfRefreshTimer;
let watchedEditingId = null;
let watchedEditingTitle = '';
let watchedDialogVersion = 0;
function watchDateToday() { return new Date(Date.now() + 8 * 3600000).toISOString().slice(0,10); }
function watchedShelfItems(items) {
  return items.flatMap(item => [
    ...(item.watched ? [{...item, completedSeason:null}] : []),
    ...Object.entries(item.seasonCompletions || {}).map(([season,record]) => ({...item,...record,completedSeason:Number(season)})),
  ]).sort((a,b) =>
    String(b.watchedDate || '').localeCompare(String(a.watchedDate || '')) ||
    (b.watchedRecordedAt || 0) - (a.watchedRecordedAt || 0) || String(a.id).localeCompare(String(b.id)));
}
function completionLabel(season) { return season == null ? '全剧' : season === 0 ? '特别篇' : `第 ${season} 季`; }
function watchRecord(id, scope) {
  const item = savedSeries.get(String(id));
  return scope === '' ? (item?.watched ? item : null) : item?.seasonCompletions?.[scope];
}
function fillWatchRecord() {
  const record = watchRecord(watchedEditingId, $('#watched-scope').value);
  $('#watched-date').value = record?.watchedDate || watchDateToday();
  $('#watched-stars').value = String(record?.personalStars || '');
  $('#watched-remove').hidden = !record;
  $('#watched-feedback').textContent = '';
}
function openWatchedDate(id, title = '', season = undefined) {
  const item = savedSeries.get(String(id));
  watchedEditingId = String(id);
  watchedEditingTitle = item?.originalTitle || item?.title || title || `TMDb ${id}`;
  const version = ++watchedDialogVersion;
  $('#watched-series').textContent = watchedEditingTitle;
  $('#watched-date').max = watchDateToday();
  const scope = $('#watched-scope');
  const options = (seasons, selected) => {
    const known = new Set([...seasons.map(s=>s.number), ...Object.keys(item?.seasonCompletions || {}).map(Number)]);
    if (selected !== '') known.add(Number(selected));
    scope.replaceChildren();
    for (const value of ['', ...[...known].filter(n=>Number.isInteger(n)&&n>=0).sort((a,b)=>a-b).map(String)]) {
      const option = document.createElement('option'); option.value = value;
      option.textContent = completionLabel(value === '' ? null : Number(value)); scope.append(option);
    }
    scope.value = selected;
  };
  if (season === undefined && !item?.watched) {
    const recent = watchedShelfItems(item ? [item] : [])[0];
    season = recent?.completedSeason;
  }
  options(item?.seasons || [], season == null ? '' : String(season)); fillWatchRecord();
  const hint = $('#watched-scope-note');
  hint.textContent = item?.seasons?.length ? '每季独立记录，不会替代全剧记录。' : '正在读取 TMDb 季列表…';
  $('#watched-dialog').showModal(); $('#watched-date').focus();
  if (!item?.seasons?.length) getSeriesSummary(String(id)).then(data => {
    if (version !== watchedDialogVersion || !$('#watched-dialog').open) return;
    options(data.seasons || [], scope.value);
    hint.textContent = data.seasons?.length ? '每季独立记录，不会替代全剧记录。' : 'TMDb 暂无分季资料，可以记录全剧。';
  }).catch(()=>{if(version===watchedDialogVersion&&$('#watched-dialog').open)hint.textContent='暂时无法读取 TMDb 季列表，仍可保存全剧或编辑已有记录。'});
}
function episodeCopyTitle(number, title) { return `E${String(Number(number)).padStart(2, '0')}: ${title || ''}`; }
const shelfStatusTypes = {favorite:{icon:'🌟',label:'收藏'},watching:{icon:'👀',label:'在看'},watched:{icon:'✅',label:'已看'}};
function paintShelfStatus(button, action, enabled, title = '') {
  const status = shelfStatusTypes[action]; if (!status) return;
  button.classList.add('shelf-state');
  button.textContent = `${status.icon}${button.dataset.compact === 'true' ? '' : ' ' + status.label}`;
  button.setAttribute('aria-pressed', String(!!enabled));
  button.setAttribute('aria-label', `${enabled ? (action==='watched'?'编辑':'取消') : '标记'}${status.label}${title ? '：' + title : ''}`);
  button.title = action==='watched'?'记录完看日期与个人评分':`${status.icon} ${status.label}${enabled ? ' · 已选中，点击取消' : ' · 未选中，点击标记'}`;
}
function shelfActions(id, title) {
  const actions = document.createElement('span'); actions.className = 'actions shelf-actions';
  for (const action of Object.keys(shelfStatusTypes)) {
    const button = document.createElement('button'); button.type = 'button'; button.className = 'mini-button';
    button.dataset.shelfId = String(id); button.dataset.shelfAction = action;
    const saved = savedSeries.get(String(id));
    paintShelfStatus(button, action, saved?.[action] || (action==='watched'&&Object.keys(saved?.seasonCompletions || {}).length), title);
    button.addEventListener('click', async () => {
      if (action === 'watched') { openWatchedDate(id, title); return; }
      button.disabled = true;
      try {
        const enabled = !savedSeries.get(String(id))?.[action];
        const data = await api('/api/tmdb/shelf', {id:String(id), title, action, enabled});
        renderShelf(data.items); if (data.artwork_pending) scheduleShelfRefresh();
      }
      catch (error) { toast(error.message, true); } finally { button.disabled = false; }
    }); actions.append(button);
  }
  return actions;
}
function renderShelf(items) {
  savedSeries = new Map(items.map(item => [String(item.id), item]));
  const host = $('#series-shelf-list'); host.replaceChildren();
  const timeline = shelfFilter === 'watched';
  const shelfItems = items.filter(item => !item.shelfHidden);
  const visible = timeline ? watchedShelfItems(items) : shelfItems.filter(item => shelfFilter === 'all' || item[shelfFilter]);
  host.classList.toggle('shelf-timeline', timeline);
  $('#shelf-count').textContent = timeline ? `${visible.length} 条记录` : `${visible.length} / ${shelfItems.length}`;
  $$('[data-shelf-filter]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.shelfFilter === shelfFilter)));
  if (!visible.length) {
    const empty = document.createElement('p'); empty.className = 'shelf-empty';
    empty.textContent = timeline ? '还没有观看记录。查询剧集后点“已观看”，就会按完成日期出现在这里。' : items.length ? '这个分组还没有剧集，切换“全部”查看。' : '查询剧集后点“收藏”“在看”或“已观看”，留住下一次与好故事相遇的位置。'; host.append(empty);
  }
  const imageURL = (path, size) => /^\/[\w-]+\.(jpg|png|webp)$/.test(path || '') ? `https://image.tmdb.org/t/p/${size}${path}` : '';
  let monthKey = null, timelineGroup = null;
  for (const item of visible) {
    const name = item.originalTitle || item.title || `TMDb ${item.id}`;
    const card = document.createElement('article'); card.className = 'shelf-card'; card.dataset.seriesId = item.id;
    const backdropURL = imageURL(item.backdropPath, 'w780');
    if (backdropURL) {
      const background = document.createElement('img'); background.className = 'shelf-backdrop'; background.src = backdropURL; background.alt = ''; background.loading = 'lazy'; background.setAttribute('aria-hidden','true'); background.onerror = () => background.remove(); card.append(background);
    }
    const poster = document.createElement('button'); poster.type = 'button'; poster.className = 'shelf-poster'; poster.setAttribute('aria-label', `查询 ${name} 的单集`);
    const placeholder = document.createElement('span'); placeholder.className = 'shelf-poster-fallback'; placeholder.textContent = name.slice(0,1); poster.append(placeholder);
    const posterURL = imageURL(item.posterPath, 'w185');
    if (posterURL) { const image = document.createElement('img'); image.src = posterURL; image.alt = `${name} 封面`; image.loading = 'lazy'; image.onerror = () => image.remove(); poster.append(image); }
    poster.onclick = () => openEpisodeDialog(item.id, name);
    const content = document.createElement('div'); content.className = 'shelf-info';
    const title = document.createElement('button'); title.className = 'shelf-title'; title.type = 'button'; title.textContent = name; title.title = name; title.onclick = () => openEpisodeDialog(item.id, name);
    const translated = document.createElement('p'); translated.className = 'shelf-subtitle'; translated.textContent = item.translatedTitle && item.translatedTitle !== name ? item.translatedTitle : ''; translated.hidden = !translated.textContent;
    const meta = document.createElement('p'); meta.className = 'shelf-meta'; meta.textContent = [item.year, item.rating > 0 ? `TMDb ${Number(item.rating).toFixed(1)}` : '', item.certification ? `${item.certificationCountry || '地区未标注'} · ${item.certification}` : '暂无内容分级'].filter(Boolean).join(' · ');
    const genre = document.createElement('p'); genre.className = 'shelf-genre'; genre.textContent = `类型：${(item.genres || []).join(' · ') || 'TMDb 暂未提供'}${item.series_type ? ` · ${item.series_type}` : ''}`;
    const counts = document.createElement('p'); counts.className = 'shelf-genre'; counts.textContent = `${Number.isInteger(item.season_count) ? `${item.season_count} 季` : '季数待补充'}${Number.isInteger(item.episode_count) ? ` · 正式集 ${item.episode_count} 集` : ''}${item.has_specials ? ' · 另含特别篇' : ''}`;
    const progress = document.createElement('p'); progress.className = 'shelf-progress'; progress.textContent = `${timeline ? completionLabel(item.completedSeason)+'已看' : item.watched ? '全剧已看' + (item.watching ? ' · 重看中' : '') : item.watching ? '在看' : Object.keys(item.seasonCompletions || {}).length ? '已有分季完看记录' : '已收藏'}${!timeline&&item.season !== undefined ? ` · 上次查询 S${String(item.season).padStart(2,'0')}E${String(item.episode).padStart(2,'0')}` : ''}`;
    const actions = document.createElement('div'); actions.className = 'shelf-quick-actions';
    const quick = shelfActions(item.id, name);
    const favorite = quick.querySelector('[data-shelf-action="favorite"]'); favorite.classList.add('shelf-star'); favorite.dataset.compact = 'true'; paintShelfStatus(favorite,'favorite',item.favorite,name);
    quick.removeChild(favorite); actions.append(quick);
    const source = document.createElement('button'); source.type = 'button'; source.className = 'mini-button'; source.textContent = '源数据'; source.onclick = () => loadSource(item.id); actions.append(source);
    progress.textContent = progress.textContent.replace('上次查询', '进度');
    const editProgress = document.createElement('button'); editProgress.type = 'button'; editProgress.className = 'mini-button'; editProgress.textContent = '✏️ 录入进度'; editProgress.onclick = () => openProgressDialog(item.id);
    const tmdbLink = document.createElement('a'); tmdbLink.className = 'mini-button'; tmdbLink.textContent = 'TMDb 详情'; tmdbLink.href = `https://www.themoviedb.org/tv/${encodeURIComponent(item.id)}`; tmdbLink.target = '_blank'; tmdbLink.rel = 'noopener noreferrer';
    const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'mini-button shelf-delete'; remove.textContent = '🗑 删除剧集'; remove.setAttribute('aria-label',`删除 ${name} 的 Namer 记录`); remove.onclick = async () => {remove.disabled=true;try{await deleteShelfItem(item.id)}catch(error){toast(error.message,true)}finally{remove.disabled=false}};
    actions.append(editProgress, tmdbLink, remove);
    remove.hidden = timeline;
    content.append(title, translated, meta, genre, counts, progress);
    if (timeline || item.watched || Object.keys(item.seasonCompletions || {}).length) {
      const date = document.createElement('button'); date.type = 'button'; date.className = 'shelf-watched-date';
      const firstSeason = Object.keys(item.seasonCompletions || {})[0];
      const season = timeline ? item.completedSeason : item.watched || firstSeason === undefined ? null : Number(firstSeason);
      const record = timeline ? item : watchRecord(item.id, season == null ? '' : String(season));
      date.textContent = `📅 ${completionLabel(season)} · ${record?.watchedDate || '补充日期'}${record?.personalStars ? ` · 个人 ${record.personalStars}★` : ''}`;
      date.setAttribute('aria-label',`编辑 ${name} ${completionLabel(season)}的观看日期和评分`);
      date.onclick = () => openWatchedDate(item.id, name, season); content.append(date);
    }
    content.append(actions); card.append(poster, content, favorite);
    if (timeline) {
      const key = /^\d{4}-\d{2}-\d{2}$/.test(item.watchedDate || '') ? item.watchedDate.slice(0,7) : 'unknown';
      if (key !== monthKey) {
        monthKey = key;
        const group = document.createElement('section'); group.className = 'shelf-month';
        const heading = document.createElement('h4'); heading.className = 'shelf-month-heading';
        heading.textContent = key === 'unknown' ? '日期待补' : `${key.slice(0,4)} 年 ${Number(key.slice(5))} 月`;
        timelineGroup = document.createElement('div'); timelineGroup.className = 'shelf-month-list';
        group.append(heading, timelineGroup); host.append(group);
      }
      timelineGroup.append(card);
    } else host.append(card);
  }
  $$('[data-shelf-id]').forEach(button => { const item = savedSeries.get(button.dataset.shelfId), action = button.dataset.shelfAction; paintShelfStatus(button,action,item?.[action] || (action==='watched'&&Object.keys(item?.seasonCompletions || {}).length),item?.originalTitle || item?.title || ''); });
}
function scheduleShelfRefresh() { clearTimeout(shelfRefreshTimer); shelfRefreshTimer = setTimeout(loadShelf, 1500); }
let progressEditingId = null;
function openProgressDialog(id) {
  const item = savedSeries.get(String(id)); if (!item) return;
  progressEditingId = String(id);
  $('#progress-series').textContent = item.originalTitle || item.title || `TMDb ${id}`;
  $('#progress-season').value = String(item.season ?? 1);
  $('#progress-episode').value = String(item.episode ?? 1);
  $('#progress-feedback').textContent = '';
  $('#progress-dialog').showModal(); $('#progress-season').focus();
}
async function deleteShelfItem(id) {
  const item = savedSeries.get(String(id)); if (!item) return;
  const name = item.originalTitle || item.title || `TMDb ${id}`;
  if (!confirm(`🗑 从「我的剧集」移除「${name}」？观看时间线、完看日期、评分和进度全部保留，不会删除网盘文件。再次标记收藏或在看可重新显示。`)) return;
  const data = await api('/api/tmdb/shelf', {id:String(id),action:'delete',confirm:true});
  renderShelf(data.items); if (data.artwork_pending) scheduleShelfRefresh();
  toast('✓ 已从我的剧集移除，观看时间线与评分已保留');
}
async function loadShelf() {
  clearTimeout(shelfRefreshTimer);
  try { const data = await api('/api/tmdb/shelf'); renderShelf(data.items); if (data.artwork_pending) scheduleShelfRefresh(); }
  catch (error) { $('#series-shelf-list').textContent = `收藏读取失败：${error.message}`; }
}
const namerQueue = new Map();
let namerBatchPlans = [];
const basePath = location.pathname.startsWith('/media-tools') ? '/media-tools' : '';
if (!basePath) { $('.home-link').href = 'https://banwugan.avecove.com/'; $('.home-link').textContent = '← 私人电影馆'; }
const mediaPathGroups = [
  {label:'115', root:'115'},
  {label:'光鸭', root:'GuangYa'},
  {label:'123', root:'123'},
  {label:'百度', root:'Baidu'},
];
const tvDirectories = ['01美', '01中', '01韩', '01日', '01台', '01英', '01欧'];
const movieDirectories = ['01国', '01外'];
const animeDirectories = {115:['01中','01日','01美'], GuangYa:['01中','01日'], 123:['01中'], Baidu:['01中','01日']};

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
      <span class="preset-type">动漫</span>
      ${(animeDirectories[provider.root] || []).map(name => `<button type="button" data-kind="tv" data-path="/${provider.root}/00漫/${name}/">${name}</button>`).join('')}
    </div>
  </section>`).join('');
}

$('#namer-presets').innerHTML = presetMarkup();

function switchView(name) {
  if (!Object.hasOwn(titles, name)) name = 'namer';
  $$('.nav-item').forEach(item => item.classList.toggle('active', item.dataset.view === name));
  $$('.view').forEach(view => view.classList.toggle('active', view.id === `view-${name}`));
  $('#page-title').textContent = titles[name];
  closeMobileNav();
  location.hash = name;
  if (name === 'detective') { loadDetective(); loadAutomationStatus(); }
}

$$('.nav-item').forEach(item => item.addEventListener('click', () => switchView(item.dataset.view)));
function closeMobileNav() {
  $('.sidebar').classList.remove('open');
  $('#nav-scrim').hidden = true;
  $('#mobile-menu').setAttribute('aria-expanded', 'false');
}
$('#mobile-menu').addEventListener('click', () => {
  const open = $('#mobile-menu').getAttribute('aria-expanded') !== 'true';
  $('.sidebar').classList.toggle('open', open);
  $('#nav-scrim').hidden = !open;
  $('#mobile-menu').setAttribute('aria-expanded', String(open));
});
$('#nav-scrim').addEventListener('click', closeMobileNav);
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
  hideArtwork();
  if (mediaKind === 'tv' && /^\d+$/.test(query)) return loadSource(query);
  checkLibrary(query);
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

function seriesInfoMarkup(data) {
  const seasons = data.seasons || [], ratings = data.content_ratings || [];
  const count = Number.isInteger(data.season_count) ? `${data.season_count} 季` : '季数暂无资料';
  const episodes = Number.isInteger(data.episode_count) ? ` · 正式集 ${data.episode_count} 集` : '';
  const types = [...(data.genres || []), ...(data.series_type ? [data.series_type] : [])];
  const ratingBadge = row => `<span class="content-rating">${escapeHtml(row.country)} · ${escapeHtml(row.rating)}</span>`;
  return `<section class="series-facts" aria-label="剧集季集、类型与内容分级"><div class="series-facts-heading"><strong>${count}${episodes}</strong>${data.has_specials ? '<small>特别篇单列，不计入季数</small>' : ''}</div><dl class="series-facts-meta"><div><dt>类型</dt><dd>${types.length ? types.map(escapeHtml).join(' · ') : 'TMDb 暂未提供'}</dd></div><div><dt>内容分级</dt><dd>${ratings.length ? ratings.slice(0,3).map(ratingBadge).join(' ') + (ratings.length > 3 ? `<details class="other-content-ratings"><summary>其他地区（${ratings.length-3}）</summary>${ratings.slice(3).map(ratingBadge).join(' ')}</details>` : '') : 'TMDb 暂未提供（不代表全年龄）'}</dd></div></dl><p class="meta series-rating-note">分级按地区展示，与 TMDb 评分无关；季集数以当前 TMDb 数据为准。</p>${seasons.length ? `<details class="series-season-list" open><summary>每季集数 · ${seasons.length} 项</summary><div class="series-season-grid">${seasons.map(row => `<div class="series-season-item"><strong>${row.number === 0 ? '特别篇 S00' : `Season ${String(row.number).padStart(2,'0')}`}</strong><span>${Number.isInteger(row.episode_count) ? `${row.episode_count} 集` : '集数暂无资料'}</span>${row.name ? `<small>${escapeHtml(row.name)}</small>` : ''}</div>`).join('')}</div></details>` : '<p class="meta">TMDb 暂无分季清单。</p>'}</section>`;
}

const summaryRequests = new Map();
function getSeriesSummary(id) {
  if (!summaryRequests.has(id)) summaryRequests.set(id, api('/api/tmdb/summary', {tmdb_id:id}).catch(error=>{summaryRequests.delete(id);throw error}));
  return summaryRequests.get(id);
}
async function loadSeriesSummary(id, host) {
  host.textContent = '正在读取季集、类型与内容分级…';
  host.setAttribute('aria-busy', 'true');
  try {
    const data = await getSeriesSummary(id);
    host.innerHTML = seriesInfoMarkup(data);
  } catch (error) {
    host.replaceChildren();
    const message = document.createElement('p'); message.className = 'meta'; message.textContent = error.message;
    const retry = document.createElement('button'); retry.type = 'button'; retry.className = 'mini-button'; retry.textContent = '↻ 重试剧集资料'; retry.onclick = () => loadSeriesSummary(id, host);
    host.append(message, retry);
  } finally { host.setAttribute('aria-busy', 'false'); }
}

function renderSearch(results, kind) {
  hideArtwork();
  const target = $('#tmdb-result');
  if (!results.length) return fail(target, new Error('没有找到匹配结果'));
  target.className = 'result-space search-list';
  target.innerHTML = results.map(item => `<article class="search-item">
    <div><h3>${escapeHtml(item.original_title || item.title || '未命名')}</h3><p>${item.kind === 'movie' ? '电影' : '剧集'} · 源片名${item.title && item.title !== item.original_title ? ` · 译名：${escapeHtml(item.title)}` : ''}${item.year ? ` · ${item.year}` : ''} · ${escapeHtml(item.language || '未知语言')} · <span class="id-tag">TMDb ${item.id}</span></p>${item.recommended_name ? `<div class="copy-name"><code>${escapeHtml(item.recommended_name)}</code></div>` : ''}</div>
    <div class="actions"><button class="mini-button copy-title-button" data-title="${escapeHtml(item.original_title || item.title || '')}">复制源片名</button><button class="mini-button copy-folder-button" data-name="${escapeHtml(item.recommended_name || '')}">复制源语言目录名</button><button class="mini-button copy-id-button" data-id="${item.id}">复制 TMDb 号</button>${(item.kind || kind) === 'tv' ? `<button class="mini-button episode-button" data-id="${item.id}" data-title="${escapeHtml(item.title || item.original_title || '')}">🎬 查询单集</button><button class="mini-button source-button" data-id="${item.id}">🖼 源数据与图片</button>` : ''}</div>
  </article>`).join('');
  target.querySelectorAll('.search-item').forEach((card, index) => {
    const item = results[index]; if ((item.kind || kind) !== 'tv') return;
    const details = document.createElement('details'); details.className = 'search-series-info';
    const summary = document.createElement('summary'); summary.textContent = '季集概览 · 类型 · 内容分级';
    const host = document.createElement('div'); let loaded = false;
    details.addEventListener('toggle', () => { if (details.open && !loaded) { loaded = true; loadSeriesSummary(String(item.id), host); } });
    details.append(summary, host); card.append(details);
    if (index === 0) details.open = true;
  });
  $$('.source-button').forEach(button => button.addEventListener('click', () => loadSource(button.dataset.id)));
  target.querySelectorAll('.search-item').forEach((card, index) => { const item = results[index]; if ((item.kind || kind) === 'tv') card.querySelector('.actions').append(shelfActions(item.id, item.original_title || item.title)); });
  $$('.episode-button').forEach(button => button.addEventListener('click', () => openEpisodeDialog(button.dataset.id, button.dataset.title)));
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

let episodeRequest = 0;
function openEpisodeDialog(id, title) {
  episodeRequest++;
  const dialog = $('#episode-dialog');
  dialog.dataset.seriesId = id;
  $('#episode-series').textContent = title ? `${title} · TMDb ${id}` : `TMDb ${id}`;
  const saved = savedSeries.get(String(id));
  $('#episode-season').value = saved?.season ?? '';
  $('#episode-number').value = saved?.episode ?? '';
  $('#episode-result').replaceChildren();
  $('#episode-submit').disabled = false;
  $('#episode-submit').textContent = '读取单集';
  dialog.showModal();
  $('#episode-season').focus();
}
$('#episode-close').addEventListener('click', () => $('#episode-dialog').close());
$('#episode-dialog').addEventListener('close', () => { episodeRequest++; });
$('#episode-form').addEventListener('submit', async event => {
  event.preventDefault();
  const seasonInput = $('#episode-season');
  const episodeInput = $('#episode-number');
  const season = seasonInput.value.trim();
  const episode = episodeInput.value.trim();
  const target = $('#episode-result');
  if (!/^\d{1,3}$/.test(season) || !/^\d{1,4}$/.test(episode) || Number(episode) < 1) {
    fail(target, new Error('季号和集号只填数字；集号从 1 开始，特别篇季号填 0'));
    (!/^\d{1,3}$/.test(season) ? seasonInput : episodeInput).focus();
    return;
  }
  const seasonNumber = String(Number(season)).padStart(2, '0');
  const episodeNumber = String(Number(episode)).padStart(2, '0');
  const code = `S${seasonNumber}E${episodeNumber}`;
  seasonInput.value = seasonNumber;
  episodeInput.value = episodeNumber;
  const request = ++episodeRequest;
  const button = $('#episode-submit');
  button.disabled = true;
  button.textContent = '读取中…';
  loading(target, `正在读取 ${code}`);
  try {
    const data = await api('/api/tmdb/episode', {tmdb_id: $('#episode-dialog').dataset.seriesId, code});
    if (request !== episodeRequest) return;
    target.className = 'episode-detail';
    target.innerHTML = `<h3>${escapeHtml(data.code)} · ${escapeHtml(data.name || data.translated_name || '暂无标题')}</h3>${data.translated_name && data.translated_name !== data.name ? `<p class="meta">译名：${escapeHtml(data.translated_name)}</p>` : ''}<p class="meta">首播 ${escapeHtml(data.air_date || '未知')}${data.runtime ? ` · ${Number(data.runtime)} 分钟` : ''} · ${data.vote_count ? `TMDb ${Number(data.rating).toFixed(1)}（${Number(data.vote_count)} 人评分）` : '暂无评分'} · 源语言 ${escapeHtml(data.original_language)}</p><p class="episode-overview">${escapeHtml(data.overview || 'TMDb 暂无该集简介')}</p>${data.original_overview && data.original_overview !== data.overview ? `<details><summary>源语言简介</summary><p class="episode-overview">${escapeHtml(data.original_overview)}</p></details>` : ''}<div class="actions"><button class="mini-button" id="episode-copy-title" type="button">复制源单集名</button><button class="mini-button" id="episode-copy-overview" type="button">复制简介</button><a class="mini-button" href="${escapeHtml(data.detail_url)}" target="_blank" rel="noopener noreferrer">TMDb 单集详情 ↗</a></div>`;
    const copyName = episodeCopyTitle(episodeNumber, data.name || data.translated_name);
    $('#episode-copy-title').textContent = `复制 ${copyName}`;
    $('#episode-copy-title').addEventListener('click', async () => { await navigator.clipboard.writeText(copyName); toast('集号与源单集名已复制'); });
    if (savedSeries.has($('#episode-dialog').dataset.seriesId)) {
      api('/api/tmdb/shelf', {id:$('#episode-dialog').dataset.seriesId, action:'progress', season:Number(season), episode:Number(episode)}).then(data => renderShelf(data.items)).catch(() => toast('单集已读取，但上次查询位置保存失败', true));
    }
    $('#episode-copy-overview').addEventListener('click', async () => { await navigator.clipboard.writeText(data.overview || ''); toast('简介已复制'); });
  } catch (error) {
    if (request === episodeRequest) fail(target, error);
  } finally {
    if (request === episodeRequest) { button.disabled = false; button.textContent = '读取单集'; }
  }
});

let artworkRequest = 0;
function hideArtwork() {
  artworkRequest++;
  $('#tmdb-artwork').hidden = true;
  $('#tmdb-workspace').classList.remove('has-artwork');
}

async function loadSource(id, season = null) {
  const request = ++artworkRequest;
  const target = $('#tmdb-result');
  const side = $('#tmdb-artwork');
  side.hidden = false;
  $('#tmdb-workspace').classList.add('has-artwork');
  loading(target, season === null ? '正在读取剧集源数据' : `正在读取 Season ${String(season).padStart(2,'0')} 的源数据与图片`);
  side.innerHTML = '<h3>🖼 封面与剧照</h3><p class="meta">按需读取当前季，不遍历全剧</p><p id="artwork-progress" role="status">正在读取…</p>';
  try {
    const response = await api('/api/tmdb/artwork', {tmdb_id:id, season});
    let data = response.data;
    while (!data) {
      if (request !== artworkRequest) return;
      const job = await api(`/api/jobs/${response.job_id}`);
      if (request !== artworkRequest) return;
      if (job.status === 'failed') throw new Error(job.output || '图片读取失败');
      if (job.status === 'completed') { data = job.data; break; }
      $('#artwork-progress').textContent = job.current || '正在读取…';
      await new Promise(resolve => setTimeout(resolve, 800));
    }
    if (request !== artworkRequest) return;
    lastSourceData = data;
    renderSourceWorkspace(data);
    renderArtwork(data);
  } catch (error) {
    if (request !== artworkRequest) return;
    fail(target, error);
    side.innerHTML = '<h3>🖼 读取失败</h3><p>请重试；暂时的网络错误不代表没有图片。</p><button class="secondary" id="artwork-retry">↻ 重新读取</button>';
    $('#artwork-retry').addEventListener('click', () => loadSource(id, season));
  }
}

function renderSourceWorkspace(data) {
  const target = $('#tmdb-result');
  target.className = 'result-space result-card';
  const scope = data.season === null ? '全剧概览' : `Season ${String(data.season).padStart(2,'0')} · ${data.episodes.length} 集`;
  target.innerHTML = `<div class="result-toolbar"><div><strong>${escapeHtml(data.original_name)}</strong><div class="meta">${escapeHtml(data.title)} · ${escapeHtml(data.year)} · ${escapeHtml(data.original_language)} · TMDb ${data.tmdb_id}</div></div></div><div class="source-name-bar"><code>${escapeHtml(data.recommended_name)}</code></div><div class="actions source-workspace-actions"><button class="mini-button" data-source-copy="title">复制源剧名</button><button class="mini-button" data-source-copy="folder">复制目录名</button><button class="mini-button" id="workspace-episode">🎬 查询单集</button><button class="mini-button" id="copy-json">复制当前源数据</button><button class="mini-button" id="download-json">下载源数据</button><button class="mini-button" id="source-all">读取全剧源数据</button></div><h3>${escapeHtml(scope)}</h3>${data.season === null ? `<p class="episode-overview">${escapeHtml(data.overview || '暂无简介')}</p><p class="meta">季数与每季集数见上方概览。在图片栏选择一季，源数据与图片一起切换。</p>` : `<h4>${escapeHtml(data.season_name || '')}</h4><div class="source-episode-list">${data.episodes.map(ep => `<details class="source-episode"><summary><b>E${String(ep.episode).padStart(2,'0')}</b> ${escapeHtml(ep.name || '暂无标题')}</summary><p class="episode-overview">${escapeHtml(ep.overview || '暂无源语言简介')}</p><div class="actions"><button class="mini-button workspace-copy" data-copy="${escapeHtml(ep.name)}">复制单集名</button><button class="mini-button workspace-copy" data-copy="${escapeHtml(ep.overview)}">复制源简介</button><button class="mini-button workspace-episode" data-episode="${ep.episode}">查看中文详情</button></div></details>`).join('') || '<p class="meta">该季暂无单集数据。</p>'}</div>`}`;
  $('[data-source-copy="title"]').addEventListener('click', async () => { await navigator.clipboard.writeText(data.original_name); toast('源剧名已复制'); });
  target.querySelector('.source-name-bar').insertAdjacentHTML('beforebegin', seriesInfoMarkup(data));
  target.querySelector('.source-workspace-actions').append(shelfActions(data.tmdb_id, data.original_name || data.title));
  target.querySelectorAll('.source-episode').forEach((row, index) => { const button = row.querySelector('.workspace-copy'); button.dataset.copy = episodeCopyTitle(data.episodes[index].episode, data.episodes[index].name); });
  $('[data-source-copy="folder"]').addEventListener('click', async () => { await navigator.clipboard.writeText(data.recommended_name); toast('目录名已复制'); });
  $('#workspace-episode').addEventListener('click', () => { openEpisodeDialog(String(data.tmdb_id), data.title); if (data.season !== null) $('#episode-season').value = String(data.season); });
  $('#copy-json').addEventListener('click', async () => { await navigator.clipboard.writeText(JSON.stringify(data, null, 2)); toast('当前源数据已复制'); });
  $('#download-json').addEventListener('click', downloadSource);
  $('#source-all').addEventListener('click', () => loadAllSource(String(data.tmdb_id)));
  $$('.workspace-copy').forEach(button => button.addEventListener('click', async () => { await navigator.clipboard.writeText(button.dataset.copy); toast('已复制'); }));
  $$('.workspace-episode').forEach(button => button.addEventListener('click', () => {
    openEpisodeDialog(String(data.tmdb_id), data.title);
    $('#episode-season').value = String(data.season); $('#episode-number').value = button.dataset.episode;
    $('#episode-form').requestSubmit();
  }));
}

function artworkGroup(data, key, title) {
  const images = data[key] || [];
  const scope = data.season === null ? 'series' : `S${String(data.season).padStart(2,'0')}`;
  return `<section class="artwork-group"><h4>${title}<small>${images.length} / 最多 5 张</small></h4>${images.length ? `<div class="artwork-grid ${key === 'posters' ? 'artwork-posters' : ''}">${images.map((image, index) => {
    const filename = `tmdb-${data.tmdb_id}-${scope}-${key}-${index + 1}`;
    const download = `${basePath}/api/tmdb/image?path=${encodeURIComponent(image.file_path)}&name=${encodeURIComponent(filename)}`;
    return `<figure class="artwork-card"><a href="${escapeHtml(image.original_url)}" target="_blank" rel="noopener noreferrer"><img src="${escapeHtml(image.preview_url)}" alt="${escapeHtml(data.original_name)} ${escapeHtml(title)} ${index + 1}" loading="lazy" referrerpolicy="no-referrer"></a><figcaption><strong>${image.vote_count ? `★ ${Number(image.vote_average).toFixed(2)}` : '暂无图片评分'}</strong><small>${image.vote_count ? `${Number(image.vote_count)} 票 · ` : ''}${Number(image.width) || '?'} × ${Number(image.height) || '?'}${image.episode ? ` · E${String(image.episode).padStart(2,'0')}` : ''}${image.iso_639_1 ? ` · ${escapeHtml(image.iso_639_1)}` : ' · 无语言'}</small><div class="actions"><a class="mini-button" href="${escapeHtml(download)}">下载原图</a><button class="mini-button artwork-copy" data-url="${escapeHtml(image.original_url)}">复制链接</button></div></figcaption></figure>`;
  }).join('')}</div>` : '<p class="meta artwork-empty">TMDb 暂无此类图片；不会拿其他季或全剧图片冒充。</p>'}</section>`;
}

function renderArtwork(data) {
  const side = $('#tmdb-artwork');
  side.innerHTML = `<div class="result-toolbar"><h3>🖼 封面与剧照</h3><a class="mini-button" href="${escapeHtml(data.detail_url)}" target="_blank" rel="noopener noreferrer">TMDb ↗</a></div><label class="artwork-season-label">源数据与图片 · 季选择<select id="artwork-season"><option value="">全剧概览</option>${data.seasons.map(item => `<option value="${item.number}"${item.number === data.season ? ' selected' : ''}>${item.number === 0 ? '特别篇 S00' : `Season ${String(item.number).padStart(2,'0')}`} · ${Number.isInteger(item.episode_count) ? `${item.episode_count} 集` : '集数暂无资料'}</option>`).join('')}</select></label><p class="meta artwork-ranking">每类按图片评分降序取前 5，评分相同优先票数；无投票项排后，再按清晰度排序。这里不是剧集评分。</p>${data.warnings.length ? `<p class="artwork-warning">${escapeHtml(data.warnings.join('；'))}</p><button class="mini-button" id="artwork-retry">↻ 重试本季</button>` : ''}${artworkGroup(data, 'posters', data.season === null ? '剧集封面' : '本季封面')}${artworkGroup(data, data.season === null ? 'backdrops' : 'stills', data.season === null ? '全剧背景图' : '本季剧照')}<p class="meta artwork-credit">图片与数据来源 TMDb。This product uses the TMDB API but is not endorsed or certified by TMDB.</p>`;
  $('#artwork-season').addEventListener('change', event => loadSource(String(data.tmdb_id), event.target.value === '' ? null : Number(event.target.value)));
  $$('.artwork-copy').forEach(button => button.addEventListener('click', async () => { await navigator.clipboard.writeText(button.dataset.url); toast('原图链接已复制'); }));
  if (data.warnings.length) $('#artwork-retry').addEventListener('click', () => loadSource(String(data.tmdb_id), data.season));
}

async function loadAllSource(id) {
  const request = ++artworkRequest;
  const target = $('#tmdb-result');
  loading(target, '正在读取全部季与单集');
  try {
    const data = await api('/api/tmdb/source', {tmdb_id: id});
    if (request !== artworkRequest) return;
    lastSourceData = data;
    const seasons = data.episodes.reduce((groups, episode) => {
      (groups[episode.season] ||= []).push(episode);
      return groups;
    }, {});
    target.className = 'result-space result-card';
    target.innerHTML = `<div class="result-toolbar"><div><strong>${escapeHtml(data.title || data.original_name)}</strong><div class="meta">${data.year || '年份未知'} · TMDb ${data.tmdb_id} · ${escapeHtml(data.original_language)} · ${data.episodes.length} 集</div></div><div class="actions"><button class="mini-button" id="copy-source-title">仅复制剧名</button><button class="mini-button" id="copy-source-folder">复制文件夹名</button><button class="mini-button" id="copy-tmdb-id">复制 TMDb 号</button><button class="mini-button" id="copy-json">复制源数据</button><button class="mini-button" id="download-json">下载</button></div></div>
      <div class="source-name-bar"><code>${escapeHtml(data.recommended_name || '')}</code></div>
      <div class="season-groups">${Object.entries(seasons).sort((a,b) => Number(a[0]) - Number(b[0])).map(([season, episodes], index) => `<details class="season-group" ${index === 0 ? 'open' : ''}><summary><span>Season ${String(season).padStart(2,'0')}</span><small>${episodes.length} 集</small></summary><div class="table-wrap"><table><thead><tr><th>集</th><th>源语言标题</th><th>源语言简介</th></tr></thead><tbody>${episodes.map(ep => `<tr><td class="num">E${String(ep.episode).padStart(2,'0')}</td><td><div class="episode-copy"><span>${escapeHtml(ep.name) || '—'}</span><button class="mini-button copy-episode-name" data-copy="${escapeHtml(ep.name)}">复制单集名</button></div></td><td class="overview"><div class="episode-copy"><span>${escapeHtml(ep.overview) || '—'}</span><button class="mini-button copy-episode-overview" data-copy="${escapeHtml(ep.overview)}">复制简介</button></div></td></tr>`).join('')}</tbody></table></div></details>`).join('')}</div>`;
    target.querySelector('.source-name-bar').insertAdjacentHTML('beforebegin', seriesInfoMarkup(data));
    const episodeButton = document.createElement('button');
    episodeButton.type = 'button'; episodeButton.className = 'mini-button'; episodeButton.textContent = '🎬 查询单集';
    episodeButton.addEventListener('click', () => openEpisodeDialog(String(data.tmdb_id), data.title || data.original_name));
    target.querySelector('.actions').prepend(episodeButton);
    $('#copy-tmdb-id').addEventListener('click', async () => { await navigator.clipboard.writeText(String(data.tmdb_id)); toast('TMDb ID 已复制'); });
    $('#copy-source-title').addEventListener('click', async () => { await navigator.clipboard.writeText(data.title || data.original_name); toast('剧名已复制'); });
    $('#copy-source-folder').addEventListener('click', async () => { await navigator.clipboard.writeText(data.recommended_name || data.original_name); toast('文件夹名已复制'); });
    $('#copy-json').addEventListener('click', async () => { await navigator.clipboard.writeText(JSON.stringify(lastSourceData, null, 2)); toast('JSON 已复制'); });
    $('#download-json').addEventListener('click', downloadSource);
    $$('.copy-episode-name').forEach(button => button.addEventListener('click', async () => { await navigator.clipboard.writeText(`${button.closest('tr').querySelector('.num').textContent}: ${button.dataset.copy}`); toast('集号与源单集名已复制'); }));
    $$('.copy-episode-overview').forEach(button => button.addEventListener('click', async () => { await navigator.clipboard.writeText(button.dataset.copy); toast('单集简介已复制'); }));
  } catch (error) { if (request === artworkRequest) fail(target, error); }
}

function downloadSource() {
  const blob = new Blob([JSON.stringify(lastSourceData, null, 2)], {type:'application/json'});
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  const season = lastSourceData.season == null ? '' : `-S${String(lastSourceData.season).padStart(2,'0')}`;
  link.download = `tmdb-${lastSourceData.tmdb_id}${season}-${lastSourceData.original_language}.json`;
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

$('#namer-presets').addEventListener('click', event => {
  const button = event.target.closest('button[data-path]');
  if (!button) return;
  const input = $('#namer-path');
  input.value = button.dataset.path;
  $('#namer-kind').value = button.dataset.kind || 'tv';
  input.focus();
  input.setSelectionRange(input.value.length, input.value.length);
});

const directoryNameOrder = new Intl.Collator('zh-CN', {numeric:true, sensitivity:'base'});
function sortDirectories(items) {
  return [...items].sort((a, b) => directoryNameOrder.compare(a.name || '', b.name || '') || String(a.path || '').localeCompare(String(b.path || '')));
}
let directoriesLoading = false;
async function loadActualPresets() {
  if (directoriesLoading) return;
  directoriesLoading = true;
  const reload = $('#namer-directory-reload');
  reload.disabled = true; reload.textContent = '正在读取…';
  const host = $('#namer-presets');
  try {
  for (const provider of mediaPathGroups) {
    const section = [...host.querySelectorAll('.preset-provider')].find(node => node.querySelector('strong')?.textContent === provider.label);
    if (!section) continue;
    const list = section.querySelector('.preset-directory-list');
    list.innerHTML = '';
    for (const [directory, label, kind] of [['00剧','剧集','tv'],['00影','电影','movie'],['00漫','动漫','tv']]) {
      try {
        const data = await api('/api/library/directories?path=' + encodeURIComponent(`/${provider.root}/${directory}`));
        if (!data.directories.length) continue;
        const group = document.createElement('details'); group.open = true;
        const summary = document.createElement('summary'); summary.textContent = `${label} · ${data.directories.length}`;
        group.append(summary);
        const content = document.createElement('div'); content.className = 'preset-category-items'; group.append(content);
        for (const item of sortDirectories(data.directories)) {
          const entry = document.createElement('div'); entry.className = 'preset-directory-entry';
          const button = document.createElement('button'); button.type = 'button'; button.className = 'preset-directory-name'; button.dataset.path = item.path + '/'; button.dataset.kind = kind; button.textContent = item.name;
          const expand = document.createElement('button'); expand.type = 'button'; expand.className = 'preset-expand'; expand.textContent = '展开 ▾'; expand.setAttribute('aria-expanded', 'false'); expand.setAttribute('aria-label', `展开 ${item.name} 的子目录`);
          entry.append(button, expand); content.append(entry);
          const children = document.createElement('div'); children.className = 'preset-children'; children.hidden = true;
          let loaded = false;
          expand.addEventListener('click', async () => {
            const opening = children.hidden;
            content.querySelectorAll('.preset-children').forEach(node => { node.hidden = true; });
            content.querySelectorAll('.preset-expand').forEach(node => { node.textContent = '展开 ▾'; node.setAttribute('aria-expanded','false'); });
            if (!opening) return;
            if (loaded) { children.hidden = false; expand.textContent = '收起 ▴'; expand.setAttribute('aria-expanded','true'); return; }
            expand.disabled = true;
            expand.textContent = '读取…';
            try {
              const nested = await api('/api/library/directories?path=' + encodeURIComponent(item.path));
              const heading = document.createElement('small'); heading.className = 'preset-children-label'; heading.textContent = `${item.name} · ${nested.directories.length} 个子目录`; children.append(heading);
              const directoryItems = sortDirectories(nested.directories).map(child => ({...child, kind, provider:provider.root, category:item.name}));
              if (directoryItems.length) {
                const all = document.createElement('button'); all.type = 'button'; all.className = 'preset-select-all'; all.textContent = '本目录全选 / 取消';
                all.addEventListener('click', () => {
                  if (directoryItems.every(child => namerQueue.has(child.path))) {
                    directoryItems.forEach(child => namerQueue.delete(child.path)); updateNamerQueue();
                  } else addNamerItems(directoryItems);
                });
                children.append(all);
              }
              for (const child of directoryItems) {
                const row = document.createElement('div'); row.className = 'preset-work';
                const check = document.createElement('input'); check.type = 'checkbox'; check.className = 'preset-work-check'; check.dataset.path = child.path; check.checked = namerQueue.has(child.path); check.setAttribute('aria-label', `选择 ${child.name}`);
                check.addEventListener('change', () => {
                  if (check.checked) addNamerItems([child]);
                  else { namerQueue.delete(child.path); updateNamerQueue(); }
                });
                const pick = document.createElement('button'); pick.type = 'button'; pick.dataset.path = child.path; pick.dataset.kind = kind; pick.textContent = child.name; pick.title = child.name;
                row.classList.toggle('selected', check.checked); row.append(check, pick); children.append(row);
              }
              if (!nested.directories.length) heading.textContent = `${item.name} · 没有子目录`;
              loaded = true; children.hidden = false; expand.textContent = '收起 ▴'; expand.setAttribute('aria-expanded','true');
            } catch (error) { toast(error.message, true); expand.textContent = '重试 ▾'; }
            expand.disabled = false;
          });
          content.append(children);
        }
        list.append(group);
      } catch (error) {
        // Unavailable paths are not proof of deletion. Keep a retry control.
        const retry = document.createElement('button'); retry.type = 'button'; retry.textContent = `${label} · 重试读取`;
        retry.addEventListener('click', loadActualPresets); list.append(retry);
      }
    }
  }
  } finally {
    directoriesLoading = false;
    reload.disabled = false; reload.textContent = '↻ 重新扫描目录';
  }
}

$('#namer-directory-reload').addEventListener('click', async () => {
  await loadActualPresets();
  toast('目录列表已重新读取，已勾选项目保留；展开可读取最新子目录');
});
loadActualPresets();

function renderIdentification(data) {
  const target = $('#namer-result');
  if (data.kind === 'collection') {
    $('#namer-kind').value = 'collection';
    const identified = data.members.filter(item => item.status === 'identified');
    target.className = 'result-space result-card';
    target.innerHTML = `<div class="result-toolbar"><div><strong>${escapeHtml(data.resolved.title)}</strong><p>${identified.length} 部已识别 · ${data.members.length - identified.length} 部需核验 · ${data.missing.length} 部待入库</p></div><button class="primary" id="confirm-collection">生成合集整理预览</button></div><p>${escapeHtml(data.reason)}</p><details><summary>查看合集成员</summary>${data.members.map(item => `<p>${escapeHtml(item.title || item.path)} ${item.year || ''} · ${item.status === 'identified' ? `TMDb ${item.tmdb_id}` : escapeHtml(item.reason)}</p>`).join('')}</details>${data.missing.length ? `<details><summary>查看待入库目录 (${data.missing.length})</summary>${data.missing.map(item => `<p>${escapeHtml(item.path.split('/').pop())}</p>`).join('')}</details>` : ''}`;
    $('#confirm-collection').addEventListener('click', () => createNamerPlan(data.collection_id));
    const memberRows = target.querySelector('details')?.querySelectorAll('p') || [];
    data.members.forEach((item, index) => {
      if (item.status === 'identified' || !memberRows[index]) return;
      const button = document.createElement('button'); button.type = 'button'; button.className = 'mini-button'; button.textContent = '修正这部电影';
      button.addEventListener('click', () => {
        $('#namer-path').value = item.path; $('#namer-kind').value = 'movie'; $('#namer-id').value = '';
        const advanced = $('#namer-id').closest('details'); if (advanced) advanced.open = true;
        $('#namer-id').focus(); toast('填入这部电影的 TMDb 编号后识别；未识别项默认保留，不会误改');
      });
      memberRows[index].append(button);
    });
    return;
  }
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
    const data = await api('/api/namer/plan', {path:$('#namer-path').value, tmdb_id:tmdbId, kind:$('#namer-kind').value, title_style:$('#namer-style').value, source_parent:$('#namer-source-parent').checked, fill_missing:$('#namer-fill-missing').checked});
    renderPlan(data);
  } catch (error) { fail(target, error); }
}

function renderPlan(plan) {
  const target = $('#namer-result');
  const blocked = plan.conflicts.length > 0;
  target.className = 'result-space result-card';
  target.innerHTML = `<div class="result-toolbar"><div><strong>${escapeHtml(plan.resolved.title)}</strong><div class="meta">${escapeHtml(plan.root)} · ${plan.operations.length} 项修改 · ${plan.skipped.length} 项跳过</div></div><span class="status ${blocked ? 'failed' : (plan.operations.length ? 'planned' : 'compliant')}">${blocked ? '有冲突' : (plan.operations.length ? '等待确认' : '已规范')}</span></div>
    ${blocked ? `<div class="conflict-box">${plan.conflicts.map(escapeHtml).join('<br>')}<p>不会覆盖或删除现有文件。可跳过冲突，只执行安全项。</p><button class="secondary" id="skip-plan-conflicts" type="button">跳过冲突，保留其余</button></div>` : ''}
    <div class="actions"><button class="mini-button" id="edit-plan-id" type="button">重新指定 TMDb</button><button class="mini-button" id="retain-plan-items" type="button">保留勾选项的原名称</button><button class="mini-button" id="cancel-namer-plan" type="button">暂不执行</button></div>
    <div class="table-wrap"><table><thead><tr><th>类型</th><th>改名前 → 改名后</th></tr></thead><tbody>${plan.operations.map(op => `<tr><td>${escapeHtml(op.kind)}</td><td><div class="operation"><span class="from">${escapeHtml(op.source)}</span><span class="arrow">↓</span><span class="to">${escapeHtml(op.target)}</span></div></td></tr>`).join('') || '<tr><td colspan="2">无需修改</td></tr>'}</tbody></table></div>
    ${!blocked && plan.operations.length ? `<div class="apply-panel"><label><span>执行确认</span><div class="confirm-row"><input id="apply-confirm" autocomplete="off" placeholder="执行 ${plan.operations.length} 项"><button class="secondary" type="button" id="fill-confirm">一键填入</button></div></label><button class="primary danger" id="apply-plan">确认执行</button></div>` : ''}`;
  const button = $('#apply-plan');
  if (button) button.addEventListener('click', () => applyPlan(plan.plan_id, plan.operations.length));
  const fillButton = $('#fill-confirm');
  if (fillButton) fillButton.addEventListener('click', () => { $('#apply-confirm').value = `执行 ${plan.operations.length} 项`; toast('确认文字已填入'); });
  target.querySelectorAll('tbody tr').forEach((row, index) => {
    if (!plan.operations[index]) return;
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox'; checkbox.className = 'retain-operation'; checkbox.dataset.index = index;
    checkbox.setAttribute('aria-label', '保留该项原名称'); row.cells[0].prepend(checkbox);
  });
  async function revise(skipConflicts) {
    const excluded = [...target.querySelectorAll('.retain-operation:checked')].map(node => Number(node.dataset.index));
    if (!skipConflicts && !excluded.length) return toast('请勾选要保留原名称的操作');
    try {
      const revised = await api('/api/namer/plan/review', {plan_id:plan.plan_id, exclude_indices:excluded, skip_conflicts:skipConflicts});
      renderPlan({...revised, resolved:plan.resolved});
      toast('已更新预览，尚未执行改名');
    } catch (error) { toast(error.message, true); }
  }
  $('#skip-plan-conflicts')?.addEventListener('click', () => revise(true));
  $('#retain-plan-items').addEventListener('click', () => revise(false));
  $('#edit-plan-id').addEventListener('click', () => {
    $('#namer-path').value = plan.root;
    const advanced = $('#namer-id').closest('details'); if (advanced) advanced.open = true;
    $('#namer-id').focus(); $('#namer-id').select(); toast('填入正确 TMDb 编号，再点击自动识别');
  });
  $('#cancel-namer-plan').addEventListener('click', () => { target.innerHTML = '<div class="history-empty">已暂缓，本次没有执行任何改名。</div>'; });
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
    const progress = namerProgress(job);
    target.className = 'result-space job-card';
    target.innerHTML = `<span class="status ${escapeHtml(job.status)}">${job.status === 'running' ? '执行中' : (job.status === 'completed' ? '已完成' : '失败')}</span><h3>${job.status === 'running' ? '正在执行改名' : (job.status === 'completed' ? '改名已完成' : '改名任务失败')}</h3><p>${escapeHtml(job.path)}</p>${progress}${job.output ? `<pre>${escapeHtml(job.output)}</pre>` : '<p>操作正在服务器后台安全执行，并逐项写入回滚记录。</p>'}`;
    if (job.status === 'running') setTimeout(() => pollNamerJob(jobId), 2500);
    else if (job.status === 'completed') {
      toast('改名已完成');
    } else toast('改名任务失败，请查看错误信息', true);
  } catch (error) { fail(target, error); }
}

function namerProgress(job) {
  const total = Math.max(0, Number(job.operations) || 0);
  const done = Math.min(total, Math.max(0, Number(job.completed_operations) || 0));
  const percent = total ? Math.round(done / total * 100) : 0;
  const directory = job.total ? `<span>目录 ${Math.min(job.total, (Number(job.completed) || 0) + (Number(job.failed) || 0))}/${job.total}</span>` : '';
  return `<div class="namer-progress" role="progressbar" aria-label="Namer 改名进度" aria-valuemin="0" aria-valuemax="${total}" aria-valuenow="${done}"><div class="namer-progress-meta"><strong>${done} / ${total} 项</strong><span>${percent}%</span>${directory}</div><div class="namer-progress-track"><div class="namer-progress-fill" style="width:${percent}%"></div></div>${job.current ? `<small>当前目录：${escapeHtml(job.current)}</small>` : ''}${job.current_operation ? `<small>最近完成：${escapeHtml(job.current_operation)}</small>` : ''}</div>`;
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

function beginNamerForPath(path, kind) {
  $('#namer-path').value = path;
  $('#namer-kind').value = kind === 'movie' ? 'movie' : 'tv';
  $('#namer-form').requestSubmit();
  $('#namer-result').scrollIntoView({behavior:'smooth', block:'center'});
}

function updateNamerQueue() {
  const count = namerQueue.size;
  const providers = new Set([...namerQueue.values()].map(item => item.provider || item.path.split('/')[1]));
  $('#namer-queue-count').textContent = count ? `已选 ${count} 个目录 · ${providers.size} 个网盘 · 串行处理` : '展开目录，勾选需要改名的作品';
  $('#namer-plan-queue').disabled = !count;
  $('#namer-clear-queue').disabled = !count;
  $$('.preset-work-check').forEach(check => {
    check.checked = namerQueue.has(check.dataset.path);
    check.closest('.preset-work').classList.toggle('selected', check.checked);
  });
  const selected = $('#namer-selected'); selected.hidden = !count;
  selected.replaceChildren();
  for (const item of namerQueue.values()) {
    const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'namer-selected-item';
    remove.textContent = `${providerLabel(item.provider || item.path.split('/')[1])} · ${item.name} ×`; remove.title = `移除：${item.path}`;
    remove.addEventListener('click', () => { namerQueue.delete(item.path); updateNamerQueue(); }); selected.append(remove);
  }
}

function addNamerItems(items) {
  const paths = new Set([...namerQueue.keys(), ...items.map(item => item.path)]);
  if (paths.size > 30) { toast('每批最多 30 个目录，请分批勾选；当前选择已保留', true); updateNamerQueue(); return; }
  items.forEach(item => namerQueue.set(item.path, item));
  updateNamerQueue();
}

$('#namer-plan-queue').addEventListener('click', planNamerQueue);
$('#namer-clear-queue').addEventListener('click', () => { namerQueue.clear(); updateNamerQueue(); });

async function planNamerQueue() {
  const button = $('#namer-plan-queue');
  const target = $('#namer-result');
  const items = [...namerQueue.values()];
  const sourceParent = $('#namer-source-parent').checked;
  namerBatchPlans = [];
  button.disabled = true;
  for (let index = 0; index < items.length; index += 1) {
    const item = items[index];
    target.className = 'result-space job-card';
    target.innerHTML = `<span class="status running">预览中</span><h3>正在串行识别 ${index + 1}/${items.length}</h3><p>${escapeHtml(item.path)}</p><p>逐个调用 TMDb 并生成只读计划，不会在此阶段改名。</p>`;
    try {
      const identified = await api('/api/namer/identify', {path:item.path, kind:item.kind, title_style:'auto', source_parent:sourceParent});
      const plan = await api('/api/namer/plan', {path:item.path, tmdb_id:identified.tmdb_id, kind:identified.kind || item.kind, title_style:'auto', source_parent:sourceParent, fill_missing:$('#namer-fill-missing').checked});
      namerBatchPlans.push({...plan, queue_item:item});
    } catch (error) {
      namerBatchPlans.push({queue_item:item, error:error.message || String(error), operations:[], conflicts:[]});
    }
  }
  const executable = namerBatchPlans.filter(plan => !plan.error && !plan.conflicts.length && plan.operations.length);
  const operationCount = executable.reduce((sum, plan) => sum + plan.operations.length, 0);
  const failed = namerBatchPlans.length - executable.length;
  target.className = 'result-space result-card';
  target.innerHTML = `<div class="result-toolbar"><div><strong>${executable.length} 个目录可以批量执行</strong><div class="meta">共 ${operationCount} 项改名 · ${failed} 个无需处理或需单独审核</div></div><span class="status ${executable.length ? 'planned' : 'compliant'}">${executable.length ? '等待确认' : '无需执行'}</span></div><div class="compact-task-list">${namerBatchPlans.map(plan => `<article class="compact-task-row"><div><strong>${escapeHtml(plan.queue_item.name)}</strong><small>${plan.error ? escapeHtml(plan.error) : (plan.conflicts.length ? `${plan.conflicts.length} 个冲突` : `${plan.operations.length} 项改名`)}</small></div><span class="status ${plan.error || plan.conflicts.length ? 'failed' : (plan.operations.length ? 'planned' : 'compliant')}">${plan.error || plan.conflicts.length ? '待审核' : (plan.operations.length ? '可执行' : '已规范')}</span></article>`).join('')}</div>${executable.length ? `<div class="apply-panel"><label><span>批量执行确认</span><div class="confirm-row"><input id="batch-apply-confirm" autocomplete="off" placeholder="批量执行 ${executable.length} 个目录"><button class="secondary" id="batch-fill-confirm" type="button">一键填入</button></div></label><button class="primary danger" id="batch-apply-plan" type="button">确认批量执行</button></div>` : ''}`;
  if (executable.length) {
    $('#batch-fill-confirm').addEventListener('click', () => { $('#batch-apply-confirm').value = `批量执行 ${executable.length} 个目录`; });
    $('#batch-apply-plan').addEventListener('click', () => applyNamerBatch(executable));
  }
  target.querySelectorAll('.compact-task-row').forEach((row, index) => {
    const plan = namerBatchPlans[index];
    if (!plan || (!plan.error && !plan.conflicts.length)) return;
    const actions = document.createElement('div'); actions.className = 'actions';
    const review = document.createElement('button'); review.type = 'button'; review.className = 'mini-button'; review.textContent = '立即处理';
    review.addEventListener('click', () => {
      $('#namer-path').value = plan.queue_item.path;
      $('#namer-kind').value = plan.queue_item.kind || 'tv';
      if (plan.plan_id) renderPlan(plan);
      else {
        $('#namer-id').value = '';
        const advanced = $('#namer-id').closest('details'); if (advanced) advanced.open = true;
        $('#namer-id').focus(); toast('可填写 TMDb 编号后重新识别');
      }
      $('#namer-form').scrollIntoView({behavior:'smooth', block:'start'});
    });
    const skip = document.createElement('button'); skip.type = 'button'; skip.className = 'mini-button'; skip.textContent = '本次跳过';
    skip.addEventListener('click', () => { namerQueue.delete(plan.queue_item.path); row.querySelector('.status').textContent = '已跳过'; updateNamerQueue(); });
    actions.append(review, skip); row.append(actions);
  });
  updateNamerQueue();
}

async function applyNamerBatch(plans) {
  const button = $('#batch-apply-plan');
  button.disabled = true;
  try {
    const data = await api('/api/namer/batch/apply', {plan_ids:plans.map(plan => plan.plan_id), confirmation:$('#batch-apply-confirm').value});
    toast('批量改名已在后台严格串行启动');
    pollNamerBatchJob(data.job_id);
  } catch (error) { toast(error.message || String(error), true); button.disabled = false; }
}

async function pollNamerBatchJob(jobId) {
  const target = $('#namer-result');
  try {
    const job = await api(`/api/jobs/${jobId}`);
    target.className = 'result-space job-card';
    target.innerHTML = `<span class="status ${escapeHtml(job.status)}">${job.status === 'running' ? '执行中' : (job.status === 'completed' ? '已完成' : '部分失败')}</span><h3>${job.status === 'running' ? `正在串行改名第 ${job.current_index || 1}/${job.total} 个目录` : `批量改名完成 ${job.completed || 0}/${job.total}`}</h3>${namerProgress(job)}${job.output ? `<pre>${escapeHtml(job.output)}</pre>` : '<p>服务器一次只处理一个目录，页面可以关闭。</p>'}`;
    if (job.status === 'running') setTimeout(() => pollNamerBatchJob(jobId), 2500);
    else {
      namerQueue.clear(); updateNamerQueue();
      toast(job.status === 'completed' ? '批量改名完成' : '部分目录失败，请查看任务记录', job.status !== 'completed');
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

let litepanChanges = [];
let handoffItems = [];
async function loadHandoffInbox() {
  try {
    const data = await api('/api/namer/handoff');
    handoffItems = data.items || [];
    $('#handoff-status').textContent = data.pending ? `${data.pending} 条通知 · 本批 ${handoffItems.length} 项 · 加入后仍需预览确认` : '暂无收录通知 · 资源完成转存后可通过本机 notify 命令交接';
    $('#handoff-add').disabled = !handoffItems.length;
    $('#handoff-ack').disabled = !handoffItems.length;
  } catch (error) { $('#handoff-status').textContent = `通知读取失败：${error.message}`; }
}
$('#handoff-reload').addEventListener('click', loadHandoffInbox);
$('#handoff-add').addEventListener('click', () => { addNamerItems(handoffItems); toast('通知已加入选择；请生成预览并确认，尚未改名'); });
$('#handoff-ack').addEventListener('click', async () => {
  if (!confirm('📥 仅标记本批通知已处理？不会删除或改名网盘文件。')) return;
  try { await api('/api/namer/handoff/ack', {items:handoffItems.map(({id, signature}) => ({id, signature}))}); await loadHandoffInbox(); }
  catch (error) { toast(error.message, true); }
});
async function loadLitepanChanges() {
  const status = $('#litepan-changes-status');
  try {
    const data = await api('/api/litepan/changes');
    litepanChanges = data.items || [];
    status.textContent = !data.ready ? '等待首次同步建立基线' : data.pending
      ? `${data.pending} 项内容变化 · 本批 ${litepanChanges.length} 项 · 加入后仍需预览确认`
      : `暂无新变化 · 已跟踪 ${data.trackedWorks} 个目录 · 每 5 分钟增量核对`;
    $('#litepan-changes-add').disabled = !litepanChanges.length;
    $('#litepan-changes-ack').disabled = !litepanChanges.length;
  } catch (error) { status.textContent = `变化记录暂不可用：${error.message}`; }
}
$('#litepan-changes-reload').addEventListener('click', loadLitepanChanges);
$('#litepan-changes-add').addEventListener('click', () => addNamerItems(litepanChanges));
$('#litepan-changes-ack').addEventListener('click', async () => {
  if (!confirm(`📋 将本批 ${litepanChanges.length} 项标记为已处理？仅清除变化提示，不修改网盘文件。`)) return;
  try {
    await api('/api/litepan/changes/ack', {items:litepanChanges.map(({id, signature}) => ({id, signature}))});
    await loadLitepanChanges(); toast('已更新变化记录');
  } catch (error) { toast(error.message, true); }
});

$('#today').textContent = new Intl.DateTimeFormat('zh-CN', {month:'long', day:'numeric', weekday:'short'}).format(new Date());
applyScene();
setInterval(applyScene, 300000);
const initial = location.hash.slice(1);
switchView(initial);
updateNamerQueue();
checkHealth();
$('#namer-utilities').addEventListener('toggle', () => {
  if ($('#namer-utilities').open) { loadLitepanChanges(); loadHandoffInbox(); }
});
$('#shelf-reload').addEventListener('click', loadShelf);
$('#progress-cancel').addEventListener('click', () => $('#progress-dialog').close());
$('#progress-form').addEventListener('submit', async event => {
  event.preventDefault();
  const season = $('#progress-season').value.trim(), episode = $('#progress-episode').value.trim();
  if (!/^\d{1,3}$/.test(season) || !/^\d{1,4}$/.test(episode) || Number(episode)<1) {
    $('#progress-feedback').textContent = '季号请填 0–999，集号请填 1–9999 的整数'; return;
  }
  if ($('#progress-save').disabled || !progressEditingId) return;
  const id=progressEditingId; $('#progress-save').disabled=true;
  try {
    const data=await api('/api/tmdb/shelf',{id,action:'progress',season:Number(season),episode:Number(episode)});
    renderShelf(data.items); if(data.artwork_pending) scheduleShelfRefresh();
    if(progressEditingId===id&&$('#progress-dialog').open)$('#progress-dialog').close();
    toast('✓ 观看进度已保存；不会自动标记全剧已看');
  } catch(error) { $('#progress-feedback').textContent=error.message; }
  finally { $('#progress-save').disabled=false; }
});
$('#watched-cancel').addEventListener('click', () => $('#watched-dialog').close());
$('#watched-scope').addEventListener('change', fillWatchRecord);
let watchSaving = false;
async function saveWatchRecord(enabled) {
  if (!watchedEditingId || watchSaving) return;
  const scope = $('#watched-scope').value;
  const day = $('#watched-date').value;
  if (enabled && (!/^\d{4}-\d{2}-\d{2}$/.test(day) || day > watchDateToday() || day < '1900-01-01')) {
    $('#watched-feedback').textContent = '请选择 1900 年至今天之间的有效日期'; return;
  }
  const version = watchedDialogVersion;
  const payload = {id:watchedEditingId,title:watchedEditingTitle,action:'watched',enabled,seasonNumber:scope===''?null:Number(scope)};
  if (enabled) Object.assign(payload,{watchedDate:day,personalStars:$('#watched-stars').value===''?null:Number($('#watched-stars').value)});
  watchSaving = true;
  $('#watched-save').disabled = $('#watched-remove').disabled = true;
  try {
    const data = await api('/api/tmdb/shelf', payload);
    renderShelf(data.items); if (data.artwork_pending) scheduleShelfRefresh();
    if (version === watchedDialogVersion && $('#watched-dialog').open) $('#watched-dialog').close();
    toast(enabled ? '✓ 完看日期与评分已保存' : '已移除这条完看记录');
  } catch (error) { if (version === watchedDialogVersion) $('#watched-feedback').textContent = error.message; }
  finally { watchSaving = false; $('#watched-save').disabled = $('#watched-remove').disabled = false; }
}
$('#watched-form').addEventListener('submit', event => {event.preventDefault();return saveWatchRecord(true)});
$('#watched-remove').addEventListener('click', () => {
  const scope=$('#watched-scope').value;
  if (watchRecord(watchedEditingId,scope) && confirm(`🗑 移除「${completionLabel(scope===''?null:Number(scope))}」的完看日期和评分？其他季与收藏不受影响。`)) return saveWatchRecord(false);
});
$$('[data-shelf-filter]').forEach(button => button.addEventListener('click', () => { shelfFilter = button.dataset.shelfFilter; renderShelf([...savedSeries.values()]); }));
loadShelf();

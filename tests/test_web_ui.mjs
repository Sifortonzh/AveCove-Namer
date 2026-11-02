import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

// Isolated DOM contract test: no browser, cloud requests or mutations.
const nodes = new Map();
class Element {
  constructor() { this.dataset = {}; this.listeners = {}; this.value = ''; this.innerHTML = ''; this.classList = {add() {}, remove() {}, toggle() {}}; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  querySelectorAll() { return []; }
  querySelector() { return new Element(); }
  replaceChildren() { this.innerHTML = ''; }
  insertAdjacentHTML(position, html) { this.innerHTML += html; }
  showModal() { this.open = true; }
  close() { this.open = false; this.listeners.close?.(); }
  focus() { this.focused = true; }
  setAttribute(name,value) { (this.attributes??={})[name]=String(value); }
  getAttribute(name) { return this.attributes?.[name]??null; }
  append(...children) { (this.children??=[]).push(...children); }
  removeChild(child) { this.children=(this.children||[]).filter(x=>x!==child); }
}
const element = selector => {
  if (!nodes.has(selector)) nodes.set(selector, new Element());
  return nodes.get(selector);
};
const calls = [];
let episodeResponse = async () => ({code:'S01E02', name:'The Kingsroad', translated_name:'国王大道', overview:'中文简介', original_language:'en', detail_url:'https://www.themoviedb.org/tv/1399/season/1/episode/2'});
const artworkData = {tmdb_id:1399,title:'权力的游戏',original_name:'Game of Thrones',original_language:'en',year:'2011',recommended_name:'Game of Thrones (2011)',overview:'简介',season:null,seasons:[{number:1,episode_count:10}],episodes:[],warnings:[],posters:[],backdrops:[],stills:[],detail_url:'https://www.themoviedb.org/tv/1399'};
let artworkResponse = async () => ({data:artworkData});
const summaryData = {seasons:[{number:0,name:'特别篇',episode_count:3},{number:1,name:'第一季',episode_count:10},{number:2,name:'第二季',episode_count:8}],season_count:2,episode_count:18,has_specials:true,genres:['剧情','悬疑'],series_type:'Scripted',content_ratings:[{country:'US',rating:'TV-MA'}]};
const context = vm.createContext({
  document: {querySelector:element, querySelectorAll:() => [], createElement:()=>new Element(), documentElement:{dataset:{}}},
  location: {pathname:'/media-tools/', hash:'#emby'},
  navigator: {clipboard:{writeText:async () => {}}},
  setTimeout:() => 1, clearTimeout() {}, setInterval() {}, console,
  fetch:async (url, options) => {
    calls.push({url, options});
    const data = url.endsWith('/api/tmdb/summary') ? summaryData : url.endsWith('/api/tmdb/episode') ? await episodeResponse() : url.endsWith('/api/tmdb/artwork') ? await artworkResponse() : {status:'ok'};
    return {ok:true, json:async () => data};
  },
});
vm.runInContext(fs.readFileSync(new URL('../src/avecove_namer/web/app.js', import.meta.url), 'utf8'), context);
for (const [action,icon] of [['favorite','🌟'],['watching','👀'],['watched','✅']]) {
  const button = new Element(); context.statusTestButton = button;
  vm.runInContext(`paintShelfStatus(statusTestButton, '${action}', false, 'Example')`, context);
  assert.ok(button.textContent.startsWith(icon)); assert.equal(button.getAttribute('aria-pressed'),'false');
  assert.match(button.getAttribute('aria-label'),/标记.*Example/);
  button.dataset.compact='true';
  vm.runInContext(`paintShelfStatus(statusTestButton, '${action}', true, 'Example')`, context);
  assert.equal(button.textContent,icon); assert.equal(button.getAttribute('aria-pressed'),'true');
  assert.match(button.getAttribute('aria-label'),/取消.*Example/);
}
assert.deepEqual(JSON.parse(vm.runInContext('JSON.stringify(watchedShelfItems([{id:"1",watched:true,watchedDate:"2025-01-01",updatedAt:999},{id:"2",watched:true,watchedDate:"2025-02-01"},{id:"3",watching:true}]).map(x=>x.id))',context)), ['2','1']);
vm.runInContext('savedSeries = new Map([["1",{id:"1",watched:true,watchedDate:"2025-01-01",title:"Test series"}]]) ; openWatchedDate("1")',context);
assert.equal(element('#watched-dialog').open,true);
assert.equal(element('#watched-date').value,'2025-01-01');
element('#watched-date').value='2999-01-01';
const beforeInvalidDate = calls.length;
await element('#watched-form').listeners.submit({preventDefault(){}});
assert.equal(calls.length,beforeInvalidDate);
assert.match(element('#watched-feedback').textContent,/今天/);
element('#watched-cancel').listeners.click();
assert.equal(element('#watched-dialog').open,false);
assert.equal(context.location.hash, 'namer', 'Old Emby bookmark falls back to Namer');
assert.equal(element('#page-title').textContent, 'AveCove Namer');
assert.doesNotMatch(fs.readFileSync(new URL('../src/avecove_namer/web/index.html', import.meta.url), 'utf8'), /scout-workbench|巡影收录目录|AveCove Media Tools/);
element('#mobile-menu').listeners.click();
assert.equal(element('#mobile-menu').getAttribute('aria-expanded'),'true');
assert.equal(element('#nav-scrim').hidden,false);
element('#nav-scrim').listeners.click();
assert.equal(element('#mobile-menu').getAttribute('aria-expanded'),'false');
assert.equal(element('#nav-scrim').hidden,true);
vm.runInContext('renderSearch([{id:1399,kind:"tv",title:"权力的游戏"}], "tv")', context);
assert.match(element('#tmdb-result').innerHTML, /🎬 查询单集/);
vm.runInContext('renderSearch([{id:843,kind:"movie",title:"花樣年華"}], "movie")', context);
assert.doesNotMatch(element('#tmdb-result').innerHTML, /episode-button/);
vm.runInContext('openEpisodeDialog("1399", "权力的游戏")', context);
assert.equal(element('#episode-dialog').open, true);
element('#episode-season').value = '1';
element('#episode-number').value = '2';
await element('#episode-form').listeners.submit({preventDefault() {}});
assert.equal(element('#episode-season').value, '01');
assert.equal(element('#episode-number').value, '02');
assert.match(element('#episode-result').innerHTML, /The Kingsroad/);
assert.match(element('#episode-result').innerHTML, /中文简介/);
assert.deepEqual(JSON.parse(calls.at(-1).options.body), {tmdb_id:'1399', code:'S01E02'});
element('#episode-season').value = 'S_';
const before = calls.length;
await element('#episode-form').listeners.submit({preventDefault() {}});
assert.equal(calls.length, before, 'Invalid input sends no request');
element('#episode-season').value = '0';
element('#episode-number').value = '1';
await element('#episode-form').listeners.submit({preventDefault() {}});
assert.deepEqual(JSON.parse(calls.at(-1).options.body), {tmdb_id:'1399', code:'S00E01'});
element('#episode-season').value = '100';
element('#episode-number').value = '002';
await element('#episode-form').listeners.submit({preventDefault() {}});
assert.deepEqual(JSON.parse(calls.at(-1).options.body), {tmdb_id:'1399', code:'S100E02'});
episodeResponse = async () => { throw new Error('未找到该集'); };
element('#episode-season').value = '99';
element('#episode-number').value = '999';
await element('#episode-form').listeners.submit({preventDefault() {}});
assert.match(element('#episode-result').innerHTML, /未找到该集/);
assert.equal(element('#episode-submit').disabled, false);
element('#episode-close').listeners.click();
assert.equal(element('#episode-dialog').open, false);
await vm.runInContext('loadSource("1399")', context);
assert.equal(element('#tmdb-artwork').hidden, false);
assert.match(element('#tmdb-result').innerHTML, /Game of Thrones/);
assert.match(element('#tmdb-artwork').innerHTML, /artwork-season/);
assert.match(element('#tmdb-artwork').innerHTML, /暂无此类图片/);
artworkResponse = async () => ({data:{...artworkData,season:1,episodes:[{episode:2,name:'The Kingsroad',overview:'source text'}],posters:[{file_path:'/abc.jpg',original_url:'https://image.tmdb.org/t/p/original/abc.jpg',preview_url:'https://image.tmdb.org/t/p/w500/abc.jpg',vote_count:3,vote_average:8,width:1000,height:1500}]}});
await vm.runInContext('loadSource("1399", 1)', context);
assert.match(element('#tmdb-result').innerHTML, /The Kingsroad/);
assert.match(element('#tmdb-artwork').innerHTML, /★ 8.00/);
assert.match(element('#tmdb-artwork').innerHTML, /api\/tmdb\/image\?path=/);
assert.deepEqual(JSON.parse(calls.at(-1).options.body), {tmdb_id:'1399',season:1});
let resolveOldArtwork;
artworkResponse = () => new Promise(resolve => { resolveOldArtwork = resolve; });
const oldArtwork = vm.runInContext('loadSource("1399", 1)', context);
artworkResponse = async () => ({data:{...artworkData,original_name:'Latest selection'}});
await vm.runInContext('loadSource("1399")', context);
resolveOldArtwork({data:{...artworkData,original_name:'Stale selection'}});
await oldArtwork;
assert.match(element('#tmdb-result').innerHTML, /Latest selection/);
assert.doesNotMatch(element('#tmdb-result').innerHTML, /Stale selection/);
vm.runInContext('renderSearch([{id:1399,kind:"tv",title:"权力的游戏"}], "tv")', context);
assert.equal(element('#tmdb-artwork').hidden, true);
// Notifications only select paths; explicit preview/apply remains separate.
const notifyStart = calls.length;
vm.runInContext('handoffItems=[{id:"notice-1",signature:"v1",path:"/123/00影/01国/Example",name:"Example",provider:"123",kind:"movie"}]', context);
element('#handoff-add').listeners.click();
assert.equal(calls.length, notifyStart, 'Adding notification to queue performs no cloud or apply request');
assert.equal(vm.runInContext('namerQueue.has("/123/00影/01国/Example")', context), true);
assert.match(fs.readFileSync(new URL('../src/avecove_namer/web/index.html', import.meta.url), 'utf8'), /namer-mark\.svg\?v=0\.7\.0/);
console.log('UI contracts passed: startup, TV/movie, dialogs, artwork, validation and notification-to-queue without cloud mutation');
const facts=vm.runInContext(`seriesInfoMarkup(${JSON.stringify(summaryData)})`,context);
assert.match(facts,/2 季/); assert.match(facts,/Season 01/); assert.match(facts,/10 集/); assert.match(facts,/8 集/);
assert.match(facts,/特别篇 S00/); assert.match(facts,/剧情 · 悬疑/); assert.match(facts,/US · TV-MA/);
const emptyFacts=vm.runInContext('seriesInfoMarkup({})',context);
assert.match(emptyFacts,/季数暂无资料/); assert.match(emptyFacts,/不代表全年龄/);
const countBeforeSummary=calls.length;
await vm.runInContext('loadSeriesSummary("1399", document.querySelector("#test-summary"))',context);
await vm.runInContext('loadSeriesSummary("1399", document.querySelector("#test-summary"))',context);
assert.equal(calls.length-countBeforeSummary,1,'Summary uses one shared request, not image/episode traversal');
assert.match(element('#test-summary').innerHTML,/TV-MA/);
assert.equal(element('#test-summary').getAttribute('aria-busy'),'false');
console.log('Series facts UI: season distribution, regional classifications, missing data and lightweight shared request passed.');
element('#tmdb-query').value='1399'; element('#tmdb-year').value='';
const beforeIdLookup=calls.length;
await element('#tmdb-form').listeners.submit({preventDefault(){}});
assert.equal(calls.slice(beforeIdLookup).some(call=>call.url.endsWith('/api/library/search')),false,'TMDb ID lookup must not implicitly enumerate cloud directories');

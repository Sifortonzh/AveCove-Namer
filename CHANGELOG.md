# Changelog / 更新记录

## 0.7.13 — 2026-11-07

- Add scene-aware liquid-glass chrome, with solid reduced-transparency and unsupported-browser fallbacks.

## 0.7.12 — 2026-11-07

- Separate the personal series shelf from search, fold identification history into TMDb, and simplify headings and service status.

## 0.7.11 — 2026-11-06

- Page/list-scoped browser-persistent pins and star-only personal ratings; keep timeline dates unchanged.

## 0.7.10 — 2026-11-06

- Replace shelf removal with explicit Archive/Restore actions and an archive filter; preserve all saved history.

## 0.7.9 — 2026-11-06

- Add independent Watch Later status to TV search, source details and shelf, with a persistent tag and filter.

## 0.7.8 — 2026-11-06

- Auto-hide full-series completions while retaining history. Add distinct vertical-season/horizontal-total progress bars and compact icon actions; move TMDb link into poster dialog.

## 0.7.7 — 2026-11-06

- Shelf deletion now hides the series only: keep all completion dates, stars, progress and timeline entries. Favorite/Watching can restore visibility.

## 0.7.6 — 2026-11-06

- Add manual numeric season/episode progress, TMDb detail links and confirmed shelf deletion. Preserve other series and cloud resources.

## 0.7.5 — 2026-11-03

- Record personal stars and completion dates separately for a whole series and each season, including specials. Edit or remove one record from the watch timeline without affecting the others.

## 0.7.4 — 2026-11-02

- Fix narrow search results: full-width metadata followed by two-column actions and a separate three-state row. Long source names wrap horizontally instead of shrinking to one character per line.
- Reflow episode/source actions and preserve 44px mobile targets; root-domain and legacy URLs share the same assets.
- 修复手机端标题、目录名竖排与按钮挤压；保留批量操作和全部观看记录。

## 0.7.3 — 2026-11-02

- Unify Favorite / Watching / Watched marks as 🌟 / 👀 / ✅ through one presentation helper across search, source and shelf cards; refresh selected labels consistently.
- Muted inactive marks, keyboard labels and larger mobile targets preserve independent flags, dates and progress without a data migration.
- 统一观看状态图标及未选中淡化，保留现有 LitePan 协作、原地址与所有观看记录；独立域名接入复用现有服务。

## 0.7.2 — 2026-11-01

- Add a shared TV facts contract for season totals, per-season episode counts, genres, series type and region-specific content classifications. Specials are separate; unknown data remains explicitly unknown.
- Add an on-demand lightweight summary in search results and a facts block in source/artwork details. Genre and classification badges also appear in the persistent series shelf.
- Fetch details and classifications together; no image/episode traversal is needed for summaries. Background refresh upgrades existing shelf metadata without changing saved flags or watch progress.
- Direct TV-ID queries do not implicitly search cloud folders; cloud lookup remains available through its explicit button.
- 新增季集概览、类型及地区分级，查询详情与收藏共用数据规则；保留收藏、在看、观看日期和进度。

## 0.7.1 — 2026-11-01

- Unify the browser title, workspace brand and health service identity as **AveCove Namer**.
- Remove Scout directory browsing and private alias mappings; old `@scout` paths fail explicitly instead of silently targeting normal folders.
- Retain existing LitePan/Vesta detection and the independent acquisition-notification inbox.
- 统一应用名称为 AveCove Namer；移除巡影专属接入，不影响正常媒体库及批量改名。

## 0.7.0 — 2026-11-01

- Add a local completed-acquisition notification CLI and durable inbox, with version-aware deduplication and stale-ack protection. No public write webhook or cloud mutation is introduced.
- Add notification review, one-click cross-drive queue selection and explicit dismissal to the workspace. Existing LitePan/Vesta detection remains unchanged.
- Refresh the Namer identity with a small-size-friendly clapperboard/N SVG.
- 新增收录通知交接通道及审核入口；保留预览、确认、日志边界，不自动执行改名或删除资源。

## 0.6.0 — 2026-10-31

- Consolidate the live LitePan-first workspace, cross-drive batch selection, source-language naming and collection/season improvements.
- Include persistent favorites, watching progress, watched dates, monthly timeline and source-language artwork.
- Keep legacy Emby/OpenList compatibility separate from the current three-page UI.
- Add version/revision/active-job health reporting, multi-version CI, package artifacts, an SSH publisher with backup + rollback, and bilingual documentation.
- 整合此前服务器上的功能更新；建立源码、GitHub 提交与线上版本可核对的发布流程。

Older changes remain available in Git history. This release does not delete cloud resources, reset stored collections, or install a cloud skill.

# Changelog / 更新记录

## 0.7.2 — 2026-11-01

- Add a shared TV facts contract for season totals, per-season episode counts, genres, series type and region-specific content classifications. Specials are separate; unknown data remains explicitly unknown.
- Add an on-demand lightweight summary in search results and a facts block in source/artwork details. Genre and classification badges also appear in the persistent series shelf.
- Fetch details and classifications together; no image/episode traversal is needed for summaries. Background refresh upgrades existing shelf metadata without changing saved flags or watch progress.
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

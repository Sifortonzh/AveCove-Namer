# Scout and Namer / 巡影与 Namer

Namer's TMDb workspace now has persistent Favorite and Watching lists. Saved
series remember the last successfully queried season and episode. Episode-title
copy actions produce `E11: Up All Right` rather than the bare title.

TMDb 查询页支持收藏、在看与上次季集位置，数据保存在服务器，跨浏览器可用。

## Acquisition workflow / 收录工作流

Scout acquires into configured `00影/Media Scout`, `00剧/Media Scout`, and
`00漫/Media Scout` directories. Open **Namer → 辅助目录与更新日志 → 巡影收录目录**, choose a drive and category, select
works across drives, generate previews, then explicitly confirm renaming.
Namer accesses these folders through the existing LitePan account connection;
it does not export Scout credentials or move its library roots. No automatic
cloud renaming or deletion is enabled.

巡影收录后，Namer 展开“巡影收录目录”，按网盘和类型读取作品，跨盘勾选、预览、
确认执行。沿用 LitePan 凭据，不复制巡影 token，也不移动目录。这里只按需读取，
并非后台全盘扫描或自动改名；不要在巡影正在转存同一作品时改名。

The private `/opt/docker/avecove-namer/scout-roots.json` maps cloud providers to
their actual Scout roots. Virtual `/115/@scout/00剧` is an alias, not a new cloud
directory. The original `/115/00剧/...` mapping remains unchanged.

## Configurable Scout directory names / 指定巡影目录

Scout supports these existing environment settings at **drive binding time**:

- `MEDIA_TRACK_LIBRARY_ROOT_DIR` (default `Mediary Scout`)
- `MEDIA_TRACK_LIBRARY_MOVIES_DIR` (default `Movies`)
- `MEDIA_TRACK_LIBRARY_TV_DIR` (default `TV`)
- `MEDIA_TRACK_LIBRARY_ANIME_DIR` (default `Anime`)

Already-bound drives use stored directory IDs. Changing these variables does
not migrate or redirect them. Choose target folders first; changes to existing
bindings need separate validated ID mapping and a backup, not a blind rebind.
The user selected existing `00影`, `00剧`, and `00漫` folders on 2026-10-27.
Their IDs are resolved through LitePan and applied transactionally only when
no Scout workflow is running. A private connected-storage backup is retained.
Existing cloud files are not moved. The original Scout container root is kept
for staging; movie/TV/anime acquisition targets point to the existing categories.

已按用户选择将后续收录目标设为各盘现有 00影、00剧、00漫；旧文件不自动搬动，
原收录根目录继续保留作暂存。地区分类目录不会被当成作品送去改名。

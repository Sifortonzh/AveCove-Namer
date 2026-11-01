# Cloud skill + Namer / 网盘 skill 协作约定

Version 0.7.0 implements a local completed-acquisition inbox. This is **not** an
installed cloud skill or enabled Hermes automation. Existing LitePan/Vesta
five-minute change detection remains unchanged.

## Implemented handoff / 已实现的交接

On the existing Namer server, after transfer finishes, send one JSON object on
stdin using the service account and the installed source interpreter:

```sh
printf '%s' '{"source":"hermes","path":"/123/00影/01国/Example (2026)","kind":"movie","event_id":"transfer-file-version-2","ready":true}' |
  PYTHONPATH=/opt/docker/avecove-namer/current/src python3 -m avecove_namer notify
```

The path is illustrative. Default private database:
`/opt/docker/avecove-namer/handoff-inbox.sqlite3` (0600, outside releases).
Custom installs must set **the same** `AVECOVE_HANDOFF_INBOX` absolute path for
the web service and notifying process, or use CLI `--inbox`. Reuse existing SSH
access; no public write webhook, new token or cloud permission is introduced.

`ready:true` is mandatory; never submit during transfer. Use a stable `event_id`
or content `signature` for retries; change it only for genuinely new content.
Duplicate path/kind/version notices stay acknowledged. Without a version, a
notice means only the first acquisition for that path. Broad category roots,
traversal and invalid types are rejected. Different paths remain separate
resolution/source copies. Capacity: 500 pending, 30 displayed per batch.

In **辅助目录与更新日志 → 收录通知**, add notices to the existing cross-drive
batch queue, preview, then explicitly confirm execution. Ignoring acknowledges
only the displayed version, never a newer notification or any cloud file.

- `GET /api/namer/handoff`: pending notices; no cloud calls.
- `POST /api/namer/handoff/ack`: `{"items":[{"id":"...","signature":"..."}]}`.
- There is intentionally no HTTP receive endpoint; use the local CLI above.

收录工具完成转存后才能提交；页面可一键加入批量队列，但仍需预览确认。
通知、忽略通知都不会触发扫描、自动改名或删除。未安装或配置外部 skill/Hermes。

## Ownership / 分工

- **Cloud skill:** search/acquire resources and return a provider + exact work directory + optional TMDb ID. Use the already-authorized cloud connection; do not export tokens.
- **LitePan:** one storage adapter for directory listing and file operations.
- **Namer:** verify identity, generate a plan, expose ambiguity/collisions, then apply only after explicit user confirmation; retain rollback journals.
- **Scout/Vesta:** acquisition/change signals, not authorization to rename or delete.

## Minimal handoff / 最小交接

```json
{"provider":"123","path":"/123/00影/01国/Example (2026)","kind":"movie","tmdb_id":12345}
```

The example is illustrative, not a real resource. Existing endpoints are `POST /api/namer/identify` (inspect its current payload before integration), `/api/namer/plan`, and the explicit review/apply flow. Do not treat the illustrative object as a drop-in request schema.

## Boundaries / 边界

Only inspect selected works; no full-drive enumeration by default. Prefer stable file IDs to modified timestamps so a rename does not look like new content. Never rename a directory while Scout is writing it. Conflicts, unknown matches, missing metadata and uncertain move results stop for review. Different resolutions remain separate resources. A skill must not silently call apply or delete.

## Next step / 下一步

选定具体网盘 skill / Hermes 的来源和已有授权后，将它的“转存完成”回调接到上面的本机命令即可。复用现有识别、预览、确认与日志，不需要第二套执行引擎。本轮不安装外部代理、不扩张账号权限。

# Cloud skill + Namer / 网盘 skill 协作约定

This is a design contract, not an installed skill or an enabled cloud automation.

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

先选择具体网盘 skill 的来源与授权方式，再做一个“目录交接适配器”，复用 Namer 的预览、确认、日志接口。无需再造第二套识别与执行引擎；本轮不安装 skill、不扩张账号权限。

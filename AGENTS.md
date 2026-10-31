# Working on AveCove Namer

- Treat LitePan as the current cloud adapter. Preserve legacy compatibility unless removal is explicitly requested.
- Never rename, delete or enumerate cloud resources just to test a code change. Use isolated fixtures and read-only checks.
- Preserve user shelves, rate state, credentials and work journals outside release directories. Never commit them.
- Keep `README.md` and `README.zh-CN.md` linked and current; update `CHANGELOG.md` for user-visible releases. Keep package and `__version__` values consistent.
- Run `PYTHONPATH=src python3 -m unittest discover -s tests -q` and `node tests/test_web_ui.mjs` before publishing. CI must also pass.
- Commit reviewed changes and use `python3 scripts/publish.py --host <existing SSH alias>` to publish and deploy. Do not patch files in an existing production release directory.
- Deployment must stop if jobs are active. Keep backups and validate the exact public `/api/health` revision against Git HEAD. Report GitHub commit/tag, running revision, service health and public access separately.
- Do not create new deployment credentials or automatic production access without user approval. Use the already-configured SSH workflow.

# Namer 2026-11-01 update

- Scope: Notes' Namer skill/Hermes handoff workflow and new icon. Scout service/cloud-folder deletion is out of this update's scope.
- Implemented: local-only stdin notification CLI, durable SQLite inbox outside releases, version-aware duplicate/stale-ack protection, UI review/add-to-batch/dismiss, new SVG mark.
- Explicitly NOT enabled: external skill/Hermes installation, public write webhook, cloud scans for tests, automatic rename/delete.
- Verified locally: 171 Python tests and UI contracts, including notification-to-queue without cloud calls. Existing public revision before release: 2339944, activeJobs 0.
- Deployment must use the existing SSH publisher to ali-m2, refusing active jobs. Check its output and exact public revision; do not infer deployment from this source note. Runtime data and credentials remain outside releases.

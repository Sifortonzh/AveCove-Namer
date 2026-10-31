# Releases / 发布与回滚

## Contract / 交付标准

1. Review changes and run Python + UI tests. Commit only intended source.
2. Push via SSH to GitHub. CI tests Python 3.10, 3.11, 3.13 and builds distributions.
3. Archive that exact commit; no live secrets or work directories are copied.
4. On the server, refuse active jobs, retain a source/data snapshot, and create a new immutable release directory.
5. Switch `current` atomically, restart the existing service, verify local health and revision. Restore the old symlink automatically on local health failure.
6. Verify public health returns the same revision. A public mismatch requires investigation; it is never reported as successful deployment.

发布前审核并提交，之后用 `scripts/publish.py` 一键测试、push、备份、切换和核验。GitHub CI 与本地测试分别记录；不要把“push 成功”当成“公网已更新”。

## Existing server requirements / 现有服务器要求

- Existing SSH host alias with permission to operate the existing service.
- Python 3.10+, `systemctl`, and a running `avecove-namer-web` service.
- `/opt/docker/avecove-namer/current` must be a release symlink.
- First installation must already provide the version-aware health endpoint. Old deployments without `activeJobs` fail closed; bootstrap only after verifying no jobs are active.
- Secret files, `series-shelf.json`, rate state, scan state, and work journals remain outside `current`.
- The publisher refuses dirty worktrees and existing target releases. It does not overwrite release directories or delete backups.

No SSH key is generated; no key is uploaded to GitHub. GitHub Actions does **not** access production. Unattended deployment would require a separately approved credential/environment setup.

## Rollback / 回滚

The deploy output reports `previous` and `backup`. Validate those exact paths before restoring the previous symlink and restarting the service. Do not reset user data or cloud folders. Local startup failure rolls back automatically; public-proxy failure is reported for investigation without modifying user data.

There is intentionally no backup pruning in this release. Review disk usage and retention before introducing an automatic cleanup policy.

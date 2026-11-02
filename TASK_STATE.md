# Namer 2026-11-01 update

## 2026-11-02: 0.7.3 verified, 0.7.4 mobile follow-up

- Shared status renderer: favorite 🌟, watching 👀, watched ✅; muted inactive marks, accessible labels, mobile targets; no shelf migration or cloud mutations.
- Existing online revision verified as 4757f4e, activeJobs=0 at start. Root-domain routing already supported by app; namer.avecove.com is approved, should point to ali-m2 47.120.61.12, same backend and LitePan configuration. DNS/certificate/public access must be verified live.
- 0.7.3 deployed as 7c7e35e via guarded publisher, public health matches; 175 tests and UI contracts pass. GitHub Actions run 41389366048 succeeded. Backup `/opt/docker/avecove-namer/backups/release-20261102-123558`.
- Independent namer.avecove.com Nginx entry created; same loopback 8787, no extra Basic Auth, original my/media-tools unchanged. Certificate obtained to 2027-01-31 after existing scheduled renewal released its lock; Nginx config tested/reloaded. Some DNS resolvers still return former wildcard IPs: verify pinned origin and normal public separately.
- 0.7.4 fixes supplied mobile screenshot: result info gets a full row, actions use two columns, status group its own three-column row, directory names wrap at full width; episode/source actions reflow. Local 175 tests and UI contracts pass; pending exact commit deploy and browser geometry checks. No runtime data/cloud changes.

## Current delivery: 0.7.2 TV facts

- Scope: season count/per-season episodes, genres, TMDb series type and regional content ratings. Shared by search summaries, artwork/source views and saved-series cards.
- Lightweight `POST /api/tmdb/summary` fetches TV details + appended ratings once; no cloud/image/episode traversal. First search match auto-loads, other matches load on expansion.
- Specials S00 separate from regular-season totals; unknown counts/classifications are explicit. Existing shelf metadata upgrades in the background without resetting flags/progress.
- Verified locally: 175 Python tests and JS UI contracts. Publish using existing ali-m2 SSH publisher; active jobs block deployment, and public revision must match HEAD. Deployment status must be checked live, not inferred from this file.

## Prior delivery / cleanup history

- Current scope: 0.7.1 names the app AveCove Namer and removes Scout integration. User explicitly approved deleting the nonempty 115 Media Scout folder, including its one work.
- Implemented: local-only stdin notification CLI, durable SQLite inbox outside releases, version-aware duplicate/stale-ack protection, UI review/add-to-batch/dismiss, new SVG mark.
- Explicitly NOT enabled: external skill/Hermes installation, public write webhook, cloud scans for tests, automatic rename/delete.
- Verified locally: 171 Python tests and UI contracts, including notification-to-queue without cloud calls. Existing public revision before release: 2339944, activeJobs 0.
- Deployment must use the existing SSH publisher to ali-m2, refusing active jobs. Check its output and exact public revision; do not infer deployment from this source note. Runtime data and credentials remain outside releases.
- Scout UI and alias resolution removed; legacy @scout requests now fail explicitly. LitePan/Vesta and the notification inbox remain independent.
- Verified Scout removal on ali-m2: both application/database containers, two dedicated volumes, own network and tagged images, source/runtime data, private maps, quek Nginx vhost and domain certificate removed. LitePan still running. Nine per-category cloud Media Scout folders deleted and force-refreshed; only the approved 115 TV folder contained a work. Legacy account-root staging folders are checked separately.

# Namer 2026-11-01 update

## 2026-11-07: 0.7.12 navigation

- Notes requested separate 风花雪月 shelf, TMDb-only query workspace with collapsed history, and compact health status by brand. Old detective URL opens the same fold; saved data and existing APIs are unchanged. GuangYa scan scheduling is handled independently by the LitePan bridge.

## 2026-11-06: 0.7.11 pins and stars

- Page/list namespaces in browser localStorage, safe memory fallback. Pin folders within loaded category; TMDb results (kind+ID), saved filters and identification lists independently. Keep index-based action identities attached when DOM reorders. Timeline pinned group retains dates.
- Stars shown as exact glyph count; rating picker uses stars, with accessible label. No API/data migration or cloud changes.

## 2026-11-06: 0.7.10 archive

- Explicit archive/restore APIs reuse non-destructive shelfHidden. Archive filter includes previously manually hidden entries; restore keeps all saved fields and overrides completion auto-hide. Legacy delete API remains non-destructive for old clients.

## 2026-11-06: 0.7.9 Watch Later

- Independent queued flag retained even without favorite/watching; search button, shelf tag and filter. Existing shelf is TV-specific; this release does not introduce mixed movie IDs or cloud acquisition. Re-queue restores hidden entries; mobile search actions two columns.

## 2026-11-06: 0.7.8 completion and progress UI

- Full completions auto-hide unless watching/explicitly restored; season completions keep displaying. Legacy whole-completion rows auto-hide at render time, no data migration.
- Purple season bar on right; blue cumulative bar below. TMDb season episode counts exclude specials from full-series position; missing earlier-season data stays unknown. Icon actions, TMDb link moved into episode dialog. Preserve all history.

## 2026-11-06: 0.7.7 hide, not erase

- User correction: delete only hides My Series; timeline/dates/stars/progress remain. Hidden rows retained in storage and timeline, excluded from normal shelf filters/counts. Marking Favorite/Watching true restores display. No destructive migration.

## 2026-11-06: 0.7.6 shelf management

- Latest Notes Namer section: delete shelf series, manually enter S/E progress, link TMDb details. Add confirmed delete API (shelf only), strict progress validation and responsive numeric form. Preserve watched dates/stars and unrelated rows.
- Publish with existing guarded publisher; do not interrupt active jobs or mutate cloud folders to test.

## 2026-11-03: 0.7.5 watch timeline

- Existing whole-series dates retain their storage shape; season records are optional and keyed by TMDb season number. Personal stars are separate from TMDb ratings. Timeline sorts by completion date.
- Test Python storage and isolated UI before publishing with `scripts/publish.py --host ali-m2`; verify exact public revision and CI before handoff.

## 2026-11-02: 0.7.3 verified, 0.7.4 mobile follow-up

- Shared status renderer: favorite 🌟, watching 👀, watched ✅; muted inactive marks, accessible labels, mobile targets; no shelf migration or cloud mutations.
- Existing online revision verified as 4757f4e, activeJobs=0 at start. Root-domain routing already supported by app; namer.avecove.com is approved, should point to ali-m2 47.120.61.12, same backend and LitePan configuration. DNS/certificate/public access must be verified live.
- 0.7.3 deployed as 7c7e35e via guarded publisher, public health matches; 175 tests and UI contracts pass. GitHub Actions run 41389366048 succeeded. Backup `/opt/docker/avecove-namer/backups/release-20261102-123558`.
- Independent namer.avecove.com Nginx entry created; same loopback 8787, no extra Basic Auth, original my/media-tools unchanged. Certificate obtained to 2027-01-31 after existing scheduled renewal released its lock; Nginx config tested/reloaded. Some DNS resolvers still return former wildcard IPs: verify pinned origin and normal public separately.
- 0.7.4 fixes supplied mobile screenshot: result info gets a full row, actions use two columns, status group its own three-column row, directory names wrap at full width; episode/source actions reflow. First deployment 3b32e2f: 175 tests + UI contracts and Actions 41389966379 passed, public old endpoint matches. Backup `/opt/docker/avecove-namer/backups/release-20261102-124239`.
- Safari responsive preview visually verified production UI with isolated fixture at 360x640 (long name/action grid), 390x844 (numeric episode dialog), 768x1024 (search result). `tests/mobile_preview.py` is loopback-only and rejects mutations, never contacts cloud services. Live source view also read at 390px with public TV ID 2691; no cloud enumeration. Root entry's return link goes to Banwugan, not a self-loop.
- Independent HTTPS certificate valid and remote public GET returns exact 0.7.4 revision; server Nginx configuration valid. Mac default resolver/proxy sometimes still points to former wildcard IPs and yields certificate-host mismatch; do not bypass that warning. Use old my/media-tools until local cache expires. This is not a LitePan connection change.

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

# AveCove Namer

Reviewable cloud media naming, original-language TMDb metadata, and a personal series shelf.

[简体中文](README.zh-CN.md) · [Release workflow](docs/releases.md) · [Changelog](CHANGELOG.md)

## Current release: 0.7.5

- **Watch timeline:** select ✅ Watched to record a whole series or a TMDb season, set its completion date and optionally give it 1–5 personal stars. Existing records remain intact; entries sort by completion date.

- **Mobile query layout:** titles and directory names use a full-width information row; actions wrap into a two-column grid, with an independent three-state row. Episode/source actions also reflow without squeezing text vertically.

- **Consistent watch marks:** 🌟 Favorite, 👀 Watching and ✅ Watched share one state renderer across search, details and saved cards. Inactive marks are muted; text, accessible labels and independent persisted flags remain available. Root-domain and legacy `/media-tools/` access reuse the same backend and LitePan settings.

- **TV facts:** query cards and source details show season/episode distributions, genres, TMDb series type and region-labeled content ratings. Specials stay separate; missing data is never presented as zero or all-ages. The first search match loads one summary request; other matches load on expansion, without image/episode traversal. Shelf cards reuse the same facts.

- **Completed-acquisition inbox:** a local `notify` command for skill/Hermes handoffs, durable version-aware deduplication, and one-click batch selection. No external agent or unattended rename is enabled.
- **Namer identity:** a compact clapperboard/N mark for the workspace and browser icon.

- **LitePan first:** browse only selected folders, select works across drives, preview a batch, then explicitly confirm changes. No full-drive scan is required.
- **Original-language naming:** consistent parent and child names, `{tmdb=...}` IDs, season names and source metadata. Movies, series, and movie collections are supported.
- **Review before mutation:** ambiguity and collisions stay reviewable; stale-source checks and rollback journals protect execution. Resolution variants are not automatically deleted.
- **TMDb workspace:** numeric S/E inputs, source-language episode details, image selection by season, and copyable episode titles.
- **My series:** Favorite, Watching, last queried episode, Watched dates, and a monthly timeline. Posters prefer original-language artwork, then textless artwork.
- **Focused UI:** a prominent cross-drive batch queue; acquisition notifications and LitePan/Vesta updates live in a collapsed auxiliary panel. Mobile layout and dark/light scenes are included. Scout integration has been retired.
- **Release visibility:** `/api/health` reports the software version, deployed Git revision, and active job count.

OpenList and Emby compatibility code remains for older installations. The current web navigation has **Namer / TMDb / Records**, not an Emby refresh page. Naming does not automatically trigger Emby refreshes. Optional historical integrations must be configured separately.

## Run and test

Python 3.10+; Node.js 22 is used for UI contract tests, not as an application server.

```sh
python3 -m pip install -e .
python3 -m unittest discover -s tests -v
node tests/test_web_ui.mjs
avecove-namer --help
avecove-namer-web --host 127.0.0.1 --port 8787
```

Credentials are read from protected server-side files. Configure the `AVECOVE_*` environment variables in `webapp.Settings.from_env`; start from the service examples under `deploy/`. Do not commit runtime data, cloud roots, account passwords, API keys, or execution journals. Keep the app behind your existing HTTPS/access arrangement; the publisher does not add an authentication layer.

## Rename pacing

LitePan operations remain sequential per provider. 115 starts at 500 ms, reduces by 50 ms after 50 successes, and has a 300 ms floor; persisted backoff can make its actual interval higher. Other providers default to **0 ms additional delay**, not parallel/unlimited calls. Rate-limit or transient provider errors increase the interval; ambiguous timed-out moves are not blindly retried. Persisted state lives outside the release directory.

## Publish

Commit reviewed changes, then run:

```sh
python3 scripts/publish.py --host YOUR_EXISTING_SSH_ALIAS --url https://YOUR_HOST/media-tools
```

The publisher runs local tests, pushes the exact commit through Git SSH, archives only committed files, backs up the previous server source, switches releases, and verifies the **same revision** locally and publicly. Failed local health checks restore the old release. Running jobs block deployment. GitHub Actions independently tests Python 3.10/3.11/3.13, UI contracts and package builds.

This is an operator-initiated publisher using existing SSH access, **not unattended push-to-production**. No new GitHub deployment credentials are created or copied. See [release details](docs/releases.md).

## Cloud skill integration

Use an acquisition/browse skill to find a specific directory, then pass its provider, path and TMDb identity to Namer's preview stage. Keep execution inside Namer's explicit confirmation and journal flow. Do not duplicate credentials or let a skill bypass the plan. See [integration contract](docs/cloud-skill.md).

MIT license. TMDb artwork and metadata belong to their respective owners; this project is not endorsed by TMDb or cloud providers.

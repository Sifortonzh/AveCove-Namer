# AveCove Namer

Safe, reviewable media naming for OpenList, Emby, Infuse, and SenPlayer.

[简体中文](README.zh-CN.md)

> Status: v0.2.2 alpha. Start with a small canary folder and review every plan before execution.

AveCove Namer is an independent, clean-room media naming tool built for cloud-drive libraries. Its default TV rule deliberately prioritizes reliable library matching over episode-title decoration:

```text
Modern.Family.2009.S01E01.1080p.BluRay.x265.DTS.mkv
Modern.Family.2009.S01E01.1080p.BluRay.x265.DTS.zh-CN.sup
```

The series year is included, episode titles are omitted, and useful release metadata is retained.

## What v0.2 includes

- Local filesystem and OpenList backends.
- Year-aware TV and movie naming.
- Origin-aware movie and series folder naming with Emby TMDB ID tags.
- Automatic selected-work-folder naming whenever a verified TMDB ID is supplied.
- Recursive media naming without renaming or moving existing season folders such as `Season 01`, `Season01`, `S01`, or `第一季`.
- Technical/release metadata preservation.
- Sidecar subtitle pairing with a full video-stem match.
- Read-only JSON and optional CSV plans.
- Existing-target and duplicate-target conflict detection.
- Exact root/count confirmation before mutations.
- Stale-source checks and append-only rollback journals.
- Read-only TMDB title search as a verification aid.
- No database, background scheduler, or resident service.

TMDB search accepts either an API Read Access Token or a v3 API Key stored in a `0600` credential file.

## Quick start

Python 3.10 or newer is required.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
avecove-namer --help
```

### Local canary

Generate a plan without changing files:

```bash
avecove-namer plan \
  --backend local \
  --path "/media/Modern Family (2009)" \
  --output work/modern-family.json \
  --csv work/modern-family.csv
```

Preview it again:

```bash
avecove-namer apply \
  --backend local \
  --plan work/modern-family.json \
  --journal work/modern-family.rollback.jsonl
```

Only after reviewing the plan, execute it with the exact root and operation count printed by the preview:

```bash
avecove-namer apply \
  --backend local \
  --plan work/modern-family.json \
  --journal work/modern-family.rollback.jsonl \
  --execute \
  --confirm-root "/media/Modern Family (2009)" \
  --confirm-count 24
```

Preview and execute rollback:

```bash
avecove-namer rollback --backend local \
  --journal work/modern-family.rollback.jsonl

avecove-namer rollback --backend local \
  --journal work/modern-family.rollback.jsonl --execute
```

### OpenList

Create a protected token file. The password is entered interactively and is never written to command history:

```bash
avecove-namer login \
  --openlist-url "https://openlist.example.com" \
  --username admin \
  --token-file "$HOME/.config/avecove-namer/openlist.token"
```

Check connectivity and generate a read-only plan:

```bash
avecove-namer check \
  --backend openlist \
  --openlist-url "https://openlist.example.com" \
  --openlist-token-file "$HOME/.config/avecove-namer/openlist.token" \
  --path "/115/TV/Modern Family (2009)"

avecove-namer plan \
  --backend openlist \
  --openlist-url "https://openlist.example.com" \
  --openlist-token-file "$HOME/.config/avecove-namer/openlist.token" \
  --path "/115/TV/Modern Family (2009)" \
  --output work/modern-family.json \
  --csv work/modern-family.csv
```

OpenList execution uses the same preview and exact-confirmation flow as the local backend. Token files must have `0600` permissions. Rename requests use a conservative three-second cooldown by default to reduce cloud-provider rate-limit risk, including on 115.

Resolve TMDB metadata and select the title language automatically. Supplying a verified TMDB ID includes the selected work folder in the reviewed plan by default:

```bash
avecove-namer plan \
  --backend openlist \
  --openlist-url "https://openlist.example.com" \
  --openlist-token-file "$HOME/.config/avecove-namer/openlist.token" \
  --path "/Baidu/Movies/Kill Bill 1" \
  --tmdb-id 24 \
  --tmdb-token-file "$HOME/.config/avecove-namer/tmdb.token" \
  --media-kind movie \
  --title-style auto \
  --output work/kill-bill.json
```

Use `--no-rename-root-folder` only when the selected folder name must remain unchanged.

### Guided server workflow

After installing `deploy/avecove-namer-run`, one command performs a read-only preview, explicit confirmation, cloud rename, narrow STRM sync, affected Emby library refresh, and final verification. By default it syncs STRM files only and preserves existing image and subtitle sidecars, preventing large sidecar downloads from stalling a small server:

```bash
avecove-namer-run "/GuangYa/00剧/01美/Game of Thrones (2011)" 1399 tv
```

The optional final argument selects `auto` (default), `english`, `chinese`, `original`, or `bilingual` title styling. The script changes files only after the operator types uppercase `YES`, and every run keeps a separate rollback journal.

## Naming policy

The default episode pattern is:

```text
{Series.Title}.{Year}.S{season:02}E{episode:02}.{Technical.Metadata}.{ext}
```

The subtitle pattern is:

```text
{Complete.Video.Stem}.{language}.{subtitle-ext}
```

Bilingual markers such as `chs&eng` and `cht&eng` are preserved. Existing season-folder names are deliberately left unchanged; the selected work folder and files inside it are the normalization targets.

If the series year cannot be inferred from the folder or filename, the item is skipped. A verified title and year can be supplied with `--title` and `--year`. See [Naming rules](docs/naming-rules.md).

## Safety model

Planning is read-only. Execution is refused when the plan has conflicts, the target already exists, the source changed or disappeared, the requested root is broad, or the supplied root/count confirmation differs. Every completed rename is recorded immediately so a partially completed run can be reversed.

Cloud providers may still impose rename limits or temporary API failures. Always begin with one small folder.

## Docker

The image is a run-on-demand CLI rather than a resident service:

```bash
docker build -t avecove-namer:dev .
docker run --rm avecove-namer:dev --help
```

The included [Compose example](compose.example.yml) caps the container at 0.25 CPU and 192 MB of memory.

## Roadmap

- Incremental state and change-only scanning.
- Targeted Emby refresh after successful operations.
- Multi-library rules and reusable naming profiles.
- Optional review UI and scheduled jobs.
- Broader canary validation across supported cloud providers.

## Development

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

## License

[MIT](LICENSE)

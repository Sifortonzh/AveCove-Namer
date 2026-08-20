# Naming rules

[简体中文](naming-rules.zh-CN.md)

## TV episodes

Default:

```text
Series.Title.2020.S01E01.1080p.WEB-DL.x265.DDP5.1.mkv
```

- The series title and first-air year form the stable identity.
- `SxxEyy` is required for episode matching.
- Episode titles are intentionally omitted in v0.1.
- Recognized technical/release tokens are retained in their original order.
- Unrecognized decorative text before the technical tail is dropped.

The year is resolved in this order: explicit `--year`, a parent folder such as `Series Name (2020)`, then the filename. Missing years are skipped.

## Movies

Default:

```text
Movie.Title.2024.2160p.BluRay.REMUX.DV.HDR.HEVC.TrueHD.Atmos.mkv
```

The title, release year, and recognized technical/release tail are retained.

## Sidecar subtitles

Subtitle names match the complete resulting video stem, followed by a language tag and subtitle extension:

```text
Series.Title.2020.S01E01.1080p.WEB-DL.x265.DDP5.1.zh-CN.sup
```

Existing common language tags are retained. If no language is detected, `zh-CN` is used by default and can be changed with `--subtitle-language-default`.

## Deliberate safety limits

- No guessed year when a verified value is unavailable.
- No automatic episode-title lookup in v0.1.
- No overwriting occupied target paths.
- No execution from a plan containing conflicts.

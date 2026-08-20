from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath

from .models import ParsedMedia


VIDEO_EXTENSIONS = {
    ".mkv", ".mp4", ".m4v", ".mov", ".avi", ".ts", ".m2ts", ".wmv", ".flv", ".webm"
}
SUBTITLE_EXTENSIONS = {".srt", ".ass", ".ssa", ".sup", ".sub", ".vtt", ".smi", ".idx"}
YEAR_RE = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")
EPISODE_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])S(?P<season>\d{1,2})[ ._-]*E(?P<episode>\d{1,3})(?!\d)"
)
ALT_EPISODE_RE = re.compile(r"(?i)(?<!\d)(?P<season>\d{1,2})x(?P<episode>\d{1,3})(?!\d)")
FOLDER_YEAR_RE = re.compile(r"^(?P<title>.+?)\s*[\[(](?P<year>19\d{2}|20\d{2})[\])]$")
TMDB_SUFFIX_RE = re.compile(r"\s*\{tmdb-\d+\}\s*$", re.IGNORECASE)

TECHNICAL_START_RE = re.compile(
    r"(?i)(?:^|[ ._-])(?:"
    r"2160p|1080p|1080i|720p|576p|480p|4k|uhd|bluray|blu-ray|bdrip|brrip|"
    r"remux|web-dl|webdl|webrip|hdtv|dvdrip|hdr10\+?|hdr|dolby[ ._-]*vision|dv|"
    r"x26[45]|h\.?26[45]|hevc|av1|avc|dts(?:-hd)?|truehd|atmos|ddp?\d(?:\.\d)?|"
    r"aac|flac|opus|10bit|8bit"
    r")(?:$|[ ._-])"
)

CANONICAL_TECH = {
    "4k": "2160p",
    "uhd": "UHD",
    "bluray": "BluRay",
    "blu-ray": "BluRay",
    "webdl": "WEB-DL",
    "web-dl": "WEB-DL",
    "webrip": "WEBRip",
    "remux": "REMUX",
    "hdr": "HDR",
    "hdr10": "HDR10",
    "hdr10+": "HDR10+",
    "dv": "DV",
    "dolbyvision": "DV",
    "dolby-vision": "DV",
    "hevc": "HEVC",
    "avc": "AVC",
    "dts-hd": "DTS-HD",
    "truehd": "TrueHD",
    "atmos": "Atmos",
}


@dataclass(frozen=True)
class NamingPolicy:
    include_series_year: bool = True
    include_episode_title: bool = False
    preserve_technical_tail: bool = True
    subtitle_language_default: str = "zh-CN"

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def extension_of(name: str) -> str:
    suffix = PurePosixPath(name).suffix
    return suffix.lower()


def clean_component(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    value = TMDB_SUFFIX_RE.sub("", value)
    value = re.sub(r"[\[\]{}（）()]", " ", value)
    value = re.sub(r"[\\/:|<>*?\"']", ".", value)
    value = re.sub(r"[\s._-]+", ".", value)
    return value.strip(".")


def display_title(value: str) -> str:
    value = TMDB_SUFFIX_RE.sub("", unicodedata.normalize("NFKC", value)).strip()
    match = FOLDER_YEAR_RE.match(value)
    if match:
        value = match.group("title")
    return re.sub(r"[._]+", " ", value).strip()


def canonicalize_tail(value: str) -> tuple[str, ...]:
    value = re.sub(r"^[ ._-]+", "", value)
    if not value:
        return ()
    tokens = [token for token in re.split(r"[ ._]+", value) if token]
    result: list[str] = []
    for token in tokens:
        key = token.casefold().replace(" ", "")
        result.append(CANONICAL_TECH.get(key, token))
    return tuple(result)


def technical_tail(value: str) -> tuple[str, ...]:
    match = TECHNICAL_START_RE.search(value)
    if not match:
        return ()
    return canonicalize_tail(value[match.start():])


def parse_media_name(name: str) -> ParsedMedia:
    extension = extension_of(name)
    stem = name[: -len(extension)] if extension else name
    episode_match = EPISODE_RE.search(stem) or ALT_EPISODE_RE.search(stem)
    if episode_match:
        prefix = stem[:episode_match.start()]
        year_matches = list(YEAR_RE.finditer(prefix))
        year = int(year_matches[-1].group(1)) if year_matches else None
        if year_matches:
            prefix = prefix[:year_matches[-1].start()]
        tail = technical_tail(stem[episode_match.end():])
        return ParsedMedia(
            source_name=name,
            kind="episode",
            extension=extension,
            title=display_title(prefix),
            year=year,
            season=int(episode_match.group("season")),
            episode=int(episode_match.group("episode")),
            technical_tail=tail,
        )

    year_matches = list(YEAR_RE.finditer(stem))
    year = int(year_matches[-1].group(1)) if year_matches else None
    if year_matches:
        title_part = stem[:year_matches[-1].start()]
        tail = technical_tail(stem[year_matches[-1].end():])
    else:
        title_part = stem
        tail = technical_tail(stem)
        if tail:
            marker = TECHNICAL_START_RE.search(stem)
            title_part = stem[:marker.start()] if marker else stem
    return ParsedMedia(
        source_name=name,
        kind="movie" if extension in VIDEO_EXTENSIONS else "other",
        extension=extension,
        title=display_title(title_part),
        year=year,
        technical_tail=tail,
    )


def infer_context(path: str) -> tuple[str | None, int | None]:
    parts = PurePosixPath(path).parts[:-1]
    for part in reversed(parts):
        if re.fullmatch(r"(?i)Season[ ._-]*\d{1,2}", part):
            continue
        cleaned = TMDB_SUFFIX_RE.sub("", part).strip()
        match = FOLDER_YEAR_RE.match(cleaned)
        if match:
            return display_title(match.group("title")), int(match.group("year"))
    return None, None


def build_video_name(
    parsed: ParsedMedia,
    policy: NamingPolicy,
    title: str | None = None,
    year: int | None = None,
) -> str:
    resolved_title = clean_component(title or parsed.title or "Untitled")
    resolved_year = year or parsed.year
    parts = [resolved_title]

    if parsed.kind == "episode":
        if policy.include_series_year and resolved_year:
            parts.append(str(resolved_year))
        parts.append(f"S{parsed.season:02d}E{parsed.episode:02d}")
    elif resolved_year:
        parts.append(str(resolved_year))

    if policy.preserve_technical_tail:
        parts.extend(parsed.technical_tail)
    return ".".join(filter(None, parts)) + parsed.extension


def subtitle_language_suffix(name: str, default: str = "zh-CN") -> str:
    stem = name[: -len(extension_of(name))]
    patterns = (
        (r"(?i)(?:^|[._-])(zh[-_.]?(?:cn|hans)|chs|sc)(?:$|[._-])", "zh-CN"),
        (r"(?i)(?:^|[._-])(zh[-_.]?(?:tw|hant)|cht|tc)(?:$|[._-])", "zh-TW"),
        (r"(?i)(?:^|[._-])(en|eng)(?:$|[._-])", "en"),
        (r"(?i)(?:^|[._-])(ja|jpn)(?:$|[._-])", "ja"),
        (r"(?i)(?:^|[._-])(ko|kor)(?:$|[._-])", "ko"),
    )
    for pattern, language in patterns:
        if re.search(pattern, stem):
            return language
    return default


def build_subtitle_name(video_target: str, subtitle_name: str, policy: NamingPolicy) -> str:
    video_extension = extension_of(video_target)
    video_stem = video_target[: -len(video_extension)]
    subtitle_extension = extension_of(subtitle_name)
    language = subtitle_language_suffix(subtitle_name, policy.subtitle_language_default)
    return f"{video_stem}.{language}{subtitle_extension}"


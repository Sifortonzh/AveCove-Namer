from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path, PurePosixPath
import random
import re
import secrets
import shutil
import sqlite3
import subprocess
import threading
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request
from urllib.parse import parse_qs, urlparse

from .backends import BackendError, OpenListBackend
from .detective import _find_movie_tmdb_id, _find_tv_tmdb_id, fingerprint, infer_search_terms
from .executor import ExecutionError, execute_plan
from .models import RenameOperation, RenamePlan
from .naming import NamingPolicy
from .planner import make_plan, read_plan, write_plan
from .tmdb import TMDBClient, TMDBError


MAX_BODY = 32 * 1024
ALLOWED_ROOTS = ("/115/", "/Baidu/", "/Quark/", "/123/", "/GuangYa/")
BROAD_NAMER_PATHS = {
    *{
        f"/{provider}/00剧/{category}"
        for provider in ("115", "GuangYa", "123", "Baidu")
        for category in ("01美", "01中", "01韩", "01日", "01台", "01英")
    },
    *{
        f"/{provider}/00影/{category}"
        for provider in ("115", "GuangYa", "123", "Baidu")
        for category in ("01国", "01外")
    },
    *{
        f"/{provider}/00漫/{category}"
        for provider in ("115", "GuangYa", "123", "Baidu")
        for category in ("01中", "01日", "01美")
    },
}

CLOUD_LIBRARY_ROOTS = {
    "tv": (
        "/115/00漫/01中", "/115/00漫/01日", "/115/00漫/01美",
        "/123/00漫/01中",
        "/GuangYa/00漫/01中", "/GuangYa/00漫/01日",
        "/Baidu/00漫/01中", "/Baidu/00漫/01日",
        "/115/00剧/01中", "/115/00剧/01日", "/115/00剧/01韩", "/115/00剧/01美", "/115/00剧/01台", "/115/00剧/01英", "/115/00剧/02其他",
        "/123/00剧/01中", "/123/00剧/01日", "/123/00剧/01韩", "/123/00剧/01美", "/123/00剧/01台", "/123/00剧/01英",
        "/GuangYa/00剧/01中", "/GuangYa/00剧/01日", "/GuangYa/00剧/01韩", "/GuangYa/00剧/01美", "/GuangYa/00剧/01台", "/GuangYa/00剧/01英",
        "/Baidu/00剧/01中", "/Baidu/00剧/01日", "/Baidu/00剧/01韩", "/Baidu/00剧/01美", "/Baidu/00剧/01台", "/Baidu/00剧/01英",
        "/Quark/00剧/01中", "/Quark/00剧/01日", "/Quark/00剧/01韩", "/Quark/00剧/01美", "/Quark/00剧/01台", "/Quark/00剧/01英",
    ),
    "movie": (
        "/115/00影/01国", "/115/00影/01外", "/123/00影/01国", "/123/00影/01外",
        "/GuangYa/00影/01国", "/GuangYa/00影/01外", "/Baidu/00影/01国", "/Baidu/00影/01外",
        "/Quark/00影/01国", "/Quark/00影/01外", "/Quark/00影",
    ),
}
MOVIE_GROUP_DIRS = {"总其他"}

AUTOMATION_TIMERS = (
    "avecove-namer-detective-115.timer",
    "avecove-namer-detective-123.timer",
    "avecove-namer-detective-baidu.timer",
    "avecove-namer-detective-guangya.timer",
)

STRM_EPISODE_RE = re.compile(r"(?i)^(?P<title>.+?)[ ._-](?P<year>19\d{2}|20\d{2})[ ._-]S(?P<season>\d{1,2})E(?P<episode>\d{1,3})")
STRM_DISC_RE = re.compile(r"(?i)^(?P<title>.+?)[ ._-](?P<year>19\d{2}|20\d{2})[ ._-]S(?P<season>\d{1,2})D(?P<disc>\d{1,2})")
STRM_MOVIE_RE = re.compile(r"(?i)^(?P<title>.+?)[ ._-](?P<year>19\d{2}|20\d{2})(?:[ ._-]|$)")
PATH_SEASON_RE = re.compile(r"(?i)(?:season|(?<![a-z0-9])s)[ ._-]*0*(\d{1,2})(?:[^0-9]|$)")
PATH_CHINESE_SEASON_RE = re.compile(r"第[ ._-]*0*(\d{1,2})[ ._-]*季")
EMBY_LIBRARY_IDS = {
    "00剧/01美": {3}, "00剧/01英": {3}, "00剧/02其他": {3}, "00剧/01中": {11},
    "00剧/01韩": {18}, "00剧/01日": {22}, "00剧/01台": {101295},
    "00影/01外": {25}, "00影/01国": {30}, "00漫/01日": {41}, "00漫/01中": {45},
    "00漫/01美": {45}, "00综": {36},
}


def normalized_media_name(value: object) -> str:
    text = re.sub(r"\{\s*tmdb\s*=\s*\d+\s*\}", "", str(value or ""), flags=re.IGNORECASE)
    text = re.sub(r"[（(](?:19|20)\d{2}[）)]", "", text)
    return "".join(character for character in text.casefold() if character.isalnum())


def media_tmdb_id(value: object) -> str | None:
    match = re.search(r"\{\s*tmdb\s*=\s*(\d+)\s*\}", str(value or ""), flags=re.IGNORECASE)
    return match.group(1) if match else None


def media_modified_timestamp(value: object) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except ValueError:
        return None


def normalized_lookup_query(value: object) -> tuple[str, int | None]:
    """Turn copied release/folder names into a TMDb-friendly title and year."""
    raw = str(value or "").strip()
    title, year = infer_search_terms(raw)
    return (title or raw.strip(" ._-"), year)


def media_name_matches(name: object, aliases: set[str]) -> bool:
    candidate = normalized_media_name(name)
    if not candidate:
        return False
    return any(len(alias) >= 2 and (alias in candidate or candidate in alias) for alias in aliases)


def recommended_folder_name(item: dict[str, object]) -> str:
    language = str(item.get("language") or "").casefold()
    chinese_origin = language in {"zh", "cn", "yue"}
    if chinese_origin:
        title = str(item.get("title") or item.get("original_title") or "").strip()
    else:
        title = str(item.get("original_title") or item.get("title") or "").strip()
    title = title.replace("/", " ").replace("\\", " ").strip()
    year = int(item.get("year") or 0)
    tmdb_id = int(item.get("id") or 0)
    if not title or not year or not tmdb_id:
        return ""
    if chinese_origin:
        return f"{title}（{year}） {{tmdb={tmdb_id}}}"
    return f"{title} ({year}) {{tmdb={tmdb_id}}}"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_secret(path: Path) -> str:
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise ValueError(f"Credential file is empty: {path}")
    return value


def safe_cloud_path(value: object) -> str:
    path = "/" + str(value or "").strip().strip("/")
    if "\n" in path or "\r" in path or any(part == ".." for part in PurePosixPath(path).parts):
        raise ValueError("Invalid media path")
    if not any(path.startswith(root) for root in ALLOWED_ROOTS):
        raise ValueError("Path must be under 115, Baidu, Quark, 123, or GuangYa")
    return path


@dataclass
class Settings:
    host: str
    port: int
    openlist_url: str
    openlist_token_file: Path
    tmdb_token_file: Path
    work_root: Path
    detective_summary: Path
    refresh_worker: Path
    media_index_root: Path = Path("/opt/docker/emby/media/openlist-local-tree")
    emby_url: str = "http://127.0.0.1:8097"
    emby_auth_db: Path = Path("/opt/docker/emby/config/data/authentication.db")
    emby_username: str = "zhongyunxing"
    manual_history: Path = Path("/opt/docker/avecove-namer/manual-history.jsonl")
    namer_completed_state: Path = Path("/opt/docker/avecove-namer/namer-completed.json")

    @classmethod
    def from_env(cls, host: str, port: int) -> "Settings":
        return cls(
            host=host,
            port=port,
            openlist_url=os.getenv("AVECOVE_OPENLIST_URL", "http://127.0.0.1:5245"),
            openlist_token_file=Path(os.getenv("AVECOVE_OPENLIST_TOKEN_FILE", "/opt/docker/avecove-namer/secrets/openlist.token")),
            tmdb_token_file=Path(os.getenv("AVECOVE_TMDB_TOKEN_FILE", "/opt/docker/avecove-namer/secrets/tmdb.token")),
            work_root=Path(os.getenv("AVECOVE_NAMER_WORK_ROOT", "/opt/docker/avecove-namer/web-jobs")),
            detective_summary=Path(os.getenv("AVECOVE_DETECTIVE_SUMMARY", "/opt/docker/avecove-namer/detective/last-summary.json")),
            refresh_worker=Path(os.getenv("AVECOVE_REFRESH_WORKER", "/opt/emby-strm/refresh-one-source-openlist-lowload.py")),
            media_index_root=Path(os.getenv("AVECOVE_MEDIA_INDEX_ROOT", "/opt/docker/emby/media/openlist-local-tree")),
            emby_url=os.getenv("AVECOVE_EMBY_URL", "http://127.0.0.1:8097"),
            emby_auth_db=Path(os.getenv("AVECOVE_EMBY_AUTH_DB", "/opt/docker/emby/config/data/authentication.db")),
            emby_username=os.getenv("AVECOVE_EMBY_USERNAME", "zhongyunxing"),
            manual_history=Path(os.getenv("AVECOVE_MANUAL_HISTORY", "/opt/docker/avecove-namer/manual-history.jsonl")),
            namer_completed_state=Path(os.getenv("AVECOVE_NAMER_COMPLETED_STATE", "/opt/docker/avecove-namer/namer-completed.json")),
        )


class App:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.settings.work_root.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, dict[str, Any]] = {}
        self._jobs_lock = threading.Lock()
        self._cloud_cache: dict[str, tuple[float, list[dict[str, object]]]] = {}
        self._cloud_cache_lock = threading.Lock()
        self._manual_history_lock = threading.Lock()
        self._namer_completed_lock = threading.Lock()
        self._namer_pending_cache: dict[tuple[str, str], bool] = {}

    def tmdb(self) -> TMDBClient:
        return TMDBClient(read_secret(self.settings.tmdb_token_file))

    def openlist(self) -> OpenListBackend:
        return OpenListBackend(self.settings.openlist_url, read_secret(self.settings.openlist_token_file))

    def search(self, payload: dict[str, Any]) -> dict[str, Any]:
        raw_query = str(payload.get("query") or "").strip()
        if not raw_query:
            raise ValueError("请输入剧名或电影名")
        query, inferred_year = normalized_lookup_query(raw_query)
        kind = str(payload.get("kind") or "tv")
        year_value = payload.get("year")
        year = int(year_value) if year_value else inferred_year
        results = self.tmdb().search(query, kind, year, "zh-CN")
        resolved_kind = kind
        if not results:
            resolved_kind = "movie" if kind == "tv" else "tv"
            results = self.tmdb().search(query, resolved_kind, year, "zh-CN")
        for item in results:
            item["kind"] = resolved_kind
            item["recommended_name"] = recommended_folder_name(item)
        return {"results": results, "resolved_kind": resolved_kind}

    def _availability_aliases(self, query: str, kind: str) -> set[str]:
        clean_query, inferred_year = normalized_lookup_query(query)
        aliases = {normalized_media_name(query), normalized_media_name(clean_query)}
        try:
            for item in self.tmdb().search(clean_query, kind, inferred_year, "zh-CN")[:3]:
                aliases.add(normalized_media_name(item.get("title")))
                aliases.add(normalized_media_name(item.get("original_title")))
        except TMDBError:
            pass
        return {alias for alias in aliases if alias}

    def _local_library_matches(self, aliases: set[str]) -> list[str]:
        root = self.settings.media_index_root
        if not root.is_dir():
            return []
        matches: list[str] = []
        for current, directories, filenames in os.walk(root):
            relative = Path(current).relative_to(root)
            for directory in directories:
                if media_name_matches(directory, aliases):
                    matches.append("/" + str(relative / directory))
                    if len(matches) >= 20:
                        return list(dict.fromkeys(matches))
            if len(relative.parts) >= 3:
                for filename in filenames:
                    if filename.casefold().endswith(".strm") and media_name_matches(Path(filename).stem, aliases):
                        matches.append("/" + str(relative))
                        if len(matches) >= 20:
                            return list(dict.fromkeys(matches))
        return list(dict.fromkeys(matches))

    def _cloud_directories(self, backend: OpenListBackend, root: str) -> list[dict[str, object]]:
        now = time.monotonic()
        with self._cloud_cache_lock:
            cached = self._cloud_cache.get(root)
            if cached and now - cached[0] < 300:
                return cached[1]
        directories = backend.list_directories(root, refresh=False)
        with self._cloud_cache_lock:
            self._cloud_cache[root] = (now, directories)
        return directories

    def library_search(self, payload: dict[str, Any]) -> dict[str, Any]:
        query = str(payload.get("query") or "").strip()
        kind = str(payload.get("kind") or "tv")
        if not query:
            raise ValueError("请输入要检查的资源名称")
        if kind not in CLOUD_LIBRARY_ROOTS:
            raise ValueError("媒体类型无效")
        aliases = self._availability_aliases(query, kind)
        backend = self.openlist()
        matches: list[dict[str, str]] = []

        # Verify local-index hits against OpenList, avoiding false positives
        # from STRM files left behind after a cloud deletion.
        for path in self._local_library_matches(aliases):
            try:
                if backend.exists(path):
                    matches.append({"provider": PurePosixPath(path).parts[1], "path": path})
            except BackendError:
                continue

        # New cloud folders may not have reached Emby yet, so fall back to a
        # shallow category lookup. Cached listings keep this cheap on the host.
        if not matches:
            for root in CLOUD_LIBRARY_ROOTS[kind]:
                try:
                    directories = self._cloud_directories(backend, root)
                except BackendError:
                    continue
                for item in directories:
                    name = str(item.get("name") or "")
                    if media_name_matches(name, aliases):
                        path = str(PurePosixPath(root) / name)
                        matches.append({"provider": PurePosixPath(path).parts[1], "path": path})

        unique = list({item["path"]: item for item in matches}.values())
        unique.sort(key=lambda item: (item["provider"].casefold(), item["path"].casefold()))
        return {
            "found": bool(unique),
            "message": "快快观看吧！" if unique else "快快收藏吧！",
            "matches": unique[:20],
        }

    def emby_pending(self) -> dict[str, Any]:
        """Shallowly compare cloud titles with the local STRM index."""
        backend = self.openlist()
        roots = sorted({root for values in CLOUD_LIBRARY_ROOTS.values() for root in values})
        pending: list[dict[str, str]] = []
        errors: list[dict[str, str]] = []
        cloud_titles = present_titles = 0
        scanned_roots = 0
        # Fetch shallow category listings with a small fixed worker pool.
        # A single unavailable cloud must not hold the entire inbox hostage.
        category_roots = [root for root in roots if len(PurePosixPath(root).parts) >= 4]
        with ThreadPoolExecutor(max_workers=4) as executor:
            listings = {root: executor.submit(self._cloud_directories, backend, root) for root in category_roots}
            resolved_listings: list[tuple[str, list[dict[str, object]]]] = []
            for root, future in listings.items():
                try:
                    directories = future.result()
                except BackendError as exc:
                    errors.append({"root": root, "error": str(exc)})
                    continue
                resolved_listings.append((root, directories))
                if root in CLOUD_LIBRARY_ROOTS["movie"]:
                    for item in directories:
                        group_name = str(item.get("name") or "").strip()
                        if group_name not in MOVIE_GROUP_DIRS:
                            continue
                        group_root = str(PurePosixPath(root) / group_name)
                        try:
                            resolved_listings.append((group_root, self._cloud_directories(backend, group_root)))
                        except BackendError as exc:
                            errors.append({"root": group_root, "error": str(exc)})
            for root, directories in resolved_listings:
                scanned_roots += 1
                media_kind = "movie" if any(root == movie_root or root.startswith(movie_root + "/") for movie_root in CLOUD_LIBRARY_ROOTS["movie"]) else "tv"
                local_root = self.settings.media_index_root / root.lstrip("/")
                local_tmdb_ids: set[str] = set()
                local_names: set[str] = set()
                local_tmdb_latest: dict[str, float] = {}
                local_name_latest: dict[str, float] = {}
                if local_root.is_dir():
                    for local_title in local_root.iterdir():
                        if not local_title.is_dir():
                            continue
                        strm_files = list(local_title.rglob("*.strm"))
                        if not strm_files:
                            continue
                        latest = max(path.stat().st_mtime for path in strm_files)
                        tmdb_id = media_tmdb_id(local_title.name)
                        if tmdb_id:
                            local_tmdb_ids.add(tmdb_id)
                            local_tmdb_latest[tmdb_id] = max(local_tmdb_latest.get(tmdb_id, 0), latest)
                        normalized = normalized_media_name(local_title.name)
                        if normalized:
                            local_names.add(normalized)
                            local_name_latest[normalized] = max(local_name_latest.get(normalized, 0), latest)
                for item in directories:
                    name = str(item.get("name") or "").strip()
                    if not name or (media_kind == "movie" and name in MOVIE_GROUP_DIRS):
                        continue
                    cloud_titles += 1
                    tmdb_id = media_tmdb_id(name)
                    normalized = normalized_media_name(name)
                    local_latest = local_tmdb_latest.get(tmdb_id or "") or local_name_latest.get(normalized)
                    already_present = (tmdb_id and tmdb_id in local_tmdb_ids) or (normalized and normalized in local_names)
                    cloud_modified = media_modified_timestamp(item.get("modified"))
                    if already_present:
                        present_titles += 1
                        if not cloud_modified or not local_latest or cloud_modified <= local_latest + 60:
                            continue
                    path = str(PurePosixPath(root) / name)
                    pending.append({
                        "provider": PurePosixPath(root).parts[1],
                        "category": " / ".join(PurePosixPath(root).parts[-2:]) if PurePosixPath(root).name in MOVIE_GROUP_DIRS else PurePosixPath(root).name,
                        "kind": media_kind,
                        "name": name,
                        "path": path,
                        "reason": "subscription_update" if already_present else "new_title",
                        "modified": str(item.get("modified") or ""),
                    })
        unique = list({item["path"]: item for item in pending}.values())
        unique.sort(key=lambda item: (item["provider"].casefold(), item["category"].casefold(), item["name"].casefold()))
        unique.sort(key=lambda item: media_modified_timestamp(item.get("modified")) or 0, reverse=True)
        return {
            "mode": "shallow",
            "scanned_roots": scanned_roots,
            "cloud_titles": cloud_titles,
            "present_titles": present_titles,
            "pending_count": len(unique),
            "pending": unique,
            "errors": errors,
        }

    def _namer_completed_fingerprints(self) -> dict[str, str]:
        """Merge automatic Detective baselines with manual Namer completions."""
        completed: dict[str, str] = {}
        state_root = self.settings.detective_summary.parent
        state_files = [state_root / "state.json", *sorted(state_root.glob("*/state.json"))]
        for state_path in state_files:
            if not state_path.is_file():
                continue
            try:
                works = json.loads(state_path.read_text(encoding="utf-8")).get("works") or {}
            except (OSError, json.JSONDecodeError):
                continue
            for path, signature in works.items():
                value = str(signature or "")
                if value and not value.startswith("pending:"):
                    completed[str(path)] = value
        state_path = self.settings.namer_completed_state
        if state_path.is_file():
            try:
                works = json.loads(state_path.read_text(encoding="utf-8")).get("works") or {}
            except (OSError, json.JSONDecodeError):
                works = {}
            completed.update({str(path): str(signature) for path, signature in works.items() if signature})
        return completed

    def _remember_namer_completion(self, path: str, signature: str) -> None:
        state_path = self.settings.namer_completed_state
        state_path.parent.mkdir(parents=True, exist_ok=True)
        with self._namer_completed_lock:
            data: dict[str, Any] = {"version": 1, "works": {}}
            if state_path.is_file():
                try:
                    loaded = json.loads(state_path.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        data = loaded
                except (OSError, json.JSONDecodeError):
                    pass
            works = data.setdefault("works", {})
            if not isinstance(works, dict):
                works = {}
                data["works"] = works
            works[path] = signature
            data["updated_at"] = utc_now()
            temporary = state_path.with_suffix(state_path.suffix + ".tmp")
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, state_path)

    def _known_namer_candidate_needs_work(self, item: dict[str, str], expected: str | None) -> bool:
        path = item["path"]
        entries = self.openlist().scan(path, refresh=True)
        current_signature = fingerprint(entries)
        if expected and current_signature == expected:
            return False
        tmdb_id = media_tmdb_id(PurePosixPath(path).name)
        if not tmdb_id:
            return True
        kind = item["kind"]
        resolved = self.tmdb().resolve_title(int(tmdb_id), kind, "auto")
        plan = make_plan(
            entries,
            path,
            self.openlist().name,
            NamingPolicy(),
            str(resolved["title"]),
            int(resolved["year"]) if resolved.get("year") else None,
            True,
            int(tmdb_id),
            str(resolved["primary_language"]),
            kind,
            root_title_override=str(resolved["title"]),
        )
        # A source-language parent name is allowed. Only child/media changes
        # mean the work still belongs in the Namer inbox.
        remaining = [operation for operation in plan.operations if operation.kind != "rename_directory"]
        if not remaining and not plan.conflicts:
            self._remember_namer_completion(path, current_signature)
            return False
        return True

    def namer_pending(self) -> dict[str, Any]:
        """Return only entries that still have real naming work to perform."""
        result = self.emby_pending()
        candidates = {item["path"]: item for item in result["pending"]}
        # A movie can already have STRM files in Emby while its cloud folder
        # still needs Namer. Inspect known grouping folders independently.
        backend = self.openlist()
        for root in CLOUD_LIBRARY_ROOTS["movie"]:
            if not root.endswith("/01国"):
                continue
            try:
                groups = self._cloud_directories(backend, root)
            except BackendError:
                continue
            for group in groups:
                group_name = str(group.get("name") or "").strip()
                if group_name not in MOVIE_GROUP_DIRS:
                    continue
                group_root = str(PurePosixPath(root) / group_name)
                try:
                    titles = self._cloud_directories(backend, group_root)
                except BackendError:
                    continue
                for title in titles:
                    name = str(title.get("name") or "").strip()
                    if not name:
                        continue
                    path = str(PurePosixPath(group_root) / name)
                    candidates[path] = {
                        "provider": PurePosixPath(root).parts[1],
                        "category": f"01国 / {group_name}",
                        "kind": "movie", "name": name, "path": path,
                        "reason": "new_title", "modified": str(title.get("modified") or ""),
                    }
        completed = self._namer_completed_fingerprints()
        pending: list[dict[str, str]] = []
        checks: list[tuple[dict[str, str], str | None]] = []
        filtered = 0
        for item in candidates.values():
            # This is a container below the 115 movie category, not a title.
            if item["kind"] == "movie" and item["name"] in {"总其他"}:
                filtered += 1
                continue
            cache_key = (item["path"], str(item.get("modified") or ""))
            if cache_key in self._namer_pending_cache:
                if self._namer_pending_cache[cache_key]:
                    pending.append(item)
                else:
                    filtered += 1
                continue
            expected = completed.get(item["path"])
            if not expected and not media_tmdb_id(item["name"]):
                pending.append(item)
            else:
                checks.append((item, expected))
        if checks:
            with ThreadPoolExecutor(max_workers=5) as executor:
                futures = [(item, executor.submit(self._known_namer_candidate_needs_work, item, expected)) for item, expected in checks]
                for item, future in futures:
                    try:
                        needs_work = future.result()
                        self._namer_pending_cache[(item["path"], str(item.get("modified") or ""))] = needs_work
                        if needs_work:
                            pending.append(item)
                        else:
                            filtered += 1
                    except (BackendError, TMDBError, ValueError):
                        # Never hide a candidate when verification is uncertain.
                        pending.append(item)
        pending.sort(key=lambda item: media_modified_timestamp(item.get("modified")) or 0, reverse=True)
        return {
            **result,
            "pending_count": len(pending),
            "pending": pending,
            "filtered_completed": filtered,
            "mode": "verified_namer",
        }

    def emby_residuals(self) -> dict[str, Any]:
        """Find Emby series or movies whose local STRM path no longer exists."""
        data = self._emby_get("/Items", {
            "Recursive": "true", "IncludeItemTypes": "Series,Movie",
            "Fields": "Path,ProviderIds", "Limit": 5000,
        })
        items: list[dict[str, str]] = []
        marker = "/openlist-local-tree/"
        present_tmdb: set[str] = set()
        present_names: set[tuple[str, str]] = set()
        for strm_path in self.settings.media_index_root.rglob("*.strm") if self.settings.media_index_root.is_dir() else ():
            relative = strm_path.relative_to(self.settings.media_index_root)
            if len(relative.parts) < 4:
                continue
            provider, category, title = relative.parts[0], relative.parts[2], relative.parts[3]
            tmdb_id = media_tmdb_id(title)
            if tmdb_id:
                present_tmdb.add(tmdb_id)
            normalized = normalized_media_name(title)
            if normalized:
                present_names.add((category, normalized))
        for item in data.get("Items", []) if isinstance(data, dict) else []:
            emby_path = str(item.get("Path") or "")
            if marker not in emby_path:
                continue
            relative = emby_path.split(marker, 1)[1].strip("/")
            local_path = self.settings.media_index_root / relative
            item_type = str(item.get("Type") or "")
            exists = local_path.is_file() if item_type == "Movie" else local_path.is_dir() and any(local_path.rglob("*.strm"))
            if exists:
                continue
            parts = PurePosixPath(relative).parts
            provider = parts[0] if parts else "其他"
            category = parts[2] if len(parts) > 2 else ""
            provider_ids = item.get("ProviderIds") if isinstance(item.get("ProviderIds"), dict) else {}
            tmdb_id = str(provider_ids.get("Tmdb") or provider_ids.get("TMDb") or "")
            normalized = normalized_media_name(item.get("Name"))
            if (tmdb_id and tmdb_id in present_tmdb) or (normalized and (category, normalized) in present_names):
                continue
            items.append({
                "id": str(item.get("Id") or ""), "name": str(item.get("Name") or PurePosixPath(relative).name),
                "type": item_type, "provider": provider, "path": "/" + relative,
            })
        items.sort(key=lambda item: (item["provider"].casefold(), item["name"].casefold()))
        return {"count": len(items), "items": items}

    def _strm_identity(self, relative: Path) -> tuple[str, str] | None:
        if len(relative.parts) < 4:
            return None
        stem = relative.stem
        match = STRM_EPISODE_RE.search(stem)
        kind = "episode"
        suffix = ""
        if match:
            suffix = f"S{int(match.group('season')):02d}E{int(match.group('episode')):02d}"
        else:
            match = STRM_DISC_RE.search(stem)
            kind = "disc"
            if match:
                suffix = f"S{int(match.group('season')):02d}D{int(match.group('disc')):02d}"
        if not match and relative.parts[1] == "00影":
            match = STRM_MOVIE_RE.search(stem)
            kind = "movie"
        if not match:
            return None
        tmdb_id = next((media_tmdb_id(part) for part in relative.parts[3:] if media_tmdb_id(part)), None)
        title = f"tmdb:{tmdb_id}" if tmdb_id else normalized_media_name(match.group("title"))
        year = match.group("year")
        if not title:
            return None
        category = "/".join(relative.parts[:3])
        key = f"{category}|{kind}|{title}|{year}|{suffix}"
        series_name = relative.parts[3] if tmdb_id else f"{match.group('title').replace('.', ' ')} ({year})"
        label = series_name + (f" · {suffix}" if suffix else "")
        return key, label

    def emby_duplicates(self) -> dict[str, Any]:
        root = self.settings.media_index_root
        groups: dict[str, list[Path]] = {}
        labels: dict[str, str] = {}
        for path in root.rglob("*.strm") if root.is_dir() else ():
            relative = path.relative_to(root)
            identity = self._strm_identity(relative)
            if not identity:
                continue
            key, label = identity
            groups.setdefault(key, []).append(path)
            labels[key] = label
        plan_id = secrets.token_hex(8)
        output = []
        stored_groups = []
        for key, paths in groups.items():
            if len(paths) < 2:
                continue
            title_key = key.split("|")[2]
            suffix = key.rsplit("|", 1)[-1]
            season_match = re.match(r"S(\d{2})", suffix)
            expected_season = int(season_match.group(1)) if season_match else None
            def priority(path: Path) -> tuple[int, int, int, str]:
                folder_seasons = []
                for part in path.parts[:-1]:
                    match = PATH_SEASON_RE.search(part) or PATH_CHINESE_SEASON_RE.search(part)
                    if match:
                        folder_seasons.append(int(match.group(1)))
                correct_season = int(expected_season is None or expected_season in folder_seasons or not folder_seasons)
                if title_key.startswith("tmdb:"):
                    expected_tmdb = title_key.split(":", 1)[1]
                    parent_match = max((int(media_tmdb_id(part) == expected_tmdb) for part in path.parts[:-1]), default=0)
                else:
                    parent_match = max((int(title_key in normalized_media_name(part)) for part in path.parts[:-1]), default=0)
                return correct_season, parent_match, path.stat().st_mtime_ns, str(path)
            keep = max(paths, key=priority)
            remove = sorted((path for path in paths if path != keep), key=str)
            group_id = secrets.token_hex(6)
            item = {
                "id": group_id, "label": labels[key],
                "provider": keep.relative_to(root).parts[0],
                "category": keep.relative_to(root).parts[2],
                "series": keep.relative_to(root).parts[3],
                "keep": str(keep.relative_to(root)),
                "remove": [str(path.relative_to(root)) for path in remove],
            }
            output.append(item)
            stored_groups.append(item)
        output.sort(key=lambda item: item["label"].casefold())
        plan_dir = self.settings.work_root / "duplicate-plans"
        plan_dir.mkdir(parents=True, exist_ok=True)
        (plan_dir / f"{plan_id}.json").write_text(json.dumps({"groups": stored_groups}, ensure_ascii=False), encoding="utf-8")
        return {"plan_id": plan_id, "group_count": len(output), "remove_count": sum(len(item["remove"]) for item in output), "groups": output}

    def clean_emby_duplicates(self, payload: dict[str, Any]) -> dict[str, Any]:
        plan_id = str(payload.get("plan_id") or "")
        group_ids = payload.get("group_ids")
        if group_ids == "all":
            group_ids = None
        elif not isinstance(group_ids, list):
            legacy_group = str(payload.get("group_id") or "")
            group_ids = [legacy_group] if legacy_group else []
        if not re.fullmatch(r"[0-9a-f]{16}", plan_id) or (group_ids is not None and any(not re.fullmatch(r"[0-9a-f]{12}", str(value)) for value in group_ids)):
            raise ValueError("重复项清理计划无效")
        plan_path = self.settings.work_root / "duplicate-plans" / f"{plan_id}.json"
        if not plan_path.is_file():
            raise ValueError("清理计划已失效，请重新检测")
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        groups = plan.get("groups", [])
        selected = groups if group_ids is None else [item for item in groups if item.get("id") in set(group_ids)]
        if not selected:
            raise ValueError("找不到要清理的重复组")
        count = sum(len(group.get("remove") or []) for group in selected)
        if str(payload.get("confirmation") or "").strip() != f"清理 {count} 项":
            raise ValueError(f"确认文字必须是：清理 {count} 项")
        root = self.settings.media_index_root.resolve()
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        quarantine = Path("/opt/emby-strm/quarantine/duplicate-cleanup") / stamp
        moved = []
        affected_ids: set[int] = set()
        for value in [value for group in selected for value in group["remove"]]:
            source = (root / value).resolve()
            if root not in source.parents or source.suffix.casefold() != ".strm":
                raise ValueError("检测到不安全的 STRM 路径，已停止")
            if not source.is_file():
                continue
            destination = quarantine / value
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))
            moved.append(value)
            for marker, ids in EMBY_LIBRARY_IDS.items():
                if marker in value:
                    affected_ids.update(ids)
        token = self._emby_token()
        for item_id in sorted(affected_ids):
            url = self.settings.emby_url.rstrip("/") + f"/emby/Items/{item_id}/Refresh?Recursive=true"
            request = urllib.request.Request(url, data=b"", method="POST", headers={"X-Emby-Token": token})
            urllib.request.urlopen(request, timeout=20).read()
        return {"moved": len(moved), "quarantine": str(quarantine), "refreshed_libraries": sorted(affected_ids)}

    def automation_status(self) -> dict[str, Any]:
        states: dict[str, bool] = {}
        for timer in AUTOMATION_TIMERS:
            result = subprocess.run(
                ["systemctl", "is-active", "--quiet", timer],
                check=False,
                timeout=10,
            )
            states[timer] = result.returncode == 0
        return {"enabled": all(states.values()), "timers": states}

    def set_automation(self, payload: dict[str, Any]) -> dict[str, Any]:
        enabled = payload.get("enabled")
        if not isinstance(enabled, bool):
            raise ValueError("自动扫描状态无效")
        action = "enable" if enabled else "disable"
        result = subprocess.run(
            ["systemctl", action, "--now", *AUTOMATION_TIMERS],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise ValueError((result.stderr or result.stdout or "无法修改自动扫描状态").strip())
        return self.automation_status()

    def _emby_token(self) -> str:
        with sqlite3.connect(self.settings.emby_auth_db) as database:
            row = database.execute(
                "SELECT AccessToken FROM Tokens_2 WHERE IsActive=1 ORDER BY DateLastActivityInt DESC LIMIT 1"
            ).fetchone()
        if not row or not row[0]:
            raise ValueError("Emby 没有可用的服务端令牌")
        return str(row[0])

    def _emby_get(self, path: str, params: dict[str, object] | None = None) -> Any:
        url = self.settings.emby_url.rstrip("/") + "/emby" + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, headers={"X-Emby-Token": self._emby_token(), "User-Agent": "AveCove-Media/1"})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                content_type = response.headers.get_content_type()
                if content_type.startswith("image/"):
                    return response.read(), content_type
                return json.load(response)
        except (urllib.error.URLError, json.JSONDecodeError) as exc:
            raise ValueError(f"Emby 连接失败：{exc}") from exc

    def _emby_user_id(self) -> str:
        users = self._emby_get("/Users")
        for user in users if isinstance(users, list) else []:
            if str(user.get("Name") or "") == self.settings.emby_username:
                return str(user["Id"])
        raise ValueError("找不到指定的 Emby 用户")

    def watch_recommendations(self, payload: dict[str, Any]) -> dict[str, Any]:
        source = str(payload.get("source") or "emby")
        kind = str(payload.get("kind") or "all")
        mood = str(payload.get("mood") or "all")
        if source not in {"emby", "trending"} or kind not in {"all", "movie", "tv"}:
            raise ValueError("推荐条件无效")
        if source == "trending":
            client = self.tmdb()
            kinds = ("movie", "tv") if kind == "all" else (kind,)
            items: list[dict[str, Any]] = []
            # Run independent TMDb pools concurrently so a much larger random
            # catalogue does not make the home page wait on six serial calls.
            jobs = []
            with ThreadPoolExecutor(max_workers=6) as executor:
                for selected_kind in kinds:
                    jobs.append(executor.submit(client.trending, selected_kind, "zh-CN"))
                    # Pull a different page on every request instead of repeatedly
                    # showing only TMDb's first popular page.  The language pool
                    # keeps Chinese and Cantonese work represented, while the
                    # global pool greatly expands overseas variety.
                    jobs.append(
                        executor.submit(
                            client.discover,
                            selected_kind,
                            original_language="zh|yue",
                            page=random.randint(1, 5),
                            language="zh-CN",
                        )
                    )
                    jobs.append(
                        executor.submit(
                            client.discover,
                            selected_kind,
                            page=random.randint(1, 12),
                            language="zh-CN",
                        )
                    )
                for job in jobs:
                    items.extend(job.result())

            deduplicated: dict[tuple[str, str], dict[str, Any]] = {}
            for item in items:
                key = (str(item.get("kind") or ""), str(item.get("id") or ""))
                if key[1] and item.get("poster_path"):
                    deduplicated[key] = item
            pools: dict[tuple[str, str], list[dict[str, Any]]] = {}
            domestic_languages = {"zh", "cn", "yue"}
            for item in deduplicated.values():
                origin = "domestic" if str(item.get("language") or "").casefold() in domestic_languages else "overseas"
                pools.setdefault((str(item.get("kind")), origin), []).append(item)
            for pool in pools.values():
                random.shuffle(pool)

            # Round-robin the four pools so each refresh is random but remains
            # balanced across movie/TV and domestic/overseas content.
            items = []
            pool_order = [(media_kind, origin) for media_kind in kinds for origin in ("domestic", "overseas")]
            random.shuffle(pool_order)
            while len(items) < 54 and any(pools.get(key) for key in pool_order):
                for key in pool_order:
                    pool = pools.get(key) or []
                    if pool:
                        items.append(pool.pop())
                        if len(items) >= 32:
                            break
            genre_sets = {
                "relaxed": {16, 35, 10751, 10762},
                "spectacle": {12, 14, 28, 878, 10759, 10765},
                "mystery": {53, 80, 9648},
            }
            allowed = genre_sets.get(mood)
            if allowed:
                filtered = [item for item in items if allowed.intersection(int(value) for value in item.get("genre_ids", []))]
                items = filtered or items
            return {
                "source": "trending",
                "items": [
                    {
                        "id": str(item["id"]),
                        "title": item.get("title") or item.get("original_title"),
                        "original": item.get("original_title") or item.get("title"),
                        "year": item.get("year"),
                        "minutes": None,
                        "genres": "TMDb 本周热门",
                        "note": item.get("overview") or "近期热度较高，适合加入待看片单。",
                        "kind": item.get("kind"),
                        "language": item.get("language"),
                        "origin": "国产" if str(item.get("language") or "").casefold() in domestic_languages else "海外",
                        "image_url": f"https://image.tmdb.org/t/p/w780{item['poster_path']}" if item.get("poster_path") else None,
                        "open_url": f"https://www.themoviedb.org/{item['kind']}/{item['id']}",
                        "rating": item.get("rating"),
                    }
                    for item in items
                ],
            }

        include_types = {"all": "Movie,Series", "movie": "Movie", "tv": "Series"}[kind]
        data = self._emby_get(
            f"/Users/{self._emby_user_id()}/Items",
            {
                "Recursive": "true",
                "IncludeItemTypes": include_types,
                "Fields": "Genres,Overview,RunTimeTicks,ProductionYear,CommunityRating,ImageTags,OriginalTitle",
                "SortBy": "Random",
                "Limit": 120,
            },
        )
        items = list(data.get("Items") or []) if isinstance(data, dict) else []
        mood_terms = {
            "relaxed": {"喜剧", "comedy", "动画", "animation", "家庭", "family"},
            "spectacle": {"动作", "action", "冒险", "adventure", "科幻", "science fiction", "奇幻", "fantasy"},
            "mystery": {"悬疑", "mystery", "犯罪", "crime", "惊悚", "thriller"},
        }
        if mood == "short":
            filtered = [item for item in items if 0 < int(item.get("RunTimeTicks") or 0) / 600_000_000 <= 110]
            items = filtered or items
        elif mood in mood_terms:
            wanted = mood_terms[mood]
            filtered = [item for item in items if any(str(genre).casefold() in wanted for genre in item.get("Genres") or [])]
            items = filtered or items
        random.shuffle(items)
        output = []
        for item in items[:30]:
            ticks = int(item.get("RunTimeTicks") or 0)
            item_id = str(item.get("Id") or "")
            output.append(
                {
                    "id": item_id,
                    "title": item.get("Name"),
                    "original": item.get("OriginalTitle") or item.get("Name"),
                    "year": item.get("ProductionYear"),
                    "minutes": round(ticks / 600_000_000) if ticks else None,
                    "genres": " · ".join(str(value) for value in (item.get("Genres") or [])[:3]),
                    "note": item.get("Overview") or "来自你的 Emby 片库，今晚可以直接打开观看。",
                    "kind": "tv" if item.get("Type") == "Series" else "movie",
                    "image_url": f"/media-tools/api/watch/image/{item_id}" if item.get("ImageTags", {}).get("Primary") else None,
                    "open_url": f"https://emby.avecrouge.top/web/index.html#!/item?id={item_id}",
                    "rating": item.get("CommunityRating"),
                }
            )
        return {"source": "emby", "total": data.get("TotalRecordCount", len(output)), "items": output}

    def watch_trailer(self, payload: dict[str, Any]) -> dict[str, Any]:
        value = str(payload.get("tmdb_id") or payload.get("id") or "").strip()
        kind = str(payload.get("kind") or "").strip()
        if not value.isdecimal() or kind not in {"movie", "tv"}:
            raise ValueError("影片编号或类型无效")
        tmdb_id = int(value)
        trailer = self.tmdb().trailer(tmdb_id, kind)
        return {
            "tmdb_id": tmdb_id,
            "kind": kind,
            "available": trailer is not None,
            "trailer": trailer,
            "detail_url": f"https://www.themoviedb.org/{kind}/{tmdb_id}",
        }

    def emby_image(self, item_id: str) -> tuple[bytes, str]:
        if not item_id or not item_id.isalnum():
            raise ValueError("图片编号无效")
        return self._emby_get(f"/Items/{item_id}/Images/Primary", {"maxWidth": 780, "quality": 86})

    def source_episodes(self, payload: dict[str, Any]) -> dict[str, Any]:
        value = str(payload.get("query") or payload.get("tmdb_id") or "").strip()
        if not value:
            raise ValueError("请输入 TMDb 剧集 ID 或剧名")
        client = self.tmdb()
        if value.isdecimal():
            data = client.original_episode_metadata(int(value))
            resolved = client.resolve_title(int(value), "tv", "auto")
            data["title"] = resolved["title"]
            data["recommended_name"] = recommended_folder_name(
                {
                    "id": data["tmdb_id"],
                    "title": data["original_name"],
                    "original_title": data["original_name"],
                    "year": data.get("year"),
                    "language": data["original_language"],
                }
            )
            return data
        query, inferred_year = normalized_lookup_query(value)
        results = client.search(query, "tv", inferred_year, "zh-CN")
        if not results:
            raise ValueError("没有找到该剧")
        exact = [item for item in results if query.casefold() in {str(item.get("title") or "").casefold(), str(item.get("original_title") or "").casefold()}]
        if len(exact) == 1:
            return self.source_episodes({"tmdb_id": int(exact[0]["id"])})
        return {"selection_required": True, "results": results}

    def identify_path(self, payload: dict[str, Any]) -> dict[str, Any]:
        path = safe_cloud_path(payload.get("path"))
        if path.rstrip("/") in BROAD_NAMER_PATHS:
            raise ValueError("请在常用路径后继续填写具体作品文件夹，避免扫描整个媒体库")
        kind = str(payload.get("kind") or "tv")
        style = str(payload.get("title_style") or "auto")
        source_parent = bool(payload.get("source_parent"))
        if kind not in {"tv", "movie"}:
            raise ValueError("媒体类型无效")
        client = self.tmdb()
        supplied_id = int(payload.get("tmdb_id") or 0)
        score = 1.0
        reason = "手动指定 TMDb ID"
        query_title = None
        query_year = None
        source_available = True
        warning = None
        if supplied_id:
            tmdb_id = supplied_id
        else:
            try:
                entries = self.openlist().scan(path, refresh=True)
            except BackendError:
                # Identification only needs the copied folder name.  OpenList
                # is still required later when a rename plan is executed.
                entries = []
                source_available = False
                warning = "OpenList 尚未同步该目录；已直接根据复制的 115 名称完成识别。生成改名预览前需等 OpenList 可访问该目录。"
            finder = _find_tv_tmdb_id if kind == "tv" else _find_movie_tmdb_id
            tmdb_id, score, reason, query_title, query_year = finder(client, PurePosixPath(path).name, entries)
            if not tmdb_id:
                raise ValueError(f"未能高置信度识别：{reason}。可以在高级选项中手动填写 TMDb ID。")
        resolved = client.resolve_title(tmdb_id, kind, style)
        parent_title = str(resolved["original_title"] if source_parent else resolved["title"])
        child_title = str(resolved["english_title"] if source_parent else resolved["title"])
        parent_language = (
            "zh" if str(resolved.get("original_language") or "").casefold() in {"zh", "cn", "yue"} else "en"
        ) if source_parent else str(resolved["primary_language"])
        recommended_name = recommended_folder_name(
            {
                "id": tmdb_id,
                "title": parent_title,
                "original_title": parent_title,
                "year": resolved.get("year"),
                "language": parent_language,
            }
        )
        result = {
            "path": path,
            "kind": kind,
            "tmdb_id": tmdb_id,
            "score": round(score, 4),
            "reason": reason,
            "query_title": query_title,
            "query_year": query_year,
            "recommended_name": recommended_name,
            "parent_title": parent_title,
            "child_title": child_title,
            "source_parent": source_parent,
            "source_available": source_available,
            "warning": warning,
            "resolved": resolved,
        }
        self._append_manual_event(
            {
                "created_at": utc_now(),
                "provider": PurePosixPath(path).parts[1],
                "path": path,
                "status": "identified",
                "tmdb_id": tmdb_id,
                "reason": f"TMDb {tmdb_id} · {resolved['title']}",
            }
        )
        return result

    def create_plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        path = safe_cloud_path(payload.get("path"))
        tmdb_id = int(payload.get("tmdb_id") or 0)
        if tmdb_id < 1:
            raise ValueError("TMDb ID 必须是正整数")
        kind = str(payload.get("kind") or "tv")
        if kind not in {"tv", "movie"}:
            raise ValueError("媒体类型无效")
        style = str(payload.get("title_style") or "auto")
        source_parent = bool(payload.get("source_parent"))
        resolved = self.tmdb().resolve_title(tmdb_id, kind, style)
        parent_title = str(resolved["original_title"] if source_parent else resolved["title"])
        child_title = str(resolved["english_title"] if source_parent else resolved["title"])
        parent_language = (
            "zh" if str(resolved.get("original_language") or "").casefold() in {"zh", "cn", "yue"} else "en"
        ) if source_parent else str(resolved["primary_language"])
        backend = self.openlist()
        try:
            entries = backend.scan(path, refresh=True)
        except BackendError as exc:
            raise ValueError("名称已可识别，但 OpenList 尚未同步到该目录，暂时无法执行改名。请先在 OpenList 刷新该存储后再生成预览。") from exc
        plan = make_plan(
            entries,
            path,
            backend.name,
            NamingPolicy(),
            child_title,
            int(resolved["year"]) if resolved.get("year") else None,
            True,
            tmdb_id,
            parent_language,
            kind,
            root_title_override=parent_title,
        )
        for operation in plan.operations:
            if operation.kind == "rename_directory" and backend.exists(operation.target):
                plan.conflicts.append(f"Target exists: {operation.target}")
        plan.conflicts = sorted(set(plan.conflicts))
        plan_id = secrets.token_hex(12)
        job_dir = self.settings.work_root / plan_id
        job_dir.mkdir(mode=0o700)
        write_plan(plan, str(job_dir / "plan.json"), str(job_dir / "plan.csv"))
        if not plan.operations and not plan.conflicts:
            self._remember_namer_completion(path, fingerprint(entries))
        return {"plan_id": plan_id, "resolved": resolved, **plan.to_dict()}

    def apply_plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        plan_id = str(payload.get("plan_id") or "")
        if not plan_id or not plan_id.isalnum():
            raise ValueError("计划编号无效")
        plan_path = self.settings.work_root / plan_id / "plan.json"
        if not plan_path.is_file():
            raise ValueError("找不到改名计划，请重新预览")
        plan = read_plan(str(plan_path))
        expected = f"执行 {len(plan.operations)} 项"
        if str(payload.get("confirmation") or "").strip() != expected:
            raise ValueError(f"请输入“{expected}”确认")
        if plan.conflicts:
            raise ValueError("计划仍有冲突，不能执行")
        if not plan.operations:
            return {"status": "compliant", "message": "已经符合命名规则，无需修改"}
        claim = self.settings.work_root / plan_id / ".apply-started"
        try:
            descriptor = os.open(claim, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)
        except FileExistsError:
            raise ValueError("这个计划已经提交执行") from None
        job_id = secrets.token_hex(8)
        with self._jobs_lock:
            self._jobs[job_id] = {
                "id": job_id,
                "type": "namer_apply",
                "path": plan.root,
                "operations": len(plan.operations),
                "completed_operations": 0,
                "current_operation": None,
                "status": "running",
                "started_at": utc_now(),
            }
        threading.Thread(target=self._run_apply, args=(job_id, plan_id, plan), daemon=True).start()
        return {"status": "running", "job_id": job_id}

    def apply_batch(self, payload: dict[str, Any]) -> dict[str, Any]:
        raw_ids = payload.get("plan_ids")
        if not isinstance(raw_ids, list) or not raw_ids:
            raise ValueError("请先为批量队列生成预览")
        plan_ids = list(dict.fromkeys(str(value) for value in raw_ids))
        if len(plan_ids) > 30 or any(not value.isalnum() for value in plan_ids):
            raise ValueError("批量计划无效；一次最多处理 30 个目录")
        plans: list[tuple[str, RenamePlan]] = []
        for plan_id in plan_ids:
            plan_path = self.settings.work_root / plan_id / "plan.json"
            if not plan_path.is_file():
                raise ValueError("部分改名预览已失效，请重新生成")
            plan = read_plan(str(plan_path))
            if plan.conflicts:
                raise ValueError(f"{plan.root} 仍有冲突，不能加入批量执行")
            if plan.operations:
                plans.append((plan_id, plan))
        if not plans:
            raise ValueError("所选目录均已规范，无需执行")
        expected = f"批量执行 {len(plans)} 个目录"
        if str(payload.get("confirmation") or "").strip() != expected:
            raise ValueError(f"请输入“{expected}”确认")
        with self._jobs_lock:
            if any(job.get("type") in {"namer_apply", "namer_batch_apply"} and job.get("status") == "running" for job in self._jobs.values()):
                raise ValueError("已有 Namer 改名任务正在执行")
        for plan_id, _ in plans:
            if (self.settings.work_root / plan_id / ".apply-started").exists():
                raise ValueError("队列中有计划已经执行过，请重新生成预览")
        for plan_id, _ in plans:
            claim = self.settings.work_root / plan_id / ".apply-started"
            descriptor = os.open(claim, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)
        job_id = secrets.token_hex(8)
        operation_count = sum(len(plan.operations) for _, plan in plans)
        with self._jobs_lock:
            self._jobs[job_id] = {
                "id": job_id, "type": "namer_batch_apply", "status": "running",
                "started_at": utc_now(), "total": len(plans), "completed": 0,
                "failed": 0, "operations": operation_count, "current": plans[0][1].root,
                "completed_operations": 0, "current_operation": None,
            }
        threading.Thread(target=self._run_batch_apply, args=(job_id, plans), daemon=True).start()
        return {"status": "running", "job_id": job_id}

    def _run_batch_apply(self, job_id: str, plans: list[tuple[str, RenamePlan]]) -> None:
        completed = failed = 0
        summaries: list[str] = []
        refresh_paths: list[str] = []
        for index, (plan_id, plan) in enumerate(plans, 1):
            with self._jobs_lock:
                self._jobs[job_id].update({"current": plan.root, "current_index": index})
                earlier_operations = self._jobs[job_id]["completed_operations"]
            def report_progress(count: int, operation: RenameOperation) -> None:
                with self._jobs_lock:
                    self._jobs[job_id].update({
                        "completed_operations": earlier_operations + count,
                        "current_operation": operation.target,
                    })
            try:
                execute_plan(
                    plan, self.openlist(),
                    str(self.settings.work_root / plan_id / "rollback.jsonl"),
                    execute=True, confirm_root=plan.root, confirm_count=len(plan.operations),
                    on_progress=report_progress,
                )
                final_path = next(
                    (operation.target for operation in plan.operations if operation.kind == "rename_directory" and operation.source == plan.root),
                    plan.root,
                )
                refresh_paths.append(final_path)
                try:
                    final_entries = self.openlist().scan(final_path, refresh=True)
                    self._remember_namer_completion(final_path, fingerprint(final_entries))
                except (BackendError, OSError):
                    pass
                completed += 1
                summaries.append(f"[{index}/{len(plans)}] 改名完成：{final_path}")
            except Exception as exc:
                failed += 1
                summaries.append(f"[{index}/{len(plans)}] 失败：{plan.root} · {exc}")
            with self._jobs_lock:
                self._jobs[job_id].update({"completed": completed, "failed": failed, "current_operation": None, "output": "\n".join(summaries[-30:])})
        with self._jobs_lock:
            self._jobs[job_id].update({
                "status": "completed" if failed == 0 else "failed", "current": None,
                "refresh_paths": refresh_paths, "finished_at": utc_now(),
            })

    def _run_apply(self, job_id: str, plan_id: str, plan: RenamePlan) -> None:
        journal = self.settings.work_root / plan_id / "rollback.jsonl"
        def report_progress(count: int, operation: RenameOperation) -> None:
            with self._jobs_lock:
                self._jobs[job_id].update({"completed_operations": count, "current_operation": operation.target})
        try:
            completed = execute_plan(
                plan,
                self.openlist(),
                str(journal),
                execute=True,
                confirm_root=plan.root,
                confirm_count=len(plan.operations),
                on_progress=report_progress,
            )
            final_path = plan.root
            for operation in plan.operations:
                if operation.kind == "rename_directory" and operation.source == plan.root:
                    final_path = operation.target
                    break
            try:
                final_entries = self.openlist().scan(final_path, refresh=True)
                self._remember_namer_completion(final_path, fingerprint(final_entries))
            except (BackendError, OSError):
                pass
            refresh_job = self.start_refresh(final_path)
            update = {
                "status": "completed",
                "operations": len(completed),
                "completed_operations": len(completed),
                "current_operation": None,
                "path": final_path,
                "journal": str(journal),
                "refresh_job": refresh_job,
                "finished_at": utc_now(),
            }
        except Exception as exc:
            update = {"status": "failed", "output": str(exc), "finished_at": utc_now()}
        with self._jobs_lock:
            self._jobs[job_id].update(update)

    def _append_manual_event(self, event: dict[str, Any]) -> None:
        self.settings.manual_history.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event, ensure_ascii=False) + "\n"
        with self._manual_history_lock:
            with self.settings.manual_history.open("a", encoding="utf-8") as stream:
                stream.write(line)

    def _manual_events(self, limit: int = 20) -> list[dict[str, Any]]:
        if not self.settings.manual_history.is_file():
            return []
        with self._manual_history_lock:
            lines = self.settings.manual_history.read_text(encoding="utf-8").splitlines()
        events: list[dict[str, Any]] = []
        for line in reversed(lines):
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                events.append(value)
            if len(events) >= limit:
                break
        return events

    def detective(self) -> dict[str, Any]:
        candidates = []
        if self.settings.detective_summary.is_file():
            candidates.append(self.settings.detective_summary)
        candidates.extend(sorted(self.settings.detective_summary.parent.glob("*/last-summary.json")))
        candidates = list(dict.fromkeys(candidates))
        events: list[dict[str, Any]] = []
        created_values: list[str] = []
        for summary_path in candidates:
            data = json.loads(summary_path.read_text(encoding="utf-8"))
            created_values.append(str(data.get("created_at") or ""))
            provider = summary_path.parent.name if summary_path.parent != self.settings.detective_summary.parent else "legacy"
            for event in data.get("events") or []:
                events.append({"provider": provider, **event})
        counts: dict[str, int] = {}
        for event in events:
            status = str(event.get("status") or "unknown")
            counts[status] = counts.get(status, 0) + 1
        return {
            "created_at": max(created_values, default="") or None,
            "events": events,
            "manual_events": self._manual_events(20),
            "counts": counts,
        }

    def start_refresh(self, prefix: object) -> str:
        media_path = safe_cloud_path(prefix)
        if not self.settings.refresh_worker.is_file():
            raise ValueError("服务器未配置 Emby 刷新程序")
        job_id = secrets.token_hex(8)
        with self._jobs_lock:
            if any(job.get("type") in {"emby_refresh", "emby_refresh_queue"} and job.get("status") == "running" for job in self._jobs.values()):
                raise ValueError("已有一个 Emby 同步任务正在运行，请完成后再同步下一项")
            self._jobs[job_id] = {"id": job_id, "type": "emby_refresh", "path": media_path, "status": "running", "started_at": utc_now()}
        threading.Thread(target=self._run_refresh, args=(job_id, media_path), daemon=True).start()
        return job_id

    def start_refresh_queue(self, payload: dict[str, Any]) -> str:
        raw_paths = payload.get("paths")
        if not isinstance(raw_paths, list) or not raw_paths:
            raise ValueError("请先把需要更新的项目加入刷新集合")
        paths = list(dict.fromkeys(safe_cloud_path(value) for value in raw_paths))
        if len(paths) > 200:
            raise ValueError("一次最多加入 200 个项目")
        if not self.settings.refresh_worker.is_file():
            raise ValueError("服务器未配置 Emby 刷新程序")
        job_id = secrets.token_hex(8)
        with self._jobs_lock:
            if any(job.get("type") in {"emby_refresh", "emby_refresh_queue"} and job.get("status") == "running" for job in self._jobs.values()):
                raise ValueError("已有一个 Emby 同步任务正在运行，请完成后再启动队列")
            self._jobs[job_id] = {
                "id": job_id, "type": "emby_refresh_queue", "paths": paths,
                "status": "running", "started_at": utc_now(), "total": len(paths),
                "completed": 0, "failed": 0, "current": paths[0], "output": "",
            }
        threading.Thread(target=self._run_refresh_queue, args=(job_id, paths), daemon=True).start()
        return job_id

    def _run_refresh_queue(self, job_id: str, paths: list[str]) -> None:
        env = {**os.environ, "REFRESH_CPU_LIMIT": "0.35", "REFRESH_ALLOW_CONTAINERS": "__none__", "REFRESH_PRESERVE_IMAGES": "1", "REFRESH_PRESERVE_SIDECARS": "1"}
        completed = failed = 0
        summaries = []
        for index, media_path in enumerate(paths, 1):
            with self._jobs_lock:
                self._jobs[job_id].update({"current": media_path, "current_index": index})
            try:
                result = subprocess.run(
                    [str(self.settings.refresh_worker), "--prefix", media_path], check=False,
                    capture_output=True, text=True, timeout=45 * 60, env=env,
                )
                if result.returncode == 0:
                    completed += 1
                    summaries.append(f"[{index}/{len(paths)}] 完成：{media_path}")
                else:
                    failed += 1
                    summaries.append(f"[{index}/{len(paths)}] 失败：{media_path}")
            except Exception as exc:
                failed += 1
                summaries.append(f"[{index}/{len(paths)}] 失败：{media_path} · {exc}")
            with self._jobs_lock:
                self._jobs[job_id].update({"completed": completed, "failed": failed, "output": "\n".join(summaries[-30:])})
        with self._jobs_lock:
            self._jobs[job_id].update({
                "status": "completed" if failed == 0 else "failed", "completed": completed,
                "failed": failed, "current": None, "finished_at": utc_now(),
            })

    def _run_refresh(self, job_id: str, media_path: str) -> None:
        env = {**os.environ, "REFRESH_CPU_LIMIT": "0.35", "REFRESH_ALLOW_CONTAINERS": "__none__", "REFRESH_PRESERVE_IMAGES": "1", "REFRESH_PRESERVE_SIDECARS": "1"}
        try:
            result = subprocess.run(
                [str(self.settings.refresh_worker), "--prefix", media_path],
                check=False,
                capture_output=True,
                text=True,
                timeout=45 * 60,
                env=env,
            )
            output = (result.stdout + "\n" + result.stderr).strip()[-8000:]
            update = {"status": "completed" if result.returncode == 0 else "failed", "exit_code": result.returncode, "output": output, "finished_at": utc_now()}
        except Exception as exc:  # subprocess failures are returned to the private operator UI
            update = {"status": "failed", "output": str(exc), "finished_at": utc_now()}
        with self._jobs_lock:
            self._jobs[job_id].update(update)

    def job(self, job_id: str) -> dict[str, Any]:
        with self._jobs_lock:
            if job_id not in self._jobs:
                raise ValueError("找不到任务")
            return dict(self._jobs[job_id])


def handler_factory(app: App):
    static_root = Path(__file__).with_name("web")

    def routed_path(raw_path: str) -> str:
        path = urlparse(raw_path).path
        if path == "/media-tools":
            return "/"
        if path.startswith("/media-tools/"):
            return path[len("/media-tools"):]
        return path

    class Handler(BaseHTTPRequestHandler):
        server_version = "AveCoveMedia/1"

        def log_message(self, fmt: str, *args: object) -> None:
            print(f"{self.address_string()} - {fmt % args}")

        def json_response(self, status: int, value: object) -> None:
            body = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def binary_response(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "private, max-age=3600")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            if length < 1 or length > MAX_BODY:
                raise ValueError("请求内容大小无效")
            value = json.loads(self.rfile.read(length))
            if not isinstance(value, dict):
                raise ValueError("请求格式无效")
            return value

        def api_error(self, exc: Exception) -> None:
            if isinstance(exc, (ValueError, json.JSONDecodeError)):
                status = HTTPStatus.BAD_REQUEST
            elif isinstance(exc, (TMDBError, BackendError, ExecutionError)):
                status = HTTPStatus.BAD_GATEWAY
            else:
                status = HTTPStatus.INTERNAL_SERVER_ERROR
            self.json_response(status, {"error": str(exc)})

        def do_GET(self) -> None:
            path = routed_path(self.path)
            try:
                if path == "/api/health":
                    self.json_response(HTTPStatus.OK, {"status": "ok", "service": "AveCove Media Tools"})
                    return
                if path == "/api/detective":
                    self.json_response(HTTPStatus.OK, app.detective())
                    return
                if path == "/api/automation":
                    self.json_response(HTTPStatus.OK, app.automation_status())
                    return
                if path == "/api/emby/pending":
                    self.json_response(HTTPStatus.OK, app.emby_pending())
                    return
                if path == "/api/namer/pending":
                    self.json_response(HTTPStatus.OK, app.namer_pending())
                    return
                if path == "/api/emby/residuals":
                    self.json_response(HTTPStatus.OK, app.emby_residuals())
                    return
                if path == "/api/emby/duplicates":
                    self.json_response(HTTPStatus.OK, app.emby_duplicates())
                    return
                if path.startswith("/api/watch/image/"):
                    body, content_type = app.emby_image(path.rsplit("/", 1)[-1])
                    self.binary_response(HTTPStatus.OK, body, content_type)
                    return
                if path.startswith("/api/jobs/"):
                    self.json_response(HTTPStatus.OK, app.job(path.rsplit("/", 1)[-1]))
                    return
                if path.startswith("/api/"):
                    self.json_response(HTTPStatus.NOT_FOUND, {"error": "接口不存在"})
                    return
                relative = "index.html" if path in {"", "/"} else path.lstrip("/")
                target = (static_root / relative).resolve()
                if static_root.resolve() not in target.parents or not target.is_file():
                    target = static_root / "index.html"
                body = target.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-cache" if target.name == "index.html" else "public, max-age=3600")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("Referrer-Policy", "same-origin")
                self.end_headers()
                self.wfile.write(body)
            except Exception as exc:
                self.api_error(exc)

        def do_POST(self) -> None:
            try:
                routes = {
                    "/api/tmdb/search": app.search,
                    "/api/tmdb/source": app.source_episodes,
                    "/api/library/search": app.library_search,
                    "/api/automation": app.set_automation,
                    "/api/namer/plan": app.create_plan,
                    "/api/namer/identify": app.identify_path,
                    "/api/namer/apply": app.apply_plan,
                    "/api/namer/batch/apply": app.apply_batch,
                    "/api/emby/refresh": lambda payload: {"job_id": app.start_refresh(payload.get("path"))},
                    "/api/emby/refresh-queue": lambda payload: {"job_id": app.start_refresh_queue(payload)},
                    "/api/emby/duplicates/clean": app.clean_emby_duplicates,
                    "/api/watch/recommendations": app.watch_recommendations,
                    "/api/watch/trailer": app.watch_trailer,
                }
                action = routes.get(routed_path(self.path))
                if not action:
                    self.json_response(HTTPStatus.NOT_FOUND, {"error": "接口不存在"})
                    return
                self.json_response(HTTPStatus.OK, action(self.read_json()))
            except Exception as exc:
                self.api_error(exc)

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AveCove Media Tools private web UI")
    parser.add_argument("--host", default=os.getenv("AVECOVE_WEB_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("AVECOVE_WEB_PORT", "8787")))
    args = parser.parse_args(argv)
    settings = Settings.from_env(args.host, args.port)
    server = ThreadingHTTPServer((settings.host, settings.port), handler_factory(App(settings)))
    print(f"AveCove Media Tools listening on http://{settings.host}:{settings.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

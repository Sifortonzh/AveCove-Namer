from __future__ import annotations

import base64

import argparse
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
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

from .backends import BackendError, LitePanWebDAVBackend, OpenListBackend
from .shelf import SeriesShelf
from .handoff import HandoffInbox
LITEPAN_CHANGES_FILE = Path('/opt/banwugan-litepan-bridge/namer-changes.json')
from .detective import _find_movie_tmdb_id, _find_tv_tmdb_id, discover_movie_works, fingerprint, infer_search_terms
from .executor import ExecutionError, execute_plan
from .models import RenameOperation, RenamePlan
from .naming import NamingPolicy, VIDEO_EXTENSIONS
from .planner import _season_from_folder, make_plan, normalized_season_folder_name, read_plan, write_plan
from .tmdb import TMDBClient, TMDBError, series_summary
from .workflow import LitePanWorkflow


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
        "/115/00剧/01中", "/115/00剧/01日", "/115/00剧/01韩", "/115/00剧/01美", "/115/00剧/01台", "/115/00剧/01英", "/115/00剧/01欧",
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
    "00剧/01美": {3}, "00剧/01英": {3}, "00剧/01欧": {3}, "00剧/01中": {11},
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
    media_index_root: Path = Path("/opt/docker/litepan/strm")
    litepan_db_path: Path = Path("/opt/docker/litepan/data/litepan.db")
    emby_url: str = "http://127.0.0.1:8097"
    emby_auth_db: Path = Path("/opt/docker/emby/config/data/authentication.db")
    emby_username: str = "zhongyunxing"
    manual_history: Path = Path("/opt/docker/avecove-namer/manual-history.jsonl")
    namer_completed_state: Path = Path("/opt/docker/avecove-namer/namer-completed.json")
    backend_name: str = "openlist"
    litepan_url: str = "http://127.0.0.1:5211"
    litepan_username: str = "zhongyunxing"
    litepan_password_file: Path = Path("/opt/docker/avecove-namer/secrets/litepan.password")
    litepan_roots_file: Path = Path("/opt/docker/avecove-namer/secrets/litepan-roots.json")

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
            media_index_root=Path(os.getenv("AVECOVE_MEDIA_INDEX_ROOT", "/opt/docker/litepan/strm")),
            litepan_db_path=Path(os.getenv("AVECOVE_LITEPAN_DB_PATH", "/opt/docker/litepan/data/litepan.db")),
            emby_url=os.getenv("AVECOVE_EMBY_URL", "http://127.0.0.1:8097"),
            emby_auth_db=Path(os.getenv("AVECOVE_EMBY_AUTH_DB", "/opt/docker/emby/config/data/authentication.db")),
            emby_username=os.getenv("AVECOVE_EMBY_USERNAME", "zhongyunxing"),
            manual_history=Path(os.getenv("AVECOVE_MANUAL_HISTORY", "/opt/docker/avecove-namer/manual-history.jsonl")),
            namer_completed_state=Path(os.getenv("AVECOVE_NAMER_COMPLETED_STATE", "/opt/docker/avecove-namer/namer-completed.json")),
            backend_name=os.getenv("AVECOVE_NAMER_BACKEND", "openlist"),
            litepan_url=os.getenv("AVECOVE_LITEPAN_URL", "http://127.0.0.1:5211"),
            litepan_username=os.getenv("AVECOVE_LITEPAN_USERNAME", "zhongyunxing"),
            litepan_password_file=Path(os.getenv("AVECOVE_LITEPAN_PASSWORD_FILE", "/opt/docker/avecove-namer/secrets/litepan.password")),
            litepan_roots_file=Path(os.getenv("AVECOVE_LITEPAN_ROOTS_FILE", "/opt/docker/avecove-namer/secrets/litepan-roots.json")),
        )


class App:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.settings.work_root.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, dict[str, Any]] = {}
        self.series_shelf = SeriesShelf(self.settings.work_root.parent / 'series-shelf.json', self.shelf_metadata)
        self.handoff_inbox = HandoffInbox(os.getenv('AVECOVE_HANDOFF_INBOX', str(self.settings.work_root.parent / 'handoff-inbox.sqlite3')))
        self._jobs_lock = threading.Lock()
        self._artwork_lock = threading.Lock()
        self._artwork_slots = threading.BoundedSemaphore(2)
        self._artwork_cache: dict[tuple[int, int | None], tuple[float, dict]] = {}
        self._artwork_inflight: dict[tuple[int, int | None], str] = {}
        self._litepan_workflow_lock = threading.Lock()
        self._cloud_cache: dict[str, tuple[float, list[dict[str, object]]]] = {}
        self._cloud_cache_lock = threading.Lock()
        self._manual_history_lock = threading.Lock()
        self._namer_completed_lock = threading.Lock()
        self._namer_pending_cache: dict[tuple[str, str], bool] = {}
        self._strm_check_lock = threading.Lock()
        self._strm_check_results = {}
        self._strm_check_queue = {}
        self._strm_check_running = False

    def _strm_file_difference(self, cloud_path, local_titles):
        entries = self.openlist().scan(cloud_path, refresh=False)
        videos = [entry for entry in entries if PurePosixPath(entry.path).suffix.casefold() in VIDEO_EXTENSIONS]
        files = [file for title in local_titles for file in title.rglob('*.strm')]
        if self.settings.backend_name == 'litepan' and videos and all(entry.file_id and entry.account_id for entry in videos):
            cloud_ids = {(str(entry.account_id), str(entry.file_id)) for entry in videos}
            local_ids = set()
            for file in files:
                identity = LitePanWorkflow.file_identity(file)
                if not identity:
                    return {'verification_error': '存在无法解析的旧 STRM 链接，需单独核验，不按名称判定删除'}
                try:
                    file_id = base64.urlsafe_b64decode(identity[1] + '=' * (-len(identity[1]) % 4)).decode()
                except (ValueError, UnicodeError):
                    return {'verification_error': 'STRM 文件标识无法解析，需单独核验'}
                local_ids.add((identity[0], file_id))
            return {'missing_files': len(cloud_ids - local_ids), 'stale_files': len(local_ids - cloud_ids),
                    'relocated_files': 0, 'cloud_files': len(cloud_ids), 'strm_files': len(local_ids),
                    'comparison': 'litepan_file_id'}
        cloud = {str(PurePosixPath(entry.path).relative_to(cloud_path).with_suffix(''))
                 for entry in entries if PurePosixPath(entry.path).suffix.casefold() in VIDEO_EXTENSIONS}
        local = set()
        for title in local_titles:
            for file in title.rglob('*.strm'):
                relative = file.relative_to(title)
                # LitePan's /n/ segment preserves the original video name,
                # including ISO names that differ from the STRM filename.
                try:
                    parts = urllib.parse.unquote(urlparse(file.read_text().strip()).path).split('/')
                    name = parts[parts.index('n') + 1] if 'n' in parts else relative.name
                except (OSError, ValueError, IndexError):
                    name = relative.name
                local.add(str(PurePosixPath(relative.parent, PurePosixPath(name).stem)))
        if not cloud and local:
            raise ValueError('云盘返回空清单，暂不判定删除，需复核')
        cloud_names = Counter(PurePosixPath(path).name for path in cloud)
        local_names = Counter(PurePosixPath(path).name for path in local)
        moved = len(cloud - local) if cloud_names == local_names else 0
        return {'missing_files': 0 if moved else len(cloud - local),
                'stale_files': 0 if moved else len(local - cloud), 'relocated_files': moved,
                'cloud_files': len(cloud), 'strm_files': len(local)}

    def _run_strm_checks(self):
        try:
            while True:
                with self._strm_check_lock:
                    if not self._strm_check_queue:
                        return
                    key = min(self._strm_check_queue, key=lambda item: len(item[2]))
                    titles = self._strm_check_queue.pop(key)
                try:
                    result = self._strm_file_difference(key[0], titles)
                except Exception as exc:
                    result = {'verification_error': str(exc)[:200]}
                with self._strm_check_lock:
                    self._strm_check_results[key] = (time.monotonic(), result)
                time.sleep(0.25)  # one cloud work at a time, no video downloads
        finally:
            with self._strm_check_lock:
                self._strm_check_running = False

    def _checked_strm_difference(self, path, modified, titles):
        signature = tuple(sorted((str(file), file.stat().st_mtime_ns, file.stat().st_size)
                                 for title in titles for file in title.rglob('*.strm')))
        key = (path, str(modified or ''), signature)
        with self._strm_check_lock:
            cached = self._strm_check_results.get(key)
            if cached and (time.monotonic() - cached[0] < 3600 or self._strm_check_running):
                return cached[1]
            self._strm_check_queue[key] = titles
            if not self._strm_check_running:
                self._strm_check_running = True
                threading.Thread(target=self._run_strm_checks, daemon=True).start()
        return None

    def shelf_metadata(self, ident: int) -> dict[str, Any]:
        client = self.tmdb()
        details = client._get(f'/tv/{ident}', {'language':'zh-CN', 'append_to_response':'content_ratings'})
        language = str(details.get('original_language') or '')
        images = client._get(f'/tv/{ident}/images', {})
        summary = series_summary(details)
        primary = next(iter(summary['content_ratings']), {})
        def image_path(value):
            return value if isinstance(value, str) and re.fullmatch(r'/[\w-]+\.(?:jpg|png|webp)', value) else ''
        def source_image(kind, fallback):
            from .tmdb import ranked_images
            valid = [row for row in images.get(kind, []) if image_path(row.get('file_path'))]
            for code in (language, None):
                ranked = ranked_images([row for row in valid if row.get('iso_639_1') == code])
                if ranked:
                    return ranked[0]['file_path']
            return image_path(fallback)
        return {**summary, 'originalTitle': str(details.get('original_name') or ''),
                'translatedTitle': str(details.get('name') or ''),
                'posterPath': source_image('posters', details.get('poster_path')),
                'backdropPath': source_image('backdrops', details.get('backdrop_path')),
                'artworkLanguage': language,
                'year': str(details.get('first_air_date') or '')[:4],
                'rating': float(details.get('vote_average') or 0),
                'certification': primary.get('rating', ''), 'certificationCountry': primary.get('country', ''),
                'status': str(details.get('status') or '')}

    def tmdb(self) -> TMDBClient:
        return TMDBClient(read_secret(self.settings.tmdb_token_file))

    def openlist(self) -> OpenListBackend | LitePanWebDAVBackend:
        if self.settings.backend_name == "litepan":
            roots = json.loads(self.settings.litepan_roots_file.read_text(encoding="utf-8"))
            return LitePanWebDAVBackend(
                self.settings.litepan_url,
                self.settings.litepan_username,
                read_secret(self.settings.litepan_password_file),
                roots,
            )
        return OpenListBackend(self.settings.openlist_url, read_secret(self.settings.openlist_token_file))

    def start_season_audit(self, payload: dict[str, Any]) -> dict[str, str]:
        with self._jobs_lock:
            if any(job.get("type") == "season_audit" and job.get("status") == "running" for job in self._jobs.values()):
                raise ValueError("季目录巡检已在运行，请等待结果")
            job_id = secrets.token_hex(8)
            self._jobs[job_id] = {"id": job_id, "type": "season_audit", "status": "running",
                                  "total": len(CLOUD_LIBRARY_ROOTS["tv"]), "completed": 0,
                                  "found": 0, "current": None, "started_at": utc_now()}
        threading.Thread(target=self._run_season_audit, args=(job_id,), daemon=True).start()
        return {"job_id": job_id}

    def _run_season_audit(self, job_id: str) -> None:
        backend = self.openlist()
        grouped: dict[str, list[RenameOperation]] = {}
        errors: list[str] = []
        for index, category in enumerate(CLOUD_LIBRARY_ROOTS["tv"], 1):
            with self._jobs_lock:
                self._jobs[job_id].update({"current": category, "completed": index - 1})
            try:
                shows = backend.list_directories(category)
                for show in shows:
                    show_path = str(PurePosixPath(category) / str(show["name"]))
                    try:
                        children = backend.list_directories(show_path)
                    except BackendError as exc:
                        errors.append(f"{show_path}: {exc}")
                        continue
                    sibling_names = {str(child["name"]) for child in children}
                    season_numbers = [_season_from_folder(name) for name in sibling_names]
                    width = 3 if any(number is not None and number >= 100 for number in season_numbers) else 2
                    for child in children:
                        name = str(child["name"])
                        target_name = normalized_season_folder_name(name, width)
                        if target_name is None or name == target_name:
                            continue
                        if target_name in sibling_names:
                            errors.append(f"{show_path}/{name}: 目标 {target_name} 已存在，跳过")
                            continue
                        root = "/" + "/".join(PurePosixPath(category).parts[1:3])
                        grouped.setdefault(root, []).append(RenameOperation(
                            source=f"{show_path}/{name}", target=f"{show_path}/{target_name}",
                            kind="rename_directory", reason="normalized_season_folder", confidence=1.0,
                        ))
            except BackendError as exc:
                errors.append(f"{category}: {exc}")
            with self._jobs_lock:
                self._jobs[job_id].update({"completed": index, "found": sum(map(len, grouped.values())),
                                          "errors": len(errors)})
        plans: list[dict[str, Any]] = []
        for root, operations in grouped.items():
            targets: set[str] = set()
            unique: list[RenameOperation] = []
            for operation in operations:
                if operation.target in targets:
                    errors.append(f"{operation.target}: 多个目录指向同一目标，跳过")
                    continue
                targets.add(operation.target)
                unique.append(operation)
            if not unique:
                continue
            plan = RenamePlan(version=1, created_at=utc_now(), backend=backend.name, root=root,
                              policy={"mode": "season_folders_only"}, operations=unique)
            plan_id = secrets.token_hex(12)
            job_dir = self.settings.work_root / plan_id
            job_dir.mkdir(mode=0o700)
            write_plan(plan, str(job_dir / "plan.json"), str(job_dir / "plan.csv"))
            plans.append({"plan_id": plan_id, "root": root,
                          "operations": [operation.to_dict() for operation in unique]})
        with self._jobs_lock:
            self._jobs[job_id].update({"status": "completed", "current": None, "plans": plans,
                                      "warnings": errors[:50], "error_count": len(errors), "finished_at": utc_now()})

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

    def litepan_changes(self, payload=None):
        """Consume the private bridge inbox, without scanning or renaming files."""
        source = LITEPAN_CHANGES_FILE
        ack_path = source.with_name('namer-change-ack.json')
        if not source.is_file():
            return {'items': [], 'pending': 0, 'ready': False}
        state = json.loads(source.read_text(encoding='utf-8'))
        with self._namer_completed_lock:
            ack = json.loads(ack_path.read_text()) if ack_path.is_file() else {}
            if payload is not None:
                items = payload.get('items', [])
                if not isinstance(items, list) or len(items) > 100:
                    raise ValueError('每次最多确认 100 项')
                for item in items:
                    current = state.get('events', {}).get(str(item.get('id')))
                    if current and item.get('signature') == current['signature']:
                        ack[str(item['id'])] = current['signature']
                ack_path.parent.mkdir(parents=True, exist_ok=True)
                temporary = ack_path.with_suffix('.tmp')
                temporary.write_text(json.dumps(ack), encoding='utf-8')
                temporary.chmod(0o600)
                temporary.replace(ack_path)
        rows = [row for ident, row in state.get('events', {}).items()
                if ack.get(ident) != row['signature']]
        rows.sort(key=lambda row: (-row['detectedAt'], row['path']))
        return {'items': rows[:30], 'pending': len(rows), 'ready': True,
                'updatedAt': state['updatedAt'], 'trackedWorks': len(state.get('known', {}))}

    def library_directories(self, raw_path):
        path = safe_cloud_path(raw_path)
        try:
            directories = self._cloud_directories(self.openlist(), path)
        except BackendError as exc:
            if str(exc).startswith('LitePan directory not found:'):
                return {'path': path, 'directories': [], 'exists': False}
            raise
        return {'path': path, 'directories': [
            {'name': str(item['name']), 'path': str(PurePosixPath(path) / str(item['name']))}
            for item in sorted(directories, key=lambda item: str(item['name']))]}

    def _media_index_for_cloud_root(self, root: str) -> Path:
        """Resolve a cloud category through LitePan's STRM task mapping."""
        if self.settings.backend_name != "litepan" or not self.settings.litepan_db_path.is_file():
            return self.settings.media_index_root / root.lstrip("/")
        provider = PurePosixPath(root).parts[1]
        roots = json.loads(self.settings.litepan_roots_file.read_text(encoding="utf-8"))
        account_root = PurePosixPath(roots.get(provider, ""))
        db_uri = f"file:{self.settings.litepan_db_path}?mode=ro"
        with sqlite3.connect(db_uri, uri=True, timeout=3) as connection:
            rows = connection.execute(
                "SELECT path, group_dir, output_folder FROM strm_tasks WHERE status='active'"
            ).fetchall()
        best: tuple[int, Path] | None = None
        for source, group_dir, output_folder in rows:
            source_parts = PurePosixPath(str(source)).parts
            if source_parts[:len(account_root.parts)] != account_root.parts:
                continue
            virtual_root = PurePosixPath("/") / provider / PurePosixPath(*source_parts[len(account_root.parts):])
            virtual_text = str(virtual_root)
            if root != virtual_text and not root.startswith(virtual_text + "/"):
                continue
            extra = PurePosixPath(root).parts[len(virtual_root.parts):]
            target = self.settings.media_index_root / str(group_dir or "") / str(output_folder or "")
            for part in extra:
                target /= part
            if best is None or len(virtual_root.parts) > best[0]:
                best = (len(virtual_root.parts), target)
        return best[1] if best else self.settings.media_index_root / root.lstrip("/")

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

        # Verify local-index hits against LitePan, avoiding false positives
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
        verifying_count = 0
        verification_errors = []
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
                local_root = self._media_index_for_cloud_root(root)
                local_tmdb_ids: set[str] = set()
                local_names: set[str] = set()
                local_tmdb_latest: dict[str, float] = {}
                local_name_latest: dict[str, float] = {}
                local_by_id = {}
                local_by_name = {}
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
                            local_by_id.setdefault(tmdb_id, []).append(local_title)
                        normalized = normalized_media_name(local_title.name)
                        if normalized:
                            local_names.add(normalized)
                            local_name_latest[normalized] = max(local_name_latest.get(normalized, 0), latest)
                            local_by_name.setdefault(normalized, []).append(local_title)
                for item in directories:
                    name = str(item.get("name") or "").strip()
                    if not name or name.endswith(' - 待入库') or (media_kind == "movie" and name in MOVIE_GROUP_DIRS):
                        continue
                    cloud_titles += 1
                    tmdb_id = media_tmdb_id(name)
                    normalized = normalized_media_name(name)
                    local_latest = local_tmdb_latest.get(tmdb_id or "") or local_name_latest.get(normalized)
                    already_present = (tmdb_id and tmdb_id in local_tmdb_ids) or (normalized and normalized in local_names)
                    path = str(PurePosixPath(root) / name)
                    difference = {}
                    if already_present:
                        present_titles += 1
                        titles = local_by_id.get(tmdb_id) or local_by_name.get(normalized) or []
                        exact = [title for title in titles if title.name == name]
                        difference = self._checked_strm_difference(path, item.get('modified'), exact or titles)
                        if difference is None:
                            verifying_count += 1
                            continue
                        if difference.get('verification_error'):
                            verification_errors.append({'path': path, 'error': difference['verification_error']})
                            continue
                        if not difference['missing_files'] and not difference['stale_files'] and not difference.get('relocated_files'):
                            continue
                    pending.append({
                        "provider": PurePosixPath(root).parts[1],
                        "category": " / ".join(PurePosixPath(root).parts[-2:]) if PurePosixPath(root).name in MOVIE_GROUP_DIRS else PurePosixPath(root).name,
                        "kind": media_kind,
                        "name": name,
                        "path": path,
                        "reason": "subscription_update" if already_present else "new_title",
                        "modified": str(item.get("modified") or ""),
                        **difference,
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
            "new_count": sum(item['reason'] == 'new_title' for item in unique),
            "update_count": sum(item['reason'] == 'subscription_update' for item in unique),
            "verifying_count": verifying_count,
            "verification_errors": verification_errors,
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
            if item["kind"] == "movie":
                children = self.openlist().list_directories(path)
                if children and all(media_tmdb_id(child.get("name")) for child in children):
                    return False
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
        # The inbox is for Emby/Infuse recognition failures, not optional
        # cosmetic reformatting (4K -> 2160p, translated episode titles, etc.).
        remaining = []
        for operation in plan.operations:
            source = PurePosixPath(operation.source)
            if operation.reason == "origin_aware_tmdb_root_folder":
                continue  # A valid year + TMDb ID already exists on this parent.
            if operation.reason == "movie_name" and re.search(r"(?:19|20)\d{2}", source.stem):
                continue
            if operation.reason == "year_aware_episode_name" and re.search(r"(?i)S\d{1,3}E\d{1,3}|S\d{1,3}D\d{1,3}", source.stem):
                continue
            if operation.reason in {"paired_subtitle", "paired_subtitle_variant"}:
                stem = source.stem.casefold()
                if any(PurePosixPath(entry.path).parent == source.parent and
                       stem.startswith(PurePosixPath(entry.path).stem.casefold() + ".")
                       for entry in entries if PurePosixPath(entry.path).suffix.casefold() in {".mkv", ".mp4", ".ts", ".iso"}):
                    continue
            if operation.reason == "normalized_season_folder" and (PATH_SEASON_RE.search(source.name) or PATH_CHINESE_SEASON_RE.search(source.name)):
                continue
            remaining.append(operation)
        unrecognized_video = any(skip.get("reason") == "tv_video_without_episode_or_disc" for skip in plan.skipped)
        if not remaining and not unrecognized_video:
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
                # Raw titles need identification. A named collection whose
                # children already carry TMDb IDs is not itself a film.
                if item["kind"] == "movie":
                    try:
                        children = backend.list_directories(item["path"])
                    except (BackendError, AttributeError):
                        children = []
                    if children and all(media_tmdb_id(child.get("name")) for child in children):
                        filtered += 1
                        continue
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
        markers = ("/litepan-strm/", "/openlist-local-tree/")
        present_tmdb: set[tuple[str, str]] = set()
        present_names: set[tuple[str, str]] = set()
        for strm_path in self.settings.media_index_root.rglob("*.strm") if self.settings.media_index_root.is_dir() else ():
            relative = strm_path.relative_to(self.settings.media_index_root)
            if len(relative.parts) < 4:
                continue
            provider, category, title = self._strm_layout(relative)
            tmdb_id = next((media_tmdb_id(part) for part in relative.parts if media_tmdb_id(part)), None)
            if tmdb_id:
                kind = 'Movie' if relative.parts[0] in {'06好莱坞', '07私人订制'} or '00影' in relative.parts else 'Series'
                present_tmdb.add((kind, tmdb_id))
            normalized = normalized_media_name(title)
            if normalized:
                present_names.add((category, normalized))
            for part in relative.parts[2:-1]:
                normalized = normalized_media_name(part)
                if normalized:
                    present_names.add((category, normalized))
        for item in data.get("Items", []) if isinstance(data, dict) else []:
            emby_path = str(item.get("Path") or "")
            marker = next((marker for marker in markers if marker in emby_path), None)
            if marker is None:
                continue
            relative = emby_path.split(marker, 1)[1].strip("/")
            local_path = self.settings.media_index_root / relative
            item_type = str(item.get("Type") or "")
            exists = local_path.is_file() if item_type == "Movie" else local_path.is_dir() and any(local_path.rglob("*.strm"))
            if marker == '/litepan-strm/' and item_type == 'Series' and len(PurePosixPath(relative).parts) == 2:
                provider, _, _ = self._strm_layout(PurePosixPath(relative))
                items.append({'id': str(item.get('Id') or ''), 'name': str(item.get('Name') or ''),
                    'type': item_type, 'provider': provider, 'path': '/' + relative,
                    'reason': '网盘分类目录被误识别为剧集，需要修正库入口'})
                continue
            if exists:
                continue
            parts = PurePosixPath(relative).parts
            provider, category, _ = self._strm_layout(PurePosixPath(relative))
            provider_ids = item.get("ProviderIds") if isinstance(item.get("ProviderIds"), dict) else {}
            tmdb_id = str(provider_ids.get("Tmdb") or provider_ids.get("TMDb") or "")
            normalized = normalized_media_name(item.get("Name"))
            replaced = bool((tmdb_id and (item_type, tmdb_id) in present_tmdb) or (normalized and (category, normalized) in present_names))
            items.append({
                "id": str(item.get("Id") or ""), "name": str(item.get("Name") or PurePosixPath(relative).name),
                "type": item_type, "provider": provider, "path": "/" + relative,
                "emby_path": emby_path,
                "reason": "旧路径索引，资源已在新目录" if replaced else "本地 STRM 缺失，云盘状态待核验",
            })
        items.sort(key=lambda item: (item["provider"].casefold(), item["name"].casefold()))
        decisions = self._residual_decisions()
        for item in items:
            item['review'] = decisions.get(item['id'] + '|' + item['path'], {}).get('action', 'unreviewed')
        return {"count": len(items), "items": items}

    def _residual_decisions(self):
        path = self.settings.work_root / 'emby-residual-decisions.json'
        try:
            return json.loads(path.read_text()) if path.is_file() else {}
        except (OSError, ValueError):
            return {}

    def review_emby_residual(self, payload):
        item_id = str(payload.get('id') or '')
        action = str(payload.get('action') or '')
        if not item_id.isdigit() or action not in {'keep', 'clean', 'verify'}:
            raise ValueError('核验操作无效')
        # Re-read current Emby and local paths; a stale browser card is not authority to delete.
        item = next((item for item in self.emby_residuals()['items'] if item['id'] == item_id), None)
        if not item:
            return {'status': 'resolved', 'message': '该项目已恢复或不再是残留，无需清理'}
        if payload.get('path') != item['path']:
            raise ValueError('项目路径已变化，请重新核验')
        if action == 'clean':
            if payload.get('confirmation') != f'清理旧索引 {item_id}':
                raise ValueError('请确认清理旧索引')
            relative = item['path'].lstrip('/')
            local = (self.settings.media_index_root / relative).resolve()
            if self.settings.media_index_root.resolve() not in local.parents or '..' in PurePosixPath(relative).parts:
                raise ValueError('项目路径不安全')
            if local.exists():
                raise ValueError('本地目录或文件仍存在，已阻止清理；请先修复库入口或同步')
            flow = LitePanWorkflow(self.settings, lambda message: None, self._emby_token)
            with self._litepan_workflow_lock:
                flow.save_json(self.settings.work_root / 'residual-reviews' / f'{item_id}-{secrets.token_hex(4)}.json',
                               {'item': item, 'action': action, 'created_at': utc_now()})
                pending = self.settings.work_root / 'litepan-emby-pending.json'
                backlog = json.loads(pending.read_text()) if pending.is_file() else []
                emby_path = item.get('emby_path') or '/data/litepan-strm/' + relative
                if not emby_path.startswith(('/data/litepan-strm/', '/data/openlist-local-tree/')):
                    raise ValueError('Emby 媒体路径不在受管理目录内')
                flow.notify_pending(backlog + [{'Path': emby_path, 'UpdateType': 'Deleted'}], pending)
            return {'status': 'submitted', 'message': '已提交旧索引清理，等待 Emby 更新；未删除云盘视频或 STRM'}
        with self._manual_history_lock:
            decisions = self._residual_decisions()
            key = item_id + '|' + item['path']
            if action == 'keep':
                decisions[key] = {'action': 'keep', 'created_at': utc_now()}
            else:
                decisions.pop(key, None)
            LitePanWorkflow(self.settings, lambda message: None, self._emby_token).save_json(
                self.settings.work_root / 'emby-residual-decisions.json', decisions)
        return {'status': 'kept' if action == 'keep' else 'review', 'item': item,
                'message': '已保留，不执行删除' if action == 'keep' else item['reason']}

    def review_namer_plan(self, payload):
        plan_id = str(payload.get('plan_id') or '')
        if not re.fullmatch(r'[0-9a-f]{24}', plan_id):
            raise ValueError('改名计划无效')
        directory = self.settings.work_root / plan_id
        if (directory / '.apply-started').exists():
            raise ValueError('计划已执行，不能再修改')
        plan = read_plan(str(directory / 'plan.json'))
        excluded = payload.get('exclude_indices', [])
        if not isinstance(excluded, list) or any(type(i) is not int or i < 0 or i >= len(plan.operations) for i in excluded):
            raise ValueError('选择的操作无效')
        backend = self.openlist()
        selected = [op for i, op in enumerate(plan.operations) if i not in excluded]
        counts = Counter(op.target for op in selected)
        safe = []
        for op in selected:
            if payload.get('skip_conflicts') and (counts[op.target] > 1 or backend.exists(op.target)):
                plan.skipped.append({'path': op.source, 'reason': '用户选择跳过冲突，保留原文件'})
            else:
                safe.append(op)
        for i in excluded:
            plan.skipped.append({'path': plan.operations[i].source, 'reason': '用户选择保留原名称'})
        plan.operations = safe
        plan.conflicts = [conflict for conflict in plan.conflicts
                          if not conflict.startswith(('Duplicate target:', 'Target exists:'))]
        counts = Counter(op.target for op in safe)
        for op in safe:
            if counts[op.target] > 1:
                plan.conflicts.append(f'Duplicate target: {op.target}')
            elif backend.exists(op.target):
                plan.conflicts.append(f'Target exists: {op.target}')
        new_id = secrets.token_hex(12)
        output = self.settings.work_root / new_id
        output.mkdir(mode=0o700, parents=True)
        write_plan(plan, str(output / 'plan.json'), str(output / 'plan.csv'))
        return {'plan_id': new_id, **plan.to_dict()}

    def clean_emby_residuals(self, payload):
        ids = payload.get('ids')
        if not isinstance(ids, list) or not ids or len(ids) > 200 or any(not str(i).isdigit() for i in ids):
            raise ValueError('请选择 1 至 200 项旧索引')
        ids = list(dict.fromkeys(str(i) for i in ids))
        if payload.get('confirmation') != f'清理 {len(ids)} 项旧索引':
            raise ValueError('批量清理确认文字不正确')
        current = {item['id']: item for item in self.emby_residuals()['items']}
        updates, reviewed, skipped = [], [], []
        base = self.settings.media_index_root.resolve()
        for item_id in ids:
            item = current.get(item_id)
            if not item:
                skipped.append({'id': item_id, 'reason': '已恢复或不再是残留'})
                continue
            if item.get('review') == 'keep':
                skipped.append({'id': item_id, 'reason': '已选择保留'})
                continue
            relative = item['path'].lstrip('/')
            local = (base / relative).resolve()
            emby_path = item.get('emby_path') or '/data/litepan-strm/' + relative
            if base not in local.parents or '..' in PurePosixPath(relative).parts or local.exists():
                skipped.append({'id': item_id, 'reason': '本地路径仍存在或不安全，需要先修复库入口'})
                continue
            if not emby_path.startswith(('/data/litepan-strm/', '/data/openlist-local-tree/')):
                skipped.append({'id': item_id, 'reason': '不在受管理目录内'})
                continue
            updates.append({'Path': emby_path, 'UpdateType': 'Deleted'})
            reviewed.append(item)
        if updates:
            flow = LitePanWorkflow(self.settings, lambda message: None, self._emby_token)
            with self._litepan_workflow_lock:
                flow.save_json(self.settings.work_root / 'residual-reviews' / f'batch-{secrets.token_hex(8)}.json',
                               {'items': reviewed, 'created_at': utc_now(), 'action': 'clean'})
                pending = self.settings.work_root / 'litepan-emby-pending.json'
                backlog = json.loads(pending.read_text()) if pending.is_file() else []
                flow.notify_pending(backlog + updates, pending)
        return {'submitted': len(reviewed), 'submitted_ids': [item['id'] for item in reviewed], 'skipped': skipped,
                'message': f'已提交 {len(reviewed)} 项旧索引清理，跳过 {len(skipped)} 项；云盘视频和本地 STRM 均未删除'}

    @staticmethod
    def _strm_layout(relative):
        parts = relative.parts
        if parts and parts[0] in {'115', '123', 'GuangYa', 'Baidu', 'Quark'}:
            return parts[0], parts[2] if len(parts) > 2 else '', parts[3] if len(parts) > 3 else ''
        bucket = parts[1] if len(parts) > 1 else ''
        provider = bucket.split('-', 1)[0]
        return provider or '其他', parts[0] if parts else '', parts[2] if len(parts) > 2 else ''

    def _strm_identity(self, relative: Path) -> tuple[str, str] | None:
        if self.settings.backend_name == 'litepan':
            identity = LitePanWorkflow.file_identity(self.settings.media_index_root / relative)
            if identity is None:
                return None  # Unknown/legacy URLs require review, never filename-only deletion.
            provider, category, title = self._strm_layout(relative)
            return f'{category}|file|{identity[0]}:{identity[1]}||', title or relative.stem
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
                "provider": self._strm_layout(keep.relative_to(root))[0],
                "category": self._strm_layout(keep.relative_to(root))[1],
                "series": self._strm_layout(keep.relative_to(root))[2],
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
        if self.settings.backend_name == 'litepan':
            for group in selected:
                keep = root / group['keep']
                identity = LitePanWorkflow.file_identity(keep)
                if not identity or not keep.is_file():
                    raise ValueError('保留项已变化，请重新检测')
                for value in group['remove']:
                    if LitePanWorkflow.file_identity(root / value) != identity:
                        raise ValueError('旧检测计划并非同一云盘文件，已阻止清理；请重新检测')
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
        if self.settings.backend_name == 'litepan':
            flow = LitePanWorkflow(self.settings, lambda message: None, self._emby_token)
            flow.save_json(quarantine / 'manifest.json', {'groups': selected, 'moved': moved})
            updates = [{'Path': '/data/litepan-strm/' + value, 'UpdateType': 'Deleted'} for value in moved]
            pending = self.settings.work_root / 'litepan-emby-pending.json'
            with self._litepan_workflow_lock:
                backlog = json.loads(pending.read_text()) if pending.exists() else []
                accepted = flow.notify_pending(backlog + updates, pending)
            return {'moved': len(moved), 'quarantine': str(quarantine), 'refreshed_libraries': [], 'accepted_updates': accepted}
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

    def start_artwork(self, payload: dict[str, Any]) -> dict[str, Any]:
        value = str(payload.get("tmdb_id") or "").strip()
        raw_season = payload.get("season")
        if not value.isdecimal() or not 0 < int(value) < 2147483648:
            raise ValueError("请输入有效的 TMDb 剧集编号")
        season = None
        if raw_season is not None and raw_season != "":
            if not re.fullmatch(r"\d{1,3}", str(raw_season)):
                raise ValueError("季号须为 0–999")
            season = int(raw_season)
        key = (int(value), season)
        with self._artwork_lock:
            cached = self._artwork_cache.get(key)
            if cached and time.monotonic() - cached[0] < 900:
                return {"data": cached[1], "cached": True}
            if key in self._artwork_inflight:
                return {"job_id": self._artwork_inflight[key]}
            job_id = secrets.token_hex(8)
            with self._jobs_lock:
                self._jobs[job_id] = {"id": job_id, "type": "tmdb_artwork", "status": "running",
                                      "current": "正在读取剧集与图片", "completed": 0, "total": 0}
            self._artwork_inflight[key] = job_id
        threading.Thread(target=self._run_artwork, args=(job_id, key), daemon=True).start()
        return {"job_id": job_id}

    def _run_artwork(self, job_id, key):
        def progress(completed, total):
            with self._jobs_lock:
                self._jobs[job_id].update(completed=completed, total=total,
                    current=f"正在汇总本季剧照 {completed}/{total} 集")
        try:
            with self._artwork_slots:
                data = self.tmdb().series_artwork(*key, on_progress=progress)
            data["recommended_name"] = recommended_folder_name({"id": key[0], "original_title": data["original_name"],
                "year": data.get("year"), "language": data["original_language"]})
            if not data.get("warnings"):
                with self._artwork_lock:
                    if len(self._artwork_cache) >= 64:
                        self._artwork_cache.pop(next(iter(self._artwork_cache)))
                    self._artwork_cache[key] = (time.monotonic(), data)
            with self._jobs_lock:
                self._jobs[job_id].update(status="completed", data=data, current="读取完成")
        except Exception as exc:
            with self._jobs_lock:
                self._jobs[job_id].update(status="failed", output=str(exc))
        finally:
            with self._artwork_lock:
                self._artwork_inflight.pop(key, None)

    def tmdb_image(self, file_path: str) -> tuple[bytes, str]:
        if not re.fullmatch(r"/[A-Za-z0-9_-]+\.(?:jpg|png|webp)", file_path, re.IGNORECASE):
            raise ValueError("图片路径无效")
        request = urllib.request.Request("https://image.tmdb.org/t/p/original" + file_path,
                                         headers={"User-Agent": "AveCove-Namer/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                content_type = response.headers.get_content_type()
                if content_type not in {"image/jpeg", "image/png", "image/webp"}:
                    raise ValueError("上游没有返回有效图片")
                body = response.read(20 * 1024 * 1024 + 1)
                if len(body) > 20 * 1024 * 1024:
                    raise ValueError("图片超过 20 MB，请打开原图保存")
                return body, content_type
        except urllib.error.URLError as exc:
            raise TMDBError("原图下载失败，请重试或直接打开原图") from exc

    def episode_details(self, payload: dict[str, Any]) -> dict[str, Any]:
        series_id = str(payload.get("tmdb_id") or "").strip()
        code = str(payload.get("code") or "").strip()
        match = re.fullmatch(r"S\s*(\d{1,3})\s*E\s*(\d{1,4})", code, re.IGNORECASE)
        if not series_id.isdecimal() or int(series_id) < 1 or not match:
            raise ValueError("请输入有效的 TMDb 剧集编号和季集号，例如 S01E02")
        season, episode = map(int, match.groups())
        if episode < 1:
            raise ValueError("集号须从 1 开始；特别篇可使用 S00E01")
        return self.tmdb().episode_details(int(series_id), season, episode)

    def series_summary(self, payload: dict[str, Any]) -> dict[str, Any]:
        ident = str(payload.get('tmdb_id') or '').strip()
        if not ident.isdecimal() or not 0 < int(ident) < 100000000:
            raise ValueError('请输入有效的 TMDb 剧集编号')
        details = self.tmdb().details(int(ident), 'tv', 'zh-CN')
        return {'tmdb_id': int(ident), **series_summary(details)}

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

    def _collection_preview(self, payload: dict[str, Any], entries=None) -> dict[str, Any]:
        path = safe_cloud_path(payload.get("path"))
        if path.rstrip("/") in BROAD_NAMER_PATHS:
            raise ValueError("请选择具体合集，不能把整个分类当成合集")
        backend, client = self.openlist(), self.tmdb()
        if entries is None:
            entries = backend.scan(path, refresh=True)
        works = discover_movie_works(str(PurePosixPath(path).parent), path, entries)
        works = [(root, rows) for root, rows in works if root != path]
        if len(works) > 60:
            raise ValueError("合集一次最多识别 60 部影片，请分批处理")
        collection_id = int(payload.get("collection_id") or payload.get("tmdb_id") or 0)
        collection = client.collection(collection_id) if collection_id else {}
        allowed = {int(part['id']) for part in collection.get('parts', [])}
        plan = RenamePlan(1, utc_now(), backend.name, path, NamingPolicy().as_dict())
        plan.policy['collection_id'] = collection_id
        members, present = [], set()
        for root, rows in works:
            try:
                tagged = media_tmdb_id(PurePosixPath(root).name)
                movie_id = int(tagged) if tagged else _find_movie_tmdb_id(client, PurePosixPath(root).name, rows)[0]
                if not movie_id:
                    raise ValueError('未能高置信度识别，保留原目录')
                if allowed and movie_id not in allowed:
                    raise ValueError('不属于指定 TMDB 合集，保留原目录')
                resolved = client.resolve_title(movie_id, 'movie', 'original')
                if not resolved.get('year') or not resolved.get('original_title'):
                    raise ValueError('源名称或年份缺失，保留原目录')
                present.add(movie_id)
                language = 'zh' if resolved.get('original_language') in {'zh', 'cn', 'yue'} else 'en'
                child = make_plan(rows, root, backend.name, NamingPolicy(), str(resolved['original_title']),
                    int(resolved['year']), True, movie_id, language, 'movie',
                    root_title_override=str(resolved['original_title']))
                plan.operations.extend(child.operations)
                plan.conflicts.extend(child.conflicts)
                plan.skipped.extend(child.skipped)
                members.append({'path': root, 'tmdb_id': movie_id, 'title': resolved['original_title'],
                    'year': resolved['year'], 'status': 'identified'})
            except (ValueError, TMDBError) as exc:
                plan.skipped.append({'path': root, 'reason': str(exc)})
                members.append({'path': root, 'status': 'unresolved', 'reason': str(exc)})
        missing = []
        for part in collection.get('parts', []):
            if int(part['id']) in present:
                continue
            original = str(part.get('original_title') or part.get('title') or '').strip()
            date = str(part.get('release_date') or '')
            if not original or not re.match(r'^\d{4}', date):
                continue
            name = re.sub(r'[\\/:*?"<>|]', ' ', original).strip() + f"（{date[:4]}） - 待入库"
            target = str(PurePosixPath(path) / name)
            missing.append({'tmdb_id': part['id'], 'title': original, 'path': target})
            if payload.get('fill_missing', True) and not backend.exists(target):
                plan.operations.append(RenameOperation(path, target, 'create_directory', 'collection_missing_placeholder', 1.0))
        targets = Counter(op.target for op in plan.operations)
        for op in plan.operations:
            if targets[op.target] > 1:
                plan.conflicts.append(f'Duplicate target: {op.target}')
            if op.kind == 'rename_directory' and backend.exists(op.target):
                plan.conflicts.append(f'Target exists: {op.target}')
        plan.conflicts = sorted(set(plan.conflicts))
        plan.operations.sort(key=lambda op: (op.kind in {'rename_directory', 'create_directory'},
            -len(PurePosixPath(op.source).parts), op.source))
        return {'kind': 'collection', 'path': path, 'tmdb_id': collection_id, 'collection_id': collection_id,
            'resolved': {'title': collection.get('name') or PurePosixPath(path).name},
            'members': members, 'missing': missing, 'recommended_name': PurePosixPath(path).name,
            'reason': '保留外层合集；逐部使用源语言子目录，视频使用英文；空目录不入 Emby',
            'plan': plan}

    def identify_path(self, payload: dict[str, Any]) -> dict[str, Any]:
        path = safe_cloud_path(payload.get("path"))
        if path.rstrip("/") in BROAD_NAMER_PATHS:
            raise ValueError("请在常用路径后继续填写具体作品文件夹，避免扫描整个媒体库")
        kind = str(payload.get("kind") or "tv")
        style = str(payload.get("title_style") or "auto")
        source_parent = bool(payload.get("source_parent"))
        if kind == 'collection':
            result = self._collection_preview(payload)
            result.pop('plan')
            return result
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
                if kind == 'movie' and not media_tmdb_id(PurePosixPath(path).name):
                    works = discover_movie_works(str(PurePosixPath(path).parent), path, entries)
                    if len(works) > 1 and all(root != path for root, _ in works):
                        result = self._collection_preview({**payload, 'kind': 'collection'}, entries)
                        result.pop('plan')
                        return result
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
        child_title = parent_title
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
        if payload.get('kind') == 'collection':
            result = self._collection_preview(payload)
            plan = result.pop('plan')
            plan_id = secrets.token_hex(12)
            job_dir = self.settings.work_root / plan_id
            job_dir.mkdir(mode=0o700, parents=True)
            write_plan(plan, str(job_dir / 'plan.json'), str(job_dir / 'plan.csv'))
            return {**result, **plan.to_dict(), 'plan_id': plan_id}
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
        child_title = parent_title
        parent_language = (
            "zh" if str(resolved.get("original_language") or "").casefold() in {"zh", "cn", "yue"} else "en"
        ) if source_parent else str(resolved["primary_language"])
        backend = self.openlist()
        try:
            entries = backend.scan(path, refresh=True)
        except BackendError as exc:
            raise ValueError("名称已可识别，但 LitePan 尚未同步到该目录，暂时无法执行改名。请先在 LitePan 刷新该网盘后再生成预览。") from exc
        if kind == 'movie':
            works = discover_movie_works(str(PurePosixPath(path).parent), path, entries)
            if len(works) > 1 and all(root != path for root, _ in works):
                raise ValueError("此目录包含多部独立电影，不能统一套用一个电影编号。请切换‘电影合集’模式，逐部识别后再生成预览。")
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
                if plan.policy.get('mode') == 'season_folders_only':
                    refresh_paths.extend(str(PurePosixPath(operation.target).parent) for operation in plan.operations)
                else:
                    refresh_paths.append(final_path)
                if plan.policy.get("mode") != "season_folders_only":
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
        refresh_job = None
        summaries.append("改名完成；Emby 自动刷新已移除")
        with self._jobs_lock:
            self._jobs[job_id].update({
                "status": "completed" if failed == 0 else "failed", "current": None,
                "refresh_paths": refresh_paths, "finished_at": utc_now(),
                "refresh_job": refresh_job, "output": "\n".join(summaries[-30:]),
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
            refresh_job = None
            update = {
                "status": "completed",
                "operations": len(completed),
                "completed_operations": len(completed),
                "current_operation": None,
                "path": final_path,
                "journal": str(journal),
                "refresh_job": refresh_job,
                "output": "改名完成；Emby 自动刷新已移除",
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
        if self.settings.backend_name == "litepan":
            return self.start_refresh_queue({"paths": [media_path]})
        if self.settings.backend_name != "litepan" and not self.settings.refresh_worker.is_file():
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
        if self.settings.backend_name != "litepan" and not self.settings.refresh_worker.is_file():
            raise ValueError("服务器未配置 Emby 刷新程序")
        job_id = secrets.token_hex(8)
        with self._jobs_lock:
            if any(job.get("type") in {"emby_refresh", "emby_refresh_queue"} and job.get("status") == "running" for job in self._jobs.values()):
                if not payload.get("after_namer"):
                    raise ValueError("已有一个 Emby 同步任务正在运行，请完成后再启动队列")
            self._jobs[job_id] = {
                "id": job_id, "type": "emby_refresh_queue", "paths": paths,
                "status": "running", "started_at": utc_now(), "total": len(paths),
                "completed": 0, "failed": 0, "current": paths[0], "output": "",
            }
        threading.Thread(target=self._run_refresh_queue, args=(job_id, paths), daemon=True).start()
        return job_id

    def _persist_refresh_job(self, job_id):
        with self._jobs_lock:
            data = dict(self._jobs[job_id])
        folder = self.settings.work_root / 'refresh-jobs'
        folder.mkdir(exist_ok=True)
        temporary = folder / (job_id + '.tmp')
        temporary.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        temporary.replace(folder / (job_id + '.json'))

    def _run_refresh_queue(self, job_id: str, paths: list[str]) -> None:
        if self.settings.backend_name == "litepan":
            messages = []
            def report(message):
                if messages and messages[-1] == message:
                    return
                messages.append(message)
                with self._jobs_lock:
                    self._jobs[job_id].update({"output": "\n".join(messages[-30:]), "current": message})
                self._persist_refresh_job(job_id)
            report("等待同步队列；关闭页面不会中断服务器任务")
            def progress(**fields):
                with self._jobs_lock:
                    self._jobs[job_id].update(fields)
                self._persist_refresh_job(job_id)
            try:
                with self._litepan_workflow_lock:
                    changed = LitePanWorkflow(self.settings, report, self._emby_token, progress).run(paths)
                    with self._strm_check_lock:
                        self._strm_check_results.clear()
                        self._strm_check_queue.clear()
                    with self._cloud_cache_lock:
                        self._cloud_cache.clear()
                update = {"status": "completed", "changed_files": changed}
            except Exception as exc:
                report(f"同步失败：{exc}。可重试 Emby 同步，无需重新执行 Namer 改名。")
                with self._jobs_lock:
                    failed_count = max(1, self._jobs[job_id].get("failed", 0))
                update = {"status": "failed", "failed": failed_count}
            with self._jobs_lock:
                self._jobs[job_id].update({**update, "current": None, "finished_at": utc_now()})
            self._persist_refresh_job(job_id)
            return
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
                saved = self.settings.work_root / 'refresh-jobs' / (job_id + '.json')
                if job_id.isalnum() and saved.is_file():
                    data = json.loads(saved.read_text())
                    if data.get('status') == 'running':
                        data.update(status='interrupted', output='服务重启后任务已中断。请重新检测后重试同步，不要重复改名。')
                    return data
                return {"id": job_id, "status": "interrupted", "output": "此任务记录已因服务更新失效，不再显示为执行中。请重新检测差异后，仅重试待同步项目；不要重复执行改名。"}
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

        def binary_response(self, status: int, body: bytes, content_type: str, filename: str | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            if filename:
                self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + urllib.parse.quote(filename, safe=""))
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
                    from . import __version__
                    marker = Path(__file__).resolve().parents[2] / '.deployed-commit'
                    revision = marker.read_text().strip() if marker.is_file() else None
                    with app._jobs_lock:
                        active = sum(job.get('status') == 'running' for job in app._jobs.values())
                    self.json_response(HTTPStatus.OK, {"status": "ok", "service": "AveCove Namer",
                        "version": __version__, "revision": revision, "activeJobs": active})
                    return
                if path == '/api/tmdb/shelf':
                    self.json_response(HTTPStatus.OK, app.series_shelf.request())
                    return
                if path == "/api/tmdb/image":
                    query = parse_qs(urlparse(self.path).query)
                    file_path = query.get("path", [""])[0]
                    body, content_type = app.tmdb_image(file_path)
                    name = re.sub(r'[^\w.() -]', '_', query.get("name", ["tmdb-image"])[0])[:120]
                    suffix = PurePosixPath(file_path).suffix.lower()
                    self.binary_response(HTTPStatus.OK, body, content_type, (name or "tmdb-image") + suffix)
                    return
                if path == "/api/detective":
                    self.json_response(HTTPStatus.OK, app.detective())
                    return
                if path == "/api/automation":
                    self.json_response(HTTPStatus.OK, app.automation_status())
                    return
                if path == '/api/library/directories':
                    raw_path = parse_qs(urlparse(self.path).query).get('path', [''])[0]
                    self.json_response(HTTPStatus.OK, app.library_directories(raw_path))
                    return
                if path == '/api/litepan/changes':
                    self.json_response(HTTPStatus.OK, app.litepan_changes())
                    return
                if path == '/api/namer/handoff':
                    self.json_response(HTTPStatus.OK, app.handoff_inbox.request())
                    return
                if path == "/api/namer/pending":
                    self.json_response(HTTPStatus.OK, app.namer_pending())
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
                    "/api/namer/handoff/ack": app.handoff_inbox.request,
                    "/api/tmdb/shelf": app.series_shelf.request,
                    "/api/litepan/changes/ack": app.litepan_changes,
                    "/api/tmdb/search": app.search,
                    "/api/tmdb/source": app.source_episodes,
                    "/api/tmdb/episode": app.episode_details,
                    "/api/tmdb/summary": app.series_summary,
                    "/api/tmdb/artwork": app.start_artwork,
                    "/api/library/search": app.library_search,
                    "/api/automation": app.set_automation,
                    "/api/namer/plan": app.create_plan,
                    "/api/namer/plan/review": app.review_namer_plan,
                    "/api/namer/seasons/scan": app.start_season_audit,
                    "/api/namer/identify": app.identify_path,
                    "/api/namer/apply": app.apply_plan,
                    "/api/namer/batch/apply": app.apply_batch,
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
    parser = argparse.ArgumentParser(description="AveCove Namer private web UI")
    parser.add_argument("--host", default=os.getenv("AVECOVE_WEB_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("AVECOVE_WEB_PORT", "8787")))
    args = parser.parse_args(argv)
    settings = Settings.from_env(args.host, args.port)
    server = ThreadingHTTPServer((settings.host, settings.port), handler_factory(App(settings)))
    print(f"AveCove Namer listening on http://{settings.host}:{settings.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

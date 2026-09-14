from __future__ import annotations

import argparse
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
from .detective import _find_movie_tmdb_id, _find_tv_tmdb_id
from .executor import ExecutionError, execute_plan
from .models import RenamePlan
from .naming import NamingPolicy
from .planner import make_plan, read_plan, write_plan
from .tmdb import TMDBClient, TMDBError


MAX_BODY = 32 * 1024
ALLOWED_ROOTS = ("/115/", "/Baidu/", "/Quark/", "/123/", "/GuangYa/")
BROAD_NAMER_PATHS = {
    "/115/00剧/01美",
    "/115/00剧/01中",
    "/115/00影/01国",
    "/115/00影/01外",
    "/123/00剧/01美",
    "/123/00剧/01中",
    "/123/00影/01国",
    "/123/00影/01外",
    "/GuangYa/00剧/01美",
    "/GuangYa/00剧/01中",
    "/GuangYa/00剧/01英",
    "/GuangYa/00剧/01韩",
    "/GuangYa/00影/01国",
    "/GuangYa/00影/01外",
    "/Baidu/00剧/01美",
    "/Baidu/00剧/01中",
    "/Baidu/00影/01国",
    "/Baidu/00影/01外",
}

CLOUD_LIBRARY_ROOTS = {
    "tv": (
        "/115/00剧/01中", "/115/00剧/01日", "/115/00剧/01韩", "/115/00剧/01美", "/115/00剧/01英", "/115/00剧/02其他",
        "/123/00剧/01中", "/123/00剧/01日", "/123/00剧/01韩", "/123/00剧/01美", "/123/00剧/01英",
        "/GuangYa/00剧/01中", "/GuangYa/00剧/01日", "/GuangYa/00剧/01韩", "/GuangYa/00剧/01美", "/GuangYa/00剧/01英",
        "/Baidu/00剧/01中", "/Baidu/00剧/01日", "/Baidu/00剧/01韩", "/Baidu/00剧/01美", "/Baidu/00剧/01英",
        "/Quark/00剧/01中", "/Quark/00剧/01日", "/Quark/00剧/01韩", "/Quark/00剧/01美", "/Quark/00剧/01英",
    ),
    "movie": (
        "/115/00影/01国", "/115/00影/01外", "/123/00影/01国", "/123/00影/01外",
        "/GuangYa/00影/01国", "/GuangYa/00影/01外", "/Baidu/00影/01国", "/Baidu/00影/01外",
        "/Quark/00影/01国", "/Quark/00影/01外", "/Quark/00影",
    ),
}


def normalized_media_name(value: object) -> str:
    text = re.sub(r"\{\s*tmdb\s*=\s*\d+\s*\}", "", str(value or ""), flags=re.IGNORECASE)
    text = re.sub(r"[（(](?:19|20)\d{2}[）)]", "", text)
    return "".join(character for character in text.casefold() if character.isalnum())


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
        )


class App:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.settings.work_root.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, dict[str, Any]] = {}
        self._jobs_lock = threading.Lock()
        self._cloud_cache: dict[str, tuple[float, list[dict[str, object]]]] = {}
        self._cloud_cache_lock = threading.Lock()

    def tmdb(self) -> TMDBClient:
        return TMDBClient(read_secret(self.settings.tmdb_token_file))

    def openlist(self) -> OpenListBackend:
        return OpenListBackend(self.settings.openlist_url, read_secret(self.settings.openlist_token_file))

    def search(self, payload: dict[str, Any]) -> dict[str, Any]:
        query = str(payload.get("query") or "").strip()
        if not query:
            raise ValueError("请输入剧名或电影名")
        kind = str(payload.get("kind") or "tv")
        year_value = payload.get("year")
        year = int(year_value) if year_value else None
        results = self.tmdb().search(query, kind, year, "zh-CN")
        for item in results:
            item["recommended_name"] = recommended_folder_name(item)
        return {"results": results}

    def _availability_aliases(self, query: str, kind: str) -> set[str]:
        aliases = {normalized_media_name(query)}
        try:
            for item in self.tmdb().search(query, kind, None, "zh-CN")[:3]:
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
            selected_kind = "tv" if kind == "all" else kind
            items = self.tmdb().trending(selected_kind, "zh-CN")
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
        results = client.search(value, "tv", None, "zh-CN")
        if not results:
            raise ValueError("没有找到该剧")
        exact = [item for item in results if value.casefold() in {str(item.get("title") or "").casefold(), str(item.get("original_title") or "").casefold()}]
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
        if supplied_id:
            tmdb_id = supplied_id
        else:
            entries = self.openlist().scan(path, refresh=True)
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
        return {
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
            "resolved": resolved,
        }

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
        entries = backend.scan(path)
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
                "status": "running",
                "started_at": utc_now(),
            }
        threading.Thread(target=self._run_apply, args=(job_id, plan_id, plan), daemon=True).start()
        return {"status": "running", "job_id": job_id}

    def _run_apply(self, job_id: str, plan_id: str, plan: RenamePlan) -> None:
        journal = self.settings.work_root / plan_id / "rollback.jsonl"
        try:
            completed = execute_plan(
                plan,
                self.openlist(),
                str(journal),
                execute=True,
                confirm_root=plan.root,
                confirm_count=len(plan.operations),
            )
            final_path = plan.root
            for operation in plan.operations:
                if operation.kind == "rename_directory" and operation.source == plan.root:
                    final_path = operation.target
                    break
            refresh_job = self.start_refresh(final_path)
            update = {
                "status": "completed",
                "operations": len(completed),
                "path": final_path,
                "journal": str(journal),
                "refresh_job": refresh_job,
                "finished_at": utc_now(),
            }
        except Exception as exc:
            update = {"status": "failed", "output": str(exc), "finished_at": utc_now()}
        with self._jobs_lock:
            self._jobs[job_id].update(update)

    def detective(self) -> dict[str, Any]:
        candidates = []
        if self.settings.detective_summary.is_file():
            candidates.append(self.settings.detective_summary)
        candidates.extend(sorted(self.settings.detective_summary.parent.glob("*/last-summary.json")))
        candidates = list(dict.fromkeys(candidates))
        if not candidates:
            return {"created_at": None, "events": [], "counts": {}}
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
        return {"created_at": max(created_values, default="") or None, "events": events, "counts": counts}

    def start_refresh(self, prefix: object) -> str:
        media_path = safe_cloud_path(prefix)
        if not self.settings.refresh_worker.is_file():
            raise ValueError("服务器未配置 Emby 刷新程序")
        job_id = secrets.token_hex(8)
        with self._jobs_lock:
            self._jobs[job_id] = {"id": job_id, "type": "emby_refresh", "path": media_path, "status": "running", "started_at": utc_now()}
        threading.Thread(target=self._run_refresh, args=(job_id, media_path), daemon=True).start()
        return job_id

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
                    "/api/namer/plan": app.create_plan,
                    "/api/namer/identify": app.identify_path,
                    "/api/namer/apply": app.apply_plan,
                    "/api/emby/refresh": lambda payload: {"job_id": app.start_refresh(payload.get("path"))},
                    "/api/watch/recommendations": app.watch_recommendations,
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

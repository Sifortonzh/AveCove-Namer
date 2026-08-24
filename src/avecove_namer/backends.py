from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from pathlib import Path, PurePosixPath

from .models import Entry


class BackendError(RuntimeError):
    pass


class StorageBackend(ABC):
    name: str

    @abstractmethod
    def scan(self, root: str) -> list[Entry]:
        raise NotImplementedError

    @abstractmethod
    def exists(self, path: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def rename(self, source: str, target: str) -> None:
        raise NotImplementedError


class LocalBackend(StorageBackend):
    name = "local"

    def scan(self, root: str) -> list[Entry]:
        root_path = Path(root).expanduser().resolve()
        if not root_path.is_dir():
            raise BackendError(f"Local root does not exist or is not a directory: {root_path}")
        entries: list[Entry] = []
        for current, directories, filenames in os.walk(root_path):
            directories.sort()
            filenames.sort()
            for filename in filenames:
                path = Path(current) / filename
                stat = path.stat()
                entries.append(
                    Entry(
                        path=str(path),
                        size=stat.st_size,
                        modified=str(stat.st_mtime_ns),
                    )
                )
        return entries

    def exists(self, path: str) -> bool:
        return Path(path).exists()

    def rename(self, source: str, target: str) -> None:
        source_path = Path(source)
        target_path = Path(target)
        if not source_path.exists():
            raise BackendError(f"Source disappeared: {source}")
        if target_path.exists():
            raise BackendError(f"Target already exists: {target}")
        if source_path.parent != target_path.parent:
            raise BackendError("v0.1 only permits in-place renames")
        source_path.rename(target_path)


class OpenListBackend(StorageBackend):
    name = "openlist"

    def __init__(
        self,
        base_url: str,
        token: str,
        timeout: float = 20.0,
        rename_interval: float = 3.0,
        rename_retries: int = 3,
    ):
        self.base_url = base_url.rstrip("/")
        self.token = token.strip()
        self.timeout = timeout
        self.rename_interval = rename_interval
        self.rename_retries = rename_retries
        self._last_rename_at = 0.0
        if not self.base_url.startswith(("http://", "https://")):
            raise BackendError("OpenList URL must start with http:// or https://")
        if not self.token:
            raise BackendError("OpenList token is empty")

    def _request(self, endpoint: str, payload: dict[str, object]) -> dict[str, object]:
        request = urllib.request.Request(
            self.base_url + endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": self.token,
                "Content-Type": "application/json",
                "User-Agent": "AveCove-Namer/0.1",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.load(response)
        except (urllib.error.URLError, json.JSONDecodeError) as exc:
            raise BackendError(f"OpenList request failed: {exc}") from exc
        if int(data.get("code", 0)) not in {200, 201}:
            raise BackendError(f"OpenList API error: {data.get('message', 'unknown error')}")
        result = data.get("data")
        return result if isinstance(result, dict) else {}

    def _list(self, path: str) -> list[dict[str, object]]:
        data = self._request(
            "/api/fs/list",
            {"path": path, "password": "", "page": 1, "per_page": 0, "refresh": False},
        )
        content = data.get("content") or []
        if not isinstance(content, list):
            raise BackendError(f"Unexpected OpenList directory response for {path}")
        return [item for item in content if isinstance(item, dict)]

    def scan(self, root: str) -> list[Entry]:
        normalized_root = "/" + root.strip("/") if root != "/" else "/"
        pending = [normalized_root]
        entries: list[Entry] = []
        while pending:
            current = pending.pop()
            for item in self._list(current):
                name = str(item.get("name", ""))
                if not name:
                    continue
                path = str(PurePosixPath(current) / name)
                if bool(item.get("is_dir")):
                    pending.append(path)
                else:
                    entries.append(
                        Entry(
                            path=path,
                            size=int(item["size"]) if item.get("size") is not None else None,
                            modified=str(item.get("modified") or "") or None,
                        )
                    )
        return sorted(entries, key=lambda entry: entry.path.casefold())

    def exists(self, path: str) -> bool:
        parent = str(PurePosixPath(path).parent)
        name = PurePosixPath(path).name
        return any(str(item.get("name")) == name for item in self._list(parent))

    def rename(self, source: str, target: str) -> None:
        source_path = PurePosixPath(source)
        target_path = PurePosixPath(target)
        if source_path.parent != target_path.parent:
            raise BackendError("v0.1 only permits in-place OpenList renames")
        payload = {
            "path": str(source_path),
            "name": target_path.name,
            "overwrite": False,
        }
        for attempt in range(self.rename_retries + 1):
            wait = self.rename_interval - (time.monotonic() - self._last_rename_at)
            if wait > 0:
                time.sleep(wait)
            try:
                self._request("/api/fs/rename", payload)
                self._last_rename_at = time.monotonic()
                return
            except BackendError:
                if attempt >= self.rename_retries:
                    raise
                time.sleep(self.rename_interval)


def login_openlist(base_url: str, username: str, password: str, timeout: float = 20.0) -> str:
    request = urllib.request.Request(
        base_url.rstrip("/") + "/api/auth/login",
        data=json.dumps({"username": username, "password": password, "otp_code": ""}).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "AveCove-Namer/0.1"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.load(response)
    except (urllib.error.URLError, json.JSONDecodeError) as exc:
        raise BackendError(f"OpenList login failed: {exc}") from exc
    if int(data.get("code", 0)) != 200:
        raise BackendError(f"OpenList login failed: {data.get('message', 'unknown error')}")
    token = (data.get("data") or {}).get("token")
    if not token:
        raise BackendError("OpenList did not return a token")
    return str(token)

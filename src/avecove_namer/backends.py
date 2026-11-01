from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import base64
import http.cookiejar
import threading
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from pathlib import Path, PurePosixPath
from uuid import uuid4

from .models import Entry


class BackendError(RuntimeError):
    pass


class StorageBackend(ABC):
    name: str

    def mkdir(self, path: str) -> None:
        raise BackendError("This backend does not support creating directories")

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

    def mkdir(self, path: str) -> None:
        Path(path).mkdir()

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
        target_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.rename(target_path)


class LitePanWebDAVBackend(StorageBackend):
    """Use LitePan's authenticated WebDAV for cloud listing and safe MOVE renames."""

    name = "litepan"
    # Account-wide pacing survives separate plan/backend instances.
    _rate_states = {}
    _rate_lock = threading.RLock()
    initial_ms = {'115': 500, '123': 0, 'GuangYa': 0, 'Baidu': 0, 'Quark': 0}
    floor_ms = {'115': 300, '123': 0, 'GuangYa': 0, 'Baidu': 0, 'Quark': 0}

    def __init__(self, base_url: str, username: str, password: str, roots: dict[str, str], timeout: float = 25.0, rename_interval: float = 1.0):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.roots = roots
        self.timeout = timeout
        self.rename_interval = rename_interval
        self._last_rename_at = 0.0
        self._api_opener = None
        self.rate_file = Path(os.getenv('AVECOVE_LITEPAN_RENAME_RATES', '/opt/docker/avecove-namer/rename-rates.json'))
        if not self.base_url.startswith(("http://", "https://")) or not username or not password:
            raise BackendError("LitePan WebDAV URL or credentials are missing")

    def _physical_parts(self, virtual_path: str) -> list[str]:
        parts = PurePosixPath(virtual_path).parts
        if len(parts) < 2 or parts[0] != "/" or parts[1] not in self.roots:
            raise BackendError(f"Unsupported LitePan cloud path: {virtual_path}")
        if '..' in parts:
            raise BackendError('Invalid cloud path')
        if len(parts) > 2 and parts[2] == '@scout':
            raise BackendError('巡影接入已移除，请选择现有媒体库中的真实作品目录')
        return [*PurePosixPath(self.roots[parts[1]]).parts[1:], *parts[2:]]

    def _url(self, virtual_path: str) -> str:
        physical = self._physical_parts(virtual_path)
        parts = PurePosixPath(virtual_path).parts
        actual = [parts[1], *physical]
        return self.base_url + "/dav/" + "/".join(urllib.parse.quote(p, safe="") for p in actual)

    def _request(self, virtual_path: str, method: str, headers: dict[str, str] | None = None) -> bytes:
        token = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
        request = urllib.request.Request(self._url(virtual_path), method=method, headers={
            "Authorization": "Basic " + token,
            "User-Agent": "AveCove-Namer-LitePan/1.0",
            **(headers or {}),
        })
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise FileNotFoundError(virtual_path) from exc
            message = exc.read(300).decode("utf-8", "replace").strip()
            raise BackendError(f"LitePan WebDAV HTTP {exc.code}: {message}") from exc
        except (TimeoutError, urllib.error.URLError) as exc:
            raise BackendError(f"LitePan WebDAV unavailable: {exc}") from exc

    def _api_list(self, path: str, refresh: bool = False) -> list[dict[str, object]]:
        account_ids = {"115": 1, "123": 2, "GuangYa": 3, "Baidu": 4, "Quark": 5}
        parts = PurePosixPath(path).parts
        provider = parts[1] if len(parts) > 1 else ""
        if provider not in account_ids:
            raise BackendError(f"Unsupported LitePan cloud path: {path}")
        if self._api_opener is None:
            opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
            form = urllib.parse.urlencode({"username": self.username, "password": self.password}).encode()
            request = urllib.request.Request(self.base_url + "/api/auth/login", form)
            try:
                with opener.open(request, timeout=self.timeout) as response:
                    login = json.load(response)
            except (OSError, ValueError) as exc:
                raise BackendError(f"LitePan login unavailable: {exc}") from exc
            if not login.get("success"):
                raise BackendError("LitePan login rejected")
            self._api_opener = opener

        def list_parent(parent: str) -> list[dict[str, object]]:
            query = urllib.parse.urlencode({"account_id": account_ids[provider], "parent_id": parent,
                                             "force_refresh": str(refresh).lower()})
            request = urllib.request.Request(self.base_url + "/api/files/list?" + query)
            try:
                with self._api_opener.open(request, timeout=self.timeout) as response:
                    payload = json.load(response)
            except (OSError, ValueError) as exc:
                raise BackendError(f"LitePan listing unavailable for {path}: {exc}") from exc
            if not payload.get("success"):
                raise BackendError(f"LitePan listing failed for {path}: {payload.get('message', '')}")
            return (payload.get("data") or {}).get("items") or []

        actual_parts = self._physical_parts(path)
        parent = "0"
        for segment in actual_parts:
            items = list_parent(parent)
            match = next((item for item in items if item.get("name") == segment and item.get("is_dir")), None)
            if match is None:
                raise BackendError(f"LitePan directory not found: {path}")
            parent = str(match["id"])
        return [{"name": item.get("name", ""), "is_dir": bool(item.get("is_dir")),
                 "file_id": str(item.get("id") or ""), "account_id": str(account_ids[provider]),
                 "size": item.get("size"), "modified": item.get("mod_time", "")}
                for item in list_parent(parent)]

    def _list(self, path: str, refresh: bool = False) -> list[dict[str, object]]:
        try:
            return self._api_list(path, refresh)
        except BackendError:
            pass
        for attempt in range(3):
            try:
                body = self._request(path, "PROPFIND", {"Depth": "1", "Cache-Control": "no-cache" if refresh else "max-age=0"})
                document = ET.fromstring(body)
                break
            except FileNotFoundError as exc:
                raise BackendError(f"LitePan directory not found: {path}") from exc
            except (BackendError, ET.ParseError) as exc:
                if attempt == 2:
                    raise BackendError(f"LitePan WebDAV listing unavailable for {path}: {exc}") from exc
                time.sleep(0.5 * (attempt + 1))
        items: list[dict[str, object]] = []
        requested = self._url(path).rstrip("/")
        for response in document.findall("{DAV:}response"):
            href = response.findtext("{DAV:}href") or ""
            if urllib.parse.unquote(urllib.parse.urlsplit(href).path).rstrip("/") == urllib.parse.unquote(urllib.parse.urlsplit(requested).path).rstrip("/"):
                continue
            name = urllib.parse.unquote(urllib.parse.urlsplit(href).path.rstrip("/").rsplit("/", 1)[-1])
            prop = response.find(".//{DAV:}prop")
            if not name or prop is None:
                continue
            is_dir = prop.find("{DAV:}resourcetype/{DAV:}collection") is not None
            raw_size = prop.findtext("{DAV:}getcontentlength")
            items.append({"name": name, "is_dir": is_dir, "size": int(raw_size) if raw_size and raw_size.isdecimal() else None,
                          "modified": prop.findtext("{DAV:}getlastmodified") or ""})
        return items

    def list_directories(self, root: str, refresh: bool = False) -> list[dict[str, object]]:
        return sorted((item for item in self._list(root, refresh) if item["is_dir"]),
                      key=lambda item: (str(item["modified"]), str(item["name"]).casefold()), reverse=True)

    def scan(self, root: str, refresh: bool = False) -> list[Entry]:
        pending = [(root, refresh)]
        entries: list[Entry] = []
        while pending:
            current, fresh = pending.pop()
            for item in self._list(current, fresh):
                path = str(PurePosixPath(current) / str(item["name"]))
                if item["is_dir"]:
                    pending.append((path, False))
                else:
                    entries.append(Entry(path=path, size=item["size"], modified=str(item["modified"]) or None,
                                         file_id=item.get('file_id'), account_id=item.get('account_id')))
        return sorted(entries, key=lambda entry: entry.path.casefold())

    def exists(self, path: str) -> bool:
        return any(item["name"] == PurePosixPath(path).name for item in self._list(str(PurePosixPath(path).parent)))

    def mkdir(self, path: str) -> None:
        self._request(path, "MKCOL", {})

    def rename(self, source: str, target: str) -> None:
        provider = PurePosixPath(source).parts[1]
        if provider != PurePosixPath(target).parts[1]:
            raise BackendError("LitePan cannot move files across cloud accounts")
        key = (str(self.rate_file), provider)
        with self._rate_lock:
            if key not in self._rate_states:
                interval = self.initial_ms.get(provider, 1000)
                try:
                    saved = json.loads(self.rate_file.read_text()).get(provider, {})
                    value = saved.get('interval_ms', interval)
                    if isinstance(value, (int, float)) and self.floor_ms.get(provider, 1000) <= value <= 10000:
                        interval = int(value)
                except (OSError, ValueError, AttributeError):
                    pass
                self._rate_states[key] = {'interval_ms': interval, 'successes': 0, 'last': 0.0, 'lock': threading.Lock()}
            state = self._rate_states[key]
        with state['lock']:
            wait = state['interval_ms'] / 1000 - (time.monotonic() - state['last'])
            if wait > 0:
                time.sleep(wait)
            try:
                self._request(source, "MOVE", {"Destination": self._url(target), "Overwrite": "F"})
            except BackendError as exc:
                # A timed-out MOVE may already have succeeded: never blindly retry.
                if any(word in str(exc) for word in ('HTTP 429', 'HTTP 403', 'HTTP 408', 'HTTP 500', 'HTTP 502', 'HTTP 503', 'HTTP 504', 'unavailable')):
                    state['interval_ms'] = min(10000, max(1000, state['interval_ms'] * 2))
                    state['successes'] = 0
                    self._save_rate(provider, state)
                raise
            else:
                state['successes'] += 1
                if state['successes'] >= 50:
                    state['interval_ms'] = max(self.floor_ms.get(provider, 1000), state['interval_ms'] - 50)
                    state['successes'] = 0
                self._save_rate(provider, state)
            finally:
                state['last'] = time.monotonic()
                self._last_rename_at = state['last']

    def _save_rate(self, provider, state):
        with self._rate_lock:
            try:
                try:
                    data = json.loads(self.rate_file.read_text())
                except (OSError, ValueError):
                    data = {}
                if not isinstance(data, dict):
                    data = {}
                data[provider] = {'interval_ms': state['interval_ms'], 'successes': state['successes']}
                self.rate_file.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.rate_file.with_suffix('.tmp')
                temporary.write_text(json.dumps(data), encoding='utf-8')
                temporary.chmod(0o600)
                temporary.replace(self.rate_file)
            except OSError:
                # Persistence failure must not turn a successful cloud rename into a retry.
                pass


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
        self.rate_profile: dict[str, float] = {}
        profile_path = Path(os.environ.get('AVECOVE_NAMER_RATE_PROFILE', '/opt/docker/avecove-namer/rate-limits.json'))
        if profile_path.is_file():
            try:
                profile = json.loads(profile_path.read_text(encoding='utf-8'))
                for root, interval in profile.items():
                    if root in {'/123', '/Baidu', '/Quark', '/GuangYa'} and isinstance(interval, (int, float)) and 0 <= interval <= 30:
                        self.rate_profile[root] = float(interval)
            except (OSError, ValueError, AttributeError):
                pass
        if not self.base_url.startswith(("http://", "https://")):
            raise BackendError("OpenList URL must start with http:// or https://")
        if not self.token:
            raise BackendError("OpenList token is empty")

    def _request(self, endpoint: str, payload: dict[str, object]) -> dict[str, object]:
        last_error: Exception | None = None
        for attempt in range(self.rename_retries + 1):
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
                break
            except (TimeoutError, urllib.error.URLError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt >= self.rename_retries:
                    raise BackendError(f"OpenList request failed after retries: {exc}") from exc
                time.sleep(3.0 * (2**attempt))
        else:  # pragma: no cover - defensive, loop always returns or raises
            raise BackendError(f"OpenList request failed: {last_error}")
        if int(data.get("code", 0)) not in {200, 201}:
            raise BackendError(f"OpenList API error: {data.get('message', 'unknown error')}")
        result = data.get("data")
        return result if isinstance(result, dict) else {}

    def _list(self, path: str, refresh: bool = False) -> list[dict[str, object]]:
        data: dict[str, object] | None = None
        refresh_error: BackendError | None = None
        for attempt in range(3):
            try:
                data = self._request(
                    "/api/fs/list",
                    {"path": path, "password": "", "page": 1, "per_page": 0, "refresh": refresh},
                )
                break
            except BackendError as exc:
                if not refresh:
                    raise
                refresh_error = exc
                if "object not found" in str(exc).casefold() and attempt < 2:
                    time.sleep(float(attempt + 1))
                    continue
                break
        if data is None and refresh:
            try:
                data = self._request(
                    "/api/fs/list",
                    {"path": path, "password": "", "page": 1, "per_page": 0, "refresh": False},
                )
            except BackendError:
                if refresh_error is not None:
                    raise refresh_error
                raise
        if data is None:  # pragma: no cover - defensive
            raise BackendError(f"OpenList returned no directory data for {path}")
        content = data.get("content") or []
        if not isinstance(content, list):
            raise BackendError(f"Unexpected OpenList directory response for {path}")
        return [item for item in content if isinstance(item, dict)]

    def list_directories(self, root: str, refresh: bool = False) -> list[dict[str, object]]:
        normalized_root = "/" + root.strip("/") if root != "/" else "/"
        return sorted(
            (item for item in self._list(normalized_root, refresh=refresh) if bool(item.get("is_dir")) and item.get("name")),
            key=lambda item: (str(item.get("modified") or ""), str(item.get("name", "")).casefold()),
            reverse=True,
        )

    def scan(self, root: str, refresh: bool = False) -> list[Entry]:
        normalized_root = "/" + root.strip("/") if root != "/" else "/"
        # A provider refresh is deliberately limited to the requested work root.
        # Refreshing every nested directory can fan out into hundreds of remote
        # API calls and overwhelm small OpenList hosts. Child listings use the
        # cache populated by OpenList and subsequent scheduled runs.
        pending = [(normalized_root, refresh)]
        entries: list[Entry] = []
        while pending:
            current, refresh_current = pending.pop()
            for item in self._list(current, refresh=refresh_current):
                name = str(item.get("name", ""))
                if not name:
                    continue
                path = str(PurePosixPath(current) / name)
                if bool(item.get("is_dir")):
                    pending.append((path, False))
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

    def _rename_once(self, source_path: PurePosixPath, target_path: PurePosixPath) -> None:
        root = '/' + source_path.parts[1]
        interval = self.rate_profile.get(root, self.rename_interval)
        payload = {
            "path": str(source_path),
            "name": target_path.name,
            "overwrite": False,
        }
        for attempt in range(self.rename_retries + 1):
            wait = interval - (time.monotonic() - self._last_rename_at)
            if wait > 0:
                time.sleep(wait)
            try:
                self._request("/api/fs/rename", payload)
                self._last_rename_at = time.monotonic()
                return
            except BackendError:
                if root in self.rate_profile:
                    interval = max(3.0, interval)
                    self.rate_profile[root] = interval
                if attempt >= self.rename_retries:
                    raise
                # Back off on provider errors even when normal requests are fast.
                time.sleep(max(3.0, interval) * (2 ** attempt))

    def _move_once(self, source_path: PurePosixPath, target_parent: PurePosixPath) -> None:
        root = "/" + source_path.parts[1]
        interval = self.rate_profile.get(root, self.rename_interval)
        payload = {
            "src_dir": str(source_path.parent),
            "dst_dir": str(target_parent),
            "names": [source_path.name],
        }
        for attempt in range(self.rename_retries + 1):
            wait = interval - (time.monotonic() - self._last_rename_at)
            if wait > 0:
                time.sleep(wait)
            try:
                self._request("/api/fs/move", payload)
                self._last_rename_at = time.monotonic()
                return
            except BackendError:
                if root in self.rate_profile:
                    interval = max(3.0, interval)
                    self.rate_profile[root] = interval
                if attempt >= self.rename_retries:
                    raise
                time.sleep(max(3.0, interval) * (2**attempt))

    def rename(self, source: str, target: str) -> None:
        source_path = PurePosixPath(source)
        target_path = PurePosixPath(target)
        if source_path.parent != target_path.parent:
            temporary = source_path.parent / f".avecove-namer-{uuid4().hex[:12]}.tmp"
            self._rename_once(source_path, temporary)
            try:
                self._move_once(temporary, target_path.parent)
                moved = target_path.parent / temporary.name
                self._rename_once(moved, target_path)
            except BackendError:
                # Best-effort rollback from either possible intermediate location.
                try:
                    moved = target_path.parent / temporary.name
                    self._move_once(moved, source_path.parent)
                except BackendError:
                    pass
                try:
                    self._rename_once(temporary, source_path)
                except BackendError:
                    pass
                raise
            return
        if source_path.name != target_path.name and source_path.name.casefold() == target_path.name.casefold():
            temporary_path = source_path.parent / f".avecove-namer-{uuid4().hex[:12]}.tmp"
            self._rename_once(source_path, temporary_path)
            try:
                self._rename_once(temporary_path, target_path)
            except BackendError:
                try:
                    self._rename_once(temporary_path, source_path)
                except BackendError:
                    pass
                raise
            return
        self._rename_once(source_path, target_path)


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

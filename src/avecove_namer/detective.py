from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path, PurePosixPath
from typing import Iterable

from .backends import BackendError, OpenListBackend
from .executor import execute_plan
from .naming import NamingPolicy
from .models import Entry, RenamePlan
from .planner import make_plan, write_plan
from .tmdb import TMDBClient


TMDB_ID_RE = re.compile(r"\{tmdb\s*(?:=|-)\s*(?P<id>\d+)\}", re.IGNORECASE)
YEAR_RE = re.compile(r"(?<!\d)(?P<year>19\d{2}|20\d{2})(?!\d)")
SEASON_PACK_RE = re.compile(r"(?i)\s*全\s*\d{1,2}\s*季.*$")
SEASON_RE = re.compile(r"(?i)(?:season[ ._-]*|(?<![a-z0-9])s)0*(\d{1,2})(?=(?:e\d|d\d|[^0-9]|$))")
CHINESE_SEASON_RE = re.compile(r"第[ ._-]*(\d{1,2}|[〇零一二两三四五六七八九十百]+)[ ._-]*季")
CHINESE_DIGITS = {"〇": 0, "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


@dataclass(frozen=True)
class WatchRoot:
    kind: str
    path: str


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_watch(value: str) -> WatchRoot:
    kind, separator, path = value.partition(":")
    normalized = "/" + path.strip("/")
    if not separator or kind not in {"tv", "movie"} or normalized == "/":
        raise ValueError("Watch roots must use tv:/path or movie:/path")
    return WatchRoot(kind=kind, path=normalized)


def infer_search_terms(folder_name: str) -> tuple[str | None, int | None]:
    value = unicodedata.normalize("NFKC", folder_name)
    value = TMDB_ID_RE.sub("", value).strip()
    year_matches = list(YEAR_RE.finditer(value))
    year = int(year_matches[-1].group("year")) if year_matches else None
    title = value[: year_matches[-1].start()] if year_matches else value
    title = SEASON_PACK_RE.sub("", title)
    title = title.strip(" []【】()（）._-")
    title = re.sub(r"[._]+", " ", title)
    title = re.sub(r"\s+", " ", title).strip()
    return (title or None), year


def normalized_title(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in value if character.isalnum())


def choose_tmdb_match(
    query: str,
    year: int | None,
    results: Iterable[dict[str, object]],
    minimum_score: float = 0.88,
) -> tuple[dict[str, object] | None, float, str]:
    query_key = normalized_title(query)
    ranked: list[tuple[float, dict[str, object]]] = []
    seen: set[int] = set()
    for result in results:
        result_id = int(result.get("id") or 0)
        if not result_id or result_id in seen:
            continue
        seen.add(result_id)
        result_year = result.get("year")
        if year and result_year and int(result_year) != year:
            continue
        candidates = [str(result.get("title") or ""), str(result.get("original_title") or "")]
        score = max(
            (SequenceMatcher(None, query_key, normalized_title(candidate)).ratio() for candidate in candidates if candidate),
            default=0.0,
        )
        ranked.append((score, result))
    ranked.sort(key=lambda item: item[0], reverse=True)
    if not ranked:
        return None, 0.0, "no year-compatible TMDB result"
    best_score, best = ranked[0]
    runner_up = ranked[1][0] if len(ranked) > 1 else 0.0
    if best_score < minimum_score:
        return None, best_score, "title similarity below threshold"
    if len(ranked) > 1 and best_score - runner_up < 0.08 and best_score < 0.999:
        return None, best_score, "TMDB result is ambiguous"
    return best, best_score, "high-confidence title and year match"


def fingerprint(entries: Iterable[object]) -> str:
    digest = hashlib.sha256()
    for entry in sorted(entries, key=lambda item: item.path.casefold()):
        digest.update(entry.path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(entry.size).encode("ascii"))
        digest.update(b"\0")
        digest.update(str(entry.modified).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def season_numbers(entries: Iterable[object]) -> set[int]:
    seasons: set[int] = set()
    for entry in entries:
        for match in SEASON_RE.finditer(str(entry.path)):
            seasons.add(int(match.group(1)))
        for match in CHINESE_SEASON_RE.finditer(str(entry.path)):
            value = match.group(1)
            if value.isdigit():
                seasons.add(int(value))
            elif "百" in value:
                hundreds, _, remainder = value.partition("百")
                total = CHINESE_DIGITS.get(hundreds, 1) * 100
                if remainder:
                    value = remainder
                    if "十" not in value:
                        total += CHINESE_DIGITS.get(value, 0)
                    else:
                        tens, _, ones = value.partition("十")
                        total += CHINESE_DIGITS.get(tens, 1) * 10 + CHINESE_DIGITS.get(ones, 0)
                seasons.add(total)
            elif "十" in value:
                tens, _, ones = value.partition("十")
                seasons.add(CHINESE_DIGITS.get(tens, 1) * 10 + CHINESE_DIGITS.get(ones, 0))
            else:
                seasons.add(CHINESE_DIGITS[value])
    return seasons


def limit_plan_for_batch(plan: RenamePlan, max_seasons: int, max_operations: int) -> tuple[RenamePlan, bool, list[int]]:
    operation_seasons: list[set[int]] = []
    pending_seasons: set[int] = set()
    for operation in plan.operations:
        seasons = season_numbers([Entry(operation.source), Entry(operation.target)])
        operation_seasons.append(seasons)
        if operation.kind != "rename_directory":
            pending_seasons.update(seasons)

    selected_seasons = sorted(pending_seasons)
    if max_seasons:
        selected_seasons = selected_seasons[:max_seasons]
    selected_set = set(selected_seasons)
    operations = [
        operation
        for operation, seasons in zip(plan.operations, operation_seasons)
        if operation.kind == "rename_directory" or not seasons or bool(seasons & selected_set)
    ]
    operations = operations[:max_operations]
    selected_paths = {value for operation in operations for value in (operation.source, operation.target)}
    conflicts = [conflict for conflict in plan.conflicts if any(path in conflict for path in selected_paths)]
    limited = RenamePlan(
        version=plan.version,
        created_at=plan.created_at,
        backend=plan.backend,
        root=plan.root,
        policy=plan.policy,
        operations=operations,
        conflicts=conflicts,
        skipped=plan.skipped,
    )
    partial = len(operations) < len(plan.operations)
    return limited, partial, selected_seasons


def load_state(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"version": 1, "works": {}}
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data.get("works"), dict):
        raise ValueError("Detective state has an invalid works mapping")
    return data


def write_json_atomic(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temporary.replace(path)


def _tmdb_id_from_name(name: str) -> int | None:
    match = TMDB_ID_RE.search(name)
    return int(match.group("id")) if match else None


def _find_tmdb_id(client: TMDBClient, title: str, year: int | None, kind: str) -> tuple[int | None, float, str]:
    combined: list[dict[str, object]] = []
    for language in ("zh-CN", "en-US"):
        combined.extend(client.search(title, kind, year, language))
        time.sleep(0.15)
    match, score, reason = choose_tmdb_match(title, year, combined)
    return (int(match["id"]) if match else None), score, reason


def run_detective(
    backend: OpenListBackend,
    tmdb: TMDBClient,
    watches: list[WatchRoot],
    state_path: Path,
    work_root: Path,
    execute: bool = False,
    bootstrap: bool = False,
    max_operations: int = 200,
    max_seasons: int = 0,
    title_style: str = "auto",
) -> dict[str, object]:
    state = load_state(state_path)
    previous = dict(state.get("works") or {})
    current: dict[str, str] = {}
    events: list[dict[str, object]] = []
    changed_roots: set[str] = set()
    run_stamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    for watch in watches:
        try:
            directories = backend.list_directories(watch.path, refresh=True)
        except BackendError as exc:
            events.append({"path": watch.path, "status": "scan_error", "reason": str(exc)})
            continue
        for directory in directories:
            work_path = str(PurePosixPath(watch.path) / str(directory["name"]))
            try:
                entries = backend.scan(work_path, refresh=True)
            except BackendError as exc:
                current[work_path] = f"pending:{previous.get(work_path, '')}"
                events.append({"path": work_path, "status": "scan_error", "reason": str(exc)})
                continue
            signature = fingerprint(entries)
            current[work_path] = signature
            previous_signature = str(previous.get(work_path) or "")
            canonical_folder = _tmdb_id_from_name(PurePosixPath(work_path).name) is not None
            changed = previous_signature != signature or previous_signature.startswith("pending:") or not canonical_folder
            if bootstrap or not changed:
                events.append({"path": work_path, "status": "baseline" if bootstrap else "unchanged"})
                continue

            title, year = infer_search_terms(PurePosixPath(work_path).name)
            tmdb_id = _tmdb_id_from_name(PurePosixPath(work_path).name)
            match_score = 1.0 if tmdb_id else 0.0
            match_reason = "existing TMDB folder tag" if tmdb_id else ""
            if not tmdb_id and title:
                tmdb_id, match_score, match_reason = _find_tmdb_id(tmdb, title, year, watch.kind)
            if not tmdb_id:
                current[work_path] = f"pending:{signature}"
                events.append(
                    {
                        "path": work_path,
                        "status": "review",
                        "reason": match_reason or "could not infer title and year",
                        "score": round(match_score, 4),
                    }
                )
                continue

            resolved = tmdb.resolve_title(tmdb_id, watch.kind, title_style)
            plan = make_plan(
                entries,
                work_path,
                backend.name,
                NamingPolicy(),
                str(resolved["title"]),
                int(resolved["year"]) if resolved.get("year") else year,
                True,
                tmdb_id,
                str(resolved["primary_language"]),
                watch.kind,
            )
            plan, partial_batch, selected_seasons = limit_plan_for_batch(plan, max_seasons, max_operations)
            for operation in plan.operations:
                if operation.kind == "rename_directory" and backend.exists(operation.target):
                    plan.conflicts.append(f"Target exists: {operation.target}")
            plan.conflicts = sorted(set(plan.conflicts))
            job = work_root / f"{run_stamp}-{hashlib.sha256(work_path.encode()).hexdigest()[:8]}"
            job.mkdir(parents=True, exist_ok=True)
            plan_path = job / "plan.json"
            journal_path = job / "rollback.jsonl"
            write_plan(plan, str(plan_path), str(job / "plan.csv"))

            if plan.conflicts:
                current[work_path] = f"pending:{signature}"
                events.append({"path": work_path, "status": "review", "reason": "plan conflicts", "plan": str(plan_path)})
                continue
            if not plan.operations:
                events.append({"path": work_path, "status": "compliant", "tmdb_id": tmdb_id})
                continue
            if not execute:
                current[work_path] = f"pending:{signature}"
                events.append(
                    {
                        "path": work_path,
                        "status": "planned",
                        "operations": len(plan.operations),
                        "plan": str(plan_path),
                        "tmdb_id": tmdb_id,
                        "seasons": selected_seasons,
                        "partial": partial_batch,
                    }
                )
                continue

            execute_plan(
                plan,
                backend,
                str(journal_path),
                execute=True,
                confirm_root=work_path,
                confirm_count=len(plan.operations),
            )
            final_path = work_path
            for operation in plan.operations:
                if operation.kind == "rename_directory" and operation.source == work_path:
                    final_path = operation.target
                    break
            final_entries = backend.scan(final_path, refresh=True)
            verify = make_plan(
                final_entries,
                final_path,
                backend.name,
                NamingPolicy(),
                str(resolved["title"]),
                int(resolved["year"]) if resolved.get("year") else year,
                True,
                tmdb_id,
                str(resolved["primary_language"]),
                watch.kind,
            )
            write_plan(verify, str(job / "verify.json"))
            if verify.conflicts:
                current.pop(work_path, None)
                current[final_path] = f"pending:{fingerprint(final_entries)}"
                events.append({"path": final_path, "status": "review", "reason": "post-apply verification conflicts", "plan": str(job / "verify.json")})
                continue
            current.pop(work_path, None)
            remaining_operations = len(verify.operations)
            current[final_path] = (
                f"pending:{fingerprint(final_entries)}" if remaining_operations else fingerprint(final_entries)
            )
            changed_roots.add(watch.path)
            events.append(
                {
                    "path": final_path,
                    "status": "applied_partial" if remaining_operations else "applied",
                    "operations": len(plan.operations),
                    "remaining_operations": remaining_operations,
                    "seasons": selected_seasons,
                    "tmdb_id": tmdb_id,
                    "score": round(match_score, 4),
                    "reason": match_reason,
                    "journal": str(journal_path),
                }
            )

    state["works"] = current
    state["updated_at"] = utc_now()
    write_json_atomic(state_path, state)
    return {
        "version": 1,
        "created_at": utc_now(),
        "bootstrap": bootstrap,
        "execute": execute,
        "changed_roots": sorted(changed_roots),
        "events": events,
    }

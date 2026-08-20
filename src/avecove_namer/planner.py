from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import PurePosixPath

from .models import Entry, RenameOperation, RenamePlan
from .naming import (
    NamingPolicy,
    SUBTITLE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    build_subtitle_name,
    build_video_name,
    extension_of,
    infer_context,
    parse_media_name,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sibling_path(source: str, target_name: str) -> str:
    return str(PurePosixPath(source).parent / target_name)


def _episode_key(name: str) -> tuple[int, int] | None:
    parsed = parse_media_name(name)
    if parsed.kind != "episode":
        return None
    return int(parsed.season), int(parsed.episode)


def make_plan(
    entries: list[Entry],
    root: str,
    backend: str,
    policy: NamingPolicy,
    title_override: str | None = None,
    year_override: int | None = None,
) -> RenamePlan:
    plan = RenamePlan(
        version=1,
        created_at=utc_now(),
        backend=backend,
        root=root,
        policy=policy.as_dict(),
    )
    known_paths = {entry.path for entry in entries}
    by_parent: dict[str, list[Entry]] = defaultdict(list)
    for entry in entries:
        by_parent[entry.parent].append(entry)

    video_targets: dict[str, str] = {}
    episode_targets: dict[tuple[str, int, int], str] = {}

    for entry in entries:
        extension = extension_of(entry.name)
        if extension not in VIDEO_EXTENSIONS:
            continue
        parsed = parse_media_name(entry.name)
        context_title, context_year = infer_context(entry.path)
        resolved_title = title_override or context_title or parsed.title
        resolved_year = year_override or context_year or parsed.year
        if parsed.kind == "episode" and policy.include_series_year and not resolved_year:
            plan.skipped.append({"path": entry.path, "reason": "series_year_missing"})
            continue
        if parsed.kind == "movie" and not resolved_year:
            plan.skipped.append({"path": entry.path, "reason": "movie_year_missing"})
            continue
        target_name = build_video_name(parsed, policy, resolved_title, resolved_year)
        target = sibling_path(entry.path, target_name)
        if target == entry.path:
            plan.skipped.append({"path": entry.path, "reason": "already_compliant"})
            video_targets[entry.path] = target
        else:
            plan.operations.append(
                RenameOperation(
                    source=entry.path,
                    target=target,
                    kind="rename_video",
                    reason="year_aware_episode_name" if parsed.kind == "episode" else "movie_name",
                    confidence=0.96 if resolved_year and resolved_title else 0.75,
                    source_size=entry.size,
                    source_modified=entry.modified,
                )
            )
            video_targets[entry.path] = target
        if parsed.kind == "episode":
            episode_targets[(entry.parent, int(parsed.season), int(parsed.episode))] = target

    for parent, siblings in by_parent.items():
        for entry in siblings:
            extension = extension_of(entry.name)
            if extension not in SUBTITLE_EXTENSIONS:
                continue
            key = _episode_key(entry.name)
            video_target = episode_targets.get((parent, *key)) if key else None
            if not video_target:
                candidates = [target for source, target in video_targets.items() if PurePosixPath(source).parent == PurePosixPath(entry.path).parent]
                video_target = candidates[0] if len(candidates) == 1 else None
            if not video_target:
                plan.skipped.append({"path": entry.path, "reason": "subtitle_video_pair_ambiguous"})
                continue
            target_name = build_subtitle_name(PurePosixPath(video_target).name, entry.name, policy)
            target = sibling_path(entry.path, target_name)
            if target == entry.path:
                plan.skipped.append({"path": entry.path, "reason": "subtitle_already_compliant"})
                continue
            plan.operations.append(
                RenameOperation(
                    source=entry.path,
                    target=target,
                    kind="rename_subtitle",
                    reason="paired_subtitle",
                    confidence=0.98,
                    source_size=entry.size,
                    source_modified=entry.modified,
                )
            )

    targets: dict[str, list[str]] = defaultdict(list)
    for operation in plan.operations:
        targets[operation.target].append(operation.source)
        if operation.target in known_paths and operation.target != operation.source:
            plan.conflicts.append(f"Target exists: {operation.target}")
    for target, source_list in targets.items():
        if len(source_list) > 1:
            plan.conflicts.append(f"Duplicate target {target}: {', '.join(source_list)}")

    plan.operations.sort(key=lambda operation: (operation.source.casefold(), operation.kind))
    plan.conflicts = sorted(set(plan.conflicts))
    return plan


def write_plan(plan: RenamePlan, json_path: str, csv_path: str | None = None) -> None:
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(plan.to_dict(), handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    if csv_path:
        with open(csv_path, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=["source", "target", "kind", "reason", "confidence", "source_size", "source_modified"],
            )
            writer.writeheader()
            for operation in plan.operations:
                writer.writerow(operation.to_dict())


def read_plan(path: str) -> RenamePlan:
    with open(path, encoding="utf-8") as handle:
        return RenamePlan.from_dict(json.load(handle))

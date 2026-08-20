from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .backends import BackendError, StorageBackend
from .models import RenameOperation, RenamePlan


class ExecutionError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def preview(plan: RenamePlan) -> str:
    lines = [
        f"Backend: {plan.backend}",
        f"Root: {plan.root}",
        f"Operations: {len(plan.operations)}",
        f"Conflicts: {len(plan.conflicts)}",
    ]
    for operation in plan.operations:
        lines.append(f"[{operation.kind}] {operation.source} -> {operation.target}")
    if plan.conflicts:
        lines.append("Conflicts:")
        lines.extend(f"- {conflict}" for conflict in plan.conflicts)
    return "\n".join(lines)


def execute_plan(
    plan: RenamePlan,
    backend: StorageBackend,
    journal_path: str,
    execute: bool = False,
    confirm_root: str | None = None,
    confirm_count: int | None = None,
) -> list[dict[str, str]]:
    if not execute:
        return []
    if plan.conflicts:
        raise ExecutionError("Plan has conflicts and cannot be executed")
    if confirm_root != plan.root:
        raise ExecutionError("--confirm-root must exactly match the plan root")
    if confirm_count != len(plan.operations):
        raise ExecutionError("--confirm-count must exactly match the operation count")
    if plan.root in {"", "/"}:
        raise ExecutionError("Refusing to mutate a broad root")

    journal: list[dict[str, str]] = []
    journal_file = Path(journal_path)
    if journal_file.exists() and journal_file.stat().st_size:
        raise ExecutionError("Journal already contains an earlier run; use a new journal path")
    journal_file.parent.mkdir(parents=True, exist_ok=True)
    for operation in plan.operations:
        if not backend.exists(operation.source):
            raise ExecutionError(f"Source is stale or missing: {operation.source}")
        if backend.exists(operation.target):
            raise ExecutionError(f"Target appeared after planning: {operation.target}")
        try:
            backend.rename(operation.source, operation.target)
        except BackendError as exc:
            raise ExecutionError(str(exc)) from exc
        row = {
            "timestamp": _now(),
            "source": operation.source,
            "target": operation.target,
            "kind": operation.kind,
        }
        journal.append(row)
        with journal_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return journal


def rollback(
    backend: StorageBackend,
    journal_path: str,
    execute: bool = False,
) -> list[RenameOperation]:
    with open(journal_path, encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    operations = [
        RenameOperation(
            source=row["target"],
            target=row["source"],
            kind=row["kind"],
            reason="rollback",
            confidence=1.0,
        )
        for row in reversed(rows)
    ]
    if not execute:
        return operations
    for operation in operations:
        if not backend.exists(operation.source):
            raise ExecutionError(f"Rollback source is missing: {operation.source}")
        if backend.exists(operation.target):
            raise ExecutionError(f"Rollback target is occupied: {operation.target}")
        backend.rename(operation.source, operation.target)
    return operations

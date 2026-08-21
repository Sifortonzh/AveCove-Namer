from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


MediaKind = Literal["movie", "episode", "subtitle", "other"]
OperationKind = Literal["rename_video", "rename_subtitle", "rename_directory"]


@dataclass(frozen=True)
class Entry:
    path: str
    is_dir: bool = False
    size: int | None = None
    modified: str | None = None

    @property
    def name(self) -> str:
        return self.path.rstrip("/").rsplit("/", 1)[-1]

    @property
    def parent(self) -> str:
        parent, _, _ = self.path.rstrip("/").rpartition("/")
        return parent or "/"


@dataclass(frozen=True)
class ParsedMedia:
    source_name: str
    kind: MediaKind
    extension: str
    title: str | None = None
    year: int | None = None
    season: int | None = None
    episode: int | None = None
    technical_tail: tuple[str, ...] = ()


@dataclass(frozen=True)
class RenameOperation:
    source: str
    target: str
    kind: OperationKind
    reason: str
    confidence: float
    source_size: int | None = None
    source_modified: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RenamePlan:
    version: int
    created_at: str
    backend: str
    root: str
    policy: dict[str, Any]
    operations: list[RenameOperation] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "created_at": self.created_at,
            "backend": self.backend,
            "root": self.root,
            "policy": self.policy,
            "operations": [operation.to_dict() for operation in self.operations],
            "conflicts": self.conflicts,
            "skipped": self.skipped,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RenamePlan":
        return cls(
            version=int(data["version"]),
            created_at=str(data["created_at"]),
            backend=str(data["backend"]),
            root=str(data["root"]),
            policy=dict(data.get("policy", {})),
            operations=[RenameOperation(**item) for item in data.get("operations", [])],
            conflicts=list(data.get("conflicts", [])),
            skipped=list(data.get("skipped", [])),
        )

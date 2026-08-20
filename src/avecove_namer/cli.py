from __future__ import annotations

import argparse
import getpass
import json
import os
import stat
import sys
from pathlib import Path

from . import __version__
from .backends import BackendError, LocalBackend, OpenListBackend, StorageBackend, login_openlist
from .executor import ExecutionError, execute_plan, preview, rollback
from .naming import NamingPolicy
from .planner import make_plan, read_plan, write_plan
from .tmdb import TMDBClient, TMDBError


def secure_read(path: str) -> str:
    token_path = Path(path).expanduser()
    mode = stat.S_IMODE(token_path.stat().st_mode)
    if mode & 0o077:
        raise ValueError(f"Credential file must use mode 0600: {token_path}")
    value = token_path.read_text(encoding="utf-8").strip()
    if not value:
        raise ValueError(f"Credential file is empty: {token_path}")
    return value


def secure_write(path: str, value: str) -> None:
    token_path = Path(path).expanduser()
    token_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(value.strip() + "\n")
    os.chmod(token_path, 0o600)


def backend_from_args(args: argparse.Namespace, expected: str | None = None) -> StorageBackend:
    selected = args.backend if hasattr(args, "backend") else expected
    if expected and selected != expected:
        raise ValueError(f"Plan backend is {expected}, not {selected}")
    if selected == "local":
        return LocalBackend()
    if selected == "openlist":
        url = args.openlist_url or os.environ.get("OPENLIST_URL")
        token_file = args.openlist_token_file or os.environ.get("OPENLIST_TOKEN_FILE")
        if not url or not token_file:
            raise ValueError("OpenList requires --openlist-url and --openlist-token-file")
        return OpenListBackend(url, secure_read(token_file))
    raise ValueError(f"Unsupported backend: {selected}")


def add_backend_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--backend", choices=("local", "openlist"), required=True)
    parser.add_argument("--openlist-url")
    parser.add_argument("--openlist-token-file")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="avecove-namer", description="Safe media naming for OpenList-backed libraries")
    parser.add_argument("--version", action="version", version=__version__)
    subcommands = parser.add_subparsers(dest="command", required=True)

    login = subcommands.add_parser("login", help="Create a protected OpenList token file")
    login.add_argument("--openlist-url", required=True)
    login.add_argument("--username", required=True)
    login.add_argument("--token-file", required=True)

    check = subcommands.add_parser("check", help="Check backend connectivity and count files")
    add_backend_arguments(check)
    check.add_argument("--path", required=True)

    plan = subcommands.add_parser("plan", help="Generate a read-only rename plan")
    add_backend_arguments(plan)
    plan.add_argument("--path", required=True)
    plan.add_argument("--title", help="Verified canonical title override")
    plan.add_argument("--year", type=int, help="Verified release or first-air year override")
    plan.add_argument("--include-episode-title", action="store_true", help="Reserved for a future TMDB episode-title resolver")
    plan.add_argument("--subtitle-language-default", default="zh-CN")
    plan.add_argument("--output", required=True)
    plan.add_argument("--csv")

    apply = subcommands.add_parser("apply", help="Preview or execute a reviewed plan")
    add_backend_arguments(apply)
    apply.add_argument("--plan", required=True)
    apply.add_argument("--journal", required=True)
    apply.add_argument("--execute", action="store_true")
    apply.add_argument("--confirm-root")
    apply.add_argument("--confirm-count", type=int)

    undo = subcommands.add_parser("rollback", help="Preview or execute a journal rollback")
    add_backend_arguments(undo)
    undo.add_argument("--journal", required=True)
    undo.add_argument("--execute", action="store_true")

    tmdb = subcommands.add_parser("tmdb-search", help="Read-only TMDB title search")
    tmdb.add_argument("--token-file", required=True)
    tmdb.add_argument("--query", required=True)
    tmdb.add_argument("--kind", choices=("tv", "movie"), default="tv")
    tmdb.add_argument("--year", type=int)
    tmdb.add_argument("--language", default="en-US")
    return parser


def run(args: argparse.Namespace) -> int:
    if args.command == "login":
        password = getpass.getpass("OpenList password: ")
        token = login_openlist(args.openlist_url, args.username, password)
        secure_write(args.token_file, token)
        print(f"Token written securely: {Path(args.token_file).expanduser()}")
        return 0

    if args.command == "tmdb-search":
        results = TMDBClient(secure_read(args.token_file)).search(args.query, args.kind, args.year, args.language)
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0

    if args.command == "check":
        backend = backend_from_args(args)
        entries = backend.scan(args.path)
        print(json.dumps({"backend": backend.name, "path": args.path, "files": len(entries)}, ensure_ascii=False, indent=2))
        return 0

    if args.command == "plan":
        if args.include_episode_title:
            raise ValueError("Episode titles are intentionally disabled in v0.1; AveCove's default is title + year + SxxEyy")
        backend = backend_from_args(args)
        entries = backend.scan(args.path)
        policy = NamingPolicy(subtitle_language_default=args.subtitle_language_default)
        rename_plan = make_plan(entries, args.path, backend.name, policy, args.title, args.year)
        write_plan(rename_plan, args.output, args.csv)
        print(preview(rename_plan))
        print(f"Plan written: {args.output}")
        return 2 if rename_plan.conflicts else 0

    if args.command == "apply":
        rename_plan = read_plan(args.plan)
        backend = backend_from_args(args, rename_plan.backend)
        print(preview(rename_plan))
        if not args.execute:
            print("Preview only. Add --execute with exact root and count confirmations to mutate files.")
            return 0
        completed = execute_plan(
            rename_plan,
            backend,
            args.journal,
            execute=True,
            confirm_root=args.confirm_root,
            confirm_count=args.confirm_count,
        )
        print(f"Executed {len(completed)} operations. Journal: {args.journal}")
        return 0

    if args.command == "rollback":
        backend = backend_from_args(args)
        operations = rollback(backend, args.journal, execute=args.execute)
        for operation in operations:
            print(f"[rollback] {operation.source} -> {operation.target}")
        print("Rollback executed." if args.execute else "Rollback preview only. Add --execute to proceed.")
        return 0
    raise ValueError(f"Unknown command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        return run(parser.parse_args(argv))
    except (BackendError, ExecutionError, TMDBError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


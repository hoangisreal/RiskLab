"""Reject private tooling and data paths from Git's tracked/publishable file set."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

PRIVATE_DIRECTORIES = {
    ".agent",
    ".agents",
    ".codex",
    ".claude",
    ".cursor",
    ".gemini",
    ".aider",
    ".windsurf",
    ".roo",
    ".continue",
    ".opencode",
    ".kaggle",
    ".secrets",
    "mlflow-data",
    "node_modules",
    ".venv",
}
PRIVATE_NAMES = {
    "agents.md",
    "agents.override.md",
    "agent.md",
    "skill.md",
    "claude.md",
    "gemini.md",
    ".cursorrules",
    ".windsurfrules",
    ".aider.conf.yml",
    "copilot-instructions.md",
    ".mcp.json",
}


def forbidden(path: str) -> bool:
    p = PurePosixPath(path.lower())
    if set(p.parts) & PRIVATE_DIRECTORIES or p.name in PRIVATE_NAMES:
        return True
    if p.parts[:2] in {
        (".github", "agents"),
        (".github", "instructions"),
        (".github", "prompts"),
        (".github", "skills"),
        ("docs", "tasks"),
    }:
        return True
    if p.parts and p.parts[0] in {"data", "features", "presentation", "catboost_info"}:
        return True
    if p.parts and p.parts[0] == "reports" and p.parts[:2] != ("reports", "reference"):
        return True
    return p.name == ".env" or (p.name.startswith(".env.") and p.name != ".env.example")


def candidate_paths(root: Path, *, revision: str | None = None) -> list[str]:
    command = (
        ["git", "ls-tree", "-r", "--name-only", "-z", revision]
        if revision
        else ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"]
    )
    result = subprocess.run(command, cwd=root, capture_output=True, check=True)
    return sorted(set(result.stdout.decode().strip("\0").split("\0")) - {""})


def broken_links(root: Path, paths: list[str]) -> list[str]:
    """Require relative Markdown targets to belong to the public candidate set."""
    public = set(paths)
    failures = []
    for path in paths:
        if not path.lower().endswith(".md"):
            continue
        text = (root / path).read_text()
        for match in re.finditer(r"!?\[[^\]\n]*\]\(([^)\s]+)\)", text):
            target = urlsplit(match.group(1))
            if target.scheme or target.netloc or not target.path:
                continue
            resolved = (root / path).parent.joinpath(unquote(target.path)).resolve()
            try:
                relative = resolved.relative_to(root.resolve()).as_posix()
            except ValueError:
                failures.append(f"{path}: target outside repository")
                continue
            if relative not in public and not any(p.startswith(relative + "/") for p in public):
                failures.append(f"{path}: unpublished or missing target {target.path}")
    return failures


def push_revisions(root: Path, lines: list[str]) -> list[str]:
    """Audit every outgoing commit, including files deleted again before the tip."""
    revisions: set[str] = set()
    for line in lines:
        _, local, _, remote = line.split()
        if set(local) == {"0"}:
            continue
        revisions.add(local)
        command = ["git", "rev-list", local]
        # A new ref may target an empty/different remote. Do not assume that
        # commits known to another local remote have already been published there.
        if set(remote) != {"0"}:
            command.append("^" + remote)
        result = subprocess.run(command, cwd=root, capture_output=True, text=True, check=True)
        revisions.update(result.stdout.splitlines())
    return sorted(revisions)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", help="Audit an existing commit instead of local candidates")
    parser.add_argument("--links", action="store_true", help="Check public relative Markdown links")
    parser.add_argument(
        "--pre-push", action="store_true", help="Audit outgoing refs from hook stdin"
    )
    args = parser.parse_args()
    if args.links and args.revision:
        parser.error("--links checks the working candidate set; omit --revision")
    root = Path(__file__).resolve().parents[1]
    if args.pre_push:
        if args.links or args.revision:
            parser.error("--pre-push cannot be combined with other modes")
        revisions = push_revisions(root, sys.stdin.read().splitlines())
        for revision in revisions:
            bad = [p for p in candidate_paths(root, revision=revision) if forbidden(p)]
            if bad:
                print(f"Push rejected: private paths in {revision}:\n" + "\n".join(bad))
                raise SystemExit(1)
        print(f"Pre-push file audit passed for {len(revisions)} outgoing revisions.")
        return
    paths = candidate_paths(root, revision=args.revision)
    bad = [p for p in paths if forbidden(p)]
    if bad:
        print("Private paths must not be published:\n" + "\n".join(bad))
        raise SystemExit(1)
    if args.links:
        failures = broken_links(root, paths)
        if failures:
            print("Broken public links:\n" + "\n".join(failures))
            raise SystemExit(1)
    print(f"Public file audit passed: {len(paths)} paths; no private tooling/data paths.")
    print("Scope: selected revision or current candidate set; historical commits are separate.")


if __name__ == "__main__":
    main()

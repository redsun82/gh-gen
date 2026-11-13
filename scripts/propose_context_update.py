#!/usr/bin/env python3
"""Refresh context definitions and maintain a single bot-owned update PR."""

import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
BRANCH = "gh-gen/context-updates"
FILES = ["src/ghgen/_context_data.json", "src/ghgen/_context_types.py"]
BOT_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"
MARKER = "<!-- gh-gen-context-update -->"
COMMIT_MARKER = "Gh-Gen-Context-Update: true"


def command(*args: str, input: str | None = None) -> str:
    result = subprocess.run(
        args, cwd=ROOT, input=input, text=True, stdout=subprocess.PIPE
    )
    if result.returncode:
        print(result.stdout, file=sys.stderr, end="")
        result.check_returncode()
    return result.stdout.strip()


def fixed_paths(data: dict) -> set[str]:
    result = set()

    def visit(index: int, path: str, parents: frozenset[int]):
        if index in parents or path == "github.event":
            return
        node = data["nodes"][index]
        for field, child in node.get("fields", {}).items():
            name = f"{path}.{field}" if path else field
            result.add(name)
            visit(child, name, parents | {index})
        if "map" in node:
            visit(node["map"], path + ".*", parents | {index})

    visit(data["root"], "", frozenset())
    return result


def summary(old: dict, new: dict) -> str:
    added = sorted(fixed_paths(new) - fixed_paths(old))
    removed = sorted(fixed_paths(old) - fixed_paths(new))
    lines = ["Refresh webhook payloads, context fields, and availability rules."]
    for label, paths in (("Added fields", added), ("Removed fields", removed)):
        if paths:
            rendered = ", ".join(f"`{path}`" for path in paths[:10])
            if len(paths) > 10:
                rendered += f" (+{len(paths) - 10} more)"
            lines.append(f"- {label}: {rendered}")
    old_events = {
        (event, activity)
        for event, types in old["events"].items()
        for activity in types
    }
    new_events = {
        (event, activity)
        for event, types in new["events"].items()
        for activity in types
    }
    if old_events != new_events:
        lines.append(
            f"- Event/activity variants: +{len(new_events - old_events)}, "
            f"-{len(old_events - new_events)}"
        )
    return "\n".join(lines) + "\n\n" + MARKER


def main() -> None:
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise RuntimeError("run this PR-writing script in its GitHub Actions workflow")
    repo = os.environ["GITHUB_REPOSITORY"]
    default = json.loads(
        command("gh", "repo", "view", repo, "--json", "defaultBranchRef")
    )["defaultBranchRef"]["name"]
    if os.environ["GITHUB_REF"] != f"refs/heads/{default}":
        raise RuntimeError("context updates must run from the default branch")
    if command("git", "status", "--porcelain"):
        raise RuntimeError("context updates require a clean checkout")
    old = json.loads((ROOT / FILES[0]).read_text())
    command("uv", "run", "python", "scripts/update_contexts.py", "--refresh")
    if not command("git", "diff", "--name-only"):
        print("Context definitions are current.")
        return
    changed = set(command("git", "diff", "--name-only").splitlines())
    if not changed <= set(FILES):
        raise RuntimeError(
            f"unexpected updater changes: {sorted(changed - set(FILES))}"
        )
    new = json.loads((ROOT / FILES[0]).read_text())
    body = summary(old, new)
    prs = json.loads(
        command(
            "gh",
            "pr",
            "list",
            "--repo",
            repo,
            "--head",
            BRANCH,
            "--state",
            "open",
            "--json",
            "number,author,body",
        )
    )
    remote = command("git", "ls-remote", "--heads", "origin", f"refs/heads/{BRANCH}")
    previous_sha = remote.split()[0] if remote else ""
    if previous_sha:
        if prs and (
            len(prs) != 1
            or not prs[0]["author"]["is_bot"]
            or MARKER not in prs[0]["body"]
        ):
            raise RuntimeError(
                "refusing to overwrite an update branch not owned by this bot"
            )
        command("git", "fetch", "origin", f"refs/heads/{BRANCH}")
        authors = set(
            command("git", "log", "HEAD..FETCH_HEAD", "--format=%ae").splitlines()
        )
        if authors - {BOT_EMAIL}:
            raise RuntimeError(
                "update branch has human commits; refusing to overwrite it"
            )
        if not prs:
            last_commit = command("git", "log", "-1", "FETCH_HEAD", "--format=%B")
            branch_files = set(
                command("git", "diff", "--name-only", "HEAD...FETCH_HEAD").splitlines()
            )
            if (
                not authors
                or COMMIT_MARKER not in last_commit
                or not branch_files <= set(FILES)
            ):
                raise RuntimeError("update branch has no recognizable bot PR or commit")
    elif prs:
        raise RuntimeError("update PR has no corresponding remote branch")
    command("uv", "run", "python", "scripts/update_contexts.py", "--check")
    command("uv", "run", "pytest", "-q")
    command("uv", "run", "gh-gen", "--check")
    command("git", "checkout", "-b", BRANCH)
    command("git", "config", "user.name", "github-actions[bot]")
    command("git", "config", "user.email", BOT_EMAIL)
    command("git", "add", "--", *FILES)
    command(
        "git", "commit", "-m", "Update GitHub context definitions", "-m", COMMIT_MARKER
    )
    command(
        "git",
        "push",
        f"--force-with-lease=refs/heads/{BRANCH}:{previous_sha}",
        "origin",
        f"HEAD:refs/heads/{BRANCH}",
    )
    title = "Update GitHub context definitions"
    if prs:
        command(
            "gh",
            "api",
            "--method",
            "PATCH",
            f"repos/{repo}/pulls/{prs[0]['number']}",
            "--input",
            "-",
            input=json.dumps({"title": title, "body": body}),
        )
    else:
        command(
            "gh",
            "api",
            "--method",
            "POST",
            f"repos/{repo}/pulls",
            "--input",
            "-",
            input=json.dumps(
                {"title": title, "body": body, "head": BRANCH, "base": default}
            ),
        )
    # Avoid suppression or approval gates on checks for token-created PRs.
    command("gh", "workflow", "run", "check.yml", "--repo", repo, "--ref", BRANCH)


if __name__ == "__main__":
    main()

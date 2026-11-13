import json
from pathlib import Path

import pytest

from scripts import propose_context_update as updater


def test_summary_is_concise():
    old = {
        "root": 0,
        "nodes": [{"fields": {"github": 1}}, {"fields": {}}],
        "events": {"push": {"default": 1}},
    }
    new = {
        "root": 0,
        "nodes": [{"fields": {"github": 1}}, {"fields": {"artifacts": 2}}, {}],
        "events": {"push": {"default": 1}, "merge_group": {"checks_requested": 2}},
    }
    text = updater.summary(old, new)
    assert "`github.artifacts`" in text
    assert "variants: +1, -0" in text
    assert text.endswith(updater.MARKER)
    assert len(text.splitlines()) <= 6


@pytest.fixture
def bot(monkeypatch, tmp_path):
    monkeypatch.setattr(updater, "ROOT", tmp_path)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    path = tmp_path / updater.FILES[0]
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"root": 0, "nodes": [{}], "events": {}}))
    calls = []

    def command(*args, input=None):
        calls.append((args, input))
        if args[:4] == ("gh", "repo", "view", "owner/repo"):
            return '{"defaultBranchRef":{"name":"main"}}'
        if args[:3] == ("git", "diff", "--name-only"):
            return "\n".join(updater.FILES)
        if args[:3] == ("gh", "pr", "list"):
            return "[]"
        return ""

    monkeypatch.setattr(updater, "command", command)
    return calls, command


def test_bot_creates_one_pr_and_dispatches_checks(bot):
    calls, _ = bot
    updater.main()
    commands = [args for args, _ in calls]
    assert ("uv", "run", "pytest", "-q") in commands
    push = next(args for args in commands if args[:2] == ("git", "push"))
    assert f"--force-with-lease=refs/heads/{updater.BRANCH}:" in push
    payload = next(
        json.loads(body)
        for args, body in calls
        if args[:4]
        == (
            "gh",
            "api",
            "--method",
            "POST",
        )
    )
    assert payload["head"] == updater.BRANCH
    assert commands[-1] == (
        "gh",
        "workflow",
        "run",
        "check.yml",
        "--repo",
        "owner/repo",
        "--ref",
        updater.BRANCH,
    )


def test_bot_refuses_human_owned_branch(bot, monkeypatch):
    _, original = bot

    def command(*args, input=None):
        if args[:2] == ("git", "ls-remote"):
            return f"abc refs/heads/{updater.BRANCH}"
        if args[:3] == ("gh", "pr", "list"):
            return '[{"number":1,"author":{"is_bot":false},"body":""}]'
        return original(*args, input=input)

    monkeypatch.setattr(updater, "command", command)
    with pytest.raises(RuntimeError, match="not owned"):
        updater.main()


def test_bot_refuses_human_commits(bot, monkeypatch):
    _, original = bot

    def command(*args, input=None):
        if args[:2] == ("git", "ls-remote"):
            return f"abc refs/heads/{updater.BRANCH}"
        if args[:3] == ("gh", "pr", "list"):
            return json.dumps(
                [{"number": 1, "author": {"is_bot": True}, "body": updater.MARKER}]
            )
        if args[:2] == ("git", "log"):
            return "human@example.com"
        return original(*args, input=input)

    monkeypatch.setattr(updater, "command", command)
    with pytest.raises(RuntimeError, match="human commits"):
        updater.main()


def test_bot_updates_owned_pr(bot, monkeypatch):
    calls, original = bot

    def command(*args, input=None):
        if args[:2] == ("git", "ls-remote"):
            return f"abc refs/heads/{updater.BRANCH}"
        if args[:3] == ("gh", "pr", "list"):
            return json.dumps(
                [{"number": 1, "author": {"is_bot": True}, "body": updater.MARKER}]
            )
        if args[:2] == ("git", "log"):
            return updater.BOT_EMAIL
        return original(*args, input=input)

    monkeypatch.setattr(updater, "command", command)
    updater.main()
    assert any(args[:4] == ("gh", "api", "--method", "PATCH") for args, _ in calls)
    push = next(args for args, _ in calls if args[:2] == ("git", "push"))
    assert f"--force-with-lease=refs/heads/{updater.BRANCH}:abc" in push


def test_bot_recovers_after_pr_creation_failed(bot, monkeypatch):
    calls, original = bot

    def command(*args, input=None):
        if args[:2] == ("git", "ls-remote"):
            return f"abc refs/heads/{updater.BRANCH}"
        if args[:2] == ("git", "log"):
            return (
                updater.BOT_EMAIL
                if args[-1] == "--format=%ae"
                else updater.COMMIT_MARKER
            )
        return original(*args, input=input)

    monkeypatch.setattr(updater, "command", command)
    updater.main()
    assert any(args[:4] == ("gh", "api", "--method", "POST") for args, _ in calls)


def test_command_failure_keeps_diagnostics(monkeypatch, capsys):
    import subprocess

    monkeypatch.setattr(
        updater.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args, 1, stdout="failure detail\n"
        ),
    )
    with pytest.raises(subprocess.CalledProcessError):
        updater.command("command")
    assert "failure detail" in capsys.readouterr().err


def test_bot_noops_without_changes(bot, monkeypatch):
    calls, original = bot

    def command(*args, input=None):
        if args[:3] == ("git", "diff", "--name-only"):
            return ""
        return original(*args, input=input)

    monkeypatch.setattr(updater, "command", command)
    updater.main()
    assert not any(args[:2] == ("git", "push") for args, _ in calls)


def test_bot_requires_default_branch(bot, monkeypatch):
    monkeypatch.setenv("GITHUB_REF", "refs/heads/feature")
    with pytest.raises(RuntimeError, match="default branch"):
        updater.main()


def test_bot_does_not_run_locally(monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    with pytest.raises(RuntimeError, match="GitHub Actions"):
        updater.main()

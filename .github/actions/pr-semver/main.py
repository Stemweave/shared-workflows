#!/usr/bin/env python3
"""Entry point of the pr-semver action: `main.py check` or `main.py release`.

Settings come from environment variables that action.yml fills from its inputs. The pull request
text is only ever read from the API here, never placed in a shell command.
"""
from __future__ import annotations

import os
import sys
import uuid

import description
import flow
from github_api import ApiError, GitHub


def _flag(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes")


def config_from_env(env) -> flow.Config:
    try:
        rules = description.Rules(
            require_standards=_flag(env.get("REQUIRE_STANDARDS", "true")),
            require_migration_for_major=_flag(env.get("REQUIRE_MIGRATION_FOR_MAJOR", "true")),
            min_section_chars=int(env.get("MIN_SECTION_CHARS", "20")),
        )
    except ValueError:
        raise flow.Failure("min_section_chars must be a whole number.") from None
    cfg = flow.Config(
        rules=rules,
        exempt_authors=frozenset(a.strip().lower() for a in env.get("EXEMPT_AUTHORS", "").split(",") if a.strip()),
        exempt_bump=env.get("EXEMPT_BUMP", "none").strip().lower(),
        tag_prefix=env.get("TAG_PREFIX", "v"),
        initial_version=env.get("INITIAL_VERSION", "0.0.0").strip(),
        update_major_tag=_flag(env.get("UPDATE_MAJOR_TAG", "false")),
        on_missing_pr=env.get("ON_MISSING_PR", "skip").strip().lower(),
        dry_run=_flag(env.get("DRY_RUN", "false")),
    )
    cfg.validate()
    return cfg


def _escape(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def annotate(level: str, message: str, title: str = "") -> None:
    suffix = f" title={_escape(title)}" if title else ""
    print(f"::{level}{suffix}::{_escape(message)}")


def write_outputs(values: dict[str, str]) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        for key, value in values.items():
            value = str(value)
            if "\n" in value:
                marker = f"ghadelimiter_{uuid.uuid4().hex}"
                handle.write(f"{key}<<{marker}\n{value}\n{marker}\n")
            else:
                handle.write(f"{key}={value}\n")


def write_summary(markdown: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(markdown + "\n")


def run_check(api, repo: str, cfg: flow.Config) -> int:
    raw = os.environ.get("PR_NUMBER", "").strip()
    if not raw.isdigit():
        raise flow.Failure("No pull request number. Call the check from a pull_request event.")
    evaluation = flow.check(api, repo, int(raw), cfg)

    write_outputs({"valid": str(not evaluation.problems).lower(), "bump": evaluation.bump})
    if evaluation.problems:
        for problem in evaluation.problems:
            annotate("error", problem, "Pull request description")
        write_summary(
            "## Pull request description needs changes\n\n"
            + "\n".join(f"- {p}" for p in evaluation.problems)
            + "\n\nThe headings and checkboxes come from the pull request template; edit the description to fix them."
        )
        print(f"PR #{evaluation.number} description is not valid.")
        return 1

    if evaluation.exempt:
        detail = f"Author {evaluation.author} is exempt from the template; version bump: **{evaluation.bump}**."
    else:
        detail = f"Version bump: **{evaluation.bump}**."
    write_summary(f"## Pull request description is valid\n\n{detail}")
    print(f"PR #{evaluation.number} is valid. Version bump: {evaluation.bump}")
    return 0


def run_release(api, repo: str, cfg: flow.Config) -> int:
    sha = os.environ.get("COMMIT_SHA", "").strip()
    default_branch = os.environ.get("DEFAULT_BRANCH", "").strip()
    if not sha or not default_branch:
        raise flow.Failure("commit_sha and default_branch are required in release mode.")

    plan = flow.plan_release(api, repo, sha, default_branch, cfg)
    for warning in plan.warnings:
        annotate("warning", warning, "Release")

    released = plan.action == "release" and not cfg.dry_run
    write_outputs(
        {
            "released": str(released).lower(),
            "bump": plan.bump,
            "version": plan.version,
            "tag": plan.tag,
            "previous_tag": plan.previous.name if plan.previous else "",
        }
    )

    if plan.action != "release":
        annotate("notice", plan.reason, "Release")
        write_summary(f"## No release\n\n{plan.reason}")
        flow.publish(api, repo, plan, sha, cfg)
        return 0

    flow.publish(api, repo, plan, sha, cfg)
    verb = "Would release" if cfg.dry_run else "Released"
    previous = plan.previous.name if plan.previous else "no earlier release"
    write_summary(f"## {verb} {plan.tag}\n\n{plan.reason} Previous: {previous}.\n\n{plan.notes}")
    print(f"{verb} {plan.tag} ({plan.bump}) from {repo}@{sha[:7]}")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in ("check", "release"):
        print("usage: main.py check|release", file=sys.stderr)
        return 2
    try:
        cfg = config_from_env(os.environ)
        repo = os.environ.get("GITHUB_REPOSITORY", "")
        if not repo:
            raise flow.Failure("GITHUB_REPOSITORY is not set.")
        api = GitHub(os.environ.get("GH_TOKEN", ""))
        return run_check(api, repo, cfg) if argv[1] == "check" else run_release(api, repo, cfg)
    except flow.Failure as failure:
        annotate("error", str(failure), "Semantic versioning")
        for problem in failure.problems:
            annotate("error", problem, "Pull request description")
        write_summary(
            f"## {failure}\n\n" + "\n".join(f"- {p}" for p in failure.problems) if failure.problems else f"## {failure}"
        )
        return 1
    except ApiError as error:
        annotate("error", str(error), "GitHub API")
        write_summary(f"## GitHub API error\n\n{error}")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))

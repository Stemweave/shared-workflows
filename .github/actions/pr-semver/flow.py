"""Decides whether a pull request is acceptable and what the next release is."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import description
import versions
from github_api import ApiError

NOTE_GROUPS = (("major", "Breaking changes"), ("minor", "Features"), ("patch", "Fixes"))


class Failure(Exception):
    """A problem to show the author or maintainer, with one line for each thing to fix."""

    def __init__(self, message: str, problems: list[str] | None = None):
        super().__init__(message)
        self.problems = problems or []


@dataclass
class Config:
    rules: description.Rules = field(default_factory=description.Rules)
    exempt_authors: frozenset[str] = frozenset()
    exempt_bump: str = "none"
    tag_prefix: str = "v"
    initial_version: str = "0.0.0"
    update_major_tag: bool = False
    on_missing_pr: str = "skip"
    dry_run: bool = False

    def validate(self) -> None:
        if self.exempt_bump not in description.BUMP_ORDER:
            raise Failure(f"exempt_bump must be one of {', '.join(description.BUMP_ORDER)}, not '{self.exempt_bump}'.")
        if self.on_missing_pr not in ("skip", "fail"):
            raise Failure(f"on_missing_pr must be skip or fail, not '{self.on_missing_pr}'.")
        try:
            versions.parse_version(self.initial_version)
        except ValueError as error:
            raise Failure(f"initial_version: {error}.") from None


@dataclass
class Evaluation:
    number: int
    title: str
    author: str
    url: str
    bump: str
    problems: list[str]
    parsed: description.Description | None
    exempt: bool


@dataclass
class Plan:
    action: str  # release, skip, or already_released
    reason: str = ""
    previous: versions.Tag | None = None
    bump: str = "none"
    version: str = ""
    tag: str = ""
    evaluations: list[Evaluation] = field(default_factory=list)
    notes: str = ""
    warnings: list[str] = field(default_factory=list)


def evaluate_pr(pr: dict, cfg: Config) -> Evaluation:
    author = (pr.get("user") or {}).get("login", "")
    base = dict(number=pr["number"], title=pr.get("title", ""), author=author, url=pr.get("html_url", ""))
    if author.lower() in cfg.exempt_authors:
        return Evaluation(**base, bump=cfg.exempt_bump, problems=[], parsed=None, exempt=True)
    parsed = description.parse(pr.get("body"), cfg.rules)
    return Evaluation(**base, bump=parsed.bump, problems=parsed.problems, parsed=parsed, exempt=False)


def check(api, repo: str, number: int, cfg: Config) -> Evaluation:
    return evaluate_pr(api.get(f"/repos/{repo}/pulls/{number}"), cfg)


def merged_prs_for_commit(api, repo: str, sha: str, default_branch: str) -> list[dict]:
    prs = api.get(f"/repos/{repo}/commits/{sha}/pulls") or []
    merged = [p for p in prs if p.get("merged_at") and (p.get("base") or {}).get("ref") == default_branch]
    exact = [p for p in merged if p.get("merge_commit_sha") == sha]
    if exact:
        return exact
    return sorted(merged, key=lambda p: p["merged_at"])[-1:]


def prs_since(api, repo: str, previous: versions.Tag, sha: str, default_branch: str) -> list[dict]:
    """Merged pull requests whose merge commit lies between the previous release and sha."""
    commit_shas: set[str] = set()
    base_date = None
    page = 1
    while True:
        data = api.get(f"/repos/{repo}/compare/{previous.sha}...{sha}", {"per_page": 100, "page": page})
        if base_date is None:
            base_date = _parse_date(data["base_commit"]["commit"]["committer"]["date"])
        commits = data.get("commits", [])
        commit_shas.update(c["sha"] for c in commits)
        if len(commits) < 100 or page >= 30:
            break
        page += 1

    cutoff = base_date - dt.timedelta(days=1)
    found = []
    query = {"state": "closed", "base": default_branch, "sort": "updated", "direction": "desc"}
    for pr in api.paginate(f"/repos/{repo}/pulls", query, max_pages=30):
        if _parse_date(pr["updated_at"]) < cutoff:
            break
        if pr.get("merged_at") and pr.get("merge_commit_sha") in commit_shas:
            found.append(pr)
    return found


def plan_release(api, repo: str, sha: str, default_branch: str, cfg: Config) -> Plan:
    raw_tags = list(api.paginate(f"/repos/{repo}/tags"))
    tags = versions.semver_tags(raw_tags, cfg.tag_prefix)
    previous = tags[-1] if tags else None

    released_here = [t for t in tags if t.sha == sha]
    if released_here:
        tag = released_here[-1]
        return Plan(
            "already_released",
            reason=f"{sha[:7]} is already released as {tag.name}.",
            previous=previous,
            version=versions.format_version(tag.version),
            tag=tag.name,
        )

    head_prs = merged_prs_for_commit(api, repo, sha, default_branch)
    if not head_prs:
        message = f"No merged pull request found for {sha[:7]} on {default_branch}. Changes should reach it through a pull request."
        if cfg.on_missing_pr == "fail":
            raise Failure(message)
        return Plan("skip", reason=message, previous=previous)

    warnings: list[str] = []
    candidates = {p["number"]: p for p in head_prs}
    if previous:
        try:
            for pr in prs_since(api, repo, previous, sha, default_branch):
                candidates.setdefault(pr["number"], pr)
        except (ApiError, KeyError) as error:
            warnings.append(
                f"Could not list pull requests merged since {previous.name} ({error}). "
                "Using only the pull request that triggered this run."
            )

    head_numbers = {p["number"] for p in head_prs}
    usable: list[Evaluation] = []
    for pr in sorted(candidates.values(), key=lambda p: p.get("merged_at") or ""):
        evaluation = evaluate_pr(pr, cfg)
        if not evaluation.problems:
            usable.append(evaluation)
        elif evaluation.number in head_numbers:
            raise Failure(
                f"Pull request #{evaluation.number} was merged with an invalid description, so no release was made. "
                "Fix the description, then re-run this workflow.",
                [f"#{evaluation.number}: {problem}" for problem in evaluation.problems],
            )
        else:
            warnings.append(
                f"#{evaluation.number} has an invalid description and adds no version bump: "
                + "; ".join(evaluation.problems)
            )

    bump = max((e.bump for e in usable), key=description.BUMP_ORDER.get, default="none")
    if bump == "none":
        return Plan(
            "skip",
            reason="No merged pull request since the last release asks for a version bump.",
            previous=previous,
            evaluations=usable,
            warnings=warnings,
        )

    current = previous.version if previous else versions.parse_version(cfg.initial_version)
    new_version = versions.format_version(versions.bump_version(current, bump))
    tag = f"{cfg.tag_prefix}{new_version}"
    if tag in {t.get("name") for t in raw_tags}:
        raise Failure(f"The tag {tag} already exists but is not a stable release of an earlier commit. Remove or rename it.")

    return Plan(
        "release",
        reason=f"{bump} bump from {len(usable)} merged pull request(s).",
        previous=previous,
        bump=bump,
        version=new_version,
        tag=tag,
        evaluations=usable,
        notes=build_notes(usable),
        warnings=warnings,
    )


def build_notes(evaluations: list[Evaluation]) -> str:
    blocks = []
    for bump, heading in NOTE_GROUPS:
        entries = [_entry(e) for e in evaluations if e.bump == bump]
        if entries:
            blocks.append(f"## {heading}\n\n" + "\n\n".join(entries))
    return "\n\n".join(blocks).strip() + "\n"


def _entry(evaluation: Evaluation) -> str:
    lines = [f"### {evaluation.title} (#{evaluation.number})", ""]
    parsed = evaluation.parsed
    if parsed:
        if description.meaningful(parsed.what, 1):
            lines += [parsed.what, ""]
        if description.meaningful(parsed.why, 1):
            lines += ["**Why**", "", parsed.why, ""]
        if evaluation.bump == "major" and description.meaningful(parsed.migration, 1):
            lines += ["**Breaking changes and migration**", "", parsed.migration, ""]
    byline = f"By @{evaluation.author}" if evaluation.author else "Merged"
    lines.append(f"{byline} in {evaluation.url}" if evaluation.url else byline)
    return "\n".join(lines)


def publish(api, repo: str, plan: Plan, sha: str, cfg: Config) -> None:
    if plan.action == "already_released":
        if cfg.update_major_tag and not cfg.dry_run:
            ensure_major_tag(api, repo, plan.tag, sha, cfg.tag_prefix)
        return
    if plan.action != "release" or cfg.dry_run:
        return
    api.post(
        f"/repos/{repo}/releases",
        {
            "tag_name": plan.tag,
            "target_commitish": sha,
            "name": plan.tag,
            "body": plan.notes,
            "draft": False,
            "prerelease": False,
            "make_latest": "true",
        },
    )
    if cfg.update_major_tag:
        ensure_major_tag(api, repo, plan.tag, sha, cfg.tag_prefix)


def ensure_major_tag(api, repo: str, tag: str, sha: str, prefix: str) -> None:
    """Points v1 at the newest 1.x.y release, so callers that pin @v1 get fixes."""
    major = versions.parse_version(tag[len(prefix) :])[0]
    if major < 1:
        return
    name = f"{prefix}{major}"
    try:
        api.patch(f"/repos/{repo}/git/refs/tags/{name}", {"sha": sha, "force": True})
    except ApiError as error:
        if error.status not in (404, 422):
            raise
        api.post(f"/repos/{repo}/git/refs", {"ref": f"refs/tags/{name}", "sha": sha})


def _parse_date(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))

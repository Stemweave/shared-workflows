"""Semantic version tags and bumping."""
from __future__ import annotations

import re
from dataclasses import dataclass

Version = tuple[int, int, int]


@dataclass(frozen=True)
class Tag:
    name: str
    version: Version
    sha: str


def parse_version(text: str) -> Version:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", text.strip())
    if not match:
        raise ValueError(f"'{text}' is not a MAJOR.MINOR.PATCH version")
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def format_version(version: Version) -> str:
    return ".".join(str(part) for part in version)


def semver_tags(raw_tags: list[dict], prefix: str) -> list[Tag]:
    """Keeps only stable MAJOR.MINOR.PATCH tags with the prefix, lowest version first.

    Pre-releases (v1.2.0-rc.1) and unrelated tags are ignored.
    """
    pattern = re.compile(rf"{re.escape(prefix)}(\d+\.\d+\.\d+)")
    tags = []
    for raw in raw_tags:
        match = pattern.fullmatch(raw.get("name", ""))
        if match:
            tags.append(Tag(raw["name"], parse_version(match.group(1)), (raw.get("commit") or {}).get("sha", "")))
    return sorted(tags, key=lambda tag: tag.version)


def bump_version(current: Version, bump: str) -> Version:
    major, minor, patch = current
    if bump == "major":
        return major + 1, 0, 0
    if bump == "minor":
        return major, minor + 1, 0
    if bump == "patch":
        return major, minor, patch + 1
    raise ValueError(f"'{bump}' is not a bump that produces a release")

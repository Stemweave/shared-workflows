"""Reads a pull request description written from the standard template.

The template's headings are a contract. A heading is matched ignoring case and punctuation, and
text inside HTML comments is ignored, so the guidance left in the template never counts as content.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

BUMP_ORDER = {"none": 0, "patch": 1, "minor": 2, "major": 3}

SECTION_ALIASES = {
    "what": ("what",),
    "why": ("why",),
    "bump": ("version bump",),
    "migration": ("breaking changes and migration", "breaking changes", "migration"),
    "testing": ("how it was tested", "testing", "how was it tested"),
    "standards": ("standards", "module standards", "workflow standards"),
}
SECTION_TITLES = {
    "what": "What",
    "why": "Why",
    "bump": "Version bump",
    "migration": "Breaking changes and migration",
    "testing": "How it was tested",
    "standards": "Standards",
}
_CANONICAL = {alias: name for name, aliases in SECTION_ALIASES.items() for alias in aliases}

_COMMENT = re.compile(r"<!--.*?-->", re.S)
_HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
_FENCE = re.compile(r"^ {0,3}(```|~~~)")
_CHECKBOX = re.compile(r"^\s*[-*+]\s+\[([ xX])\]\s+(.*\S)\s*$")
_BUMP_OPTION = re.compile(r"(major|minor|patch|none)\b")
_PLACEHOLDERS = {"", "n a", "na", "none", "nothing", "tbd", "tba", "todo", "wip"}


@dataclass
class Rules:
    require_standards: bool = True
    require_migration_for_major: bool = True
    min_section_chars: int = 20


@dataclass
class Description:
    bump: str = ""
    problems: list[str] = field(default_factory=list)
    what: str = ""
    why: str = ""
    migration: str = ""

    @property
    def valid(self) -> bool:
        return not self.problems


def _words(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def find_sections(body: str | None) -> dict[str, str]:
    """Maps each known section to its text. A section runs until the next heading of the same or a higher level."""
    text = _COMMENT.sub("", (body or "").replace("\r\n", "\n"))
    lines = text.split("\n")

    headings: list[tuple[int, int, str]] = []
    in_fence = False
    for index, line in enumerate(lines):
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        match = None if in_fence else _HEADING.match(line)
        if match:
            headings.append((index, len(match.group(1)), _words(match.group(2))))

    sections: dict[str, str] = {}
    for position, (start, level, title) in enumerate(headings):
        name = _CANONICAL.get(title)
        if name is None or name in sections:
            continue
        end = len(lines)
        for next_start, next_level, _ in headings[position + 1 :]:
            if next_level <= level:
                end = next_start
                break
        sections[name] = "\n".join(lines[start + 1 : end]).strip()
    return sections


def meaningful(text: str, min_chars: int) -> bool:
    return _words(text) not in _PLACEHOLDERS and len(text.strip()) >= min_chars


def checkboxes(text: str) -> list[tuple[str, bool]]:
    return [(m.group(2), m.group(1) != " ") for line in text.splitlines() if (m := _CHECKBOX.match(line))]


def parse(body: str | None, rules: Rules | None = None) -> Description:
    rules = rules or Rules()
    sections = find_sections(body)
    result = Description(
        what=sections.get("what", ""), why=sections.get("why", ""), migration=sections.get("migration", "")
    )
    problems = result.problems

    for name in ("what", "why"):
        title = SECTION_TITLES[name]
        if name not in sections:
            problems.append(f"Add a '{title}' section. Use the pull request template.")
        elif not meaningful(sections[name], rules.min_section_chars):
            problems.append(
                f"The '{title}' section is empty, a placeholder, or shorter than {rules.min_section_chars} characters."
            )

    result.bump = _read_bump(sections.get("bump"), problems)

    if result.bump != "none":
        if "testing" not in sections:
            problems.append("Add a 'How it was tested' section.")
        elif not meaningful(sections["testing"], rules.min_section_chars):
            problems.append(
                f"Describe how it was tested in 'How it was tested' (at least {rules.min_section_chars} characters)."
            )

    if result.bump == "major" and rules.require_migration_for_major:
        if not meaningful(result.migration, rules.min_section_chars):
            problems.append(
                "A major version bump needs 'Breaking changes and migration': say what breaks "
                "and what users must change."
            )

    if rules.require_standards:
        _check_standards(sections.get("standards"), problems)
    return result


def _read_bump(section: str | None, problems: list[str]) -> str:
    if section is None:
        problems.append("Add a 'Version bump' section. Use the pull request template.")
        return ""
    options = []
    for label, checked in checkboxes(section):
        found = _BUMP_OPTION.match(re.sub(r"[*_`]", "", label).strip().lower())
        if found:
            options.append((found.group(1), checked))
    if not options:
        problems.append("The 'Version bump' section has no Major, Minor, Patch, or None options. Use the template.")
        return ""
    chosen = [name for name, checked in options if checked]
    if not chosen:
        problems.append("Tick one option under 'Version bump': Major, Minor, Patch, or None.")
        return ""
    if len(set(chosen)) > 1:
        problems.append(f"Tick only one option under 'Version bump'. Ticked: {', '.join(sorted(set(chosen)))}.")
        return ""
    return chosen[0]


def _check_standards(section: str | None, problems: list[str]) -> None:
    if section is None:
        problems.append("Add a 'Standards' section with the checklist. Use the pull request template.")
        return
    items = checkboxes(section)
    if not items:
        problems.append("The 'Standards' section has no checklist. Use the pull request template.")
        return
    open_items = [label for label, checked in items if not checked]
    if open_items:
        shown = "; ".join(_shorten(label) for label in open_items[:5])
        more = f" and {len(open_items) - 5} more" if len(open_items) > 5 else ""
        problems.append(
            f"{len(open_items)} item(s) in 'Standards' are not ticked: {shown}{more}. "
            "Tick each one, and write N/A with a reason after any that doesn't apply."
        )


def _shorten(text: str, limit: int = 60) -> str:
    text = re.sub(r"[*_`]", "", text).strip()
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / ".github" / "actions" / "pr-semver"))

import description  # noqa: E402
import flow  # noqa: E402
import versions  # noqa: E402

SHA, OLD_SHA, MISSED_SHA = "a" * 40, "b" * 40, "c" * 40
MIGRATION = "The variable sku is now account_tier. Rename it in every module call."

BODY = """\
## What
Adds a storage account module with private endpoints.

## Why
Teams keep copying the network rules by hand and getting them wrong.

## Version bump
- [ ] **Major**: breaking change
- [x] **Minor**: new feature
- [ ] **Patch**: fix
- [ ] **None**: no release

## Breaking changes and migration
<!-- Required for Major. -->
N/A

## How it was tested
Ran terraform test and applied the example in a sandbox subscription.

## Standards
- [x] Inputs and outputs have descriptions
- [x] README is up to date
"""


def body(bump="minor", migration=None):
    text = BODY.replace("- [x] **Minor**", "- [ ] **Minor**").replace(f"- [ ] **{bump.title()}**", f"- [x] **{bump.title()}**")
    return text.replace("N/A", migration) if migration else text


def pr(number, text, sha=SHA, merged_at="2026-01-02T10:00:00Z", author="alice"):
    return {"number": number, "title": f"PR {number}", "body": text, "user": {"login": author},
            "html_url": f"https://github.com/acme/mod/pull/{number}", "merged_at": merged_at,
            "merge_commit_sha": sha, "base": {"ref": "main"}, "updated_at": merged_at}


class FakeApi:
    def __init__(self, tags=(), head=(), missed=()):
        self.tags, self.head, self.missed = list(tags), list(head), list(missed)

    def paginate(self, path, params=None, max_pages=50):
        yield from (self.tags if path.endswith("/tags") else self.head + self.missed)

    def get(self, path, params=None):
        if path.endswith("/pulls"):
            return self.head
        shas = [p["merge_commit_sha"] for p in self.head + self.missed]
        return {"base_commit": {"commit": {"committer": {"date": "2026-01-01T00:00:00Z"}}}, "commits": [{"sha": s} for s in shas]}


def tag(name, sha=OLD_SHA):
    return {"name": name, "commit": {"sha": sha}}


def plan(api):
    return flow.plan_release(api, "acme/mod", SHA, "main", flow.Config())


class Description(unittest.TestCase):
    def problems(self, text):
        return " | ".join(description.parse(text).problems)

    def test_filled_in_template_is_valid(self):
        result = description.parse(BODY)
        self.assertEqual((result.problems, result.bump), ([], "minor"))

    def test_each_bump_is_read(self):
        for bump in ("major", "minor", "patch", "none"):
            self.assertEqual(description.parse(body(bump, MIGRATION)).bump, bump)

    def test_untouched_template_is_rejected(self):
        blank = "## What\n<!-- x -->\n## Why\n<!-- y -->\n## Version bump\n- [ ] **Major**\n- [ ] **Minor**\n## Standards\n- [ ] a\n"
        text = self.problems(blank)
        for fragment in ("'What' section is empty", "'Why' section is empty", "Tick one option", "not ticked"):
            self.assertIn(fragment, text)

    def test_exactly_one_bump(self):
        self.assertIn("Tick only one", self.problems(BODY.replace("- [ ] **Patch**", "- [x] **Patch**")))

    def test_major_needs_a_migration(self):
        self.assertIn("major version bump needs", self.problems(body("major")))

    def test_unticked_standards_are_listed(self):
        self.assertIn("README is up to date", self.problems(BODY.replace("- [x] README", "- [ ] README")))

    def test_comments_and_code_blocks_are_not_content(self):
        sections = description.find_sections("## What\n<!--\n## Why\nhidden\n-->\n```\n## Why\nalso hidden\n```\n")
        self.assertNotIn("why", sections)


class Template(unittest.TestCase):
    def test_the_shipped_template_is_rejected_until_filled_in(self):
        path = pathlib.Path(__file__).resolve().parent.parent / ".github" / "PULL_REQUEST_TEMPLATE.md"
        text = path.read_text(encoding="utf-8")
        self.assertFalse(description.parse(text).valid)

        head, standards = text.split("## Standards")
        filled = head.replace("- [ ] **Patch**", "- [x] **Patch**")
        for heading in ("## What", "## Why", "## How it was tested"):
            filled = filled.replace(heading + "\n", heading + "\n\nSome real words that explain it well.\n", 1)
        filled += "## Standards" + standards.replace("- [ ]", "- [x]")
        self.assertEqual(description.parse(filled).problems, [])


class Versions(unittest.TestCase):
    def test_only_stable_tags_with_the_prefix_count_and_order_is_numeric(self):
        raw = [tag("v1.9.0"), tag("v1.10.0"), tag("v2.0.0-rc.1"), tag("latest"), tag("1.5.0")]
        self.assertEqual([t.name for t in versions.semver_tags(raw, "v")], ["v1.9.0", "v1.10.0"])

    def test_bumps(self):
        self.assertEqual(versions.bump_version((1, 2, 3), "major"), (2, 0, 0))
        self.assertEqual(versions.bump_version((1, 2, 3), "minor"), (1, 3, 0))
        self.assertEqual(versions.bump_version((1, 2, 3), "patch"), (1, 2, 4))


class Release(unittest.TestCase):
    def test_first_release_bumps_from_zero(self):
        for bump, expected in (("patch", "v0.0.1"), ("minor", "v0.1.0"), ("major", "v1.0.0")):
            result = plan(FakeApi(head=[pr(1, body(bump, MIGRATION))]))
            self.assertEqual((result.action, result.tag), ("release", expected))

    def test_bumps_from_the_latest_tag(self):
        result = plan(FakeApi(tags=[tag("v1.9.0"), tag("v1.10.0")], head=[pr(1, body("patch"))]))
        self.assertEqual(result.tag, "v1.10.1")

    def test_a_missed_merge_still_counts_and_the_highest_bump_wins(self):
        missed = pr(1, body("major", MIGRATION), sha=MISSED_SHA, merged_at="2026-01-02T09:00:00Z")
        result = plan(FakeApi(tags=[tag("v1.2.3")], head=[pr(2, body("patch"))], missed=[missed]))
        self.assertEqual((result.tag, [e.number for e in result.evaluations]), ("v2.0.0", [1, 2]))

    def test_invalid_description_on_the_merged_pr_fails(self):
        with self.assertRaises(flow.Failure):
            plan(FakeApi(head=[pr(1, "no description")]))

    def test_no_bump_no_release(self):
        self.assertEqual(plan(FakeApi(tags=[tag("v1.0.0")], head=[pr(1, body("none"))])).action, "skip")

    def test_rerun_does_not_release_twice(self):
        result = plan(FakeApi(tags=[tag("v1.0.0", SHA)], head=[pr(1, BODY)]))
        self.assertEqual((result.action, result.tag), ("already_released", "v1.0.0"))

    def test_dependency_bots_skip_the_template(self):
        cfg = flow.Config(exempt_authors=frozenset({"dependabot[bot]"}), exempt_bump="patch")
        api = FakeApi(tags=[tag("v1.0.0")], head=[pr(1, "Bumps azurerm", author="dependabot[bot]")])
        self.assertEqual(flow.plan_release(api, "acme/mod", SHA, "main", cfg).tag, "v1.0.1")


if __name__ == "__main__":
    unittest.main()

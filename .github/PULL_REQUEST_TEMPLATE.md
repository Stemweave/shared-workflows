<!--
Keep the headings as they are: the semantic versioning check reads them.
Text in comments like this one is ignored.
-->

## What

<!-- What does this change? One or two sentences a reviewer can read before opening the diff. -->

## Why

<!-- Why is it needed? The problem it solves, and a link to the ticket or issue. -->

## Version bump

<!-- Tick exactly one. Every caller pins @v1, so this decides who gets the change and when. -->

- [ ] **Major**: breaking change. Existing callers must change their workflow files
- [ ] **Minor**: new backwards-compatible feature or input
- [ ] **Patch**: backwards-compatible fix
- [ ] **None**: no release (docs or tests only)

## Breaking changes and migration

<!-- Required for Major. Say what breaks and what callers must change. Write N/A otherwise. -->

## How it was tested

<!-- Commands you ran and what you saw. Not needed for None. -->

## Standards

<!-- Tick every item. If one doesn't apply, tick it and write N/A and the reason after it. -->

- [ ] Each job asks for the least permissions it needs
- [ ] No untrusted text (PR title, body, branch name) goes into a shell command
- [ ] Third-party actions are pinned to a full commit SHA
- [ ] Every input has a description and a safe default
- [ ] Existing callers keep working, or the bump above is Major
- [ ] Tests pass and the README is up to date

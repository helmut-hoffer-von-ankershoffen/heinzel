---
name: review
description: Review the current change with the reviewer agents - critic and test-engineer always, plus architect, security-reviewer, dx-reviewer, docs-reviewer and release-engineer when the change touches their area - in parallel, and merge their findings into one go/no-go verdict. Use before committing a non-trivial change, before opening a pull request, or when asked to review.
---

# Review

1. Run `make check` first; reviewers shouldn't spend time on what tools catch. Note what changed and why
   (`git diff --stat`, `origin/main...HEAD` for a branch).
2. Start in parallel, each with the same short brief (what changed, why, which files):
   - `critic` and `test-engineer`, always;
   - `security-reviewer` for subprocesses, paths, plugin loading, dependencies, workflows, secrets, releasing;
   - `architect` for a new module, kind of let, head or dependency, or a changed public API;
   - `dx-reviewer` for commands, operations, help, output, errors, configuration, the Makefile, hooks or skills;
   - `docs-reviewer` for behaviour, settings, workflows, skills, agents or Markdown;
   - `release-engineer` for `.github/`, `scripts/`, tool configuration in `pyproject.toml`, `copier.yml`, the template.

   A change to Markdown, comments or generated files only needs `docs-reviewer`. Reviewers don't call each other.
3. Merge: deduplicate, keep the highest severity, attribute each finding to its reviewer.
4. Fix every **Critical** and **Required** finding (or reject it with a reason), re-run `make check`, and re-run a
   reviewer only for what it flagged.
5. Verdict: **GO** when no Critical or Required finding remains, otherwise **NO-GO** with the list.

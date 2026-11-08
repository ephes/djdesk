# Round 83 resume — B29 parked before review

Branch: `fix/r83-b29-dispatch-recovery`; base: `main` at
`2525eb61a5428772bde682fd6d784ae44f669138`. Origin verified as
`git@github.com:ephes/djdesk.git`. No merges, default-branch pushes, Actions
commands, deployments, releases, tags, or PRs were performed.

The required Claude review failed before reviewing any code:
`PROVIDER_ERROR`, authentication failed: "OAuth session expired and could not
be refreshed". Requested model/effort: `claude-opus-5-5`, `high`. This is **not
a review pass**. The owner explicitly requested parking the branch and stopping
on this condition so a Claude finisher can review and merge.

## Implemented, unreviewed B29

- `djdesk-command-dispatch-after-commit`: dispatch on commit, return requested
  while the saving transaction is pending, refresh immediate outcomes, and
  conditionally fail only unclaimed requests after enqueue errors.
- `djdesk-interrupted-run-explicit-recovery`: selected-ID recovery command,
  bounded to 100 IDs, requiring explicit owning-worker/command shutdown
  confirmation and a reason. Conditional running-state updates retain logs,
  record interruption, and prevent duplicate delivery from replaying commands.
- README and Unreleased changelog updated. Existing late-failure regression
  injects after `mark_finished` rather than after the removed no-op callback.

Local validation passed: `just ci` (locked sync, dependency audit: no known
vulnerabilities, Ruff, 123 Django tests, 53 Electron synthetic tests),
`just docs-html`, and separate `just lint` / `just test` runs. There is no
configured type-check command. Fixtures are synthetic; no native shell opened.
The two dispatch regressions were rerun with the original form restored and
failed: rollback dispatched once and the commit test entered enqueue inside
the transaction. Recovery tests initially failed because the command was absent.

Local artifacts, outside the repository, live in the parent workspace:
`b29-ci.log`, `b29-docs.log`, `b29-test.log`, `b29-before-fix.log`, and
`reviews/b29-r1/result.json` / `events.jsonl`. Cache environment:
`UV_CACHE_DIR=/Users/jochen/workspaces/ws-cx-r83-djdesk/.cache/uv`,
`npm_config_cache=/Users/jochen/workspaces/ws-cx-r83-djdesk/.cache/npm`,
`XDG_CACHE_HOME=/Users/jochen/workspaces/ws-cx-r83-djdesk/.cache`.

## Finisher steps

1. Read `AGENTS.md`, README, and the original owner request/report at
   `~/workspaces/ws-work-planning/reviews/scout-round83.md`; respect all cautions.
2. Fetch origin, rebase onto current `origin/main`, resolve changes, and rerun
   local checks. Review the **entire committed branch delta**: invoke the
   installed `claude-review-loop` harness with `--baseline-ref origin/main`,
   `--model claude-opus-5-5 --effort high`, a fresh run directory, and
   `--record-baseline`. A default review of clean HEAD would omit these changes.
3. Fix and review while making progress, at most four rounds; stop if the same
   required finding recurs. No merge without a completed clean or suggestion-only
   review. Fetch right before merge; rebase and rerun tests if origin moved.
4. Merge locally with `--no-ff` into main, run full local checks after merge,
   push main and verify with `git ls-remote`. Never run/wait for Actions.

## Remaining bundles — unimplemented

All six remaining bugs are still present in the inspected pinned main source;
no slice was skipped as already fixed. Reverify each against current trunk before
implementation; no new regressions or local PostgreSQL gate have run for them.

- B30: `djdesk-dashboard-history-bounded-projection`, then
  `djdesk-metadata-html-attribute-admission`.
- B31: `djdesk-notebook-filename-route-admission`, then
  `djdesk-workspace-slug-collision-backend-contract` (requires disposable local
  PostgreSQL collision integration test; PostgreSQL binaries are installed).
- B32: `djdesk-notebook-export-directory-ownership`, then
  `djdesk-notebook-export-complete-publication` (directory ownership first).

Keep fixes minimal, prove each regression fails without its fix, update docs
and Unreleased notes, and run `just ci` / `just docs-html` for each bundle.
Use public APIs, platform-independent tests, synthetic fixtures only, and no
writes under `~/projects`. No remote branch deletion or feature pushes except
explicitly parking unfinished work. Remove this temporary resume note when done.

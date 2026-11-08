# Round 83 continuation: B30 parked

Branch: `fix/r83-b30-dashboard-projection`, based on main
`810bf2851b487ef1ced37eeb1a1f9ed68df43a77`. Origin verified as
`git@github.com:ephes/djdesk.git`. Nothing from this continuation was merged.

B29 dispatch/recovery is already fixed on trunk and was skipped.

Implemented, awaiting review:

- `djdesk-dashboard-history-bounded-projection`: selector fields only; four scans
  and five runs for the selected workspace; no dashboard task log hydration;
  stable timestamp/PK order, also in the bounded status API.
- `djdesk-metadata-html-attribute-admission`: allowlisted class suffixes,
  finite bounded progress, and escaped remaining dynamic HTML attributes.

README and Unreleased changelog updated. Synthetic regressions failed before
the fix: dashboard hydrated 330 task runs, invalid selection loaded history,
and hostile metadata created an img element. Focused regressions now pass.
Local `just ci` passed: dependency audit, Ruff, 126 Django tests, 55 Node tests.
`just docs-html` passed. There is no configured separate type checker.

Review attempt: claude-review-loop, `claude-opus-5-5`, effort high. Harness
returned PROVIDER_ERROR before any review: OAuth session expired and could not
be refreshed. This is not a clean verdict; no findings were produced.
Local artifacts: `/Users/jochen/workspaces/ws-cx-r83c-djdesk/reviews/b30-round1/`.
Caller context: `/Users/jochen/workspaces/ws-cx-r83c-djdesk/b30-review-context.md`.

Resume after restoring Claude authentication. Fetch current trunk, rebase and
rerun local checks if it moved. Review the complete branch delta against main
(the changes are committed on this parked branch, so an ordinary worktree-only
review would be empty). Use the installed claude-review-loop skill with
`--model claude-opus-5-5 --effort high`; fix and re-review while progressing,
at most four rounds, stopping if the same required finding recurs. Remove this
temporary note before the final review/merge. Fetch immediately before merge;
if trunk moved, rebase and rerun checks. Only a clean or suggestion-only valid
review permits a local --no-ff merge. Run full local checks after merging, then
push main and verify with git ls-remote. No Actions runs or PRs.

Remaining bundles are unstarted and must be re-verified against current main:

1. B31: `djdesk-notebook-filename-route-admission`, then
   `djdesk-workspace-slug-collision-backend-contract` (requires local PostgreSQL
   integration coverage as well as synthetic diagnostic tests).
2. B32: `djdesk-notebook-export-directory-ownership`, then
   `djdesk-notebook-export-complete-publication`.

Source brief: `~/workspaces/ws-work-planning/reviews/scout-round83.md`.
Use synthetic portable fixtures, minimal fixes, docs/Unreleased updates, local
CI-equivalent checks and the same review/merge gate per bundle. Do not deploy,
release, tag, delete remote branches or use paid Actions. Push feature branches
only to park unfinished work. If OAuth blocks review again, park and stop.

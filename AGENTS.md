# Agent Guidelines

This repository is occasionally edited by LLM agents via the Codex CLI. Please read
before performing automated changes:

1. **Never commit files under `specs/`.** They are internal planning notes that must
   remain untracked. Keep the `.gitignore` rule intact.
2. Use `uv` and `just` recipes defined in `README.md` when installing dependencies,
   running tests, or building docs.
3. When editing files, prefer `apply_patch` and keep comments succinct (per developer
   instructions).
4. Do not amend user commits unless explicitly asked, and avoid destructive git
   commands (`reset --hard`, etc.).
5. Keep documentation and the changelog up to date whenever code changes warrant it.
6. Always run `just test` and `just lint` before committing.
7. Data Lab exports acknowledge success only after syncing the published workspace
   directory. A failed sync leaves the complete export visible and must report uncertain
   durability without rollback or automatic retry. Notebook reads check descriptor size,
   mtime and ctime around each bounded read, retrying at most three times before refusing
   concurrent edits. Keep these publication and editing contracts documented in README.md.

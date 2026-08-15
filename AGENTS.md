# AGENTS.md

## Cursor Cloud specific instructions

This repository is **documentation-only**. As of this writing it contains just:

- `README.md` — project overview
- `docs/architecture.md` — the full system-architecture design

The "Global Product Price Intelligence" product described in the docs is a **design/architecture proposal that has not been implemented yet**. There is:

- No application code or entry point
- No dependency manifest (`package.json`, `requirements.txt`, `pyproject.toml`, `go.mod`, etc.)
- No build system, tests, or lint configuration
- No services to run

Because of that, there is nothing to install, build, run, lint, or test. The startup update script is intentionally a no-op. Do not fabricate an application or add tooling/dependencies unless a task explicitly asks you to scaffold the app.

If/when real code is added (e.g. a frontend and an API/orchestrator per `docs/architecture.md`), update this section and the environment update script with the actual install/build/run/test commands for those services.

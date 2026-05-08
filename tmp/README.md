# Local scratch (`tmp/`)

Put **ephemeral** scripts, dumps, and profiling outputs here instead of the repository root.

- Contents are **gitignored** (except this file and `.gitkeep`).
- For committed benchmark reports use **`results/`**; for long-lived quarantine use **`archive/`**.

## Layout

| Path | Use |
|------|-----|
| **`scripts/`** | One-off `tmp_*.py` profiling / phase drivers (run from repo root: `python tmp/scripts/<name>.py`). |
| **`logs/`** | Debug NDJSON logs (e.g. from acceptance hardening when enabled). |

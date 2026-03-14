# AGENTS.md — neorev

## Overview

neorev is a single-file Python 3 CLI tool (`./neorev`) for interactive human review of unified diffs (git/jj). It reads a diff from stdin, renders hunks via `delta`, and lets the user annotate them. No build step, no dependencies beyond Python 3.10+ stdlib and the `delta` binary.

## Running

```sh
# Usage (no install needed):
jj show XXX | ./neorev output.txt
git diff HEAD~1 | ./neorev --clip review.txt
```

There are no tests or linting configured in the repo.

## Architecture

Single script: `neorev` (~1260 lines). Key sections separated by comment banners:

- **Data classes**: `Hunk`, `GlobalNote`, `ReviewState`, `DiffViewport`
- **Diff parsing**: `parse_diff()` → list of `Hunk`
- **Delta rendering**: pipes raw diff through the `delta` binary
- **Output formatting**: compact Markdown for LLM agents (`format_output()`)
- **Load/resume**: `load_previous_review()` / `apply_previous_review()` for resuming reviews
- **TUI / review loop**: raw terminal I/O (`termios`/`tty`), key handling, screen rendering

## Code Style

- Python 3.13+. No third-party imports.
- Dataclasses for all structured data. Functions grouped by concern with banner comments.
- Private helpers prefixed with `_`. Docstrings mandatory on all functions (imperative mood).
- Typing annotations mandatory on all function signatures.
- No verbose comments that paraphrase the code.
- Split large functions into small, single-responsibility ones when needed.
- All values that carry functionality (ANSI escape sequences, magic strings, thresholds, etc.) must be defined as named module-level constants. Never inline raw escape codes or unexplained literal values.

## Linting & Formatting

Code must pass all three:

```sh
ty check neorev
ruff check neorev
ruff format --check neorev
```

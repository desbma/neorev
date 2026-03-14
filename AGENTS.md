# AGENTS.md — neorev

## Overview

neorev is a single-file Python 3 CLI tool (`./neorev`) for interactive human review of unified diffs (git/jj). It reads a diff from stdin, renders hunks via `delta`, and lets the user annotate them. No build step, no external Python dependencies — only stdlib and the `delta` binary. Tested on Python 3.13+; may work on earlier Python 3 versions.

## Running

```sh
# Usage (no install needed):
jj show XXX | ./neorev output.txt
git diff HEAD~1 | ./neorev --clip review.txt
```

## Code Style

- Python 3.13+. No third-party imports.
- Dataclasses for all structured data.
- No `_` prefix on methods or functions. All names are plain, even internal helpers.
- Docstrings mandatory on all functions (imperative mood).
- Typing annotations mandatory on all function signatures.
- No verbose comments that paraphrase the code.
- Split large functions into small, single-responsibility ones when needed.
- Never inline raw escape codes, magic strings, thresholds, or unexplained literal values. All such values must be defined as named constants (module-level or class-level). No exceptions.

## Linting & Formatting

Code must pass all three:

```sh
ty check neorev
ruff check neorev
ruff format --check neorev
```

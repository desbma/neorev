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
- **IMPORTANT: Never inline raw escape codes, magic strings, thresholds, or unexplained literal values. All such values must be defined as named constants (module-level or class-level). No exceptions.**
- Do not add large section-separator comment blocks (e.g. `# ===...` banners). Use class docstrings and natural whitespace to organize code.

## Testing

Tests use `unittest` (stdlib only) and live in `test.py`.

- Tests must follow the same code style conventions as the main code (docstrings, type annotations, named constants, etc.).
- Tests must pass the same formatter, linter, and type checker as the main code.

```sh
# Run all tests:
./test.py

# Run a single test class:
python3 -m unittest test.TestParseDiff

# Run a single test method:
python3 -m unittest test.TestParseDiff.test_single_hunk
```

## Linting & Formatting

Code must pass all three:

```sh
ty check neorev
ruff check neorev
ruff format --check neorev
```

Tests must also pass:

```sh
ty check test.py
ruff check test.py
ruff format --check test.py
```

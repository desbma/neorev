# AGENTS.md — neorev

## Overview

neorev is a single-file Python 3 CLI tool (`./neorev`) for interactive human review of [Jujutsu](https://github.com/jj-vcs/jj) (`jj`) unified diffs. It reads a diff from stdin, renders hunks via `delta`, and lets the user annotate them. No build step: dependencies are declared in a [PEP 723](https://peps.python.org/pep-0723/) header and installed by `uv` through the shebang. Tested on Python 3.13+; may work on earlier Python 3 versions.

## Code Style

- Python 3.13+.
- Third-party imports are allowed, but each one must remove more code than it adds. Declare them in the PEP 723 header, and add them to the `tests` and `type-check` jobs of `.github/workflows/ci.yml`.
- Dataclasses for all structured data.
- No `_` prefix on any name (methods, functions, variables, attributes) unless it is genuinely unused (e.g. `for _ in range(n)`). All names are plain, even internal helpers.
- Docstrings mandatory on all functions (imperative mood).
- Typing:
  - Annotations mandatory on all function signatures. Always write the real type, never a string-quoted annotation.
  - Use `from __future__ import annotations` only when genuinely required for unresolved forward references.
  - Avoid `typing.Any`; use precise types, protocols, or generics instead. `Any` is acceptable only as a last resort when no precise type is feasible, never as a shortcut to skip proper typing.
  - Avoid `typing.cast`; prefer precise annotations, runtime narrowing (`isinstance` / assertions), or API shapes that type-check without casts.
- No verbose comments that paraphrase the code.
- Split large functions into small, single-responsibility ones when needed.
- Use `None` as the sentinel for "absent" / "not provided" values. Do not overload empty strings (`""`) or zero integers (`0`) to mean "no value" — those are legitimate values, not absence markers. This applies to function parameters, dataclass fields, return types, and CLI argument storage.
- Never inline raw escape codes, magic strings, thresholds, or unexplained literal values. All such values must be defined as named constants (module-level or class-level).
  - **Exception:** well-known external identifiers (e.g. environment variable names like `"XDG_STATE_HOME"`) that are static, external to this project, and whose variable name already encodes the value may be inlined.
- Group all module-level constants together at the top of the file, before class and function definitions.
- Do not add large section-separator comment blocks (e.g. `# ===...` banners). Use class docstrings and natural whitespace to organize code.
- At the end of any refactor, remove dead code (unused constants, types, helpers, and imports) before finishing.

## Bug Fixes

Unless stated otherwise, always use a red-green testing approach:

1. Investigate the bug and write a failing test that exercises the code path.
2. Implement the proper fix.
3. Check the added tests now pass successfully.

## Testing

Tests use `unittest`, live under `tests/`, and are split across logical modules. They import `neorev` as a module, so the interpreter running them needs the PEP 723 dependencies installed.

- Tests must follow the same code style conventions as the main code (docstrings, type annotations, named constants, etc.).
- Tests must pass the same formatter, linter, and type checker as the main code.

```sh
# Run all tests:
python3 -m unittest discover -s tests

# Run a single test class:
python3 -m unittest tests.test_diff.TestParseDiff

# Run a single test method:
python3 -m unittest tests.test_diff.TestParseDiff.test_single_hunk
```

## Linting & Formatting

Code and tests must pass all three (`ty` needs the third-party dependencies importable):

```sh
uvx --with rich ty check neorev tests
ruff check neorev tests
ruff format --check neorev tests
```

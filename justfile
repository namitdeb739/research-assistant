# Show all recipes
default:
    @just --list

# Install dependencies and set up dev environment
setup:
    #!/usr/bin/env bash
    set -euo pipefail
    uv sync --dev
    # Both hook types are required: pre-commit alone installs .git/hooks/pre-commit
    # and silently skips the commitizen hook, which is staged on commit-msg.
    uv run pre-commit install --hook-type pre-commit --hook-type commit-msg

# Run all checks (mirrors CI)
check: lint typecheck test

# Lint
lint:
    uv run ruff check src/ tests/
    uv run ruff format --check src/ tests/

# Type check
typecheck:
    uv run mypy src/ tests/

# Auto-fix lint and formatting
fix:
    uv run ruff check --fix src/ tests/
    uv run ruff format src/ tests/

# Run the tests (vault-marked tests deselected)
test:
    uv run pytest

# The tests that read a real Obsidian vault — needs VAULT_PAPERS_DIR
test-vault:
    uv run pytest -m vault

# Check dependencies for known vulnerabilities
audit:
    uv run pip-audit

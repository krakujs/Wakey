# Wakey — developer entrypoints. All targets are host-safe (see docs/engineering-standards.md §3).
SHELL := /bin/bash
PY    := .venv/bin/python
BIN   := .venv/bin

.DEFAULT_GOAL := help

.PHONY: help install check format test headers headers-check clean

help: ## show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[33m%-16s\033[0m %s\n", $$1, $$2}'

install: ## create venv and install wakey + dev tools
	python3 -m venv .venv
	$(BIN)/pip install --upgrade pip
	$(BIN)/pip install -e ".[dev]"

check: ## everything a commit must pass: format, lint, types, tests (host-safe caps)
	$(BIN)/ruff format --check src tests scripts
	$(BIN)/ruff check src tests scripts
	$(BIN)/mypy
	$(PY) scripts/add_spdx_headers.py --check
	$(BIN)/pytest

test: ## tests with capped parallelism (≤4 workers — host guardrail)
	$(BIN)/pytest -n 4

format: ## auto-format and autofix
	$(BIN)/ruff format src tests scripts
	$(BIN)/ruff check --fix src tests scripts

headers: ## add missing SPDX license headers
	$(PY) scripts/add_spdx_headers.py

headers-check: ## verify SPDX headers only
	$(PY) scripts/add_spdx_headers.py --check

clean: ## remove caches and build artifacts
	rm -rf .pytest_cache .mypy_cache .ruff_cache dist build *.egg-info
	find . -name "__pycache__" -type d -not -path "./.venv/*" -exec rm -rf {} +

# Wakey — developer entrypoints. All targets are host-safe (see docs/engineering-standards.md §3).
SHELL := /bin/bash
PY    := .venv/bin/python
BIN   := .venv/bin

.DEFAULT_GOAL := help

serve: ## start the local server with the installed virtual environment
	WAKEY_AUTO_MASTER_KEY=true $(BIN)/wakey serve

.PHONY: help install check format test headers headers-check clean gate-sandbox image-dev

help: ## show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[33m%-16s\033[0m %s\n", $$1, $$2}'

install: ## create venv and install wakey + dev tools
	python3 -m venv .venv
	$(BIN)/pip install --upgrade pip
	$(BIN)/pip install -e ".[dev]"

check: ## everything a commit must pass: format, lint, types, tests, coverage floors
	$(BIN)/ruff format --check src tests scripts demo
	$(BIN)/ruff check src tests scripts demo
	$(BIN)/mypy
	$(PY) scripts/add_spdx_headers.py --check
	$(BIN)/pytest --cov=wakey --cov-report=term-missing --cov-fail-under=90
	$(BIN)/coverage report --include=*/wakey/security/* --fail-under=95

test: ## tests with capped parallelism (≤4 workers — host guardrail)
	$(BIN)/pytest -n 4

format: ## auto-format and autofix
	$(BIN)/ruff format src tests scripts demo
	$(BIN)/ruff check --fix src tests scripts demo

headers: ## add missing SPDX license headers
	$(PY) scripts/add_spdx_headers.py

headers-check: ## verify SPDX headers only
	$(PY) scripts/add_spdx_headers.py --check

clean: ## remove caches and build artifacts
	rm -rf .pytest_cache .mypy_cache .ruff_cache dist build *.egg-info
	find . -name "__pycache__" -type d -not -path "./.venv/*" -exec rm -rf {} +

demo-two-services: ## run the two-dummy-service pipeline demo (7 scenarios)
	.venv/bin/python demo/two_service_demo.py

demo-m0: ## M0 gate rehearsal with simulated GitHub (no live calls)
	.venv/bin/python demo/m0_gate_rehearsal.py

bench: ## resource benchmark vs NFR-6 budgets (host-safe, synthetic)
	.venv/bin/python scripts/bench_resources.py

image-dev: ## build the local server image used by the sandbox gate
	docker build -t wakey:dev .

image-sandbox: ## build the sandbox-gate image (wakey + pytest)
	docker build -f Dockerfile.sandbox -t wakey:sandbox .

gate-sandbox: ## E10-T2 sandbox gate: negative tests inside a capped, network-less container
	docker build -f Dockerfile.sandbox -t wakey:sandbox .
	docker run --rm --network none --memory 512m --cpus 1 --pids-limit 64 \
		--read-only --tmpfs /tmp:rw,size=64m \
		-e SANDBOX_GATE=1 -v $(PWD)/tests:/app/tests:ro -w /app wakey:sandbox \
		python -m pytest tests/sandbox -q -p no:cacheprovider

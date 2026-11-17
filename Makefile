.PHONY: bootstrap check lint typecheck test secrets restricted hooks wt-tree wt-env

PY := .venv/bin/python

bootstrap:
	uv venv -q --python 3.12 .venv
	uv pip install -q --python $(PY) --require-hashes -r requirements-dev.lock.txt
	uv pip install -q --python $(PY) --no-deps -e .

lint:
	.venv/bin/ruff check .
	.venv/bin/ruff format --check .

typecheck:
	.venv/bin/mypy src tests scripts

test:
	$(PY) -m pytest

# gitleaks over the whole history, not only the staged files.
secrets:
	gitleaks git --no-banner --redact .

restricted:
	$(PY) scripts/check_restricted.py

check: restricted lint typecheck test secrets

# Needs pre-commit and gitleaks on PATH (for example `uv tool install pre-commit`, `brew install gitleaks`).
hooks:
	pre-commit install --hook-type pre-commit
	install -m 755 scripts/pre-push .git/hooks/pre-push

wt-tree:
	scripts/wt-tree.sh

# A separate venv with trading-lab-research's own hashed runtime lock at the pin, for code that imports wt.
wt-env: wt-tree
	uv venv -q --python 3.12 .venv-wt
	uv pip install -q --python .venv-wt/bin/python --require-hashes -r .wt-tree/requirements.lock.txt
	uv pip install -q --python .venv-wt/bin/python --no-deps -e .

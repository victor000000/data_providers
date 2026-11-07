.PHONY: setup test build datasets plan docs-check

setup:
	./scripts/setup-native.sh

test:
	./scripts/native.sh python -m pytest -q

build:
	UV_PYTHON_INSTALL_DIR="$(CURDIR)/.python-native" uv build --python .venv-native/bin/python

datasets:
	./scripts/native.sh data-providers datasets

plan:
	./scripts/native.sh data-providers maintain

docs-check:
	./scripts/native.sh python scripts/check_docs.py --cli

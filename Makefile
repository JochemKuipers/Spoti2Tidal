PY ?= python
PIP ?= $(PY) -m pip

.PHONY: help install dev-install run lint format clean

help:
	@echo "Available targets:"
	@echo "  install       Install runtime dependencies"
	@echo "  dev-install   Install dev tools (ruff, black, mypy, pyinstaller)"
	@echo "  run           Run application"
	@echo "  lint          Run ruff and black --check"
	@echo "  typecheck     Run mypy type checker"
	@echo "  format        Format code with black and ruff"
	@echo "  clean         Remove build/cache artifacts"

install:
	$(PIP) install -r requirements.txt

dev-install: install
	$(PIP) install -U pip
	$(PIP) install ruff black mypy pyinstaller

run:
	$(PY) -m main

build:
	pyinstaller --onefile main.py

lint:
	ruff check .
	black --check .

fix:
	ruff check . --fix

typecheck:
	$(PY) -m mypy .

format:
	black .
	ruff format .

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.py[co]" -delete
	rm -rf dist
	rm -rf build
	rm -rf main.spec
	rm -rf .ruff_cache

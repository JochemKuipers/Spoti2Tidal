PY ?= python
PIP ?= $(PY) -m pip

.PHONY: help install dev-install run lint format clean

help:
	@echo "Available targets:"
	@echo "  install       Install runtime dependencies"
	@echo "  dev-install   Install dev tools (ruff, pyinstaller)"
	@echo "  run           Run application"
	@echo "  lint          Lint code with ruff"
	@echo "  format        Format code with ruff"
	@echo "  clean         Remove build/cache artifacts"

install:
	$(PIP) install -r requirements.txt

dev-install: install
	$(PIP) install -U pip
	$(PIP) install ruff pyinstaller

run:
	$(PY) -m main

build:
	pyinstaller --onefile main.py

lint:
	ruff check .

format:
	ruff format .

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.py[co]" -delete
	rm -rf dist
	rm -rf build
	rm -rf main.spec
	rm -rf .ruff_cache

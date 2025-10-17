PY ?= python3
PIP ?= $(PY) -m pip
SRC_DIR := src

.PHONY: help install dev-install run lint format test clean

help:
	@echo "Available targets:"
	@echo "  install       Install runtime dependencies"
	@echo "  dev-install   Install dev tools (ruff, black, pytest)"
	@echo "  run           Run application"
	@echo "  lint          Lint code with ruff"
	@echo "  format        Format code with black"
	@echo "  test          Run tests with pytest"
	@echo "  clean         Remove build/cache artifacts"

install:
	$(PIP) install -r requirements.txt

dev-install: install
	$(PIP) install -U pip
	$(PIP) install ruff black pytest pyinstaller

run:
	$(PY) -m $(SRC_DIR).main

build:
	pyinstaller --onefile $(SRC_DIR)/main.py

lint:
	ruff check $(SRC_DIR)

format:
	black $(SRC_DIR)

test:
	pytest -q

clean:
	rm -rf .pytest_cache
	find $(SRC_DIR) -type d -name "__pycache__" -exec rm -rf {} +
	find $(SRC_DIR) -type f -name "*.py[co]" -delete
	rm -rf dist
	rm -rf build
	rm -rf main.spec
	rm -rf .ruff_cache
	rm -rf .black_cache

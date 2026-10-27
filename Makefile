# Common tasks. Individual pipeline steps: `.venv/bin/beps <step>` (see `beps --help`).
PY ?= .venv/bin/python
# PYTHONPATH fallback: Python 3.14 on macOS may skip hidden editable-install .pth files.
export PYTHONPATH := src

.PHONY: setup run app test lint all clean

setup:   ## create .venv and install the package with dev and app extras
	python3 -m venv .venv
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -e ".[dev,app]"

run:     ## every step, raw CSV -> reports and app tables (downloads the CSV if missing)
	$(PY) -m beps_prioritizer run

app:     ## open the Streamlit app at http://localhost:8501
	$(PY) -m streamlit run app/streamlit_app.py

test:
	$(PY) -m pytest -q

lint:
	$(PY) -m ruff check src tests app

all: lint run test  ## lint, rebuild everything, then test

clean:
	rm -rf data/processed/* reports/*.md .pytest_cache .ruff_cache

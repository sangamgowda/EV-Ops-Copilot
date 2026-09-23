.PHONY: help setup up down logs seed seed-local collect ingest eval test lint fmt clean

help:
	@echo "setup   create venv and install dev deps"
	@echo "up      docker compose up --build"
	@echo "down    stop and remove containers"
	@echo "logs    follow api logs"
	@echo "seed    load synthetic data (inside docker)"
	@echo "seed-local  load synthetic data from this machine into localhost:5432"
	@echo "collect fetch web pages listed in data/sources.yaml"
	@echo "eval    run the golden set"
	@echo "test    pytest"
	@echo "lint    ruff + mypy"
	@echo "fmt     ruff format"

setup:
	python -m venv .venv && . .venv/bin/activate && \
	pip install --upgrade pip && pip install -r requirements-dev.txt
	@test -f .env || cp .env.example .env
	@echo "Now put your Groq key in .env"

up:
	docker compose up --build

down:
	docker compose down

logs:
	docker compose logs -f api

seed:
	docker compose exec api python scripts/seed_synthetic_data.py --reset

seed-local:
	POSTGRES_HOST=localhost python scripts/seed_synthetic_data.py --reset

collect:
	python scripts/collect_web_data.py --sources data/sources.yaml \
		--anonymize data/anonymize.local.yaml --to-markdown data/raw/docs

eval:
	docker compose exec api python scripts/run_eval.py

test:
	PYTHONPATH=src pytest tests/ -v

lint:
	ruff check src/ tests/ scripts/
	mypy src/

fmt:
	ruff format src/ tests/ scripts/
	ruff check --fix src/ tests/ scripts/

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage

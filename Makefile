# ShockGraph PEA. Run `make help`. On Windows, run make from Git Bash (winget install ezwinports.make).
.PHONY: help env venv install role run test test-unit test-db test-db-stop bench-db lint format \
	    docker-build docker-run docker-up docker-down docker-logs llm-pull seed gds ml clean tree

ifeq ($(OS),Windows_NT)
PYTHON ?= python
VENV_BIN := .venv/Scripts
else
PYTHON ?= python3
VENV_BIN := .venv/bin
endif
PY := $(VENV_BIN)/python

TEST_URI := bolt://localhost:7688
TEST_PASSWORD := testpassword
BENCH_URI := bolt://localhost:7689
BENCH_PASSWORD := benchpassword
NEO4J_IMAGE := neo4j:5.26-community
TEST_ENV := NEO4J_TEST_URI=$(TEST_URI) NEO4J_TEST_PASSWORD=$(TEST_PASSWORD) LLM_PROVIDER=fake

help:
	@echo "Setup"
	@echo "  make env            Create .env from .env.example (then edit the passwords)"
	@echo "  make install        Create .venv, install requirements and the git hooks"
	@echo "  make role ROLE=b    Tell git (and your agent) which role you are: a, b or c"
	@echo "Develop"
	@echo "  make run            Run the API locally with reload on http://localhost:8000/docs"
	@echo "  make test-db        Start the disposable test Neo4j (port 7688, GDS) and wait for it"
	@echo "  make test           Full pytest suite with coverage (needs make test-db for Neo4j tests)"
	@echo "  make test-unit      Tests that need no database"
	@echo "  make lint           pylint on app, scripts and ml (must stay >= 9.5)"
	@echo "  make format         black"
	@echo "Data"
	@echo "  make seed           Run the full ingestion into the dev database (docker-run first)"
	@echo "  make gds            Recompute GDS projections and algorithms"
	@echo "  make ml             Train the ML model and write predictions"
	@echo "Docker"
	@echo "  make docker-build   Build the API image"
	@echo "  make docker-run     Start the full stack (nginx :80, api, neo4j, ollama) in the background"
	@echo "  make llm-pull       Download the local LLMs into the Ollama container (once, ~7 GB)"
	@echo "  make docker-logs    Follow the logs"
	@echo "  make docker-down    Stop the stack"
	@echo "  make bench-db       Start the benchmark Neo4j (port 7689)"
	@echo "Misc"
	@echo "  make clean          Remove caches"
	@echo "  make tree           Show the project tree"

env:
	@if [ ! -f .env ]; then cp .env.example .env && echo "Created .env: edit NEO4J_PASSWORD and API_KEY"; \
	else echo ".env already exists"; fi

venv:
	@if [ ! -d .venv ]; then $(PYTHON) -m venv .venv && $(PY) -m pip install --upgrade pip; fi

install: venv
	$(PY) -m pip install -r requirements.txt
	$(VENV_BIN)/pre-commit install --hook-type pre-commit --hook-type commit-msg

role:
	@if [ -z "$(ROLE)" ]; then echo "Usage: make role ROLE=a|b|c"; exit 1; fi
	git config shockgraph.role $(ROLE)
	@echo "You are role $(ROLE): read docs/roles/$(ROLE).md"

run:
	$(PY) -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

test-db:
	-docker rm -f shockgraph-neo4j-test >/dev/null 2>&1
	docker run -d --name shockgraph-neo4j-test -p 7688:7687 -p 7475:7474 \
	    -e NEO4J_AUTH=neo4j/$(TEST_PASSWORD) -e 'NEO4J_PLUGINS=["graph-data-science"]' $(NEO4J_IMAGE)
	$(PY) -m scripts.dev.wait_neo4j --uri $(TEST_URI) --password $(TEST_PASSWORD)

test-db-stop:
	-docker rm -f shockgraph-neo4j-test

bench-db:
	-docker rm -f shockgraph-neo4j-bench >/dev/null 2>&1
	docker run -d --name shockgraph-neo4j-bench -p 7689:7687 \
	    -e NEO4J_AUTH=neo4j/$(BENCH_PASSWORD) -e 'NEO4J_PLUGINS=["graph-data-science"]' \
	    -e NEO4J_server_memory_heap_max__size=2G $(NEO4J_IMAGE)
	$(PY) -m scripts.dev.wait_neo4j --uri $(BENCH_URI) --password $(BENCH_PASSWORD)

test:
	$(TEST_ENV) $(PY) -m pytest --cov --cov-report=term-missing --cov-report=xml

test-unit:
	$(TEST_ENV) $(PY) -m pytest -m "not neo4j and not gds and not containers"

lint:
	$(PY) -m pylint app scripts ml --fail-under=9.5

format:
	$(PY) -m black app scripts ml tests

docker-build:
	docker compose build

docker-run: env
	docker compose up -d --build
	@echo "API: http://localhost:$${HTTP_PORT:-80}/docs  |  Neo4j browser: http://localhost:7474"

docker-up: docker-run

docker-down:
	docker compose down

docker-logs:
	docker compose logs -f --tail=100

llm-pull:
	docker compose exec ollama ollama pull qwen2.5:7b
	docker compose exec ollama ollama pull qwen2.5:3b

seed:
	$(PY) -m scripts.seed_data

gds:
	$(PY) -m scripts.gds_refresh

ml:
	$(PY) -m ml.train

clean:
	-find . -type d -name "__pycache__" -prune -exec rm -rf {} +
	-rm -rf .pytest_cache .coverage coverage.xml htmlcov

tree:
	@find . -maxdepth 3 -not -path "./.git*" -not -path "./.venv*" -not -path "*__pycache__*" | sort

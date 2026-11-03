COMPOSE ?= docker compose
TEST_DATABASE_URL ?= postgresql+psycopg://postgres:postgres@db:5432/urban_thread_test

.PHONY: up down logs migrate replay replay-events replay-orders replay-spend inspect test lint sql-check reset

up:
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f api worker

migrate:
	$(COMPOSE) run --rm migrate

replay:
	$(COMPOSE) run --rm replay

replay-events:
	$(COMPOSE) run --rm replay python -m scripts.replay --data-dir /app/data --base-url http://api:8000 --concurrency 15 --only events

replay-orders:
	$(COMPOSE) run --rm replay python -m scripts.replay --data-dir /app/data --base-url http://api:8000 --concurrency 15 --only orders

replay-spend:
	$(COMPOSE) run --rm replay python -m scripts.replay --data-dir /app/data --base-url http://api:8000 --concurrency 15 --only spend

inspect:
	$(COMPOSE) run --rm replay python -m scripts.inspect_dataset --data-dir /app/data

test:
	$(COMPOSE) run --rm -e TEST_DATABASE_URL=$(TEST_DATABASE_URL) api pytest

lint:
	$(COMPOSE) run --rm api ruff check app scripts dashboard tests

sql-check:
	$(COMPOSE) exec -T db psql -U postgres -d urban_thread -f /queries.sql

reset:
	@echo "Destructive: stopping containers and deleting the local PostgreSQL volume."
	$(COMPOSE) down -v
	$(COMPOSE) up -d --build

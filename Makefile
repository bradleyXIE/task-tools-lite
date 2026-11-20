# One-command Docker environment. No target removes volumes or databases.
.DEFAULT_GOAL := help
COMPOSE_DEV = docker compose -f docker-compose.yml -f docker-compose.dev.yml

.PHONY: help run stop restart logs ps shell env config dev dev-docker test test-docker lint

help:          ## Show this help
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed -E 's/:.*## /\t/'

run: env       ## Build (if needed) and start the whole packaged environment: Postgres + API + Web, all in Docker
	docker compose up -d --build --wait
	@echo ""
	@echo "Task Tools is running:  UI http://127.0.0.1:$${WEB_PORT:-3010}   API http://127.0.0.1:$${API_PORT:-8010}"
	@echo "Set LLM_API_KEY in .env, then run: make restart"

stop:          ## Stop the environment (data volumes are kept)
	docker compose down

restart:       ## Restart api and web (after editing .env)
	docker compose restart api web

logs:          ## Follow logs
	docker compose logs -f

ps:            ## Show container status
	docker compose ps

shell:         ## Open a shell inside the api container (where the agent's Bash tool runs)
	docker compose exec api bash

env:           ## Create .env from .env.example if it does not exist (never overwrites)
	@[ -f .env ] || (cp .env.example .env && echo "Created .env - set LLM_API_KEY in it")

config:        ## Print the resolved compose config with secrets masked (never run the raw compose config command)
	@docker compose config | sed -E 's/((KEY|TOKEN|SECRET|PASSWORD|PASSWD|DATABASE_URL)[A-Z_]*: *).*/\1***/I; s#(://[^:/@ ]+:)[^@ ]+@#\1***@#'

dev:           ## Host dev mode (Postgres in Docker, backend and web on the host with hot reload)
	./dev.sh

dev-docker:    ## Container dev mode (hot reload inside containers)
	$(COMPOSE_DEV) up --build

test:          ## Run pytest on the host against the isolated *_test database
	cd backend && .venv/bin/pytest

test-docker:   ## Run pytest inside the dev api container (isolated *_test database)
	$(COMPOSE_DEV) up -d --wait postgres
	$(COMPOSE_DEV) run --rm api pytest

lint:          ## Lint the backend
	cd backend && .venv/bin/ruff check .

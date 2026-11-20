# Thin wrappers. No target removes volumes or databases.
COMPOSE_DEV = docker compose -f docker-compose.yml -f docker-compose.dev.yml

.PHONY: up down logs config dev dev-docker test test-docker lint

up:            ## Build and start the containerised stack
	docker compose up -d --build --wait

down:          ## Stop the stack (data volumes are kept)
	docker compose down

logs:          ## Follow logs
	docker compose logs -f

config:        ## Print the resolved compose config with secrets masked (never run raw `docker compose config`)
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

lint:
	cd backend && .venv/bin/ruff check .

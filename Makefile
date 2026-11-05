# Shortcuts. Run `make help`.
.DEFAULT_GOAL := help
COMPOSE := docker compose

help:  ## show this list
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-14s %s\n", $$1, $$2}'

setup:  ## first-time: create .env
	@test -f .env || cp .env.example .env
	@echo ".env ready"

up: setup  ## start everything (builds images the first time)
	$(COMPOSE) up -d --build
	@echo "Waiting for services..." && sleep 20
	@$(COMPOSE) ps

index:  ## load Confluence + Jira into the search index
	$(COMPOSE) exec agent-api kms index-all

reindex:  ## wipe the index and load again (needed after switching model mode)
	curl -s -X DELETE localhost:6333/collections/kms_confluence >/dev/null; curl -s -X DELETE localhost:6333/collections/kms_jira >/dev/null
	$(COMPOSE) exec agent-api kms index-all

ask:  ## ask from the terminal: make ask Q="How do I deploy?" AS=alice
	$(COMPOSE) exec agent-api kms ask "$(Q)" --as $(or $(AS),alice) --api http://localhost:8000

test:  ## fast unit tests (no services needed)
	$(COMPOSE) run --rm --no-deps agent-api sh -c "pip install -q --user pytest pytest-asyncio && python -m pytest -q -p no:cacheprovider /app/tests/test_units.py"

test-acl:  ## access-control suite against the running stack (must be 100%)
	$(COMPOSE) exec -e KMS_E2E=1 -e KMS_JIRA_URL=http://mock-atlassian:9000 agent-api sh -c "pip install -q --user pytest pytest-asyncio && python -m pytest -q -p no:cacheprovider /app/tests/test_access_control_e2e.py"

eval:  ## golden-set quality gate
	$(COMPOSE) exec agent-api python /app/eval/run_eval.py --api http://localhost:8000 --out /tmp/report.json

audit:  ## show the latest audit events
	$(COMPOSE) exec postgres psql -U kms -d kms -c "SELECT ts, event_type, user_id, doc_id, decision, reason FROM audit.events ORDER BY id DESC LIMIT 25;"

logs:  ## follow logs of the main services
	$(COMPOSE) logs -f --tail=50 agent-api mcp-atlassian ingest-worker

down:  ## stop everything (keeps data)
	$(COMPOSE) down

clean:  ## stop and delete all data and images built here
	$(COMPOSE) down -v --rmi local

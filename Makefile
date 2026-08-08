.PHONY: dev migrate test worker lint fmt

# ── Local dev ─────────────────────────────────────────────────────────────────
dev:
	docker compose up --build

dev-bg:
	docker compose up -d --build

down:
	docker compose down

# ── Database ──────────────────────────────────────────────────────────────────
migrate:
	docker compose exec backend alembic upgrade head

migrate-create:
	@read -p "Migration message: " msg; \
	docker compose exec backend alembic revision --autogenerate -m "$$msg"

db-shell:
	docker compose exec db psql -U kidschores -d kidschores

# ── Testing ───────────────────────────────────────────────────────────────────
test:
	docker compose exec backend pytest tests/ -v --cov=app --cov-report=term-missing

test-state:
	docker compose exec backend pytest tests/test_state_machine.py -v

test-ledger:
	docker compose exec backend pytest tests/test_ledger.py -v

# ── Workers ───────────────────────────────────────────────────────────────────
worker:
	docker compose exec worker celery -A app.workers.celery_app worker --beat --loglevel=info

# ── Code quality ──────────────────────────────────────────────────────────────
lint:
	cd backend && ruff check app/ tests/

fmt:
	cd backend && ruff format app/ tests/

typecheck:
	cd backend && mypy app/

# ── iOS ───────────────────────────────────────────────────────────────────────
ios-test:
	xcodebuild test \
	  -project ios/KidsChores.xcodeproj \
	  -scheme KidsChores \
	  -destination 'platform=iOS Simulator,name=iPhone 16' \
	  | xcbeautify

# ── Helpers ───────────────────────────────────────────────────────────────────
logs:
	docker compose logs -f backend worker

shell:
	docker compose exec backend bash

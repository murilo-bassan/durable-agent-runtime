.PHONY: db test api worker demo chaos up

db:
	docker compose up -d db

test: db
	python -m pytest

api:
	RUNTIME_EMBEDDED_WORKER=true python -m uvicorn runtime.api:app --port 8700

worker:
	python -m runtime.worker

demo:
	python scripts/demo.py http://localhost:8700

chaos: db
	python scripts/chaos.py

up:
	docker compose up --build

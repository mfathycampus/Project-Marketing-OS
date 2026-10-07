# Project Marketing OS

Multi-project marketing content platform: Claude (strategy/copy) → design provider → approval → calendar → publishing.

## Run (backend)
```
docker compose up -d db
cd backend && pip install -e .[dev]
cp ../.env.example ../.env   # set ANTHROPIC_API_KEY
alembic upgrade head
uvicorn app.main:app --reload
pytest
```
See `docs/decisions.md`.

# TradeAI Phase-1 API

Production-oriented FastAPI foundation for the TradeAI frontend.

## Included
- Anonymous buyer sessions (no buyer registration required)
- RFQ creation and progressive qualification
- Explainable Purchase Intent Score (0–100)
- Research / Needs More Information / Qualified / Hot states
- OTP-ready verification flow (development OTP `123456`; production provider intentionally not hard-coded)
- Controlled supplier release: first 3, then 2 per later batch
- Supplier matching using category, geography, verification/trade and response signals
- Seller opportunity feed
- Structured quotations with landed-price calculation
- Objective quote comparison (lowest landed price / fastest delivery / longest warranty)
- Admin overview and basic risk flags
- SQLite persistence for Phase 1; database path is configurable

## Run locally
```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8001
```

API docs: `http://127.0.0.1:8001/docs`

## Tests
```bash
cd backend
pytest -q
```

## Environment
- `TRADEAI_DB` database path
- `TRADEAI_ENV=development|production`
- `TRADEAI_DEV_OTP=123456`
- `TRADEAI_OTP_TTL_MINUTES=10`
- `TRADEAI_QUALIFIED_THRESHOLD=60`
- `TRADEAI_CORS_ORIGINS=https://tradeai-pgvr.onrender.com`

For production, replace the development OTP delivery with the WhatsApp provider and migrate persistence to PostgreSQL. The API contract does not need to change.

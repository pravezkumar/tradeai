TRADEAI — AI-QUALIFIED B2B TRADE NETWORK

CURRENT STATUS
- Complete interactive frontend is deployed on Render.
- Seller/Supplier Portal now includes Create Account → Login → 5-step onboarding → Dashboard.
- Frontend is a self-contained index.html with embedded CSS + JavaScript.
- Phase-1 FastAPI backend foundation is now in /backend.
- Backend is not connected to the public frontend yet.
- No production API keys are committed to GitHub.

PHASE-1 BACKEND INCLUDED
- Anonymous buyer sessions; no compulsory buyer registration.
- Progressive RFQ qualification.
- Explainable Purchase Intent Score 0–100.
- Research / Needs More Information / Qualified / Hot states.
- OTP-ready verification flow; development OTP 123456.
- Controlled supplier matching: first 3 suppliers, then 2 per later batch.
- Seller account creation and login API foundation (PBKDF2 password hashing; no plaintext passwords).
- Seller opportunity feed.
- Structured quotations and landed-price calculation.
- Objective quote comparison.
- Admin overview and basic risk flags.
- SQLite persistence for development; PostgreSQL is the intended production database.

LOCAL BACKEND
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8001

TESTS
cd backend
pytest -q

PUBLIC FRONTEND
https://tradeai-pgvr.onrender.com

NEXT PRODUCTION MILESTONE
Deploy the backend with persistent PostgreSQL, then connect the frontend to live API endpoints. After that, replace development OTP with WhatsApp delivery and add production AI extraction/qualification.

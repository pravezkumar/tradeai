import os
from pathlib import Path

TEST_DB = Path(__file__).with_name("test_tradeai.db")
os.environ["TRADEAI_DB"] = str(TEST_DB)
os.environ["TRADEAI_ENV"] = "development"
if TEST_DB.exists():
    TEST_DB.unlink()

from fastapi.testclient import TestClient
from main import app, init_db

init_db()
client = TestClient(app)


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["ok"] is True


def test_buyer_qualification_otp_top3_quote_flow():
    s = client.post("/api/buyer/sessions", json={"company": "Demo Buyer"}).json()["session_id"]
    rfq = client.post("/api/rfqs", json={
        "session_id": s,
        "requirement": "Need OPC cement for a commercial construction project",
        "category": "cement",
        "quantity": "500 bags",
        "location": "Roorkee Uttarakhand",
        "timeline": "within 7 days",
        "specifications": "OPC 53 grade",
    }).json()
    assert rfq["intent_score"] >= 60
    rid = rfq["id"]

    send = client.post("/api/otp/send", json={"session_id": s, "phone": "+919999999999"}).json()
    assert send["dev_otp"] == "123456"

    verify = client.post("/api/otp/verify", json={"session_id": s, "code": "123456"})
    assert verify.status_code == 200 and verify.json()["verified"] is True

    matches = client.post(f"/api/rfqs/{rid}/matches/release?batch=1").json()["matches"]
    assert len(matches) == 3

    q = client.post(f"/api/rfqs/{rid}/quotes", json={
        "supplier_id": matches[0]["supplier_id"],
        "unit_price": 350,
        "quantity": 500,
        "tax_percent": 18,
        "freight": 5000,
        "delivery_days": 3,
        "warranty_months": 0,
        "payment_terms": "50% advance",
        "validity_days": 7,
    })
    assert q.status_code == 201 and q.json()["landed_price"] > 0

    comp = client.get(f"/api/rfqs/{rid}/quotes/compare").json()
    assert len(comp["quotes"]) == 1


def test_low_information_stays_research():
    s = client.post("/api/buyer/sessions", json={}).json()["session_id"]
    rfq = client.post("/api/rfqs", json={"session_id": s, "requirement": "cement please"}).json()
    assert rfq["status"] in ("research", "needs_more_information")
    assert len(rfq["next_questions"]) > 0

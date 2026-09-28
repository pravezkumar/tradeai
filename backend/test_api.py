import os
from pathlib import Path

TEST_DB = Path(__file__).with_name("test_tradeai.db").resolve()
os.environ["DATABASE_URL"] = "sqlite:///" + TEST_DB.as_posix()
os.environ["TRADEAI_ENV"] = "development"
if TEST_DB.exists():
    TEST_DB.unlink()

from fastapi.testclient import TestClient
from main import app, init_db

init_db()
client = TestClient(app)


def new_buyer():
    return client.post("/api/buyer/sessions", json={"company": "Demo Buyer"}).json()["session_id"]


def full_rfq(session_id):
    return client.post("/api/rfqs", json={
        "session_id": session_id,
        "requirement": "Need OPC cement for a commercial construction project",
        "category": "cement",
        "quantity": "500 bags",
        "location": "Roorkee Uttarakhand",
        "timeline": "within 7 days",
        "specifications": "OPC 53 grade",
    }).json()


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["ok"] is True


def test_otp_is_required_before_supplier_release():
    s = new_buyer()
    rfq = full_rfq(s)
    assert rfq["intent_score"] >= 60
    assert rfq["status"] == "verification_required"
    blocked = client.post(f"/api/rfqs/{rfq['id']}/matches/release?batch=1")
    assert blocked.status_code == 409
    assert "OTP verification required" in blocked.json()["detail"]


def test_buyer_qualification_otp_top3_quote_flow():
    s = new_buyer()
    rfq = full_rfq(s)
    rid = rfq["id"]

    send = client.post("/api/otp/send", json={"session_id": s, "phone": "+919999999999"}).json()
    assert send["dev_otp"] == "123456"

    verify = client.post("/api/otp/verify", json={"session_id": s, "code": "123456"})
    assert verify.status_code == 200 and verify.json()["verified"] is True
    assert verify.json()["rfqs"][0]["status"] in ("qualified", "hot")

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


def test_seller_account_login_and_profile():
    create = client.post("/api/seller/accounts", json={
        "full_name": "Test Seller",
        "business_name": "Test Industries",
        "mobile": "+919812345678",
        "email": "seller@test.local",
        "seller_type": "Manufacturer",
        "category": "Industrial Supplies",
        "password": "TradeAITest123",
    })
    assert create.status_code == 201
    assert create.json()["seller"]["status"] == "profile_pending"

    login = client.post("/api/seller/login", json={"login": "seller@test.local", "password": "TradeAITest123"})
    assert login.status_code == 200
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    me = client.get("/api/seller/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["seller"]["business_name"] == "Test Industries"

    saved = client.put("/api/seller/profile", headers=headers, json={
        "gstin": "05ABCDE1234F1Z5",
        "capabilities": ["bulk supply", "gst invoice"],
        "service_locations": ["Roorkee", "Haridwar"],
        "capacity": "1000 units/month",
        "moq": "10 units",
    })
    assert saved.status_code == 200
    assert saved.json()["status"] == "verification_pending"


def test_low_information_stays_research():
    s = new_buyer()
    rfq = client.post("/api/rfqs", json={"session_id": s, "requirement": "cement please"}).json()
    assert rfq["status"] in ("research", "needs_more_information")
    assert len(rfq["next_questions"]) > 0

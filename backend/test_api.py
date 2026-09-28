import os
from pathlib import Path

TEST_DB = Path(__file__).with_name("test_tradeai.db").resolve()
os.environ["DATABASE_URL"] = "sqlite:///" + TEST_DB.as_posix()
os.environ["TRADEAI_ENV"] = "development"
os.environ["TRADEAI_ADMIN_TOKEN"] = "test-admin-token"
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
    assert send["channel"] == "development"
    events = client.get(f"/api/notifications/{s}").json()
    assert any(x["kind"] == "buyer_otp" for x in events)

    verify = client.post("/api/otp/verify", json={"session_id": s, "code": "123456"})
    assert verify.status_code == 200 and verify.json()["verified"] is True
    assert verify.json()["rfqs"][0]["status"] in ("qualified", "hot")

    matches = client.post(f"/api/rfqs/{rid}/matches/release?batch=1").json()["matches"]
    assert len(matches) == 3

    blocked = client.post(f"/api/rfqs/{rid}/quotes")
    assert blocked.status_code == 410

    comp = client.get(f"/api/buyer/rfqs/{rid}/quotes/compare?session_id={s}").json()
    assert len(comp["quotes"]) == 0


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


def test_verified_registered_seller_receives_real_opportunity():
    create = client.post("/api/seller/accounts", json={
        "full_name": "Cement Seller",
        "business_name": "Roorkee Cement Supply",
        "mobile": "+919812345679",
        "email": "cement@test.local",
        "seller_type": "Distributor / Wholesaler",
        "category": "cement",
        "password": "TradeAITest123",
    })
    assert create.status_code == 201
    sid = create.json()["seller"]["id"]

    login = client.post("/api/seller/login", json={"login": "cement@test.local", "password": "TradeAITest123"}).json()
    headers = {"Authorization": "Bearer " + login["access_token"]}
    saved = client.put("/api/seller/profile", headers=headers, json={
        "gstin": "05ABCDE1234F1Z6",
        "capabilities": ["cement", "bulk supply", "gst invoice"],
        "service_locations": ["Roorkee", "Uttarakhand"],
        "capacity": "5000 bags/month",
        "moq": "50 bags",
    })
    assert saved.status_code == 200 and saved.json()["matching_eligible"] is False

    verified = client.put(
        f"/api/admin/sellers/{sid}/verification",
        headers={"X-Admin-Token": "test-admin-token"},
        json={"verification_level": "Trade Verified"},
    )
    assert verified.status_code == 200 and verified.json()["matching_eligible"] is True

    s = new_buyer()
    rfq = full_rfq(s)
    client.post("/api/otp/send", json={"session_id": s, "phone": "+919999999997"})
    client.post("/api/otp/verify", json={"session_id": s, "code": "123456"})
    first = client.post(f"/api/rfqs/{rfq['id']}/matches/release?batch=1").json()["matches"]
    second = client.post(f"/api/rfqs/{rfq['id']}/matches/release?batch=2").json()["matches"]
    released_ids = [x["supplier_id"] for x in first + second]
    assert sid in released_ids

    opps = client.get("/api/seller/opportunities", headers=headers)
    assert opps.status_code == 200
    assert any(x["rfq_id"] == rfq["id"] for x in opps.json())


def test_authenticated_seller_quote_and_buyer_comparison():
    login = client.post("/api/seller/login", json={"login": "cement@test.local", "password": "TradeAITest123"}).json()
    headers = {"Authorization": "Bearer " + login["access_token"]}
    s = new_buyer()
    rfq = full_rfq(s)
    rid = rfq["id"]
    client.post("/api/otp/send", json={"session_id": s, "phone": "+919999999996"})
    client.post("/api/otp/verify", json={"session_id": s, "code": "123456"})
    client.post(f"/api/rfqs/{rid}/matches/release?batch=1")
    client.post(f"/api/rfqs/{rid}/matches/release?batch=2")
    sent = client.post(f"/api/seller/rfqs/{rid}/quotes", headers=headers, json={
        "unit_price": 340, "quantity": 500, "tax_percent": 18, "freight": 2500,
        "delivery_days": 4, "warranty_months": 6, "payment_terms": "30% advance",
        "validity_days": 7, "notes": "OPC 53 grade"
    })
    assert sent.status_code == 201 and sent.json()["status"] == "sent"
    mine = client.get("/api/seller/quotes", headers=headers)
    assert mine.status_code == 200 and any(x["rfq_id"] == rid for x in mine.json())
    buyer = client.get(f"/api/buyer/rfqs/{rid}/quotes/compare?session_id={s}")
    assert buyer.status_code == 200
    assert any(x["supplier_id"] == sent.json()["supplier_id"] for x in buyer.json()["quotes"])


def test_deal_room_consent_and_order_lifecycle():
    login = client.post("/api/seller/login", json={"login": "cement@test.local", "password": "TradeAITest123"}).json()
    headers = {"Authorization": "Bearer " + login["access_token"]}
    s = new_buyer()
    rfq = full_rfq(s)
    rid = rfq["id"]
    client.post("/api/otp/send", json={"session_id": s, "phone": "9999999995"})
    client.post("/api/otp/verify", json={"session_id": s, "code": "123456"})
    client.post(f"/api/rfqs/{rid}/matches/release?batch=1")
    client.post(f"/api/rfqs/{rid}/matches/release?batch=2")
    sent = client.post(f"/api/seller/rfqs/{rid}/quotes", headers=headers, json={
        "unit_price": 340, "quantity": 500, "tax_percent": 18, "freight": 2500,
        "delivery_days": 4, "warranty_months": 6, "validity_days": 7
    }).json()
    deal = client.post(f"/api/buyer/rfqs/{rid}/deal-room", json={
        "session_id": s, "quote_id": sent["quote_id"], "message": "Please confirm delivery."
    })
    assert deal.status_code == 201
    did = deal.json()["deal"]["id"]
    reply = client.post(f"/api/seller/deals/{did}/messages", headers=headers, json={"message": "Delivery confirmed."})
    assert reply.status_code == 201
    consent = client.put(f"/api/buyer/deals/{did}/contact-consent", json={"session_id": s, "approved": True})
    assert consent.status_code == 200 and consent.json()["buyer_contact_shared"] is True
    order = client.post(f"/api/buyer/deals/{did}/orders", json={"session_id": s, "confirm_terms": True})
    assert order.status_code == 201 and order.json()["status"] == "confirmed"
    oid = order.json()["id"]
    progress = client.patch(f"/api/seller/orders/{oid}/status", headers=headers, json={"status": "in_progress"})
    assert progress.status_code == 200
    assert any(x["id"] == oid for x in client.get("/api/seller/orders", headers=headers).json())


def test_adaptive_qualification_fallback():
    r = client.post("/api/ai/qualify", json={
        "requirement": "Need 500 bags OPC 53 grade cement in Roorkee within 7 days",
        "answers": {}
    })
    assert r.status_code == 200
    data = r.json()
    assert data["fields"]["category"] == "cement"
    assert "500 bags" in data["fields"]["quantity"]
    assert data["fields"]["location"] == "Roorkee"
    assert data["next_field"] == "specifications"


def test_duplicate_verified_mobile_is_held_for_admin_review():
    s1 = new_buyer()
    r1 = full_rfq(s1)
    client.post("/api/otp/send", json={"session_id": s1, "phone": "+919999999991"})
    client.post("/api/otp/verify", json={"session_id": s1, "code": "123456"})

    s2 = new_buyer()
    r2 = full_rfq(s2)
    client.post("/api/otp/send", json={"session_id": s2, "phone": "+919999999991"})
    verified = client.post("/api/otp/verify", json={"session_id": s2, "code": "123456"})
    assert verified.status_code == 200
    detail = client.get(f"/api/rfqs/{r2['id']}").json()
    assert detail["fraud"]["decision"] == "review"
    assert detail["status"] == "manual_review"
    held = client.post(f"/api/rfqs/{r2['id']}/matches/release?batch=1")
    assert held.status_code == 409

    approved = client.put(
        f"/api/admin/rfqs/{r2['id']}/moderation",
        headers={"X-Admin-Token": "test-admin-token"},
        json={"action": "approve", "note": "Verified repeat procurement"}
    )
    assert approved.status_code == 200
    released = client.post(f"/api/rfqs/{r2['id']}/matches/release?batch=1")
    assert released.status_code == 200


def test_admin_controls_and_automatic_second_batch():
    unauth = client.get("/api/admin/overview")
    assert unauth.status_code == 401
    headers = {"X-Admin-Token": "test-admin-token"}
    settings = client.put("/api/admin/settings", headers=headers, json={
        "first_batch": 3, "next_batch": 2, "auto_expand_minutes": 1,
        "min_responses": 2, "max_batches": 3, "duplicate_screening": True, "auto_expand": True
    })
    assert settings.status_code == 200

    s = new_buyer()
    rfq = client.post("/api/rfqs", json={
        "session_id": s,
        "requirement": "Need industrial electrical panels for a new warehouse project",
        "category": "electrical",
        "quantity": "12 panels",
        "location": "Haridwar Uttarakhand",
        "timeline": "within 14 days",
        "specifications": "415V distribution panels"
    }).json()
    rid = rfq["id"]
    client.post("/api/otp/send", json={"session_id": s, "phone": "+919999999990"})
    client.post("/api/otp/verify", json={"session_id": s, "code": "123456"})
    first = client.post(f"/api/rfqs/{rid}/matches/release?batch=1")
    assert first.status_code == 200 and len(first.json()["matches"]) == 3

    from datetime import timedelta
    from sqlalchemy import update
    from main import engine, matches, utcnow
    with engine.begin() as db:
        db.execute(update(matches).where(matches.c.rfq_id == rid).values(released_at=utcnow()-timedelta(minutes=2)))

    routed = client.post("/api/admin/routing/run", headers=headers)
    assert routed.status_code == 200
    assert any(x["rfq_id"] == rid and x["batch"] == 2 for x in routed.json()["expanded"])
    all_matches = client.get(f"/api/rfqs/{rid}/matches").json()
    assert len([x for x in all_matches if x["batch"] == 2]) >= 1

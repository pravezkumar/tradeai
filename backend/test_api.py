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
    assert send["dev_otp"] == "1234"
    assert send["channel"] == "development"
    events = client.get(f"/api/notifications/{s}").json()
    assert any(x["kind"] == "buyer_otp" for x in events)

    verify = client.post("/api/otp/verify", json={"session_id": s, "code": "1234"})
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
    client.post("/api/otp/verify", json={"session_id": s, "code": "1234"})
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
    client.post("/api/otp/verify", json={"session_id": s, "code": "1234"})
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
    client.post("/api/otp/verify", json={"session_id": s, "code": "1234"})
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
    client.post("/api/otp/verify", json={"session_id": s1, "code": "1234"})

    s2 = new_buyer()
    r2 = full_rfq(s2)
    client.post("/api/otp/send", json={"session_id": s2, "phone": "+919999999991"})
    verified = client.post("/api/otp/verify", json={"session_id": s2, "code": "1234"})
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
    client.post("/api/otp/verify", json={"session_id": s, "code": "1234"})
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


def test_supplier_question_and_call_require_buyer_consent():
    login = client.post("/api/seller/login", json={"login": "cement@test.local", "password": "TradeAITest123"}).json()
    headers = {"Authorization": "Bearer " + login["access_token"]}
    s = new_buyer()
    rfq = full_rfq(s)
    rid = rfq["id"]
    phone = "+919999999989"
    client.post("/api/otp/send", json={"session_id": s, "phone": phone})
    client.post("/api/otp/verify", json={"session_id": s, "code": "1234"})
    client.post(f"/api/rfqs/{rid}/matches/release?batch=1")
    client.post(f"/api/rfqs/{rid}/matches/release?batch=2")

    question = client.post(f"/api/seller/rfqs/{rid}/requests", headers=headers, json={
        "kind": "ask_question", "message": "Can you accept delivery in two lots?"
    })
    assert question.status_code == 201 and question.json()["status"] == "pending"

    call = client.post(f"/api/seller/rfqs/{rid}/requests", headers=headers, json={
        "kind": "request_call", "message": "Please approve a short technical call."
    })
    assert call.status_code == 201 and call.json()["contact_consent"] is False
    call_id = call.json()["id"]

    seller_before = client.get("/api/seller/requests", headers=headers).json()
    pending_call = next(x for x in seller_before if x["id"] == call_id)
    assert pending_call["buyer_contact"] is None

    buyer_inbox = client.get(f"/api/buyer/requests?session_id={s}")
    assert buyer_inbox.status_code == 200
    assert len([x for x in buyer_inbox.json() if x["rfq_id"] == rid]) == 2

    answered = client.put(f"/api/buyer/requests/{question.json()['id']}", json={
        "session_id": s, "action": "reply", "message": "Yes, two lots are acceptable."
    })
    assert answered.status_code == 200 and answered.json()["status"] == "answered"

    approved = client.put(f"/api/buyer/requests/{call_id}", json={
        "session_id": s, "action": "approve"
    })
    assert approved.status_code == 200 and approved.json()["contact_shared"] is True

    seller_after = client.get("/api/seller/requests", headers=headers).json()
    approved_call = next(x for x in seller_after if x["id"] == call_id)
    assert approved_call["buyer_contact"] == phone

    other = new_buyer()
    forbidden = client.put(f"/api/buyer/requests/{call_id}", json={
        "session_id": other, "action": "decline"
    })
    assert forbidden.status_code == 403


def test_otp_is_hashed_at_rest_and_resend_is_rate_limited():
    from sqlalchemy import select
    from main import engine, otp_codes
    s = new_buyer()
    sent = client.post("/api/otp/send", json={"session_id": s, "phone": "+919999999988"})
    assert sent.status_code == 200
    code = sent.json()["dev_otp"]
    with engine.connect() as db:
        stored = db.execute(select(otp_codes.c.code).where(otp_codes.c.session_id == s)).scalar_one()
    assert stored != code
    assert len(stored) == 64
    again = client.post("/api/otp/send", json={"session_id": s, "phone": "+919999999988"})
    assert again.status_code == 429
    verified = client.post("/api/otp/verify", json={"session_id": s, "code": code})
    assert verified.status_code == 200
    with engine.connect() as db:
        assert db.execute(select(otp_codes.c.code).where(otp_codes.c.session_id == s)).scalar() is None


def test_health_exposes_safe_integration_readiness():
    h = client.get("/api/health")
    assert h.status_code == 200
    data = h.json()
    assert data["production_readiness"]["otp_hashed_at_rest"] is True
    assert data["production_readiness"]["otp_resend_cooldown_seconds"] >= 0
    assert "configured" in data["ai"] and "configured" in data["sms"] and data["sms"]["otp_digits"] == 4


def test_otp_contract_is_four_digit_sms():
    s = new_buyer()
    sent = client.post("/api/otp/send", json={"session_id": s, "phone": "+919999999987"})
    assert sent.status_code == 200
    data = sent.json()
    assert data["otp_digits"] == 4
    assert len(data["dev_otp"]) == 4 and data["dev_otp"].isdigit()
    assert data["channel"] == "development"
    invalid = client.post("/api/otp/verify", json={"session_id": s, "code": "123456"})
    assert invalid.status_code == 422


def test_returning_buyer_passwordless_recovery():
    from sqlalchemy import select
    from main import engine, buyer_recovery_tokens
    phone = "+919811112233"
    s = new_buyer()
    rfq = full_rfq(s)
    sent = client.post("/api/otp/send", json={"session_id": s, "phone": phone}).json()
    assert client.post("/api/otp/verify", json={"session_id": s, "code": sent["dev_otp"]}).status_code == 200

    start = client.post("/api/buyer/recovery/start", json={"phone": phone})
    assert start.status_code == 200
    challenge = start.json()
    assert len(challenge["dev_otp"]) == 4

    wrong = client.post("/api/buyer/recovery/verify", json={"challenge_id": challenge["challenge_id"], "code": "9999"})
    if challenge["dev_otp"] != "9999":
        assert wrong.status_code == 400

    verified = client.post("/api/buyer/recovery/verify", json={"challenge_id": challenge["challenge_id"], "code": challenge["dev_otp"]})
    assert verified.status_code == 200
    token = verified.json()["access_token"]
    assert verified.json()["workspace_count"] >= 1

    workspace = client.get("/api/buyer/recovery/workspace", headers={"Authorization": "Bearer " + token})
    assert workspace.status_code == 200
    data = workspace.json()
    item = next(x for x in data["rfqs"] if x["rfq_id"] == rfq["id"])
    assert item["session_id"] == s
    assert data["phone_masked"].endswith("2233")
    assert data["summary"]["rfqs"] >= 1

    with engine.connect() as db:
        stored = db.execute(select(buyer_recovery_tokens.c.token_hash).where(buyer_recovery_tokens.c.phone == "919811112233")).scalar_one()
    assert stored != token and len(stored) == 64

    logged_out = client.delete("/api/buyer/recovery/session", headers={"Authorization": "Bearer " + token})
    assert logged_out.status_code == 200
    assert client.get("/api/buyer/recovery/workspace", headers={"Authorization": "Bearer " + token}).status_code == 401


def test_recovery_does_not_reveal_unknown_phone_before_verification():
    start = client.post("/api/buyer/recovery/start", json={"phone": "+919822223344"})
    assert start.status_code == 200
    body = start.json()
    assert "workspace_count" not in body and "exists" not in body
    verified = client.post("/api/buyer/recovery/verify", json={"challenge_id": body["challenge_id"], "code": body["dev_otp"]})
    assert verified.status_code == 200 and verified.json()["workspace_count"] == 0
    workspace = client.get("/api/buyer/recovery/workspace", headers={"Authorization": "Bearer " + verified.json()["access_token"]})
    assert workspace.status_code == 200 and workspace.json()["rfqs"] == []

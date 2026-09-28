from __future__ import annotations
import hashlib, hmac, json, os, secrets, uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import (
    Boolean, Column, DateTime, Float, Integer, MetaData, String, Table, Text,
    UniqueConstraint, and_, create_engine, delete, func, insert, or_, select, update
)
from sqlalchemy.engine import Engine

DATABASE_URL=os.getenv("DATABASE_URL","sqlite:///./tradeai.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL="postgresql+psycopg://"+DATABASE_URL[len("postgres://"):]
elif DATABASE_URL.startswith("postgresql://") and "+psycopg" not in DATABASE_URL:
    DATABASE_URL="postgresql+psycopg://"+DATABASE_URL[len("postgresql://"):]
DEV_OTP=os.getenv("TRADEAI_DEV_OTP","123456")
ENV=os.getenv("TRADEAI_ENV","development")
OTP_TTL=int(os.getenv("TRADEAI_OTP_TTL_MINUTES","10"))
THRESHOLD=int(os.getenv("TRADEAI_QUALIFIED_THRESHOLD","60"))
ADMIN_TOKEN=os.getenv("TRADEAI_ADMIN_TOKEN","")
engine:Engine=create_engine(DATABASE_URL,pool_pre_ping=True)
md=MetaData()

buyer_sessions=Table("buyer_sessions",md,
    Column("id",String(40),primary_key=True),Column("created_at",DateTime(timezone=True),nullable=False),
    Column("verified",Boolean,nullable=False,default=False),Column("phone",String(30)),Column("company",String(160)))
rfqs=Table("rfqs",md,
    Column("id",String(40),primary_key=True),Column("session_id",String(40),nullable=False,index=True),
    Column("requirement",Text,nullable=False),Column("category",String(160)),Column("quantity",String(160)),
    Column("location",String(200)),Column("timeline",String(160)),Column("specifications",Text),Column("budget",String(160)),
    Column("status",String(40),nullable=False),Column("intent_score",Integer,nullable=False,default=0),
    Column("score_reasons",Text,nullable=False,default="[]"),Column("risk_flags",Text,nullable=False,default="[]"),
    Column("created_at",DateTime(timezone=True),nullable=False),Column("updated_at",DateTime(timezone=True),nullable=False))
otp_codes=Table("otp_codes",md,Column("session_id",String(40),primary_key=True),Column("code",String(12),nullable=False),
    Column("expires_at",DateTime(timezone=True),nullable=False),Column("attempts",Integer,nullable=False,default=0))
seller_accounts=Table("seller_accounts",md,
    Column("id",String(40),primary_key=True),Column("full_name",String(100),nullable=False),Column("business_name",String(160),nullable=False),
    Column("mobile",String(30),nullable=False,unique=True),Column("email",String(160),nullable=False,unique=True),
    Column("seller_type",String(80),nullable=False),Column("category",String(120),nullable=False),
    Column("password_salt",String(64),nullable=False),Column("password_hash",String(128),nullable=False),
    Column("status",String(40),nullable=False,default="profile_pending"),Column("created_at",DateTime(timezone=True),nullable=False))
seller_profiles=Table("seller_profiles",md,
    Column("seller_id",String(40),primary_key=True),Column("gstin",String(30)),Column("udyam",String(60)),Column("year_established",String(10)),
    Column("team_size",String(40)),Column("description",Text),Column("capabilities",Text,nullable=False,default="[]"),
    Column("service_locations",Text,nullable=False,default="[]"),Column("capacity",String(200)),Column("moq",String(160)),
    Column("verification_level",String(40),nullable=False,default="Identity Pending"),Column("updated_at",DateTime(timezone=True),nullable=False))
seller_sessions=Table("seller_sessions",md,Column("token",String(120),primary_key=True),Column("seller_id",String(40),nullable=False,index=True),
    Column("created_at",DateTime(timezone=True),nullable=False),Column("expires_at",DateTime(timezone=True),nullable=False))
suppliers=Table("suppliers",md,Column("id",String(40),primary_key=True),Column("name",String(160),nullable=False),
    Column("categories",Text,nullable=False),Column("locations",Text,nullable=False),Column("verification",String(60),nullable=False),
    Column("trade_score",Integer,nullable=False),Column("response_score",Integer,nullable=False),Column("moq",String(160)),Column("capabilities",Text,nullable=False))
matches=Table("matches",md,Column("id",String(40),primary_key=True),Column("rfq_id",String(40),nullable=False,index=True),
    Column("supplier_id",String(40),nullable=False,index=True),Column("batch",Integer,nullable=False),Column("match_score",Integer,nullable=False),
    Column("status",String(40),nullable=False),Column("released_at",DateTime(timezone=True),nullable=False),UniqueConstraint("rfq_id","supplier_id",name="uq_match_rfq_supplier"))
quotes=Table("quotes",md,Column("id",String(40),primary_key=True),Column("rfq_id",String(40),nullable=False,index=True),
    Column("supplier_id",String(40),nullable=False,index=True),Column("unit_price",Float,nullable=False),Column("quantity",Float,nullable=False,default=1),
    Column("tax_percent",Float,nullable=False,default=0),Column("freight",Float,nullable=False,default=0),Column("delivery_days",Integer,nullable=False),
    Column("warranty_months",Integer,nullable=False,default=0),Column("payment_terms",String(240)),Column("validity_days",Integer,nullable=False,default=7),
    Column("notes",Text),Column("created_at",DateTime(timezone=True),nullable=False))

def utcnow(): return datetime.now(timezone.utc)
def as_utc(dt):
    if dt is None: return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
def rowdict(x): return dict(x._mapping) if x else None
def jload(v): 
    try:return json.loads(v or "[]")
    except:return []

def init_db():
    md.create_all(engine)
    with engine.begin() as c:
        if not c.execute(select(func.count()).select_from(suppliers)).scalar_one():
            seed=[
            dict(id="sup-001",name="Shakti Industrial Supply",categories=json.dumps(["industrial supplies","construction","cement"]),locations=json.dumps(["uttarakhand","delhi","ncr","north india"]),verification="Trade Verified",trade_score=92,response_score=90,moq="Flexible",capabilities=json.dumps(["bulk supply","gst invoice","dispatch tracking"])),
            dict(id="sup-002",name="Bharat Build Materials",categories=json.dumps(["construction","cement","packaging"]),locations=json.dumps(["uttarakhand","up","delhi","north india"]),verification="Business Verified",trade_score=88,response_score=86,moq="10 units",capabilities=json.dumps(["bulk orders","multi-brand","freight support"])),
            dict(id="sup-003",name="Apex Trade Solutions",categories=json.dumps(["industrial supplies","machinery","electrical","cement"]),locations=json.dumps(["india","uttarakhand","delhi"]),verification="Trade Verified",trade_score=90,response_score=84,moq="Flexible",capabilities=json.dumps(["industrial procurement","priority quotes","pan-india"])),
            dict(id="sup-004",name="NorthStar Enterprises",categories=json.dumps(["electrical","machinery","industrial supplies"]),locations=json.dumps(["uttarakhand","up","haryana"]),verification="Business Verified",trade_score=82,response_score=80,moq="Varies",capabilities=json.dumps(["electrical","machinery","project supply"])),
            dict(id="sup-005",name="GreenField B2B",categories=json.dumps(["food & agri","packaging"]),locations=json.dumps(["india","uttarakhand","up"]),verification="Identity Verified",trade_score=76,response_score=78,moq="Varies",capabilities=json.dumps(["agri sourcing","packaging","distribution"])) ]
            c.execute(insert(suppliers),seed)

init_db()
app=FastAPI(title="TradeAI API",version="1.3.0")
app.add_middleware(CORSMiddleware,allow_origins=[x.strip() for x in os.getenv("TRADEAI_CORS_ORIGINS","https://tradeai-pgvr.onrender.com,http://localhost:8000,http://127.0.0.1:8000").split(",") if x.strip()],allow_credentials=True,allow_methods=["*"],allow_headers=["*"])

class SessionIn(BaseModel): company:str|None=None
class RFQIn(BaseModel):
    session_id:str; requirement:str=Field(min_length=8,max_length=3000); category:str|None=None; quantity:str|None=None; location:str|None=None; timeline:str|None=None; specifications:str|None=None; budget:str|None=None
class QualificationIn(BaseModel):
    category:str|None=None; quantity:str|None=None; location:str|None=None; timeline:str|None=None; specifications:str|None=None; budget:str|None=None
class OTPIn(BaseModel): session_id:str; phone:str=Field(min_length=8,max_length=20)
class OTPVerify(BaseModel): session_id:str; code:str=Field(min_length=4,max_length=8)
class SellerAccountIn(BaseModel):
    full_name:str=Field(min_length=2,max_length=100); business_name:str=Field(min_length=2,max_length=160); mobile:str=Field(min_length=8,max_length=20); email:str=Field(min_length=5,max_length=160); seller_type:str=Field(min_length=2,max_length=80); category:str=Field(min_length=2,max_length=120); password:str=Field(min_length=8,max_length=200)
class SellerLoginIn(BaseModel): login:str=Field(min_length=5,max_length=160); password:str=Field(min_length=8,max_length=200)
class SellerVerificationIn(BaseModel): verification_level:str=Field(min_length=5,max_length=40)
class SellerProfileIn(BaseModel):
    gstin:str|None=None; udyam:str|None=None; year_established:str|None=None; team_size:str|None=None; description:str|None=None
    capabilities:list[str]=[]; service_locations:list[str]=[]; capacity:str|None=None; moq:str|None=None
class QuoteIn(BaseModel):
    supplier_id:str; unit_price:float=Field(gt=0); quantity:float=Field(default=1,gt=0); tax_percent:float=Field(default=0,ge=0,le=100); freight:float=Field(default=0,ge=0); delivery_days:int=Field(gt=0); warranty_months:int=Field(default=0,ge=0); payment_terms:str|None=None; validity_days:int=Field(default=7,gt=0); notes:str|None=None
class SellerQuoteIn(BaseModel):
    unit_price:float=Field(gt=0); quantity:float=Field(default=1,gt=0); tax_percent:float=Field(default=0,ge=0,le=100); freight:float=Field(default=0,ge=0); delivery_days:int=Field(gt=0); warranty_months:int=Field(default=0,ge=0); payment_terms:str|None=None; validity_days:int=Field(default=7,gt=0); notes:str|None=None

def get_rfq(c,rid):
    x=c.execute(select(rfqs).where(rfqs.c.id==rid)).first()
    if not x: raise HTTPException(404,"RFQ not found")
    return rowdict(x)

def calc_score(x:dict[str,Any],verified:bool):
    score=0; reasons=[]; risks=[]; req=(x.get("requirement") or "").strip()
    checks=[(len(req)>=20,15,"Clear requirement +15"),(bool(x.get("category")),10,"Category identified +10"),(bool(x.get("quantity")),15,"Quantity provided +15"),(bool(x.get("location")),15,"Location provided +15"),(bool(x.get("timeline")),15,"Timeline provided +15"),(bool(x.get("specifications")),10,"Specifications provided +10"),(bool(x.get("budget")),5,"Budget guidance +5"),(verified,20,"OTP verified +20")]
    for ok,pts,msg in checks:
        if ok: score+=pts; reasons.append(msg)
    if len(set(req.lower().split()))<=2: score-=10; risks.append("Low-information enquiry")
    if any(v in req.lower() for v in ["test test","asdf","free money"]): score-=25; risks.append("Possible spam/garbage text")
    return max(0,min(100,score)),reasons,risks

def missing(x):
    qs=[("category","Which product/service category best matches this requirement?"),("quantity","What quantity, size or capacity do you need?"),("location","Where should it be delivered or performed?"),("timeline","When do you need to purchase or start?"),("specifications","Any important grade, brand, dimensions or specifications?")]
    return [{"field":k,"question":v} for k,v in qs if not x.get(k)][:3]

def rescore(c,rid):
    x=get_rfq(c,rid)
    verified=bool(c.execute(select(buyer_sessions.c.verified).where(buyer_sessions.c.id==x["session_id"])).scalar_one())
    score,reasons,risks=calc_score(x,verified)
    status=("hot" if score>=80 else "qualified") if verified and score>=THRESHOLD else "verification_required" if score>=THRESHOLD else "needs_more_information" if score>=40 else "research"
    c.execute(update(rfqs).where(rfqs.c.id==rid).values(intent_score=score,score_reasons=json.dumps(reasons),risk_flags=json.dumps(risks),status=status,updated_at=utcnow()))
    x.update(intent_score=score,score_reasons=reasons,risk_flags=risks,status=status)
    return x

def password_hash(password,salt_hex=None):
    salt=bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(16)
    digest=hashlib.pbkdf2_hmac("sha256",password.encode(),salt,210000)
    return salt.hex(),digest.hex()
def password_ok(password,salt,expected):
    return hmac.compare_digest(password_hash(password,salt)[1],expected)
def seller_public(x):
    return {k:x[k] for k in ["id","full_name","business_name","mobile","email","seller_type","category","status","created_at"]}
def auth_seller(c,authorization):
    if not authorization or not authorization.lower().startswith("bearer "): raise HTTPException(401,"Seller login required")
    token=authorization.split(" ",1)[1].strip()
    x=c.execute(select(seller_sessions).where(seller_sessions.c.token==token)).first()
    if not x or as_utc(rowdict(x)["expires_at"])<utcnow(): raise HTTPException(401,"Seller session expired or invalid")
    return rowdict(x)["seller_id"]
def supplier_score(r,s):
    cat=(r.get("category") or r["requirement"]).lower(); loc=(r.get("location") or "").lower(); cats=[str(x).lower() for x in jload(s["categories"])]; locs=" ".join(jload(s["locations"])).lower(); n=0
    if any(x in cat or cat in x for x in cats): n+=45
    elif any(x in " ".join(cats) for x in cat.split() if len(x)>3): n+=25
    if loc and any(x in locs for x in loc.split() if len(x)>2): n+=25
    elif "india" in locs: n+=12
    return min(100,n+round(s["trade_score"]*.18)+round(s["response_score"]*.12))

VERIFICATION_LEVELS={"Identity Pending":45,"Identity Verified":60,"Business Verified":82,"Trade Verified":95}
def require_admin(x_admin_token):
    if not ADMIN_TOKEN: raise HTTPException(503,"Admin verification is not configured")
    if not x_admin_token or not secrets.compare_digest(x_admin_token,ADMIN_TOKEN): raise HTTPException(401,"Admin authorization required")
def sync_registered_supplier(c,sid):
    a=rowdict(c.execute(select(seller_accounts).where(seller_accounts.c.id==sid)).first())
    p=rowdict(c.execute(select(seller_profiles).where(seller_profiles.c.seller_id==sid)).first())
    if not a or not p: return None
    caps=jload(p["capabilities"]); locs=jload(p["service_locations"])
    categories=list(dict.fromkeys([a["category"]]+caps))
    verification=p["verification_level"] or "Identity Pending"
    trade=VERIFICATION_LEVELS.get(verification,45)
    vals=dict(name=a["business_name"],categories=json.dumps(categories),locations=json.dumps(locs),verification=verification,trade_score=trade,response_score=70,moq=p["moq"] or "Varies",capabilities=json.dumps(caps))
    if c.execute(select(suppliers.c.id).where(suppliers.c.id==sid)).first(): c.execute(update(suppliers).where(suppliers.c.id==sid).values(**vals))
    else: c.execute(insert(suppliers).values(id=sid,**vals))
    return vals

@app.get("/")
def root(): return {"service":"TradeAI API","status":"ok","version":"1.3.0"}
@app.get("/api/health")
def health():
    with engine.connect() as c: c.execute(select(1)).scalar_one()
    return {"ok":True,"database":"postgresql" if DATABASE_URL.startswith("postgresql") else "sqlite","qualification_threshold":THRESHOLD}

@app.post("/api/buyer/sessions",status_code=201)
def create_session(p:SessionIn):
    sid="buy-"+uuid.uuid4().hex[:16]
    with engine.begin() as c:c.execute(insert(buyer_sessions).values(id=sid,created_at=utcnow(),verified=False,company=p.company))
    return {"session_id":sid,"registration_required":False}

@app.post("/api/rfqs",status_code=201)
def create_rfq(p:RFQIn):
    rid="rfq-"+uuid.uuid4().hex[:16]; v=p.model_dump()
    with engine.begin() as c:
        if not c.execute(select(buyer_sessions.c.id).where(buyer_sessions.c.id==p.session_id)).first(): raise HTTPException(404,"Buyer session not found")
        c.execute(insert(rfqs).values(id=rid,**v,status="research",intent_score=0,score_reasons="[]",risk_flags="[]",created_at=utcnow(),updated_at=utcnow()))
        out=rescore(c,rid); out["next_questions"]=missing(out); return out

@app.patch("/api/rfqs/{rid}/qualify")
def qualify(rid:str,p:QualificationIn):
    with engine.begin() as c:
        get_rfq(c,rid); vals={k:v for k,v in p.model_dump().items() if v is not None}
        if vals:c.execute(update(rfqs).where(rfqs.c.id==rid).values(**vals,updated_at=utcnow()))
        out=rescore(c,rid); out["next_questions"]=missing(out); return out

@app.get("/api/rfqs/{rid}")
def rfq_detail(rid:str):
    with engine.connect() as c:
        x=get_rfq(c,rid); x["score_reasons"]=jload(x["score_reasons"]); x["risk_flags"]=jload(x["risk_flags"]); x["next_questions"]=missing(x); return x

@app.post("/api/otp/send")
def otp_send(p:OTPIn):
    code=DEV_OTP if ENV!="production" else str(secrets.randbelow(900000)+100000); exp=utcnow()+timedelta(minutes=OTP_TTL)
    with engine.begin() as c:
        if not c.execute(select(buyer_sessions.c.id).where(buyer_sessions.c.id==p.session_id)).first(): raise HTTPException(404,"Buyer session not found")
        c.execute(update(buyer_sessions).where(buyer_sessions.c.id==p.session_id).values(phone=p.phone))
        c.execute(delete(otp_codes).where(otp_codes.c.session_id==p.session_id)); c.execute(insert(otp_codes).values(session_id=p.session_id,code=code,expires_at=exp,attempts=0))
    out={"sent":True,"channel":"whatsapp_ready","expires_in_minutes":OTP_TTL}
    if ENV!="production":out["dev_otp"]=code
    return out

@app.post("/api/otp/verify")
def otp_verify(p:OTPVerify):
    with engine.begin() as c:
        x=c.execute(select(otp_codes).where(otp_codes.c.session_id==p.session_id)).first()
        if not x: raise HTTPException(404,"OTP not requested")
        x=rowdict(x)
        if x["attempts"]>=5: raise HTTPException(429,"Too many OTP attempts")
        c.execute(update(otp_codes).where(otp_codes.c.session_id==p.session_id).values(attempts=x["attempts"]+1))
        if as_utc(x["expires_at"])<utcnow(): raise HTTPException(410,"OTP expired")
        if not secrets.compare_digest(x["code"],p.code): raise HTTPException(400,"Invalid OTP")
        c.execute(update(buyer_sessions).where(buyer_sessions.c.id==p.session_id).values(verified=True))
        ids=c.execute(select(rfqs.c.id).where(rfqs.c.session_id==p.session_id)).scalars().all(); scored=[rescore(c,r) for r in ids]
        return {"verified":True,"rfqs":[{"id":x["id"],"intent_score":x["intent_score"],"status":x["status"]} for x in scored]}

@app.post("/api/seller/accounts",status_code=201)
def seller_account(p:SellerAccountIn):
    sid="sel-"+uuid.uuid4().hex[:16]; salt,digest=password_hash(p.password)
    with engine.begin() as c:
        if c.execute(select(seller_accounts.c.id).where(or_(func.lower(seller_accounts.c.email)==p.email.strip().lower(),seller_accounts.c.mobile==p.mobile.strip()))).first(): raise HTTPException(409,"Seller account already exists")
        c.execute(insert(seller_accounts).values(id=sid,full_name=p.full_name.strip(),business_name=p.business_name.strip(),mobile=p.mobile.strip(),email=p.email.strip().lower(),seller_type=p.seller_type,category=p.category,password_salt=salt,password_hash=digest,status="profile_pending",created_at=utcnow()))
        c.execute(insert(seller_profiles).values(seller_id=sid,capabilities="[]",service_locations="[]",verification_level="Identity Pending",updated_at=utcnow()))
        x=rowdict(c.execute(select(seller_accounts).where(seller_accounts.c.id==sid)).first())
        return {"seller":seller_public(x),"next_step":"business_profile"}

@app.post("/api/seller/login")
def seller_login(p:SellerLoginIn):
    with engine.begin() as c:
        x=c.execute(select(seller_accounts).where(or_(func.lower(seller_accounts.c.email)==p.login.strip().lower(),seller_accounts.c.mobile==p.login.strip()))).first()
        if not x: raise HTTPException(401,"Invalid login")
        x=rowdict(x)
        if not password_ok(p.password,x["password_salt"],x["password_hash"]): raise HTTPException(401,"Invalid login")
        token=secrets.token_urlsafe(32); exp=utcnow()+timedelta(days=7)
        c.execute(insert(seller_sessions).values(token=token,seller_id=x["id"],created_at=utcnow(),expires_at=exp))
        return {"access_token":token,"token_type":"bearer","expires_at":exp,"seller":seller_public(x)}

@app.get("/api/seller/me")
def seller_me(authorization:str|None=Header(default=None)):
    with engine.connect() as c:
        sid=auth_seller(c,authorization); a=rowdict(c.execute(select(seller_accounts).where(seller_accounts.c.id==sid)).first()); p=rowdict(c.execute(select(seller_profiles).where(seller_profiles.c.seller_id==sid)).first())
        if p: p["capabilities"]=jload(p["capabilities"]); p["service_locations"]=jload(p["service_locations"])
        return {"seller":seller_public(a),"profile":p}

@app.put("/api/seller/profile")
def seller_profile(p:SellerProfileIn,authorization:str|None=Header(default=None)):
    with engine.begin() as c:
        sid=auth_seller(c,authorization); vals=p.model_dump(); vals["capabilities"]=json.dumps(vals["capabilities"]); vals["service_locations"]=json.dumps(vals["service_locations"]); vals["updated_at"]=utcnow()
        c.execute(update(seller_profiles).where(seller_profiles.c.seller_id==sid).values(**vals))
        c.execute(update(seller_accounts).where(seller_accounts.c.id==sid).values(status="verification_pending"))
        sync_registered_supplier(c,sid)
        return {"saved":True,"status":"verification_pending","matching_eligible":False}

@app.post("/api/rfqs/{rid}/matches/release")
def release(rid:str,batch:int=1):
    if batch<1: raise HTTPException(400,"Batch must be >= 1")
    with engine.begin() as c:
        r=get_rfq(c,rid)
        verified=bool(c.execute(select(buyer_sessions.c.verified).where(buyer_sessions.c.id==r["session_id"])).scalar_one())
        if not verified: raise HTTPException(409,"Buyer OTP verification required before supplier release")
        if r["intent_score"]<THRESHOLD: raise HTTPException(409,f"RFQ intent score must be at least {THRESHOLD}")
        used=set(c.execute(select(matches.c.supplier_id).where(matches.c.rfq_id==rid)).scalars().all())
        allsup=[rowdict(x) for x in c.execute(select(suppliers).where(suppliers.c.verification!="Identity Pending")).all() if rowdict(x)["id"] not in used]
        ranked=sorted([(supplier_score(r,s),s) for s in allsup],key=lambda x:x[0],reverse=True)[:3 if batch==1 else 2]; out=[]
        for score,s in ranked:
            mid="mat-"+uuid.uuid4().hex[:12]; c.execute(insert(matches).values(id=mid,rfq_id=rid,supplier_id=s["id"],batch=batch,match_score=score,status="released",released_at=utcnow())); out.append({"match_id":mid,"supplier_id":s["id"],"supplier_name":s["name"],"match_score":score,"verification":s["verification"],"trade_score":s["trade_score"]})
        return {"rfq_id":rid,"batch":batch,"matches":out}

@app.get("/api/rfqs/{rid}/matches")
def list_matches(rid:str):
    with engine.connect() as c:
        get_rfq(c,rid); q=select(matches,suppliers.c.name.label("supplier_name"),suppliers.c.verification,suppliers.c.trade_score).join(suppliers,suppliers.c.id==matches.c.supplier_id).where(matches.c.rfq_id==rid).order_by(matches.c.batch,matches.c.match_score.desc())
        return [rowdict(x) for x in c.execute(q).all()]

@app.get("/api/seller/opportunities")
def seller_opportunities(authorization:str|None=Header(default=None)):
    with engine.connect() as c:
        sid=auth_seller(c,authorization)
        q=select(rfqs.c.id.label("rfq_id"),rfqs.c.requirement,rfqs.c.category,rfqs.c.quantity,rfqs.c.location,rfqs.c.timeline,rfqs.c.budget,rfqs.c.intent_score,rfqs.c.status,matches.c.match_score,matches.c.batch,matches.c.status.label("match_status"),matches.c.released_at).join(matches,matches.c.rfq_id==rfqs.c.id).where(matches.c.supplier_id==sid).order_by(matches.c.released_at.desc())
        return [rowdict(x) for x in c.execute(q).all()]

@app.get("/api/sellers/{sid}/opportunities")
def opportunities(sid:str):
    with engine.connect() as c:
        if not c.execute(select(suppliers.c.id).where(suppliers.c.id==sid)).first(): raise HTTPException(404,"Supplier not found")
        q=select(rfqs.c.id.label("rfq_id"),rfqs.c.requirement,rfqs.c.category,rfqs.c.quantity,rfqs.c.location,rfqs.c.timeline,rfqs.c.intent_score,rfqs.c.status,matches.c.match_score,matches.c.batch,matches.c.status.label("match_status")).join(matches,matches.c.rfq_id==rfqs.c.id).where(matches.c.supplier_id==sid).order_by(matches.c.released_at.desc())
        return [rowdict(x) for x in c.execute(q).all()]

@app.post("/api/seller/rfqs/{rid}/quotes",status_code=201)
def seller_quote(rid:str,p:SellerQuoteIn,authorization:str|None=Header(default=None)):
    with engine.begin() as c:
        sid=auth_seller(c,authorization); get_rfq(c,rid)
        if not c.execute(select(matches.c.id).where(matches.c.rfq_id==rid,matches.c.supplier_id==sid)).first(): raise HTTPException(403,"This RFQ has not been released to your supplier account")
        x=p.model_dump(); existing=c.execute(select(quotes.c.id).where(quotes.c.rfq_id==rid,quotes.c.supplier_id==sid).order_by(quotes.c.created_at.desc())).scalar()
        if existing:
            c.execute(update(quotes).where(quotes.c.id==existing).values(**x,created_at=utcnow())); qid=existing; created=False
        else:
            qid="quo-"+uuid.uuid4().hex[:14]; c.execute(insert(quotes).values(id=qid,rfq_id=rid,supplier_id=sid,created_at=utcnow(),**x)); created=True
        c.execute(update(matches).where(matches.c.rfq_id==rid,matches.c.supplier_id==sid).values(status="quoted"))
        landed=round(x["unit_price"]*x["quantity"]*(1+x["tax_percent"]/100)+x["freight"],2)
        return {"quote_id":qid,"rfq_id":rid,"supplier_id":sid,"landed_price":landed,"created":created,"status":"sent"}

@app.get("/api/seller/quotes")
def seller_quotes(authorization:str|None=Header(default=None)):
    with engine.connect() as c:
        sid=auth_seller(c,authorization)
        q=select(quotes,rfqs.c.requirement,rfqs.c.location).join(rfqs,rfqs.c.id==quotes.c.rfq_id).where(quotes.c.supplier_id==sid).order_by(quotes.c.created_at.desc())
        out=[]
        for r in c.execute(q).all():
            x=rowdict(r); x["landed_price"]=round(x["unit_price"]*x["quantity"]*(1+x["tax_percent"]/100)+x["freight"],2); x["status"]="sent"; out.append(x)
        return out

def build_quote_comparison(c,rid):
    q=select(quotes,suppliers.c.name.label("supplier_name"),suppliers.c.verification,suppliers.c.trade_score,matches.c.match_score).join(suppliers,suppliers.c.id==quotes.c.supplier_id).join(matches,and_(matches.c.rfq_id==quotes.c.rfq_id,matches.c.supplier_id==quotes.c.supplier_id)).where(quotes.c.rfq_id==rid)
    items=[]
    for r in c.execute(q).all():
        x=rowdict(r); x["landed_price"]=round(x["unit_price"]*x["quantity"]*(1+x["tax_percent"]/100)+x["freight"],2); items.append(x)
    if not items:return {"quotes":[],"objective_highlights":{}}
    return {"quotes":items,"objective_highlights":{"lowest_landed_price_quote_id":min(items,key=lambda x:x["landed_price"])["id"],"fastest_delivery_quote_id":min(items,key=lambda x:x["delivery_days"])["id"],"longest_warranty_quote_id":max(items,key=lambda x:x["warranty_months"])["id"]}}

@app.get("/api/buyer/rfqs/{rid}/quotes/compare")
def buyer_compare(rid:str,session_id:str):
    with engine.connect() as c:
        r=get_rfq(c,rid)
        if r["session_id"]!=session_id: raise HTTPException(403,"RFQ does not belong to this buyer session")
        return build_quote_comparison(c,rid)

@app.post("/api/rfqs/{rid}/quotes")
def legacy_quote_endpoint(rid:str):
    raise HTTPException(410,"Use the authenticated seller quotation endpoint")

@app.get("/api/rfqs/{rid}/quotes/compare")
def legacy_compare_endpoint(rid:str):
    raise HTTPException(410,"Use the buyer-session quotation comparison endpoint")

@app.get("/api/admin/sellers")
def admin_sellers(x_admin_token:str|None=Header(default=None)):
    require_admin(x_admin_token)
    with engine.connect() as c:
        q=select(seller_accounts.c.id,seller_accounts.c.business_name,seller_accounts.c.email,seller_accounts.c.category,seller_accounts.c.status,seller_profiles.c.verification_level,seller_profiles.c.service_locations).join(seller_profiles,seller_profiles.c.seller_id==seller_accounts.c.id)
        out=[]
        for r in c.execute(q).all():
            x=rowdict(r); x["service_locations"]=jload(x["service_locations"]); out.append(x)
        return out

@app.put("/api/admin/sellers/{sid}/verification")
def admin_verify_seller(sid:str,p:SellerVerificationIn,x_admin_token:str|None=Header(default=None)):
    require_admin(x_admin_token)
    level=p.verification_level.strip()
    if level not in VERIFICATION_LEVELS or level=="Identity Pending": raise HTTPException(400,"Use Identity Verified, Business Verified, or Trade Verified")
    with engine.begin() as c:
        if not c.execute(select(seller_accounts.c.id).where(seller_accounts.c.id==sid)).first(): raise HTTPException(404,"Seller not found")
        c.execute(update(seller_profiles).where(seller_profiles.c.seller_id==sid).values(verification_level=level,updated_at=utcnow()))
        c.execute(update(seller_accounts).where(seller_accounts.c.id==sid).values(status="active"))
        sync_registered_supplier(c,sid)
        return {"seller_id":sid,"verification_level":level,"status":"active","matching_eligible":True}

@app.get("/api/admin/overview")
def admin():
    with engine.connect() as c:
        return {"buyer_sessions":c.execute(select(func.count()).select_from(buyer_sessions)).scalar_one(),"seller_accounts":c.execute(select(func.count()).select_from(seller_accounts)).scalar_one(),"rfqs":c.execute(select(func.count()).select_from(rfqs)).scalar_one(),"qualified_rfqs":c.execute(select(func.count()).select_from(rfqs).where(rfqs.c.intent_score>=THRESHOLD)).scalar_one(),"released_matches":c.execute(select(func.count()).select_from(matches)).scalar_one(),"quotes":c.execute(select(func.count()).select_from(quotes)).scalar_one(),"controls":{"qualification_threshold":THRESHOLD,"first_batch":3,"next_batch":2,"buyer_contact_protected":True}}

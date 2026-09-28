from __future__ import annotations
import hashlib, hmac, json, os, re, secrets, uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import (
    Boolean, Column, DateTime, Float, Integer, MetaData, String, Table, Text,
    UniqueConstraint, and_, create_engine, delete, func, insert, or_, select, update
)
from sqlalchemy.engine import Engine
import httpx

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
OPENAI_API_KEY=os.getenv("OPENAI_API_KEY","").strip()
AI_MODEL=os.getenv("TRADEAI_OPENAI_MODEL","gpt-5").strip()
AI_MODE=os.getenv("TRADEAI_AI_MODE","auto").strip().lower()
META_ACCESS_TOKEN=(os.getenv("TRADEAI_WHATSAPP_ACCESS_TOKEN") or os.getenv("WHATSAPP_ACCESS_TOKEN") or "").strip()
META_PHONE_NUMBER_ID=(os.getenv("TRADEAI_WHATSAPP_PHONE_NUMBER_ID") or os.getenv("WHATSAPP_PHONE_NUMBER_ID") or "").strip()
META_GRAPH_VERSION=os.getenv("TRADEAI_META_GRAPH_VERSION","v23.0").strip()
WA_OTP_TEMPLATE=os.getenv("TRADEAI_WA_OTP_TEMPLATE","").strip()
WA_OTP_LANGUAGE=os.getenv("TRADEAI_WA_OTP_LANGUAGE","en_US").strip()
WA_RFQ_TEMPLATE=os.getenv("TRADEAI_WA_RFQ_TEMPLATE","").strip()
WA_RFQ_LANGUAGE=os.getenv("TRADEAI_WA_RFQ_LANGUAGE","en_US").strip()
WA_VERIFY_TOKEN=os.getenv("TRADEAI_WA_VERIFY_TOKEN","").strip()
ROUTING_TOKEN=os.getenv("TRADEAI_ROUTING_TOKEN","").strip()
DEFAULT_AUTO_EXPAND_MINUTES=int(os.getenv("TRADEAI_AUTO_EXPAND_MINUTES","30"))
DEFAULT_MIN_RESPONSES=int(os.getenv("TRADEAI_MIN_RESPONSES","2"))
DEFAULT_MAX_BATCHES=int(os.getenv("TRADEAI_MAX_BATCHES","3"))
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
deal_rooms=Table("deal_rooms",md,Column("id",String(40),primary_key=True),Column("rfq_id",String(40),nullable=False,index=True),
    Column("quote_id",String(40),nullable=False,index=True),Column("supplier_id",String(40),nullable=False,index=True),Column("buyer_session_id",String(40),nullable=False,index=True),
    Column("status",String(40),nullable=False,default="negotiation"),Column("contact_consent",Boolean,nullable=False,default=False),
    Column("created_at",DateTime(timezone=True),nullable=False),Column("updated_at",DateTime(timezone=True),nullable=False),UniqueConstraint("quote_id","buyer_session_id",name="uq_deal_quote_buyer"))
deal_messages=Table("deal_messages",md,Column("id",String(40),primary_key=True),Column("deal_id",String(40),nullable=False,index=True),
    Column("sender_role",String(20),nullable=False),Column("sender_id",String(40),nullable=False),Column("message",Text,nullable=False),Column("created_at",DateTime(timezone=True),nullable=False))
orders=Table("orders",md,Column("id",String(40),primary_key=True),Column("deal_id",String(40),nullable=False,unique=True,index=True),
    Column("rfq_id",String(40),nullable=False,index=True),Column("quote_id",String(40),nullable=False),Column("supplier_id",String(40),nullable=False,index=True),
    Column("buyer_session_id",String(40),nullable=False,index=True),Column("amount",Float,nullable=False),Column("status",String(40),nullable=False),
    Column("created_at",DateTime(timezone=True),nullable=False),Column("updated_at",DateTime(timezone=True),nullable=False))
notifications=Table("notifications",md,Column("id",String(40),primary_key=True),Column("kind",String(40),nullable=False,index=True),
    Column("channel",String(30),nullable=False),Column("recipient",String(40),nullable=False),Column("session_id",String(40),index=True),
    Column("rfq_id",String(40),index=True),Column("supplier_id",String(40),index=True),Column("provider_message_id",String(160),index=True),
    Column("status",String(40),nullable=False),Column("error",Text),Column("payload",Text),Column("created_at",DateTime(timezone=True),nullable=False),
    Column("updated_at",DateTime(timezone=True),nullable=False))
fraud_assessments=Table("fraud_assessments",md,Column("rfq_id",String(40),primary_key=True),Column("fingerprint",String(64),nullable=False,index=True),
    Column("risk_score",Integer,nullable=False,default=0),Column("flags",Text,nullable=False,default="[]"),Column("decision",String(30),nullable=False,default="clear"),
    Column("reviewed_by",String(80)),Column("updated_at",DateTime(timezone=True),nullable=False))
routing_events=Table("routing_events",md,Column("id",String(40),primary_key=True),Column("rfq_id",String(40),nullable=False,index=True),
    Column("event",String(60),nullable=False,index=True),Column("batch",Integer),Column("details",Text),Column("created_at",DateTime(timezone=True),nullable=False))
system_settings=Table("system_settings",md,Column("key",String(80),primary_key=True),Column("value",String(240),nullable=False),Column("updated_at",DateTime(timezone=True),nullable=False))

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
        defaults={"qualification_threshold":str(THRESHOLD),"first_batch":"3","next_batch":"2","auto_expand_minutes":str(DEFAULT_AUTO_EXPAND_MINUTES),
            "min_responses":str(DEFAULT_MIN_RESPONSES),"max_batches":str(DEFAULT_MAX_BATCHES),"duplicate_screening":"true","auto_expand":"true"}
        for key,value in defaults.items():
            if not c.execute(select(system_settings.c.key).where(system_settings.c.key==key)).first():
                c.execute(insert(system_settings).values(key=key,value=value,updated_at=utcnow()))

init_db()
app=FastAPI(title="TradeAI API",version="1.6.0")
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
class BuyerDealStartIn(BaseModel): session_id:str; quote_id:str; message:str|None=Field(default=None,max_length=2000)
class BuyerDealMessageIn(BaseModel): session_id:str; message:str=Field(min_length=1,max_length=2000)
class SellerDealMessageIn(BaseModel): message:str=Field(min_length=1,max_length=2000)
class ContactConsentIn(BaseModel): session_id:str; approved:bool
class BuyerOrderIn(BaseModel): session_id:str; confirm_terms:bool=True
class OrderStatusIn(BaseModel): status:str=Field(min_length=3,max_length=40)
class AIQualificationIn(BaseModel):
    requirement:str=Field(min_length=3,max_length=3000); latest_message:str|None=Field(default=None,max_length=2000)
    answers:dict[str,str|None]={}; conversation:list[dict[str,str]]=[]
class AdminModerationIn(BaseModel):
    action:str=Field(min_length=3,max_length=30); note:str|None=Field(default=None,max_length=500)
class AdminSettingsIn(BaseModel):
    qualification_threshold:int|None=Field(default=None,ge=40,le=90); first_batch:int|None=Field(default=None,ge=1,le=5)
    next_batch:int|None=Field(default=None,ge=1,le=5); auto_expand_minutes:int|None=Field(default=None,ge=1,le=1440)
    min_responses:int|None=Field(default=None,ge=1,le=10); max_batches:int|None=Field(default=None,ge=1,le=10)
    duplicate_screening:bool|None=None; auto_expand:bool|None=None


def get_rfq(c,rid):
    x=c.execute(select(rfqs).where(rfqs.c.id==rid)).first()
    if not x: raise HTTPException(404,"RFQ not found")
    return rowdict(x)

def setting(c,key,default=None):
    v=c.execute(select(system_settings.c.value).where(system_settings.c.key==key)).scalar()
    return default if v is None else v
def int_setting(c,key,default): 
    try:return int(setting(c,key,default))
    except:return int(default)
def bool_setting(c,key,default=True):
    return str(setting(c,key,str(default).lower())).lower() in ("1","true","yes","on")
def current_threshold(c): return int_setting(c,"qualification_threshold",THRESHOLD)

def rfq_fingerprint(x):
    parts=[x.get("requirement"),x.get("category"),x.get("quantity"),x.get("location")]
    norm="|".join(re.sub(r"[^a-z0-9]+"," ",str(v or "").lower()).strip() for v in parts)
    return hashlib.sha256(norm.encode()).hexdigest()

def assess_fraud(c,rid,keep_override=True):
    r=get_rfq(c,rid); fp=rfq_fingerprint(r); flags=[]; risk=0
    req=(r.get("requirement") or "").lower()
    if len(req.strip())<12: risk+=20; flags.append("Very short enquiry")
    if any(v in req for v in ["test test","asdf","free money","click here","http://","https://"]): risk+=65; flags.append("Spam or garbage pattern")
    sess=rowdict(c.execute(select(buyer_sessions).where(buyer_sessions.c.id==r["session_id"])).first())
    if bool_setting(c,"duplicate_screening",True):
        same_session=c.execute(select(func.count()).select_from(rfqs).where(rfqs.c.session_id==r["session_id"],rfqs.c.id!=rid,rfqs.c.created_at>=utcnow()-timedelta(hours=24))).scalar_one()
        if same_session>=2: risk+=35; flags.append("Repeated RFQs from same buyer session")
        phone=(sess or {}).get("phone")
        if phone:
            other_ids=c.execute(select(rfqs.c.id).join(buyer_sessions,buyer_sessions.c.id==rfqs.c.session_id).where(
                buyer_sessions.c.phone==phone,rfqs.c.id!=rid,rfqs.c.created_at>=utcnow()-timedelta(hours=24))).scalars().all()
            same_fp=0
            for oid in other_ids:
                other=get_rfq(c,oid)
                if rfq_fingerprint(other)==fp:same_fp+=1
            if same_fp: risk+=50; flags.append("Duplicate requirement from same verified mobile")
            if len(other_ids)>=4: risk+=35; flags.append("High RFQ velocity from verified mobile")
    risk=min(100,risk)
    existing=rowdict(c.execute(select(fraud_assessments).where(fraud_assessments.c.rfq_id==rid)).first())
    override=(existing or {}).get("decision") if keep_override else None
    if override in ("approved","blocked","research"): decision=override
    else: decision="blocked" if risk>=80 else "review" if risk>=45 else "clear"
    vals=dict(fingerprint=fp,risk_score=risk,flags=json.dumps(flags),decision=decision,updated_at=utcnow())
    if existing:c.execute(update(fraud_assessments).where(fraud_assessments.c.rfq_id==rid).values(**vals))
    else:c.execute(insert(fraud_assessments).values(rfq_id=rid,reviewed_by=None,**vals))
    return {"risk_score":risk,"flags":flags,"decision":decision}

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
    threshold=current_threshold(c)
    status=("hot" if score>=80 else "qualified") if verified and score>=threshold else "verification_required" if score>=threshold else "needs_more_information" if score>=40 else "research"
    c.execute(update(rfqs).where(rfqs.c.id==rid).values(intent_score=score,score_reasons=json.dumps(reasons),risk_flags=json.dumps(risks),status=status,updated_at=utcnow()))
    x.update(intent_score=score,score_reasons=reasons,risk_flags=risks,status=status)
    fraud=assess_fraud(c,rid)
    merged=list(dict.fromkeys(risks+fraud["flags"]))
    if fraud["decision"]=="blocked": status="blocked"
    elif fraud["decision"]=="review" and verified: status="manual_review"
    elif fraud["decision"]=="research": status="research"
    c.execute(update(rfqs).where(rfqs.c.id==rid).values(risk_flags=json.dumps(merged),status=status,updated_at=utcnow()))
    x.update(risk_flags=merged,status=status,fraud=fraud)
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
def root(): return {"service":"TradeAI API","status":"ok","version":"1.6.0"}
@app.get("/api/health")
def health():
    with engine.connect() as c: c.execute(select(1)).scalar_one()
    return {"ok":True,"database":"postgresql" if DATABASE_URL.startswith("postgresql") else "sqlite","qualification_threshold":THRESHOLD,
        "ai":{"mode":AI_MODE,"configured":bool(OPENAI_API_KEY),"model":AI_MODEL if OPENAI_API_KEY else None},
        "whatsapp":{"configured":bool(META_ACCESS_TOKEN and META_PHONE_NUMBER_ID),"otp_template_configured":bool(WA_OTP_TEMPLATE),"rfq_template_configured":bool(WA_RFQ_TEMPLATE)}}


AI_FIELDS=("category","quantity","location","timeline","specifications","budget")
def clean_field(v):
    if v is None:return None
    s=str(v).strip()
    return s[:500] if s else None

def fallback_qualification(p:AIQualificationIn):
    fields={k:clean_field(p.answers.get(k)) for k in AI_FIELDS}
    text=" ".join([p.requirement,p.latest_message or ""]).strip()
    low=text.lower()
    if not fields["category"]:
        category_map=[("epoxy","industrial epoxy flooring"),("cement","cement"),("pouch","printed packaging"),("packag","packaging"),("tank","industrial tanks"),("machine","machinery"),("electrical","electrical supplies"),("flooring","industrial flooring")]
        fields["category"]=next((v for k,v in category_map if k in low),None)
    if not fields["quantity"]:
        m=re.search(r"([\d,]+(?:\.\d+)?\s*(?:bags?|kg|kgs|mt|tons?|tonnes?|pcs?|pieces?|units?|sq\s*ft|sqft|sqm|m2|litres?|liters?|l\b))",text,re.I)
        if m: fields["quantity"]=m.group(1)
    if not fields["timeline"]:
        m=re.search(r"(within\s+\d+\s+(?:days?|weeks?|months?)|by\s+[A-Za-z0-9 ,/-]+|today|tomorrow|this week|urgent(?:ly)?)",text,re.I)
        if m: fields["timeline"]=m.group(1)[:160]
    if not fields["location"]:
        known=["Haridwar","Roorkee","Dehradun","Pantnagar","Rudrapur","Delhi","Noida","Gurugram","Faridabad","Uttarakhand"]
        hit=next((x for x in known if x.lower() in low),None)
        if hit: fields["location"]=hit
    order=["category","quantity","location","timeline","specifications"]
    prompts={
        "category":"What exact product, service or project do you need?",
        "quantity":"What quantity, area or capacity do you need?",
        "location":"Where should it be delivered or where is the work site?",
        "timeline":"When do you need it?",
        "specifications":"Any important grade, brand, dimensions or specifications suppliers should know?"
    }
    nxt=next((k for k in order if not fields[k]),None)
    quick=[]
    if nxt=="timeline":quick=["This week","Within 14 days","Within 30 days"]
    elif nxt=="specifications" and "epoxy" in (fields["category"] or "").lower():quick=["3 mm heavy-duty","2 mm standard","Need supplier recommendation"]
    preview=dict(requirement=p.requirement,**fields); score,reasons,risks=calc_score(preview,False)
    return {"provider":"rules","ai_active":False,"fields":fields,"next_field":nxt,"next_question":prompts.get(nxt),
        "quick_options":quick,"ready_for_otp":nxt is None,"intent_score_preview":score,"score_reasons":reasons,"risk_flags":risks}

def extract_response_text(data):
    for item in data.get("output",[]):
        if item.get("type")=="message":
            for part in item.get("content",[]):
                if part.get("type")=="output_text" and part.get("text"): return part["text"]
    return data.get("output_text") or ""

def openai_qualification(p:AIQualificationIn):
    schema={"type":"object","additionalProperties":False,"properties":{
        "category":{"type":["string","null"]},"quantity":{"type":["string","null"]},"location":{"type":["string","null"]},
        "timeline":{"type":["string","null"]},"specifications":{"type":["string","null"]},"budget":{"type":["string","null"]},
        "next_field":{"type":["string","null"]},"next_question":{"type":["string","null"]},
        "quick_options":{"type":"array","items":{"type":"string"}},"risk_flags":{"type":"array","items":{"type":"string"}}},
        "required":["category","quantity","location","timeline","specifications","budget","next_field","next_question","quick_options","risk_flags"]}
    instructions="""You are TradeAI's Indian B2B procurement qualification engine. Extract only facts the buyer stated or that are unambiguous from context; never invent quantity, budget, location, dates, certifications, stock or specifications. Merge the latest answer with previously captured fields. Ask exactly one useful missing question at a time. Core fields are category/product, quantity/size/capacity, delivery/work location, timeline and important specifications. Budget is optional and should not block OTP. Adapt the specification question to the category. Keep questions concise and use the buyer's apparent language (English or Hinglish). Flag obvious spam, contradictory or research-only text, but do not over-flag normal short answers. When all core fields are captured, next_field and next_question must be null."""
    user={"requirement":p.requirement,"previous_fields":p.answers,"latest_message":p.latest_message,"conversation":p.conversation[-12:]}
    payload={"model":AI_MODEL,"store":False,"instructions":instructions,"input":json.dumps(user,ensure_ascii=False),
        "text":{"format":{"type":"json_schema","name":"tradeai_qualification","strict":True,"schema":schema}}}
    with httpx.Client(timeout=25) as client:
        r=client.post("https://api.openai.com/v1/responses",headers={"Authorization":"Bearer "+OPENAI_API_KEY,"Content-Type":"application/json"},json=payload)
        r.raise_for_status(); data=json.loads(extract_response_text(r.json()))
    fields={k:clean_field(data.get(k) or p.answers.get(k)) for k in AI_FIELDS}
    preview=dict(requirement=p.requirement,**fields); score,reasons,risks=calc_score(preview,False)
    risks=list(dict.fromkeys((data.get("risk_flags") or [])+risks))
    nxt=data.get("next_field")
    if nxt not in AI_FIELDS or fields.get(nxt): nxt=next((k for k in ("category","quantity","location","timeline","specifications") if not fields[k]),None)
    question=data.get("next_question") if nxt else None
    return {"provider":"openai","ai_active":True,"fields":fields,"next_field":nxt,"next_question":question,
        "quick_options":(data.get("quick_options") or [])[:4],"ready_for_otp":nxt is None,"intent_score_preview":score,"score_reasons":reasons,"risk_flags":risks}

@app.post("/api/ai/qualify")
def ai_qualify(p:AIQualificationIn):
    if OPENAI_API_KEY and AI_MODE!="rules":
        try:return openai_qualification(p)
        except Exception:
            if AI_MODE=="openai": raise HTTPException(502,"AI qualification provider is temporarily unavailable")
    out=fallback_qualification(p)
    if OPENAI_API_KEY and AI_MODE=="auto":out["provider"]="rules_fallback"
    return out

def normalize_phone(phone):
    digits=re.sub(r"\D","",phone or "")
    if len(digits)==10:digits="91"+digits
    return digits

def record_notification(c,kind,recipient,status,session_id=None,rfq_id=None,supplier_id=None,provider_message_id=None,error=None,payload=None):
    nid="ntf-"+uuid.uuid4().hex[:14]; now=utcnow()
    c.execute(insert(notifications).values(id=nid,kind=kind,channel="whatsapp",recipient=recipient,session_id=session_id,rfq_id=rfq_id,supplier_id=supplier_id,
        provider_message_id=provider_message_id,status=status,error=(error or "")[:2000] or None,payload=json.dumps(payload or {},ensure_ascii=False)[:8000],created_at=now,updated_at=now))
    return nid

def whatsapp_template(to,template,language,components):
    if not (META_ACCESS_TOKEN and META_PHONE_NUMBER_ID and template):
        return {"configured":False,"status":"not_configured","message_id":None}
    payload={"messaging_product":"whatsapp","recipient_type":"individual","to":normalize_phone(to),"type":"template",
        "template":{"name":template,"language":{"code":language},"components":components}}
    url=f"https://graph.facebook.com/{META_GRAPH_VERSION}/{META_PHONE_NUMBER_ID}/messages"
    try:
        with httpx.Client(timeout=20) as client:
            r=client.post(url,headers={"Authorization":"Bearer "+META_ACCESS_TOKEN,"Content-Type":"application/json"},json=payload)
            r.raise_for_status(); data=r.json()
        mid=((data.get("messages") or [{}])[0]).get("id")
        return {"configured":True,"status":"accepted","message_id":mid,"payload":payload}
    except httpx.HTTPStatusError as e:
        detail=e.response.text[:1500] if e.response is not None else str(e)
        return {"configured":True,"status":"failed","message_id":None,"error":detail,"payload":payload}
    except Exception as e:
        return {"configured":True,"status":"failed","message_id":None,"error":str(e)[:1500],"payload":payload}

def send_otp_whatsapp(phone,code):
    components=[{"type":"body","parameters":[{"type":"text","text":code}]},
        {"type":"button","sub_type":"url","index":"0","parameters":[{"type":"text","text":code}]}]
    return whatsapp_template(phone,WA_OTP_TEMPLATE,WA_OTP_LANGUAGE,components)

def notify_supplier_rfq(c,supplier_id,rfq):
    a=rowdict(c.execute(select(seller_accounts).where(seller_accounts.c.id==supplier_id)).first())
    if not a or not WA_RFQ_TEMPLATE:return None
    components=[{"type":"body","parameters":[{"type":"text","text":a["business_name"]},{"type":"text","text":rfq["requirement"][:500]},{"type":"text","text":rfq.get("location") or "Not specified"}]}]
    result=whatsapp_template(a["mobile"],WA_RFQ_TEMPLATE,WA_RFQ_LANGUAGE,components)
    record_notification(c,"rfq_released",a["mobile"],result["status"],rfq_id=rfq["id"],supplier_id=supplier_id,provider_message_id=result.get("message_id"),error=result.get("error"),payload=result.get("payload"))
    return result

@app.get("/api/webhooks/whatsapp")
def whatsapp_verify(request:Request):
    q=request.query_params
    if q.get("hub.mode")=="subscribe" and WA_VERIFY_TOKEN and secrets.compare_digest(q.get("hub.verify_token",""),WA_VERIFY_TOKEN):
        return int(q.get("hub.challenge","0"))
    raise HTTPException(403,"Webhook verification failed")

@app.post("/api/webhooks/whatsapp")
async def whatsapp_webhook(request:Request):
    body=await request.json(); updated=0
    with engine.begin() as c:
        for entry in body.get("entry",[]):
            for change in entry.get("changes",[]):
                for status in (change.get("value",{}).get("statuses") or []):
                    mid=status.get("id"); state=status.get("status")
                    if mid and state:
                        result=c.execute(update(notifications).where(notifications.c.provider_message_id==mid).values(status=state,updated_at=utcnow()))
                        updated+=result.rowcount or 0
    return {"received":True,"statuses_updated":updated}

@app.get("/api/notifications/{session_id}")
def session_notifications(session_id:str):
    with engine.connect() as c:
        if not c.execute(select(buyer_sessions.c.id).where(buyer_sessions.c.id==session_id)).first():raise HTTPException(404,"Buyer session not found")
        q=select(notifications.c.id,notifications.c.kind,notifications.c.channel,notifications.c.status,notifications.c.created_at,notifications.c.updated_at).where(notifications.c.session_id==session_id).order_by(notifications.c.created_at.desc())
        return [rowdict(x) for x in c.execute(q).all()]

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
        x=get_rfq(c,rid); x["score_reasons"]=jload(x["score_reasons"]); x["risk_flags"]=jload(x["risk_flags"]); x["next_questions"]=missing(x)
        fa=rowdict(c.execute(select(fraud_assessments).where(fraud_assessments.c.rfq_id==rid)).first())
        if fa: fa["flags"]=jload(fa["flags"])
        x["fraud"]=fa; return x

@app.post("/api/otp/send")
def otp_send(p:OTPIn):
    code=DEV_OTP if ENV!="production" else str(secrets.randbelow(900000)+100000); exp=utcnow()+timedelta(minutes=OTP_TTL)
    with engine.begin() as c:
        if not c.execute(select(buyer_sessions.c.id).where(buyer_sessions.c.id==p.session_id)).first(): raise HTTPException(404,"Buyer session not found")
        c.execute(update(buyer_sessions).where(buyer_sessions.c.id==p.session_id).values(phone=p.phone))
        c.execute(delete(otp_codes).where(otp_codes.c.session_id==p.session_id)); c.execute(insert(otp_codes).values(session_id=p.session_id,code=code,expires_at=exp,attempts=0))
        delivery=send_otp_whatsapp(p.phone,code)
        status=delivery["status"] if delivery["configured"] else ("development" if ENV!="production" else "not_configured")
        record_notification(c,"buyer_otp",p.phone,status,session_id=p.session_id,provider_message_id=delivery.get("message_id"),error=delivery.get("error"),payload=delivery.get("payload"))
        if ENV=="production" and not delivery["configured"]: raise HTTPException(503,"WhatsApp OTP is not configured")
        if ENV=="production" and delivery["status"]=="failed": raise HTTPException(502,"WhatsApp OTP delivery was rejected by provider")
    out={"sent":True,"channel":"whatsapp" if delivery["configured"] else "development","delivery_status":status,"expires_in_minutes":OTP_TTL}
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

def release_batch(c,rid,batch,source="manual"):
    if batch<1: raise HTTPException(400,"Batch must be >= 1")
    r=get_rfq(c,rid)
    verified=bool(c.execute(select(buyer_sessions.c.verified).where(buyer_sessions.c.id==r["session_id"])).scalar_one())
    if not verified: raise HTTPException(409,"Buyer OTP verification required before supplier release")
    threshold=current_threshold(c)
    if r["intent_score"]<threshold: raise HTTPException(409,f"RFQ intent score must be at least {threshold}")
    fa=assess_fraud(c,rid)
    if fa["decision"] in ("blocked","review","research"): raise HTTPException(409,f"RFQ routing held by risk control: {fa['decision']}")
    used=set(c.execute(select(matches.c.supplier_id).where(matches.c.rfq_id==rid)).scalars().all())
    allsup=[rowdict(x) for x in c.execute(select(suppliers).where(suppliers.c.verification!="Identity Pending")).all() if rowdict(x)["id"] not in used]
    limit=int_setting(c,"first_batch",3) if batch==1 else int_setting(c,"next_batch",2)
    ranked=sorted([(supplier_score(r,s),s) for s in allsup],key=lambda x:x[0],reverse=True)[:limit]; out=[]
    for score,s in ranked:
        mid="mat-"+uuid.uuid4().hex[:12]
        c.execute(insert(matches).values(id=mid,rfq_id=rid,supplier_id=s["id"],batch=batch,match_score=score,status="released",released_at=utcnow()))
        notify_supplier_rfq(c,s["id"],r)
        out.append({"match_id":mid,"supplier_id":s["id"],"supplier_name":s["name"],"match_score":score,"verification":s["verification"],"trade_score":s["trade_score"]})
    c.execute(insert(routing_events).values(id="route-"+uuid.uuid4().hex[:14],rfq_id=rid,event="batch_released",batch=batch,details=json.dumps({"source":source,"released":len(out)}),created_at=utcnow()))
    return {"rfq_id":rid,"batch":batch,"matches":out,"source":source}

@app.post("/api/rfqs/{rid}/matches/release")
def release(rid:str,batch:int=1):
    with engine.begin() as c:return release_batch(c,rid,batch,"manual")


def run_auto_expansion(c):
    if not bool_setting(c,"auto_expand",True): return {"enabled":False,"expanded":[],"checked":0}
    wait_minutes=int_setting(c,"auto_expand_minutes",DEFAULT_AUTO_EXPAND_MINUTES)
    min_responses=int_setting(c,"min_responses",DEFAULT_MIN_RESPONSES)
    max_batches=int_setting(c,"max_batches",DEFAULT_MAX_BATCHES)
    cutoff=utcnow()-timedelta(minutes=wait_minutes); expanded=[]; checked=0
    ids=c.execute(select(rfqs.c.id).where(rfqs.c.status.in_(["qualified","hot"]))).scalars().all()
    for rid in ids:
        latest=c.execute(select(func.max(matches.c.batch),func.max(matches.c.released_at)).where(matches.c.rfq_id==rid)).first()
        if not latest or latest[0] is None: continue
        batch=int(latest[0]); released_at=as_utc(latest[1]); checked+=1
        if batch>=max_batches or not released_at or released_at>cutoff: continue
        responses=c.execute(select(func.count()).select_from(quotes).where(quotes.c.rfq_id==rid)).scalar_one()
        if responses>=min_responses: continue
        try:
            result=release_batch(c,rid,batch+1,"auto")
            if result["matches"]: expanded.append({"rfq_id":rid,"batch":batch+1,"released":len(result["matches"]),"responses":responses})
        except HTTPException:
            continue
    return {"enabled":True,"expanded":expanded,"checked":checked,"auto_expand_minutes":wait_minutes,"min_responses":min_responses}

@app.post("/api/internal/routing/auto-expand")
def internal_auto_expand(x_routing_token:str|None=Header(default=None)):
    if not ROUTING_TOKEN: raise HTTPException(503,"Routing scheduler token is not configured")
    if not x_routing_token or not secrets.compare_digest(x_routing_token,ROUTING_TOKEN): raise HTTPException(401,"Routing authorization required")
    with engine.begin() as c:return run_auto_expansion(c)

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

def deal_messages_list(c,did):
    return [rowdict(x) for x in c.execute(select(deal_messages).where(deal_messages.c.deal_id==did).order_by(deal_messages.c.created_at)).all()]

def buyer_deal(c,did,session_id):
    d=rowdict(c.execute(select(deal_rooms).where(deal_rooms.c.id==did)).first())
    if not d: raise HTTPException(404,"Deal room not found")
    if d["buyer_session_id"]!=session_id: raise HTTPException(403,"Deal room does not belong to this buyer session")
    return d

def seller_deal(c,did,sid):
    d=rowdict(c.execute(select(deal_rooms).where(deal_rooms.c.id==did)).first())
    if not d: raise HTTPException(404,"Deal room not found")
    if d["supplier_id"]!=sid: raise HTTPException(403,"Deal room is not assigned to this supplier")
    return d

def deal_payload(c,d,include_buyer_contact=False):
    q=rowdict(c.execute(select(quotes).where(quotes.c.id==d["quote_id"])).first())
    r=get_rfq(c,d["rfq_id"])
    s=rowdict(c.execute(select(suppliers).where(suppliers.c.id==d["supplier_id"])).first())
    order=rowdict(c.execute(select(orders).where(orders.c.deal_id==d["id"])).first())
    buyer_phone=None
    if include_buyer_contact and d["contact_consent"]:
        buyer_phone=c.execute(select(buyer_sessions.c.phone).where(buyer_sessions.c.id==d["buyer_session_id"])).scalar()
    return {"deal":d,"rfq":{"id":r["id"],"requirement":r["requirement"],"location":r["location"],"quantity":r["quantity"],"timeline":r["timeline"]},
        "quote":q,"supplier":{"id":s["id"],"name":s["name"],"verification":s["verification"]} if s else None,
        "messages":deal_messages_list(c,d["id"]),"buyer_contact":buyer_phone,"order":order}

@app.post("/api/buyer/rfqs/{rid}/deal-room",status_code=201)
def start_deal(rid:str,p:BuyerDealStartIn):
    with engine.begin() as c:
        r=get_rfq(c,rid)
        if r["session_id"]!=p.session_id: raise HTTPException(403,"RFQ does not belong to this buyer session")
        q=rowdict(c.execute(select(quotes).where(quotes.c.id==p.quote_id,quotes.c.rfq_id==rid)).first())
        if not q: raise HTTPException(404,"Quotation not found for this RFQ")
        existing=c.execute(select(deal_rooms).where(deal_rooms.c.quote_id==p.quote_id,deal_rooms.c.buyer_session_id==p.session_id)).first()
        if existing: return deal_payload(c,rowdict(existing))
        did="deal-"+uuid.uuid4().hex[:14]; now=utcnow()
        c.execute(insert(deal_rooms).values(id=did,rfq_id=rid,quote_id=p.quote_id,supplier_id=q["supplier_id"],buyer_session_id=p.session_id,status="negotiation",contact_consent=False,created_at=now,updated_at=now))
        if p.message and p.message.strip():
            c.execute(insert(deal_messages).values(id="msg-"+uuid.uuid4().hex[:14],deal_id=did,sender_role="buyer",sender_id=p.session_id,message=p.message.strip(),created_at=now))
        c.execute(update(matches).where(matches.c.rfq_id==rid,matches.c.supplier_id==q["supplier_id"]).values(status="negotiation"))
        return deal_payload(c,rowdict(c.execute(select(deal_rooms).where(deal_rooms.c.id==did)).first()))

@app.get("/api/buyer/deals/{did}")
def get_buyer_deal(did:str,session_id:str):
    with engine.connect() as c:
        d=buyer_deal(c,did,session_id); return deal_payload(c,d)

@app.post("/api/buyer/deals/{did}/messages",status_code=201)
def buyer_message(did:str,p:BuyerDealMessageIn):
    with engine.begin() as c:
        d=buyer_deal(c,did,p.session_id)
        if d["status"]=="closed": raise HTTPException(409,"Deal room is closed")
        mid="msg-"+uuid.uuid4().hex[:14]; now=utcnow()
        c.execute(insert(deal_messages).values(id=mid,deal_id=did,sender_role="buyer",sender_id=p.session_id,message=p.message.strip(),created_at=now))
        c.execute(update(deal_rooms).where(deal_rooms.c.id==did).values(updated_at=now))
        return {"message_id":mid,"sent":True}

@app.put("/api/buyer/deals/{did}/contact-consent")
def buyer_contact_consent(did:str,p:ContactConsentIn):
    with engine.begin() as c:
        buyer_deal(c,did,p.session_id); now=utcnow()
        c.execute(update(deal_rooms).where(deal_rooms.c.id==did).values(contact_consent=p.approved,updated_at=now))
        return {"deal_id":did,"contact_consent":p.approved,"buyer_contact_shared":p.approved}

@app.post("/api/buyer/deals/{did}/orders",status_code=201)
def create_order(did:str,p:BuyerOrderIn):
    if not p.confirm_terms: raise HTTPException(400,"Buyer must confirm quotation terms")
    with engine.begin() as c:
        d=buyer_deal(c,did,p.session_id)
        existing=c.execute(select(orders).where(orders.c.deal_id==did)).first()
        if existing: return rowdict(existing)
        q=rowdict(c.execute(select(quotes).where(quotes.c.id==d["quote_id"])).first())
        amount=round(q["unit_price"]*q["quantity"]*(1+q["tax_percent"]/100)+q["freight"],2)
        oid="ord-"+uuid.uuid4().hex[:14]; now=utcnow()
        c.execute(insert(orders).values(id=oid,deal_id=did,rfq_id=d["rfq_id"],quote_id=d["quote_id"],supplier_id=d["supplier_id"],buyer_session_id=p.session_id,amount=amount,status="confirmed",created_at=now,updated_at=now))
        c.execute(update(deal_rooms).where(deal_rooms.c.id==did).values(status="ordered",updated_at=now))
        c.execute(update(matches).where(matches.c.rfq_id==d["rfq_id"],matches.c.supplier_id==d["supplier_id"]).values(status="won"))
        c.execute(update(rfqs).where(rfqs.c.id==d["rfq_id"]).values(status="order_confirmed",updated_at=now))
        return rowdict(c.execute(select(orders).where(orders.c.id==oid)).first())

@app.get("/api/buyer/orders")
def buyer_orders(session_id:str):
    with engine.connect() as c:
        q=select(orders,suppliers.c.name.label("supplier_name"),rfqs.c.requirement).join(suppliers,suppliers.c.id==orders.c.supplier_id).join(rfqs,rfqs.c.id==orders.c.rfq_id).where(orders.c.buyer_session_id==session_id).order_by(orders.c.created_at.desc())
        return [rowdict(x) for x in c.execute(q).all()]

@app.get("/api/seller/deals")
def seller_deals(authorization:str|None=Header(default=None)):
    with engine.connect() as c:
        sid=auth_seller(c,authorization)
        q=select(deal_rooms,rfqs.c.requirement,rfqs.c.location,quotes.c.unit_price,quotes.c.quantity,quotes.c.tax_percent,quotes.c.freight).join(rfqs,rfqs.c.id==deal_rooms.c.rfq_id).join(quotes,quotes.c.id==deal_rooms.c.quote_id).where(deal_rooms.c.supplier_id==sid).order_by(deal_rooms.c.updated_at.desc())
        out=[]
        for x in c.execute(q).all():
            d=rowdict(x); d["landed_price"]=round(d["unit_price"]*d["quantity"]*(1+d["tax_percent"]/100)+d["freight"],2); out.append(d)
        return out

@app.get("/api/seller/deals/{did}")
def get_seller_deal(did:str,authorization:str|None=Header(default=None)):
    with engine.connect() as c:
        sid=auth_seller(c,authorization); d=seller_deal(c,did,sid); return deal_payload(c,d,include_buyer_contact=True)

@app.post("/api/seller/deals/{did}/messages",status_code=201)
def seller_message(did:str,p:SellerDealMessageIn,authorization:str|None=Header(default=None)):
    with engine.begin() as c:
        sid=auth_seller(c,authorization); d=seller_deal(c,did,sid)
        if d["status"]=="closed": raise HTTPException(409,"Deal room is closed")
        mid="msg-"+uuid.uuid4().hex[:14]; now=utcnow()
        c.execute(insert(deal_messages).values(id=mid,deal_id=did,sender_role="seller",sender_id=sid,message=p.message.strip(),created_at=now))
        c.execute(update(deal_rooms).where(deal_rooms.c.id==did).values(updated_at=now))
        return {"message_id":mid,"sent":True}

@app.get("/api/seller/orders")
def seller_orders(authorization:str|None=Header(default=None)):
    with engine.connect() as c:
        sid=auth_seller(c,authorization)
        q=select(orders,rfqs.c.requirement,rfqs.c.location).join(rfqs,rfqs.c.id==orders.c.rfq_id).where(orders.c.supplier_id==sid).order_by(orders.c.created_at.desc())
        return [rowdict(x) for x in c.execute(q).all()]

@app.patch("/api/seller/orders/{oid}/status")
def seller_order_status(oid:str,p:OrderStatusIn,authorization:str|None=Header(default=None)):
    allowed={"confirmed","in_progress","ready","dispatched","completed","cancelled"}
    status=p.status.strip().lower()
    if status not in allowed: raise HTTPException(400,"Invalid order status")
    with engine.begin() as c:
        sid=auth_seller(c,authorization)
        o=rowdict(c.execute(select(orders).where(orders.c.id==oid)).first())
        if not o: raise HTTPException(404,"Order not found")
        if o["supplier_id"]!=sid: raise HTTPException(403,"Order is not assigned to this supplier")
        c.execute(update(orders).where(orders.c.id==oid).values(status=status,updated_at=utcnow()))
        return {"order_id":oid,"status":status}

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
    require_admin(x_admin_token); level=p.verification_level.strip()
    if level not in VERIFICATION_LEVELS or level=="Identity Pending": raise HTTPException(400,"Use Identity Verified, Business Verified, or Trade Verified")
    with engine.begin() as c:
        if not c.execute(select(seller_accounts.c.id).where(seller_accounts.c.id==sid)).first(): raise HTTPException(404,"Seller not found")
        c.execute(update(seller_profiles).where(seller_profiles.c.seller_id==sid).values(verification_level=level,updated_at=utcnow()))
        c.execute(update(seller_accounts).where(seller_accounts.c.id==sid).values(status="active")); sync_registered_supplier(c,sid)
        return {"seller_id":sid,"verification_level":level,"status":"active","matching_eligible":True}

@app.get("/api/admin/rfqs")
def admin_rfqs(x_admin_token:str|None=Header(default=None),limit:int=50):
    require_admin(x_admin_token); limit=max(1,min(limit,200))
    with engine.connect() as c:
        q=select(rfqs,fraud_assessments.c.risk_score,fraud_assessments.c.decision,fraud_assessments.c.flags.label("fraud_flags")).outerjoin(fraud_assessments,fraud_assessments.c.rfq_id==rfqs.c.id).order_by(rfqs.c.created_at.desc()).limit(limit)
        out=[]
        for row in c.execute(q).all():
            x=rowdict(row); x["score_reasons"]=jload(x["score_reasons"]); x["risk_flags"]=jload(x["risk_flags"]); x["fraud_flags"]=jload(x.get("fraud_flags")); out.append(x)
        return out

@app.put("/api/admin/rfqs/{rid}/moderation")
def admin_moderate_rfq(rid:str,p:AdminModerationIn,x_admin_token:str|None=Header(default=None)):
    require_admin(x_admin_token); action=p.action.strip().lower()
    if action not in ("approve","block","research","reassess"): raise HTTPException(400,"Action must be approve, block, research, or reassess")
    with engine.begin() as c:
        get_rfq(c,rid); assess_fraud(c,rid,keep_override=False)
        decision={"approve":"approved","block":"blocked","research":"research","reassess":"clear"}[action]
        c.execute(update(fraud_assessments).where(fraud_assessments.c.rfq_id==rid).values(decision=decision,reviewed_by="admin",updated_at=utcnow()))
        out=rescore(c,rid)
        if action=="approve":
            verified=bool(c.execute(select(buyer_sessions.c.verified).where(buyer_sessions.c.id==out["session_id"])).scalar_one())
            status=("hot" if out["intent_score"]>=80 else "qualified") if verified and out["intent_score"]>=current_threshold(c) else out["status"]
            c.execute(update(rfqs).where(rfqs.c.id==rid).values(status=status,updated_at=utcnow())); out["status"]=status
        elif action=="block":
            c.execute(update(rfqs).where(rfqs.c.id==rid).values(status="blocked",updated_at=utcnow())); out["status"]="blocked"
        elif action=="research":
            c.execute(update(rfqs).where(rfqs.c.id==rid).values(status="research",updated_at=utcnow())); out["status"]="research"
        c.execute(insert(routing_events).values(id="route-"+uuid.uuid4().hex[:14],rfq_id=rid,event="admin_"+action,batch=None,details=json.dumps({"note":p.note}),created_at=utcnow()))
        return {"rfq_id":rid,"action":action,"status":out["status"],"fraud_decision":decision}

@app.get("/api/admin/settings")
def admin_settings(x_admin_token:str|None=Header(default=None)):
    require_admin(x_admin_token)
    with engine.connect() as c:return {r.key:r.value for r in c.execute(select(system_settings)).all()}

@app.put("/api/admin/settings")
def admin_update_settings(p:AdminSettingsIn,x_admin_token:str|None=Header(default=None)):
    require_admin(x_admin_token); vals={k:v for k,v in p.model_dump().items() if v is not None}
    with engine.begin() as c:
        for key,value in vals.items():
            v=str(value).lower() if isinstance(value,bool) else str(value)
            if c.execute(select(system_settings.c.key).where(system_settings.c.key==key)).first(): c.execute(update(system_settings).where(system_settings.c.key==key).values(value=v,updated_at=utcnow()))
            else:c.execute(insert(system_settings).values(key=key,value=v,updated_at=utcnow()))
        return {"saved":True,"settings":{r.key:r.value for r in c.execute(select(system_settings)).all()}}

@app.post("/api/admin/routing/run")
def admin_run_routing(x_admin_token:str|None=Header(default=None)):
    require_admin(x_admin_token)
    with engine.begin() as c:return run_auto_expansion(c)

@app.get("/api/admin/routing")
def admin_routing(x_admin_token:str|None=Header(default=None),limit:int=50):
    require_admin(x_admin_token)
    with engine.connect() as c:
        q=select(routing_events).order_by(routing_events.c.created_at.desc()).limit(max(1,min(limit,200)))
        return [rowdict(x) for x in c.execute(q).all()]

@app.get("/api/admin/overview")
def admin(x_admin_token:str|None=Header(default=None)):
    require_admin(x_admin_token)
    with engine.connect() as c:
        threshold=current_threshold(c)
        return {"buyer_sessions":c.execute(select(func.count()).select_from(buyer_sessions)).scalar_one(),
            "seller_accounts":c.execute(select(func.count()).select_from(seller_accounts)).scalar_one(),
            "rfqs":c.execute(select(func.count()).select_from(rfqs)).scalar_one(),
            "qualified_rfqs":c.execute(select(func.count()).select_from(rfqs).where(rfqs.c.intent_score>=threshold,rfqs.c.status.in_(["qualified","hot"]))).scalar_one(),
            "review_rfqs":c.execute(select(func.count()).select_from(fraud_assessments).where(fraud_assessments.c.decision=="review")).scalar_one(),
            "blocked_rfqs":c.execute(select(func.count()).select_from(fraud_assessments).where(fraud_assessments.c.decision=="blocked")).scalar_one(),
            "released_matches":c.execute(select(func.count()).select_from(matches)).scalar_one(),
            "quotes":c.execute(select(func.count()).select_from(quotes)).scalar_one(),
            "deal_rooms":c.execute(select(func.count()).select_from(deal_rooms)).scalar_one(),
            "orders":c.execute(select(func.count()).select_from(orders)).scalar_one(),
            "notifications":c.execute(select(func.count()).select_from(notifications)).scalar_one(),
            "controls":{"qualification_threshold":threshold,"first_batch":int_setting(c,"first_batch",3),"next_batch":int_setting(c,"next_batch",2),
                "auto_expand_minutes":int_setting(c,"auto_expand_minutes",DEFAULT_AUTO_EXPAND_MINUTES),"min_responses":int_setting(c,"min_responses",DEFAULT_MIN_RESPONSES),
                "max_batches":int_setting(c,"max_batches",DEFAULT_MAX_BATCHES),"duplicate_screening":bool_setting(c,"duplicate_screening",True),
                "auto_expand":bool_setting(c,"auto_expand",True),"buyer_contact_protected":True}}

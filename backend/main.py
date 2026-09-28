from __future__ import annotations
import hashlib, hmac, json, os, secrets, sqlite3, uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

DB_PATH=Path(os.getenv("TRADEAI_DB",Path(__file__).with_name("tradeai.db")))
DEV_OTP=os.getenv("TRADEAI_DEV_OTP","123456")
OTP_TTL=int(os.getenv("TRADEAI_OTP_TTL_MINUTES","10"))
THRESHOLD=int(os.getenv("TRADEAI_QUALIFIED_THRESHOLD","60"))

def now(): return datetime.now(timezone.utc).isoformat()
def db():
    c=sqlite3.connect(DB_PATH); c.row_factory=sqlite3.Row; c.execute("PRAGMA foreign_keys=ON"); return c

def init_db():
    DB_PATH.parent.mkdir(parents=True,exist_ok=True)
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS buyer_sessions(id TEXT PRIMARY KEY,created_at TEXT NOT NULL,verified INTEGER NOT NULL DEFAULT 0,phone TEXT,company TEXT);
        CREATE TABLE IF NOT EXISTS seller_accounts(id TEXT PRIMARY KEY,full_name TEXT NOT NULL,business_name TEXT NOT NULL,mobile TEXT NOT NULL UNIQUE,email TEXT NOT NULL UNIQUE,seller_type TEXT NOT NULL,category TEXT NOT NULL,password_salt TEXT NOT NULL,password_hash TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'profile_pending',created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS seller_sessions(token TEXT PRIMARY KEY,seller_id TEXT NOT NULL,created_at TEXT NOT NULL,expires_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS rfqs(id TEXT PRIMARY KEY,session_id TEXT NOT NULL,requirement TEXT NOT NULL,category TEXT,quantity TEXT,location TEXT,timeline TEXT,specifications TEXT,budget TEXT,status TEXT NOT NULL,intent_score INTEGER NOT NULL DEFAULT 0,score_reasons TEXT NOT NULL DEFAULT '[]',risk_flags TEXT NOT NULL DEFAULT '[]',created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS otp_codes(session_id TEXT PRIMARY KEY,code TEXT NOT NULL,expires_at TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS suppliers(id TEXT PRIMARY KEY,name TEXT NOT NULL,categories TEXT NOT NULL,locations TEXT NOT NULL,verification TEXT NOT NULL,trade_score INTEGER NOT NULL,response_score INTEGER NOT NULL,moq TEXT,capabilities TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS matches(id TEXT PRIMARY KEY,rfq_id TEXT NOT NULL,supplier_id TEXT NOT NULL,batch INTEGER NOT NULL,match_score INTEGER NOT NULL,status TEXT NOT NULL,released_at TEXT NOT NULL,UNIQUE(rfq_id,supplier_id));
        CREATE TABLE IF NOT EXISTS quotes(id TEXT PRIMARY KEY,rfq_id TEXT NOT NULL,supplier_id TEXT NOT NULL,unit_price REAL NOT NULL,quantity REAL NOT NULL DEFAULT 1,tax_percent REAL NOT NULL DEFAULT 0,freight REAL NOT NULL DEFAULT 0,delivery_days INTEGER NOT NULL,warranty_months INTEGER NOT NULL DEFAULT 0,payment_terms TEXT,validity_days INTEGER NOT NULL DEFAULT 7,notes TEXT,created_at TEXT NOT NULL);
        """)
        if not c.execute("SELECT COUNT(*) n FROM suppliers").fetchone()["n"]:
            rows=[
            ("sup-001","Shakti Industrial Supply",["industrial supplies","construction","cement"],["uttarakhand","delhi","ncr","north india"],"Trade Verified",92,90,"Flexible",["bulk supply","gst invoice","dispatch tracking"]),
            ("sup-002","Bharat Build Materials",["construction","cement","packaging"],["uttarakhand","up","delhi","north india"],"Business Verified",88,86,"10 units",["bulk orders","multi-brand","freight support"]),
            ("sup-003","Apex Trade Solutions",["industrial supplies","machinery","electrical","cement"],["india","uttarakhand","delhi"],"Trade Verified",90,84,"Flexible",["industrial procurement","priority quotes","pan-india"]),
            ("sup-004","NorthStar Enterprises",["electrical","machinery","industrial supplies"],["uttarakhand","up","haryana"],"Business Verified",82,80,"Varies",["electrical","machinery","project supply"]),
            ("sup-005","GreenField B2B",["food & agri","packaging"],["india","uttarakhand","up"],"Identity Verified",76,78,"Varies",["agri sourcing","packaging","distribution"])]
            for x in rows: c.execute("INSERT INTO suppliers VALUES (?,?,?,?,?,?,?,?,?)",(x[0],x[1],json.dumps(x[2]),json.dumps(x[3]),x[4],x[5],x[6],x[7],json.dumps(x[8])))

@asynccontextmanager
async def lifespan(_:FastAPI):
    init_db(); yield

app=FastAPI(title="TradeAI API",version="1.0.0",lifespan=lifespan)
app.add_middleware(CORSMiddleware,allow_origins=[x.strip() for x in os.getenv("TRADEAI_CORS_ORIGINS","https://tradeai-pgvr.onrender.com,http://localhost:8000,http://127.0.0.1:8000").split(",") if x.strip()],allow_credentials=True,allow_methods=["*"],allow_headers=["*"])

class SessionIn(BaseModel): company:str|None=None
class RFQIn(BaseModel):
    session_id:str; requirement:str=Field(min_length=8,max_length=3000); category:str|None=None; quantity:str|None=None; location:str|None=None; timeline:str|None=None; specifications:str|None=None; budget:str|None=None
class QualificationIn(BaseModel):
    category:str|None=None; quantity:str|None=None; location:str|None=None; timeline:str|None=None; specifications:str|None=None; budget:str|None=None
class OTPIn(BaseModel): session_id:str; phone:str=Field(min_length=8,max_length=20)
class OTPVerify(BaseModel): session_id:str; code:str=Field(min_length=4,max_length=8)
class SellerAccountIn(BaseModel):
    full_name:str=Field(min_length=2,max_length=100); business_name:str=Field(min_length=2,max_length=160); mobile:str=Field(min_length=8,max_length=20); email:str=Field(min_length=5,max_length=160); seller_type:str=Field(min_length=2,max_length=80); category:str=Field(min_length=2,max_length=100); password:str=Field(min_length=8,max_length=200)
class SellerLoginIn(BaseModel):
    login:str=Field(min_length=5,max_length=160); password:str=Field(min_length=8,max_length=200)
class QuoteIn(BaseModel):
    supplier_id:str; unit_price:float=Field(gt=0); quantity:float=Field(default=1,gt=0); tax_percent:float=Field(default=0,ge=0,le=100); freight:float=Field(default=0,ge=0); delivery_days:int=Field(gt=0); warranty_months:int=Field(default=0,ge=0); payment_terms:str|None=None; validity_days:int=Field(default=7,gt=0); notes:str|None=None

def rfq(c,rid):
    x=c.execute("SELECT * FROM rfqs WHERE id=?",(rid,)).fetchone()
    if not x: raise HTTPException(404,"RFQ not found")
    return x

def calc_score(x:dict[str,Any],verified:bool):
    score=0; reasons=[]; risks=[]; req=(x.get("requirement") or "").strip()
    for ok,pts,msg in [(len(req)>=20,15,"Clear requirement +15"),(bool(x.get("category")),10,"Category identified +10"),(bool(x.get("quantity")),15,"Quantity provided +15"),(bool(x.get("location")),15,"Location provided +15"),(bool(x.get("timeline")),15,"Timeline provided +15"),(bool(x.get("specifications")),10,"Specifications provided +10"),(bool(x.get("budget")),5,"Budget guidance +5"),(verified,20,"OTP verified +20")]:
        if ok: score+=pts; reasons.append(msg)
    if len(set(req.lower().split()))<=2: score-=10; risks.append("Low-information enquiry")
    if any(v in req.lower() for v in ["test test","asdf","free money"]): score-=25; risks.append("Possible spam/garbage text")
    return max(0,min(100,score)),reasons,risks

def missing(x):
    q=[("category","Which product/service category best matches this requirement?"),("quantity","What quantity, size or capacity do you need?"),("location","Where should it be delivered or performed?"),("timeline","When do you need to purchase or start?"),("specifications","Any important grade, brand, dimensions or specifications?")]
    return [{"field":k,"question":v} for k,v in q if not x.get(k)][:3]

def rescore(c,rid):
    x=dict(rfq(c,rid)); v=c.execute("SELECT verified FROM buyer_sessions WHERE id=?",(x["session_id"],)).fetchone()["verified"]; score,reasons,risks=calc_score(x,bool(v))
    status="hot" if score>=80 else "qualified" if score>=THRESHOLD else "needs_more_information" if score>=40 else "research"
    c.execute("UPDATE rfqs SET intent_score=?,score_reasons=?,risk_flags=?,status=?,updated_at=? WHERE id=?",(score,json.dumps(reasons),json.dumps(risks),status,now(),rid)); c.commit()
    x.update(intent_score=score,score_reasons=reasons,risk_flags=risks,status=status); return x

def password_hash(password:str,salt_hex:str|None=None):
    salt=bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(16)
    digest=hashlib.pbkdf2_hmac("sha256",password.encode(),salt,210000)
    return salt.hex(),digest.hex()

def password_ok(password:str,salt_hex:str,expected:str):
    _,actual=password_hash(password,salt_hex)
    return hmac.compare_digest(actual,expected)

def seller_public(x):
    return {"id":x["id"],"full_name":x["full_name"],"business_name":x["business_name"],"mobile":x["mobile"],"email":x["email"],"seller_type":x["seller_type"],"category":x["category"],"status":x["status"],"created_at":x["created_at"]}

def supplier_score(r,s):
    cat=(r["category"] or r["requirement"]).lower(); loc=(r["location"] or "").lower(); cats=json.loads(s["categories"]); locs=" ".join(json.loads(s["locations"])).lower(); n=0
    if any(x in cat or cat in x for x in cats): n+=45
    elif any(x in " ".join(cats) for x in cat.split() if len(x)>3): n+=25
    if loc and any(x in locs for x in loc.split() if len(x)>2): n+=25
    elif "india" in locs: n+=12
    return min(100,n+round(s["trade_score"]*.18)+round(s["response_score"]*.12))

@app.get("/")
def root(): return {"service":"TradeAI API","status":"ok","version":"1.0.0"}
@app.get("/api/health")
def health(): return {"ok":True,"database":DB_PATH.name,"qualification_threshold":THRESHOLD}

@app.post("/api/buyer/sessions",status_code=201)
def create_session(p:SessionIn):
    sid="buy-"+uuid.uuid4().hex[:16]
    with db() as c: c.execute("INSERT INTO buyer_sessions(id,created_at,company) VALUES (?,?,?)",(sid,now(),p.company))
    return {"session_id":sid,"registration_required":False}

@app.post("/api/rfqs",status_code=201)
def create_rfq(p:RFQIn):
    rid="rfq-"+uuid.uuid4().hex[:16]
    with db() as c:
        if not c.execute("SELECT 1 FROM buyer_sessions WHERE id=?",(p.session_id,)).fetchone(): raise HTTPException(404,"Buyer session not found")
        v=p.model_dump(); c.execute("INSERT INTO rfqs(id,session_id,requirement,category,quantity,location,timeline,specifications,budget,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,'research',?,?)",(rid,v["session_id"],v["requirement"],v["category"],v["quantity"],v["location"],v["timeline"],v["specifications"],v["budget"],now(),now()))
        out=rescore(c,rid); out["next_questions"]=missing(out); return out

@app.patch("/api/rfqs/{rid}/qualify")
def qualify(rid:str,p:QualificationIn):
    with db() as c:
        rfq(c,rid); u={k:v for k,v in p.model_dump().items() if v is not None}
        if u: c.execute("UPDATE rfqs SET "+",".join(f"{k}=?" for k in u)+",updated_at=? WHERE id=?",(*u.values(),now(),rid))
        out=rescore(c,rid); out["next_questions"]=missing(out); return out

@app.get("/api/rfqs/{rid}")
def rfq_detail(rid:str):
    with db() as c:
        x=dict(rfq(c,rid)); x["score_reasons"]=json.loads(x["score_reasons"]); x["risk_flags"]=json.loads(x["risk_flags"]); x["next_questions"]=missing(x); return x

@app.post("/api/otp/send")
def otp_send(p:OTPIn):
    exp=datetime.now(timezone.utc)+timedelta(minutes=OTP_TTL); code=DEV_OTP if os.getenv("TRADEAI_ENV","development")!="production" else str(secrets.randbelow(900000)+100000)
    with db() as c:
        if not c.execute("SELECT 1 FROM buyer_sessions WHERE id=?",(p.session_id,)).fetchone(): raise HTTPException(404,"Buyer session not found")
        c.execute("UPDATE buyer_sessions SET phone=? WHERE id=?",(p.phone,p.session_id)); c.execute("INSERT OR REPLACE INTO otp_codes VALUES (?,?,?,0)",(p.session_id,code,exp.isoformat()))
    out={"sent":True,"channel":"whatsapp_ready","expires_in_minutes":OTP_TTL}
    if os.getenv("TRADEAI_ENV","development")!="production": out["dev_otp"]=code
    return out

@app.post("/api/otp/verify")
def otp_verify(p:OTPVerify):
    with db() as c:
        x=c.execute("SELECT * FROM otp_codes WHERE session_id=?",(p.session_id,)).fetchone()
        if not x: raise HTTPException(404,"OTP not requested")
        if x["attempts"]>=5: raise HTTPException(429,"Too many OTP attempts")
        c.execute("UPDATE otp_codes SET attempts=attempts+1 WHERE session_id=?",(p.session_id,))
        if datetime.fromisoformat(x["expires_at"])<datetime.now(timezone.utc): raise HTTPException(410,"OTP expired")
        if not secrets.compare_digest(x["code"],p.code): raise HTTPException(400,"Invalid OTP")
        c.execute("UPDATE buyer_sessions SET verified=1 WHERE id=?",(p.session_id,))
        rows=c.execute("SELECT id FROM rfqs WHERE session_id=?",(p.session_id,)).fetchall(); out=[rescore(c,x["id"]) for x in rows]
        return {"verified":True,"rfqs":[{"id":x["id"],"intent_score":x["intent_score"],"status":x["status"]} for x in out]}

@app.post("/api/rfqs/{rid}/matches/release")
def release(rid:str,batch:int=1):
    if batch<1: raise HTTPException(400,"Batch must be >= 1")
    with db() as c:
        r=rfq(c,rid)
        if r["intent_score"]<THRESHOLD: raise HTTPException(409,f"RFQ intent score must be at least {THRESHOLD}")
        used={x["supplier_id"] for x in c.execute("SELECT supplier_id FROM matches WHERE rfq_id=?",(rid,))}
        ranked=sorted([(supplier_score(r,s),s) for s in c.execute("SELECT * FROM suppliers") if s["id"] not in used],key=lambda x:x[0],reverse=True)[:3 if batch==1 else 2]; out=[]
        for score,s in ranked:
            mid="mat-"+uuid.uuid4().hex[:12]; c.execute("INSERT INTO matches VALUES (?,?,?,?,?,?,?)",(mid,rid,s["id"],batch,score,"released",now())); out.append({"match_id":mid,"supplier_id":s["id"],"supplier_name":s["name"],"match_score":score,"verification":s["verification"],"trade_score":s["trade_score"]})
        return {"rfq_id":rid,"batch":batch,"matches":out}

@app.get("/api/rfqs/{rid}/matches")
def matches(rid:str):
    with db() as c:
        rfq(c,rid); return [dict(x) for x in c.execute("SELECT m.*,s.name supplier_name,s.verification,s.trade_score FROM matches m JOIN suppliers s ON s.id=m.supplier_id WHERE m.rfq_id=? ORDER BY m.batch,m.match_score DESC",(rid,))]

@app.post("/api/seller/accounts",status_code=201)
def seller_account(p:SellerAccountIn):
    sid="sel-"+uuid.uuid4().hex[:16]; salt,digest=password_hash(p.password)
    with db() as c:
        if c.execute("SELECT 1 FROM seller_accounts WHERE lower(email)=lower(?) OR mobile=?",(p.email.strip(),p.mobile.strip())).fetchone(): raise HTTPException(409,"Seller account already exists")
        c.execute("INSERT INTO seller_accounts VALUES (?,?,?,?,?,?,?,?,?,?,?)",(sid,p.full_name.strip(),p.business_name.strip(),p.mobile.strip(),p.email.strip().lower(),p.seller_type,p.category,salt,digest,"profile_pending",now()))
        x=c.execute("SELECT * FROM seller_accounts WHERE id=?",(sid,)).fetchone()
        return {"seller":seller_public(x),"next_step":"business_profile"}

@app.post("/api/seller/login")
def seller_login(p:SellerLoginIn):
    with db() as c:
        x=c.execute("SELECT * FROM seller_accounts WHERE lower(email)=lower(?) OR mobile=?",(p.login.strip(),p.login.strip())).fetchone()
        if not x or not password_ok(p.password,x["password_salt"],x["password_hash"]): raise HTTPException(401,"Invalid login")
        token=secrets.token_urlsafe(32); exp=datetime.now(timezone.utc)+timedelta(days=7)
        c.execute("INSERT INTO seller_sessions VALUES (?,?,?,?)",(token,x["id"],now(),exp.isoformat()))
        return {"access_token":token,"token_type":"bearer","expires_at":exp.isoformat(),"seller":seller_public(x)}

@app.get("/api/seller/session/{token}")
def seller_session(token:str):
    with db() as c:
        x=c.execute("SELECT a.* ,s.expires_at FROM seller_sessions s JOIN seller_accounts a ON a.id=s.seller_id WHERE s.token=?",(token,)).fetchone()
        if not x or datetime.fromisoformat(x["expires_at"])<datetime.now(timezone.utc): raise HTTPException(401,"Seller session expired or invalid")
        return {"seller":seller_public(x)}

@app.get("/api/sellers/{sid}/opportunities")
def opportunities(sid:str):
    with db() as c:
        if not c.execute("SELECT 1 FROM suppliers WHERE id=?",(sid,)).fetchone(): raise HTTPException(404,"Supplier not found")
        return [dict(x) for x in c.execute("SELECT r.id rfq_id,r.requirement,r.category,r.quantity,r.location,r.timeline,r.intent_score,r.status,m.match_score,m.batch,m.status match_status FROM matches m JOIN rfqs r ON r.id=m.rfq_id WHERE m.supplier_id=? ORDER BY m.released_at DESC",(sid,))]

@app.post("/api/rfqs/{rid}/quotes",status_code=201)
def quote(rid:str,p:QuoteIn):
    qid="quo-"+uuid.uuid4().hex[:14]
    with db() as c:
        rfq(c,rid)
        if not c.execute("SELECT 1 FROM matches WHERE rfq_id=? AND supplier_id=?",(rid,p.supplier_id)).fetchone(): raise HTTPException(403,"Supplier is not released for this RFQ")
        x=p.model_dump(); c.execute("INSERT INTO quotes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",(qid,rid,x["supplier_id"],x["unit_price"],x["quantity"],x["tax_percent"],x["freight"],x["delivery_days"],x["warranty_months"],x["payment_terms"],x["validity_days"],x["notes"],now()))
        return {"quote_id":qid,"landed_price":round(x["unit_price"]*x["quantity"]*(1+x["tax_percent"]/100)+x["freight"],2)}

@app.get("/api/rfqs/{rid}/quotes/compare")
def compare(rid:str):
    with db() as c:
        rfq(c,rid); items=[]
        for r in c.execute("SELECT q.*,s.name supplier_name,s.verification,s.trade_score FROM quotes q JOIN suppliers s ON s.id=q.supplier_id WHERE q.rfq_id=?",(rid,)):
            x=dict(r); x["landed_price"]=round(x["unit_price"]*x["quantity"]*(1+x["tax_percent"]/100)+x["freight"],2); items.append(x)
        if not items: return {"quotes":[],"objective_highlights":{}}
        return {"quotes":items,"objective_highlights":{"lowest_landed_price_quote_id":min(items,key=lambda x:x["landed_price"])["id"],"fastest_delivery_quote_id":min(items,key=lambda x:x["delivery_days"])["id"],"longest_warranty_quote_id":max(items,key=lambda x:x["warranty_months"])["id"]}}

@app.get("/api/admin/overview")
def admin():
    with db() as c:
        return {"buyer_sessions":c.execute("SELECT COUNT(*) n FROM buyer_sessions").fetchone()["n"],"rfqs":c.execute("SELECT COUNT(*) n FROM rfqs").fetchone()["n"],"qualified_rfqs":c.execute("SELECT COUNT(*) n FROM rfqs WHERE intent_score>=?",(THRESHOLD,)).fetchone()["n"],"released_matches":c.execute("SELECT COUNT(*) n FROM matches").fetchone()["n"],"quotes":c.execute("SELECT COUNT(*) n FROM quotes").fetchone()["n"],"risk_flagged_rfqs":c.execute("SELECT COUNT(*) n FROM rfqs WHERE risk_flags!='[]'").fetchone()["n"],"controls":{"qualification_threshold":THRESHOLD,"first_batch":3,"next_batch":2,"buyer_contact_protected":True}}

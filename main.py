from fastapi import FastAPI, UploadFile, File, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from sambanova import SambaNova
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import DESCENDING

import os
import asyncio
import base64
import json
import re
import uuid
import urllib.request
from datetime import datetime, timezone
from contextlib import asynccontextmanager
import stripe

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

import firebase_admin
from firebase_admin import credentials as fb_credentials, auth as fb_auth

# ─────────────────────────────
load_dotenv(os.path.join(BASE_DIR, ".env"), override=True)   # reads .env into os.environ

# ─────────────────────────────
# Config
# ─────────────────────────────
SAMBANOVA_API_KEY   = os.environ.get("SAMBANOVA_API_KEY")
SAMBANOVA_BASE_URL  = os.environ.get("SAMBANOVA_BASE_URL", "https://api.sambanova.ai/v1")
MONGO_URI           = os.environ.get("MONGO_URI", "")
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL", "")  # auto-set by Render
FIREBASE_CREDS_PATH = os.environ.get("FIREBASE_CREDS_PATH", "")
FIREBASE_CREDS_JSON = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON", "")
STRIPE_API_KEY      = os.environ.get("STRIPE_API_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
if STRIPE_API_KEY:
    stripe.api_key = STRIPE_API_KEY

# ─────────────────────────────
# Firebase Admin SDK
# ─────────────────────────────
if FIREBASE_CREDS_JSON:
    import json
    try:
        _cred_dict = json.loads(FIREBASE_CREDS_JSON)
        _cred = fb_credentials.Certificate(_cred_dict)
        if not firebase_admin._apps:
            firebase_admin.initialize_app(_cred)
        print("[INFO] Firebase Admin SDK initialised from JSON env var ✓")
    except Exception as e:
        print(f"[ERROR] Failed to parse FIREBASE_SERVICE_ACCOUNT_JSON: {e}")
elif FIREBASE_CREDS_PATH:
    if not os.path.isabs(FIREBASE_CREDS_PATH):
        FIREBASE_CREDS_PATH = os.path.join(BASE_DIR, FIREBASE_CREDS_PATH)
    if os.path.exists(FIREBASE_CREDS_PATH):
        _cred = fb_credentials.Certificate(FIREBASE_CREDS_PATH)
        if not firebase_admin._apps:
            firebase_admin.initialize_app(_cred)
        print(f"[INFO] Firebase Admin SDK initialised from file ✓ ({FIREBASE_CREDS_PATH})")
    else:
        print(f"[WARN] FIREBASE_CREDS_PATH not set or file missing at {FIREBASE_CREDS_PATH} — auth will not work.")
else:
    print("[WARN] No Firebase credentials provided (neither JSON nor PATH).")

security = HTTPBearer()


async def get_current_user(
    creds: HTTPAuthorizationCredentials = Depends(security),
) -> dict:
    """Verify the Firebase ID-token from the Authorization header."""
    if not firebase_admin._apps:
        raise HTTPException(
            status_code=501,
            detail="Firebase is not configured on the server. Set FIREBASE_CREDS_PATH in .env.",
        )
    try:
        decoded = fb_auth.verify_id_token(creds.credentials)
        return decoded  # contains 'uid', 'email', etc.
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired authentication token.")


async def get_current_admin(user: dict = Depends(get_current_user)) -> dict:
    """Check if the current user has the admin role in MongoDB."""
    user_doc = await users_collection.find_one({"_id": user["uid"]})
    if not user_doc or user_doc.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required.")
    return user

# ─────────────────────────────
# SambaNova client
# ─────────────────────────────
client_samba = SambaNova(
    api_key=SAMBANOVA_API_KEY,
    base_url=SAMBANOVA_BASE_URL,
)

MODEL = "gemma-4-31B-it"

# ─────────────────────────────
# Allowed image types
# ─────────────────────────────
ALLOWED_MIME_TYPES = {
    "image/jpeg": "image/jpeg",
    "image/jpg":  "image/jpeg",
    "image/png":  "image/png",
    "image/webp": "image/webp",
    "image/gif":  "image/gif",
}

MAX_FILE_SIZE_MB = 10

# ─────────────────────────────
# MongoDB — module-level refs filled in lifespan
# ─────────────────────────────
mongo_client: AsyncIOMotorClient | None = None
bills_collection = None
users_collection = None


# ─────────────────────────────
# Keepalive — prevents Render free tier from sleeping
# ─────────────────────────────
async def _keepalive_loop(url: str) -> None:
    """Ping /health every 14 min so Render free tier never idles out (sleeps at 15 min)."""
    ping_url = f"{url.rstrip('/')}/health"
    while True:
        await asyncio.sleep(14 * 60)  # 14 minutes
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: urllib.request.urlopen(ping_url, timeout=10)
            )
            print("[INFO] Keepalive ping sent ✓")
        except Exception as exc:
            print(f"[WARN] Keepalive ping failed: {exc}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Connect to MongoDB Atlas on startup; disconnect on shutdown."""
    global mongo_client, bills_collection, users_collection

    if not MONGO_URI:
        raise RuntimeError(
            "MONGO_URI environment variable is not set. "
            "Add it to your .env file or cloud environment."
        )

    mongo_client = AsyncIOMotorClient(MONGO_URI)
    db = mongo_client["billmate"]
    bills_collection = db["bills"]
    users_collection = db["users"]

    # Ensure index on created_at for efficient sorting
    await bills_collection.create_index([("created_at", DESCENDING)])
    await bills_collection.create_index([("user_id", 1), ("created_at", DESCENDING)])

    print("[INFO] Connected to MongoDB Atlas ✓")

    # Start keepalive background task if running on Render
    keepalive_task = None
    if RENDER_EXTERNAL_URL:
        keepalive_task = asyncio.create_task(_keepalive_loop(RENDER_EXTERNAL_URL))
        print(f"[INFO] Keepalive task started → pinging every 14 min")
    else:
        print("[INFO] RENDER_EXTERNAL_URL not set — keepalive disabled (local dev mode)")

    yield

    if keepalive_task:
        keepalive_task.cancel()
    mongo_client.close()
    print("[INFO] MongoDB connection closed.")


# ─────────────────────────────
# Helper: serialize a MongoDB document to a JSON-safe dict
# ─────────────────────────────
def serialize_bill(doc: dict) -> dict:
    """Convert a MongoDB document to a plain dict with string 'id'."""
    doc = dict(doc)
    doc["id"] = str(doc.pop("_id"))
    return doc


# ─────────────────────────────
# FastAPI app
# ─────────────────────────────
app = FastAPI(title="Bill Mate API", version="3.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─────────────────────────────
# Helper: robust JSON extraction
# ─────────────────────────────
def extract_json(raw: str) -> dict:
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    raise ValueError(f"No valid JSON found in model response: {raw[:200]}")


# ─────────────────────────────
# Pages
# ─────────────────────────────
@app.get("/", response_class=HTMLResponse)
def root():
    """Serve the main dashboard directly."""
    html_path = os.path.join(os.path.dirname(__file__), "templates", "index.html")
    return FileResponse(html_path)


@app.get("/privacy", response_class=HTMLResponse)
def privacy_policy():
    """Serve the privacy policy page."""
    html_path = os.path.join(os.path.dirname(__file__), "templates", "privacy.html")
    if os.path.exists(html_path):
        return FileResponse(html_path)
    raise HTTPException(status_code=404, detail="Privacy Policy not found")


@app.get("/terms", response_class=HTMLResponse)
def terms_of_service():
    """Serve the terms of service page."""
    html_path = os.path.join(os.path.dirname(__file__), "templates", "terms.html")
    if os.path.exists(html_path):
        return FileResponse(html_path)
    raise HTTPException(status_code=404, detail="Terms of Service not found")


@app.get("/Logo.png")
def get_logo():
    """Serve the Logo.png image."""
    logo_path = os.path.join(os.path.dirname(__file__), "templates", "Logo.png")
    if os.path.exists(logo_path):
        return FileResponse(logo_path)
    raise HTTPException(status_code=404, detail="Logo not found")


# ─────────────────────────────
# Health check
# ─────────────────────────────
@app.get("/health")
async def health():
    """Check API + database connectivity."""
    try:
        await mongo_client.admin.command("ping")
        db_status = "connected"
    except Exception as e:
        db_status = f"error: {e}"
    return {"status": "ok", "database": db_status}


# ─────────────────────────────
# Subscription endpoints
# ─────────────────────────────
async def get_user_subscription_info(user: dict):
    """Fetch user subscription info and current month scan count."""
    uid = user["uid"]
    email = user.get("email")
    name = user.get("name") or user.get("displayName")
    
    user_doc = await users_collection.find_one({"_id": uid})
    if not user_doc:
        user_doc = {"_id": uid, "is_pro": False, "email": email, "name": name, "role": "user"}
        await users_collection.insert_one(user_doc)
    else:
        updates = {}
        if email and user_doc.get("email") != email:
            updates["email"] = email
        if name and user_doc.get("name") != name:
            updates["name"] = name
        if updates:
            await users_collection.update_one({"_id": uid}, {"$set": updates})

    is_pro = True # Temporary: force everyone to be a pro user
    # is_pro = user_doc.get("is_pro", False)
    
    # Calculate scans this month
    now = datetime.now(timezone.utc)
    start_of_month = datetime(now.year, now.month, 1, tzinfo=timezone.utc).isoformat()
    
    scan_count = await bills_collection.count_documents({
        "user_id": uid,
        "created_at": {"$gte": start_of_month}
    })
    
    scan_limit = 5
    return {
        "is_pro": is_pro,
        "scan_count": scan_count,
        "scan_limit": scan_limit,
        "can_scan": is_pro or scan_count < scan_limit
    }


@app.get("/user-status")
async def user_status(user: dict = Depends(get_current_user)):
    return await get_user_subscription_info(user)


@app.post("/create-checkout-session")
async def create_checkout_session(request: Request, user: dict = Depends(get_current_user)):
    if not STRIPE_API_KEY:
        raise HTTPException(status_code=500, detail="Stripe is not configured on the server.")
    try:
        origin = request.headers.get('origin', 'http://localhost:8000')
        session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'inr',
                    'product_data': {
                        'name': 'Bill Mate Pro Subscription',
                    },
                    'unit_amount': 9900,
                    'recurring': {
                        'interval': 'month',
                    },
                },
                'quantity': 1,
            }],
            mode='subscription',
            success_url=f"{origin}/?success=true",
            cancel_url=f"{origin}/?canceled=true",
            client_reference_id=user["uid"],
        )
        return {"url": session.url}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError as e:
        raise HTTPException(status_code=400, detail="Invalid signature")

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        uid = session.get("client_reference_id")
        if uid:
            await users_collection.update_one(
                {"_id": uid},
                {"$set": {"is_pro": True, "stripe_customer_id": session.get("customer")}},
                upsert=True
            )
    elif event["type"] == "customer.subscription.deleted":
        subscription = event["data"]["object"]
        customer_id = subscription.get("customer")
        if customer_id:
            await users_collection.update_one(
                {"stripe_customer_id": customer_id},
                {"$set": {"is_pro": False}}
            )

    return {"status": "success"}

# ─────────────────────────────
# Admin Endpoints
# ─────────────────────────────
@app.get("/admin", response_class=HTMLResponse)
async def admin_portal():
    html_path = os.path.join(os.path.dirname(__file__), "templates", "admin.html")
    if os.path.exists(html_path):
        return FileResponse(html_path)
    raise HTTPException(status_code=404, detail="Admin Portal not found")

@app.get("/api/admin/users")
async def get_all_users(admin: dict = Depends(get_current_admin)):
    cursor = users_collection.find()
    users = []
    async for doc in cursor:
        uid = doc["_id"]
        # Calculate scans this month
        now = datetime.now(timezone.utc)
        start_of_month = datetime(now.year, now.month, 1, tzinfo=timezone.utc).isoformat()
        scan_count = await bills_collection.count_documents({
            "user_id": uid,
            "created_at": {"$gte": start_of_month}
        })
        
        users.append({
            "id": uid,
            "email": doc.get("email", "Unknown"),
            "name": doc.get("name", "Unknown"),
            "is_pro": doc.get("is_pro", False),
            "role": doc.get("role", "user"),
            "scan_count": scan_count
        })
    return users

@app.post("/api/admin/users/{uid}/grant-pro")
async def grant_pro(uid: str, admin: dict = Depends(get_current_admin)):
    result = await users_collection.update_one({"_id": uid}, {"$set": {"is_pro": True}})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="User not found")
    return {"success": True}

@app.post("/api/admin/users/{uid}/revoke-pro")
async def revoke_pro(uid: str, admin: dict = Depends(get_current_admin)):
    result = await users_collection.update_one({"_id": uid}, {"$set": {"is_pro": False}})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="User not found")
    return {"success": True}
# ─────────────────────────────
# GET /bills  — read from MongoDB
# ─────────────────────────────
@app.get("/bills")
async def get_bills(user: dict = Depends(get_current_user)):
    """Return all bills for the authenticated user, newest first."""
    cursor = bills_collection.find({"user_id": user["uid"]}).sort("created_at", DESCENDING)
    bills = [serialize_bill(doc) async for doc in cursor]
    return bills


# ─────────────────────────────
# POST /bills  — manually create a bill
# ─────────────────────────────
@app.post("/bills")
async def create_bill(request: Request, user: dict = Depends(get_current_user)):
    """Save a manually entered bill directly to MongoDB Atlas."""
    info = await get_user_subscription_info(user)
    if not info["can_scan"]:
        raise HTTPException(status_code=403, detail="Free limit reached. Please upgrade to Pro.")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body.")

    bill_id  = str(uuid.uuid4())
    new_bill = {
        "_id":          bill_id,
        "user_id":      user["uid"],
        "vendor_name":  body.get("vendor_name") or None,
        "bill_date":    body.get("bill_date")   or None,
        "items":        body.get("items")        or [],
        "tax":          body.get("tax")          or 0,
        "cgst_percentage": body.get("cgst_percentage"),
        "sgst_percentage": body.get("sgst_percentage"),
        "gstin":        body.get("gstin"),
        "total_amount": body.get("total_amount") or 0,
        "created_at":   datetime.now(timezone.utc).isoformat(),
        "source":       "manual",
    }
    await bills_collection.insert_one(new_bill)
    return {"success": True, "id": bill_id, **{k: v for k, v in new_bill.items() if k != "_id"}}


# ─────────────────────────────
# PATCH /bills/{bill_id}  — update in MongoDB
# ─────────────────────────────
@app.patch("/bills/{bill_id}")
async def patch_bill(bill_id: str, request: Request, user: dict = Depends(get_current_user)):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body.")

    updates = {
        "vendor_name":  body.get("vendor_name"),
        "bill_date":    body.get("bill_date"),
        "items":        body.get("items") or [],
        "tax":          body.get("tax") or 0,
        "cgst_percentage": body.get("cgst_percentage"),
        "sgst_percentage": body.get("sgst_percentage"),
        "gstin":        body.get("gstin"),
        "total_amount": body.get("total_amount") or 0,
    }

    result = await bills_collection.find_one_and_update(
        {"_id": bill_id, "user_id": user["uid"]},
        {"$set": updates},
        return_document=True,
    )

    if result is None:
        raise HTTPException(status_code=404, detail=f"Bill '{bill_id}' not found.")

    return {"success": True, "updated": serialize_bill(result)}


# ─────────────────────────────
# POST /upload-bill  — OCR + save to MongoDB
# ─────────────────────────────
@app.post("/upload-bill")
async def upload_bill(file: UploadFile = File(...), user: dict = Depends(get_current_user)):
    info = await get_user_subscription_info(user)
    if not info["can_scan"]:
        raise HTTPException(status_code=403, detail="Free limit reached. Please upgrade to Pro.")

    # 1. Validate file type
    content_type = file.content_type or ""
    mime_type = ALLOWED_MIME_TYPES.get(content_type.lower())
    if not mime_type:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{content_type}'. Please upload a JPEG, PNG, or WEBP image."
        )

    # 2. Read + validate size
    image_bytes = await file.read()
    size_mb = len(image_bytes) / (1024 * 1024)
    if size_mb > MAX_FILE_SIZE_MB:
        raise HTTPException(
            status_code=400,
            detail=f"File too large ({size_mb:.1f} MB). Maximum allowed is {MAX_FILE_SIZE_MB} MB."
        )

    # 3. Base64 encode
    base64_image = base64.b64encode(image_bytes).decode("utf-8")

    # 4. Prompt
    prompt = """Extract structured bill data from this image.

Return ONLY valid JSON with no explanation, no markdown fences, and no extra text:

{
  "vendor_name": "",
  "gstin": "",
  "date": "",
  "items": [
    {
      "item_name": "",
      "quantity": 1,
      "price": 0,
      "description": ""
    }
  ],
  "tax": 0,
  "cgst_percentage": null,
  "sgst_percentage": null,
  "total_amount": 0
}

Rules:
- carefully go through whole image of bill and extract data, sgst and cgst are tax types in india
- Detect the GSTIN number (which must be a 15-digit alphanumeric identifier)
- Identify and extract the CGST and SGST percentages
- Return ONLY the JSON object above
- Use null for any field you cannot find
- date format: DD-MM-YYYY if possible
- price and total_amount must be numbers, not strings
- description: capture any special item notes printed on the bill such as warranty period, serial number, model number, SKU, expiry date, or any other item-specific annotation. Leave as empty string if none.
"""

    # 5. Call SambaNova
    try:
        response = client_samba.chat.completions.create(
            model=MODEL,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime_type};base64,{base64_image}"
                            }
                        }
                    ]
                }
            ],
            temperature=0.1,
            top_p=0.1
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"SambaNova API error: {str(e)}")

    # 6. Parse response
    raw_content = response.choices[0].message.content.strip()
    try:
        bill_data = extract_json(raw_content)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "Could not parse bill data from image. Try a clearer photo.",
                "raw_output": raw_content[:300]
            }
        )

    # 7. Save to MongoDB Atlas
    bill_id = str(uuid.uuid4())
    new_bill = {
        "_id":          bill_id,
        "user_id":      user["uid"],
        "vendor_name":  bill_data.get("vendor_name"),
        "bill_date":    bill_data.get("date"),
        "items":        bill_data.get("items") or [],
        "tax":          bill_data.get("tax") or 0,
        "cgst_percentage": bill_data.get("cgst_percentage"),
        "sgst_percentage": bill_data.get("sgst_percentage"),
        "gstin":        bill_data.get("gstin"),
        "total_amount": bill_data.get("total_amount") or 0,
        "created_at":   datetime.now(timezone.utc).isoformat(),
    }
    await bills_collection.insert_one(new_bill)

    # 8. Return extracted data (with the generated id so the frontend can reference it)
    return {**bill_data, "id": bill_id}


# ─────────────────────────────
# DELETE /bills/{bill_id}
# ─────────────────────────────
@app.delete("/bills/{bill_id}")
async def delete_bill(bill_id: str, user: dict = Depends(get_current_user)):
    result = await bills_collection.delete_one({"_id": bill_id, "user_id": user["uid"]})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail=f"Bill '{bill_id}' not found.")
    return {"success": True}
from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse

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

# ─────────────────────────────
load_dotenv()   # reads .env into os.environ

# ─────────────────────────────
# Config
# ─────────────────────────────
SAMBANOVA_API_KEY  = os.environ.get("SAMBANOVA_API_KEY")
SAMBANOVA_BASE_URL = os.environ.get("SAMBANOVA_BASE_URL", "https://api.sambanova.ai/v1")
MONGO_URI          = os.environ.get("MONGO_URI", "")
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL", "")  # auto-set by Render

# ─────────────────────────────
# SambaNova client
# ─────────────────────────────
client_samba = SambaNova(
    api_key=SAMBANOVA_API_KEY,
    base_url=SAMBANOVA_BASE_URL,
)

MODEL = "gemma-3-12b-it"

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
    global mongo_client, bills_collection

    if not MONGO_URI:
        raise RuntimeError(
            "MONGO_URI environment variable is not set. "
            "Add it to your .env file or cloud environment."
        )

    mongo_client = AsyncIOMotorClient(MONGO_URI)
    db = mongo_client["billmate"]
    bills_collection = db["bills"]

    # Ensure index on created_at for efficient sorting
    await bills_collection.create_index([("created_at", DESCENDING)])

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
# GET /bills  — read from MongoDB
# ─────────────────────────────
@app.get("/bills")
async def get_bills():
    """Return all bills from MongoDB Atlas, newest first."""
    cursor = bills_collection.find().sort("created_at", DESCENDING)
    bills = [serialize_bill(doc) async for doc in cursor]
    return bills


# ─────────────────────────────
# PATCH /bills/{bill_id}  — update in MongoDB
# ─────────────────────────────
@app.patch("/bills/{bill_id}")
async def patch_bill(bill_id: str, request: Request):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body.")

    updates = {
        "vendor_name":  body.get("vendor_name"),
        "bill_date":    body.get("bill_date"),
        "items":        body.get("items") or [],
        "tax":          body.get("tax") or 0,
        "total_amount": body.get("total_amount") or 0,
    }

    result = await bills_collection.find_one_and_update(
        {"_id": bill_id},
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
async def upload_bill(file: UploadFile = File(...)):

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
  "date": "",
  "items": [
    {
      "item_name": "",
      "quantity": 1,
      "price": 0
    }
  ],
  "tax": 0,
  "total_amount": 0
}

Rules:
- Return ONLY the JSON object above
- Use null for any field you cannot find
- date format: DD-MM-YYYY if possible
- price and total_amount must be numbers, not strings
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
        "vendor_name":  bill_data.get("vendor_name"),
        "bill_date":    bill_data.get("date"),
        "items":        bill_data.get("items") or [],
        "tax":          bill_data.get("tax") or 0,
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
async def delete_bill(bill_id: str):
    result = await bills_collection.delete_one({"_id": bill_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail=f"Bill '{bill_id}' not found.")
    return {"success": True}
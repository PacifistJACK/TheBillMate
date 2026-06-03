from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.templating import Jinja2Templates

from sambanova import SambaNova
from dotenv import load_dotenv

import os
import base64
import json
import re
import uuid
from datetime import datetime, timezone

# ─────────────────────────────
load_dotenv()   # reads .env into os.environ

# ─────────────────────────────
# Config
# ─────────────────────────────
SAMBANOVA_API_KEY = os.environ.get("SAMBANOVA_API_KEY")

# ─────────────────────────────
# SambaNova client
# ─────────────────────────────
client = SambaNova(
    api_key=SAMBANOVA_API_KEY,
    base_url="https://api.sambanova.ai/v1",
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
# Local JSON file storage
# ─────────────────────────────
BILLS_JSON_PATH = os.path.join(os.path.dirname(__file__), "data", "bills.json")

def load_bills() -> list:
    """Load all bills from bills.json. Returns [] if missing or corrupt."""
    try:
        if os.path.exists(BILLS_JSON_PATH):
            with open(BILLS_JSON_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
    except Exception as e:
        print(f"[WARN] Could not read bills.json: {e}")
    return []

def save_bills(bills: list):
    """Overwrite bills.json with the provided list."""
    try:
        os.makedirs(os.path.dirname(BILLS_JSON_PATH), exist_ok=True)
        with open(BILLS_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(bills, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[ERROR] Could not write bills.json: {e}")
        raise

def append_bill(bill: dict):
    """Add a single bill to bills.json."""
    bills = load_bills()
    bills.append(bill)
    save_bills(bills)

def update_bill(bill_id: str, updates: dict):
    """Update an existing bill by id in bills.json. Returns the updated bill or None."""
    bills = load_bills()
    for i, b in enumerate(bills):
        if str(b.get("id")) == str(bill_id):
            bills[i] = {**b, **updates}
            save_bills(bills)
            return bills[i]
    return None

# ─────────────────────────────
# FastAPI app
# ─────────────────────────────
app = FastAPI(title="Bill Mate API", version="2.0.0")

templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))

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
def root(request: Request):
    """Serve the main dashboard directly — no login required."""
    return templates.TemplateResponse("index.html", {"request": request})


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
def health():
    return {"status": "ok"}


# ─────────────────────────────
# GET /bills  — read from JSON file
# ─────────────────────────────
@app.get("/bills")
def get_bills():
    """Return all bills from bills.json, newest first."""
    bills = load_bills()
    bills_sorted = sorted(bills, key=lambda b: b.get("created_at", ""), reverse=True)
    return bills_sorted


# ─────────────────────────────
# PATCH /bills/{bill_id}  — update in JSON file
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

    updated = update_bill(bill_id, updates)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Bill '{bill_id}' not found.")

    return {"success": True, "updated": updated}


# ─────────────────────────────
# POST /upload-bill  — OCR + save to JSON
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
- date format: YYYY-MM-DD if possible
- price and total_amount must be numbers, not strings
"""

    # 5. Call SambaNova
    try:
        response = client.chat.completions.create(
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

    # 7. Save to bills.json
    new_bill = {
        "id":           str(uuid.uuid4()),
        "vendor_name":  bill_data.get("vendor_name"),
        "bill_date":    bill_data.get("date"),
        "items":        bill_data.get("items") or [],
        "tax":          bill_data.get("tax") or 0,
        "total_amount": bill_data.get("total_amount") or 0,
        "created_at":   datetime.now(timezone.utc).isoformat(),
    }
    append_bill(new_bill)

    # 8. Return extracted data (with the generated id so the frontend can reference it)
    return {**bill_data, "id": new_bill["id"]}


# ─────────────────────────────
# DELETE /bills/{bill_id}
# ─────────────────────────────
@app.delete("/bills/{bill_id}")
def delete_bill(bill_id: str):
    bills = load_bills()
    new_bills = [b for b in bills if str(b.get("id")) != str(bill_id)]
    if len(new_bills) == len(bills):
        raise HTTPException(status_code=404, detail=f"Bill '{bill_id}' not found.")
    save_bills(new_bills)
    return {"success": True}
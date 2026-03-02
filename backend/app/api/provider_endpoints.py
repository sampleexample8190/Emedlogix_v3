"""
Provider API Endpoints
- GET  /api/provider/status                    → get document statuses for logged-in provider
- POST /api/provider/reupload-request          → request re-upload permission from admin
- GET  /api/provider/notifications             → fetch provider's notifications
- PUT  /api/provider/notifications/{id}/read   → mark a notification as read
- PUT  /api/provider/notifications/read-all    → mark all provider notifications as read
"""

import os
import jwt
from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel
from typing import Optional, Dict, Any
from dotenv import load_dotenv
from pathlib import Path
import psycopg2
import psycopg2.extras

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env", override=True)

router = APIRouter(prefix="/provider", tags=["Provider"])

SCHEMA     = "ocr_document"
JWT_SECRET = os.getenv("JWT_SECRET")
if not JWT_SECRET:
    raise RuntimeError("JWT_SECRET is not set in .env")
JWT_ALG    = "HS256"

DOCUMENT_TABLES = [
    "tax_id",
    "state_medical_license",
    "malpractice_insurance",
    "dea_certificate",
    "board_certification",
]

DOCUMENT_LABELS = {
    "tax_id": "Tax ID / IRS Letter",
    "state_medical_license": "State Medical License",
    "malpractice_insurance": "Malpractice Insurance",
    "dea_certificate": "Federal DEA Certificate",
    "board_certification": "Board Certification",
}

DB_CONFIG = {
    "host":     os.getenv("PG_HOST", "localhost"),
    "port":     int(os.getenv("PG_PORT", "5432")),
    "database": os.getenv("PG_DATABASE", "postgres"),
    "user":     os.getenv("PG_USER", "postgres"),
    "password": os.getenv("PG_PASSWORD", ""),
}

ADMIN_RECIPIENT_ID = "admin"


# ─── Helpers ──────────────────────────────────────────────────────────────────

def get_conn():
    return psycopg2.connect(**DB_CONFIG)


def _require_provider(authorization: Optional[str]) -> str:
    """Validate Bearer token and return doctor_id."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header.")
    token = authorization.split(" ", 1)[1]
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
        role = payload.get("role", "provider")
        doctor_id = payload.get("doctor_id") or payload.get("sub")
        if not doctor_id:
            raise HTTPException(status_code=401, detail="Invalid token: missing doctor_id.")
        return str(doctor_id)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token.")


# ─── Models ───────────────────────────────────────────────────────────────────

class ReuUploadRequestBody(BaseModel):
    document_type: str


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/status")
async def get_provider_status(
    authorization: Optional[str] = Header(None)
):
    """Get document statuses for the logged-in provider."""
    doctor_id = _require_provider(authorization)

    from app.services.db import get_document_statuses
    statuses = get_document_statuses(doctor_id)

    # Enrich with labels
    result = {}
    for doc_type in DOCUMENT_TABLES:
        if doc_type in statuses:
            result[doc_type] = {
                **statuses[doc_type],
                "label": DOCUMENT_LABELS.get(doc_type, doc_type)
            }
        else:
            result[doc_type] = {
                "document_type": doc_type,
                "status": "not_uploaded",
                "label": DOCUMENT_LABELS.get(doc_type, doc_type),
                "rejection_reason": None,
                "rejection_fields": None
            }
    return {"status": "success", "document_statuses": result}


@router.post("/reupload-request")
async def request_reupload(
    req: ReuUploadRequestBody,
    authorization: Optional[str] = Header(None)
):
    """Provider requests permission to re-upload an approved document."""
    doctor_id = _require_provider(authorization)

    if req.document_type not in DOCUMENT_TABLES:
        raise HTTPException(status_code=400, detail=f"Invalid document type: {req.document_type}")

    from app.services.db import get_status_for_doc, upsert_document_status, create_notification

    current = get_status_for_doc(doctor_id, req.document_type)

    # Allow re-upload request only if document is approved or rejected
    if not current:
        raise HTTPException(
            status_code=400,
            detail="No document found for this type. Please upload the document first."
        )

    allowed_statuses = ("approved", "rejected")
    if current.get("status") not in allowed_statuses:
        status_msg = current.get("status", "unknown")
        if status_msg == "reupload_requested":
            raise HTTPException(
                status_code=400,
                detail="You have already submitted a re-upload request. Please wait for admin response."
            )
        if status_msg == "reupload_approved":
            raise HTTPException(
                status_code=400,
                detail="Re-upload already approved. Please upload your new document."
            )
        raise HTTPException(
            status_code=400,
            detail=f"Cannot request re-upload while document status is '{status_msg}'."
        )

    label = DOCUMENT_LABELS.get(req.document_type, req.document_type)

    # Get provider name from DB
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute(f"SELECT name FROM {SCHEMA}.doctors WHERE doctor_id = %s", (doctor_id,))
        row = cur.fetchone()
        provider_name = row["name"] if row else "Provider"
    finally:
        cur.close()
        conn.close()

    upsert_document_status(
        doctor_id=doctor_id,
        document_type=req.document_type,
        status="reupload_requested"
    )

    # Notify admin
    create_notification(
        role="admin",
        recipient_id=ADMIN_RECIPIENT_ID,
        notification_type="reupload_requested",
        message=f"🔄 Provider {provider_name} has requested permission to re-upload their {label}.",
        metadata={
            "doctor_id": doctor_id,
            "provider_name": provider_name,
            "document_type": req.document_type,
            "label": label
        }
    )

    # Confirm to provider
    create_notification(
        role="provider",
        recipient_id=doctor_id,
        notification_type="reupload_request_sent",
        message=f"🔄 Your re-upload request for {label} has been sent to the administrator.",
        metadata={"document_type": req.document_type, "label": label}
    )

    return {
        "status": "success",
        "message": f"Re-upload request submitted for {label}. You will be notified once admin reviews it.",
        "document_type": req.document_type,
        "new_status": "reupload_requested"
    }


@router.get("/notifications")
async def get_provider_notifications(
    unread: bool = False,
    authorization: Optional[str] = Header(None)
):
    """Fetch notifications for the logged-in provider."""
    doctor_id = _require_provider(authorization)
    from app.services.db import get_notifications, get_unread_notification_count

    notifications = get_notifications("provider", doctor_id, unread_only=unread)
    unread_count = get_unread_notification_count("provider", doctor_id)
    return {
        "status": "success",
        "unread_count": unread_count,
        "notifications": notifications
    }


@router.put("/notifications/{notification_id}/read")
async def mark_provider_notification_read(
    notification_id: int,
    authorization: Optional[str] = Header(None)
):
    """Mark a specific provider notification as read."""
    doctor_id = _require_provider(authorization)
    from app.services.db import mark_notification_read

    updated = mark_notification_read(notification_id)
    if not updated:
        raise HTTPException(status_code=404, detail="Notification not found.")
    return {"status": "success", "message": "Notification marked as read."}


@router.put("/notifications/read-all")
async def mark_all_notifications_read(
    authorization: Optional[str] = Header(None)
):
    """Mark all notifications for the provider as read."""
    doctor_id = _require_provider(authorization)
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            f"UPDATE {SCHEMA}.notifications SET is_read = TRUE WHERE recipient_role = 'provider' AND recipient_id = %s",
            (doctor_id,)
        )
        conn.commit()
        return {"status": "success", "message": "All notifications marked as read."}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()


@router.delete("/notifications/clear-all")
async def clear_all_provider_notifications(
    authorization: Optional[str] = Header(None)
):
    """Delete all notifications for the provider."""
    doctor_id = _require_provider(authorization)
    from app.services.db import delete_all_notifications
    try:
        delete_all_notifications("provider", doctor_id)
        return {"status": "success", "message": "All notifications cleared."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/profile")
async def get_my_profile(
    authorization: Optional[str] = Header(None)
):
    """Build a merged credentialing profile for the logged-in provider."""
    doctor_id = _require_provider(authorization)
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    try:
        docs: Dict[str, Any] = {}
        for table in DOCUMENT_TABLES:
            cur.execute(
                f"SELECT * FROM {SCHEMA}.{table} WHERE doctor_id = %s ORDER BY uploaded_at DESC LIMIT 1",
                (doctor_id,)
            )
            row = cur.fetchone()
            docs[table] = dict(row) if row else None

        # Fetch document statuses
        cur.execute(
            f"SELECT * FROM {SCHEMA}.document_status WHERE doctor_id = %s",
            (doctor_id,)
        )
        status_rows = cur.fetchall()
        doc_statuses = {}
        for sr in status_rows:
            s = dict(sr)
            s["doctor_id"] = str(s["doctor_id"])
            for ts_field in ["reviewed_at", "created_at", "updated_at"]:
                if s.get(ts_field):
                    s[ts_field] = s[ts_field].isoformat()
            doc_statuses[s["document_type"]] = s

        # --- Check Verification Cache (CMS/SAM Data) ---
        cur.execute(f"SELECT * FROM {SCHEMA}.verification_cache WHERE doctor_id = %s", (doctor_id,))
        cache_row = cur.fetchone()
        
        # New: Fetch doctor basic info
        cur.execute(f"SELECT name, email FROM {SCHEMA}.doctors WHERE doctor_id = %s", (doctor_id,))
        doctor_basic = cur.fetchone()
        doctor_name = doctor_basic["name"] if doctor_basic else "Provider"

    finally:
        cur.close()
        conn.close()

    tax   = docs.get("tax_id")
    lic   = docs.get("state_medical_license")
    mal   = docs.get("malpractice_insurance")
    dea   = docs.get("dea_certificate")
    board = docs.get("board_certification")

    def first_val(*values):
        for v in values:
            if v and str(v).strip() and str(v).lower() != 'none':
                return str(v).strip()
        return ""

    def split_name(full_name: str):
        parts = (full_name or "").strip().split()
        if len(parts) >= 2:
            return parts[0], parts[-1]
        return full_name or "", ""

    raw_first = first_val(
        lic.get("first_name") if lic else None,
        dea.get("first_name") if dea else None,
        tax.get("first_name") if tax else None,
    )
    raw_last = first_val(
        lic.get("last_name") if lic else None,
        dea.get("last_name") if dea else None,
        tax.get("last_name") if tax else None,
    )

    if not raw_first or not raw_last:
        full = first_val(
            lic.get("provider_name") if lic else None,
            dea.get("provider_name") if dea else None,
            tax.get("provider_name") if tax else None,
            mal.get("provider_name") if mal else None,
            board.get("provider_name") if board else None,
        )
        if full:
            raw_first, raw_last = split_name(full)

    # Prepare Verification Results from Cache
    cms_profile = None
    compliance = None
    cms_status = "not_matched"
    
    if cache_row:
        import json
        cms_status = cache_row["cms_status"]
        try:
            # psycopg2 RealDictCursor may already parse jsonb as dict
            c_prof = cache_row.get("cms_profile")
            cms_profile = c_prof if isinstance(c_prof, dict) else (json.loads(c_prof) if c_prof else None)
            
            c_comp = cache_row.get("compliance")
            compliance = c_comp if isinstance(c_comp, dict) else (json.loads(c_comp) if c_comp else None)
        except Exception as e:
            print(f"⚠️ Error parsing cache JSON: {e}")

    # --- Live Fetch: ONLY on a true cache miss (no row at all) ---
    # If cms_status is 'not_matched' or 'pending' it means we already tried
    # and the CMS couldn't find the provider. Don't re-run the expensive
    # network call on every page load — just use what we have from the cache.
    if not cache_row:
        print(f"🔍 [Provider API] No cache for {doctor_id[:8]}. Running Matching Engine...")
        try:
            from app.models.provider import OCRPayload
            from app.services.matching import MatchingEngine
            import asyncio

            ocr_payload = OCRPayload(
                npi="",
                license_number=first_val(lic.get("license_number") if lic else None),
                first_name=raw_first,
                last_name=raw_last,
                state=first_val(
                    lic.get("address_state") if lic else None,
                    dea.get("address_state") if dea else None,
                    tax.get("address_state") if tax else None,
                ),
                city=first_val(lic.get("address_city") if lic else None),
                zip_code=first_val(lic.get("address_zip")  if lic else None),
                tax_id=first_val(tax.get("ein")           if tax else None),
                dea_number=first_val(dea.get("dea_number")    if dea else None),
                malpractice_policy=first_val(mal.get("policy_number") if mal else None),
                board_certification=first_val(board.get("certification_type") if board else None),
                training_certifications=[]
            )
            engine = MatchingEngine()
            result = await engine.process_ocr_data(ocr_payload)

            if result.status == "success" and result.provider_profile:
                cms_profile = result.provider_profile.dict() if hasattr(result.provider_profile, "dict") else vars(result.provider_profile)
                compliance  = result.compliance.dict() if result.compliance and hasattr(result.compliance, "dict") else (vars(result.compliance) if result.compliance else None)
                cms_status  = "verified"

            # Update cache in the background
            from app.services.db import update_verification_cache
            asyncio.create_task(update_verification_cache(doctor_id))
        except Exception as ex:
            print(f"⚠️ Live Fetch Failed: {ex}")

    # Prepare detailed OCR dictionary for AI and Frontend Form Filling
    ocr_details = {
        "first_name":           raw_first,
        "last_name":            raw_last,
        "full_name":            doctor_name,
        "npi":                  cms_profile.get("npi") if cms_profile else None,
        "taxonomy_code":        cms_profile.get("taxonomy_code") if cms_profile else None,
        "specialty":            first_val(board.get("specialty_code") if board else None, cms_profile.get("taxonomy_description") if cms_profile else None),
        # State Medical License
        "license_number":       lic.get("license_number") if lic else None,
        "license_status":       lic.get("license_status") if lic else None,
        "lic_issue_date":       lic.get("issue_date") if lic else None,
        "lic_expiry_date":      lic.get("expiration_date") if lic else None,
        "state":                lic.get("address_state") if lic else None,
        "city":                 lic.get("address_city") if lic else None,
        "zip_code":             lic.get("address_zip") if lic else None,
        "address_line_1":       lic.get("address_street") if lic else None,
        "gender":               lic.get("gender") if lic else None,
        "date_of_birth":        lic.get("date_of_birth") if lic else None,
        "contact_phone":        lic.get("contact_phone") if lic else None,
        # Tax ID
        "tax_id":               tax.get("ein") if tax else None,
        "tax_id_ein":           tax.get("ein") if tax else None,
        "business_name":        tax.get("business_name") if tax else None,
        # DEA Certificate
        "dea_number":           dea.get("dea_number") if dea else None,
        "dea_issue_date":       dea.get("issue_date") if dea else None,
        "dea_expiry_date":      dea.get("expiration_date") if dea else None,
        "dea_schedules":        dea.get("schedules") if dea else None,
        # Malpractice Insurance
        "malpractice_policy":   mal.get("policy_number") if mal else None,
        "policy_number":        mal.get("policy_number") if mal else None,
        "insurer_name":         mal.get("insurer_name") if mal else None,
        "mal_effective_date":   mal.get("effective_date") if mal else None,
        "mal_expiry_date":      mal.get("expiration_date") if mal else None,
        "coverage_per_claim":   mal.get("coverage_per_claim") if mal else None,
        # Board Certification
        "board_certification":  board.get("certification_type") if board else None,
        "board_status":         board.get("status") if board else None,
        "certifying_board":     board.get("certifying_board") if board else None,
        "board_specialty":      board.get("specialty_code") if board else None,
        "board_issue_date":     board.get("issue_date") if board else None,
        "board_expiry_date":    board.get("expiration_date") if board else None,
    }

    return {
        "status": "success",
        "profile": {
            **ocr_details,
            "cms_status": cms_status,
            "verification_results": {
                "cms_profile": cms_profile,
                "compliance": compliance,
                "cms_status": cms_status
            }
        },
        "document_statuses": doc_statuses
    }

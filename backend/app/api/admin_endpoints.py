"""
Admin API Endpoints
- POST /api/admin/login                                    → admin authentication
- GET  /api/admin/providers                                → list all providers with status summary
- GET  /api/admin/providers/{doctor_id}                    → full detail for one provider
- GET  /api/admin/providers/{doctor_id}/profile            → CMS profile + OCR details
- PUT  /api/admin/providers/{doctor_id}/{table}/{record_id} → edit a field in any doc table
- POST /api/admin/providers/{doctor_id}/approve            → approve document(s)
- POST /api/admin/providers/{doctor_id}/reject             → reject a document with reason + field mapping
- POST /api/admin/providers/{doctor_id}/reupload-decision  → approve/reject provider's reupload request
- GET  /api/admin/notifications                            → fetch admin notifications
- PUT  /api/admin/notifications/{id}/read                  → mark notification as read
"""

import os
import jwt
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel
from typing import Optional, Any, Dict, List
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from pathlib import Path
import json
import time

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env", override=True)

router = APIRouter(prefix="/admin", tags=["Admin"])

# ─── Config ───────────────────────────────────────────────────────────────────
SCHEMA      = "ocr_document"
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL")
ADMIN_PASS  = os.getenv("ADMIN_PASSWORD")
if not ADMIN_EMAIL or not ADMIN_PASS:
    raise RuntimeError("ADMIN_EMAIL and ADMIN_PASSWORD must be set in .env")
JWT_SECRET  = os.getenv("JWT_SECRET")
if not JWT_SECRET:
    raise RuntimeError("JWT_SECRET is not set in .env")
JWT_ALG     = "HS256"
ADMIN_RECIPIENT_ID = "admin"  # fixed recipient_id for admin notifications

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


# ─── Helpers ──────────────────────────────────────────────────────────────────

def get_conn():
    return psycopg2.connect(**DB_CONFIG)


def _require_admin(authorization: Optional[str]):
    """Validate Bearer token and confirm it's an admin token."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header.")
    token = authorization.split(" ", 1)[1]
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
        if payload.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Admin access required.")
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token.")


def _make_admin_token() -> str:
    payload = {
        "role": "admin",
        "email": ADMIN_EMAIL,
        "exp": datetime.now(timezone.utc) + timedelta(hours=8),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


def _get_doctor_name(conn, doctor_id: str) -> str:
    """Helper to get doctor name for notification messages."""
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute(f"SELECT name FROM {SCHEMA}.doctors WHERE doctor_id = %s", (doctor_id,))
        row = cur.fetchone()
        return row["name"] if row else "Provider"
    finally:
        cur.close()


# ─── Models ───────────────────────────────────────────────────────────────────

class AdminLoginRequest(BaseModel):
    email: str
    password: str


class UpdateFieldRequest(BaseModel):
    field: str
    value: Any


class ApproveRequest(BaseModel):
    document_types: List[str]  # list of doc types to approve, e.g. ["tax_id", "dea_certificate"]


class RejectRequest(BaseModel):
    document_type: str
    rejection_reason: str
    rejection_fields: Optional[List[str]] = []  # optional field names admin flags


class ReuUploadDecisionRequest(BaseModel):
    document_type: str
    approved: bool
    reason: Optional[str] = None


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/login")
async def admin_login(req: AdminLoginRequest):
    """Admin login with hardcoded credentials (no DB change)."""
    if req.email.strip().lower() != ADMIN_EMAIL.lower() or req.password != ADMIN_PASS:
        raise HTTPException(status_code=401, detail="Invalid admin credentials.")
    token = _make_admin_token()
    return {"status": "success", "token": token, "email": ADMIN_EMAIL}


@router.get("/providers")
async def list_providers(authorization: Optional[str] = Header(None)):
    """Return all registered providers (from doctors table) with document status summary."""
    _require_admin(authorization)
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute(
            f"SELECT doctor_id, name, email, created_at FROM {SCHEMA}.doctors ORDER BY created_at DESC"
        )
        rows = cur.fetchall()
        providers = []
        for row in rows:
            d = dict(row)
            d["doctor_id"] = str(d["doctor_id"])
            d["created_at"] = d["created_at"].isoformat() if d["created_at"] else None
            
            # 1. Fetch document status map
            cur.execute(
                f"SELECT document_type, status FROM {SCHEMA}.document_status WHERE doctor_id = %s",
                (d["doctor_id"],)
            )
            status_rows = cur.fetchall()
            status_map = {r["document_type"]: r["status"] for r in status_rows}
            d["document_statuses"] = status_map

            # 2. Fetch NPI from verification_cache
            cur.execute(
                f"SELECT cms_profile FROM {SCHEMA}.verification_cache WHERE doctor_id = %s",
                (d["doctor_id"],)
            )
            cache_row = cur.fetchone()
            d["npi"] = "Pending"
            if cache_row and cache_row["cms_profile"]:
                try:
                    profile_data = cache_row["cms_profile"]
                    if not isinstance(profile_data, dict):
                        import json
                        profile_data = json.loads(profile_data)
                    d["npi"] = profile_data.get("npi", "Pending")
                except: pass

            # 3. Aggregate counts (optimized count from doc_statuses and counts)
            statuses = list(status_map.values())
            d["approved_count"] = statuses.count("approved")
            d["rejected_count"] = statuses.count("rejected")
            
            # Document counts (still a bit heavy but improved)
            total_docs = 0
            for table in DOCUMENT_TABLES:
                cur.execute(f"SELECT COUNT(*) FROM {SCHEMA}.{table} WHERE doctor_id = %s", (d["doctor_id"],))
                total_docs += cur.fetchone()["count"]
            d["total_documents"] = total_docs
            
            d["pending_count"] = total_docs - (d["approved_count"] + d["rejected_count"])
            d["has_reupload_request"] = any(s == "reupload_requested" for s in statuses)

            providers.append(d)
        return {"status": "success", "count": len(providers), "providers": providers}
    finally:
        cur.close()
        conn.close()


@router.get("/providers/{doctor_id}")
async def get_provider_detail(
    doctor_id: str,
    authorization: Optional[str] = Header(None)
):
    """Return full details (all doc tables) for a single provider with document statuses."""
    _require_admin(authorization)
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute(
            f"SELECT doctor_id, name, email, created_at FROM {SCHEMA}.doctors WHERE doctor_id = %s",
            (doctor_id,)
        )
        doctor_row = cur.fetchone()
        if not doctor_row:
            raise HTTPException(status_code=404, detail="Provider not found.")
        doctor = dict(doctor_row)
        doctor["doctor_id"] = str(doctor["doctor_id"])
        doctor["created_at"] = doctor["created_at"].isoformat() if doctor["created_at"] else None

        # All documents
        documents = {}
        for table in DOCUMENT_TABLES:
            cur.execute(
                f"SELECT * FROM {SCHEMA}.{table} WHERE doctor_id = %s ORDER BY uploaded_at DESC",
                (doctor_id,)
            )
            rows = cur.fetchall()
            documents[table] = []
            for r in rows:
                rec = dict(r)
                rec["doctor_id"] = str(rec["doctor_id"])
                if rec.get("uploaded_at"):
                    rec["uploaded_at"] = rec["uploaded_at"].isoformat()
                documents[table].append(rec)

        # Document statuses
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

        return {
            "status": "success",
            "doctor": doctor,
            "documents": documents,
            "document_statuses": doc_statuses
        }
    finally:
        cur.close()
        conn.close()


@router.get("/providers/{doctor_id}/profile")
async def get_provider_profile(
    doctor_id: str,
    authorization: Optional[str] = Header(None)
):
    """Build a merged credentialing profile + document statuses."""
    _require_admin(authorization)
    # --- Optimized Document Fetching (Single Connection) ---
    start_all = time.time()
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    docs = {}
    
    try:
        t0 = time.time()
        for table_name in DOCUMENT_TABLES:
            cur.execute(
                f"SELECT * FROM {SCHEMA}.{table_name} WHERE doctor_id = %s ORDER BY uploaded_at DESC LIMIT 1",
                (doctor_id,)
            )
            row = cur.fetchone()
            docs[table_name] = dict(row) if row else None
        t1 = time.time()
        print(f"⏱️ DB: Docs fetch took {round((t1-t0)*1000, 2)}ms")

        cur.execute(f"SELECT * FROM {SCHEMA}.document_status WHERE doctor_id = %s", (doctor_id,))
        status_rows = cur.fetchall()
        doc_statuses = {}
        for sr in status_rows:
            s = dict(sr)
            s["doctor_id"] = str(s["doctor_id"])
            for ts_field in ["reviewed_at", "created_at", "updated_at"]:
                if s.get(ts_field): s[ts_field] = s[ts_field].isoformat()
            doc_statuses[s["document_type"]] = s

        # --- Check Verification Cache ---
        cur.execute(f"SELECT * FROM {SCHEMA}.verification_cache WHERE doctor_id = %s", (doctor_id,))
        cache_row = cur.fetchone()
        print(f"⏱️ DB: Total metadata fetch took {round((time.time()-t1)*1000, 2)}ms")
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
        if len(parts) >= 2: return parts[0], parts[-1]
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
        if full: raw_first, raw_last = split_name(full)

    license_number = first_val(lic.get("license_number") if lic else None)
    state          = first_val(
        lic.get("address_state") if lic else None,
        dea.get("address_state") if dea else None,
        tax.get("address_state") if tax else None,
    )
    city           = first_val(lic.get("address_city") if lic else None)
    zip_code       = first_val(lic.get("address_zip")  if lic else None)
    dea_number     = first_val(dea.get("dea_number")    if dea else None)
    tax_id_val     = first_val(tax.get("ein")            if tax else None)
    mal_policy     = first_val(mal.get("policy_number")  if mal else None)
    board_cert     = first_val(board.get("certification_type") if board else None)

    cms_profile = None
    compliance  = None
    cms_status  = "not_matched"

    if cache_row:
        cms_status = cache_row["cms_status"]
        try:
            # psycopg2 RealDictCursor may already parse jsonb as dict
            c_prof = cache_row.get("cms_profile")
            cms_profile = c_prof if isinstance(c_prof, dict) else (json.loads(c_prof) if c_prof else None)
            
            c_comp = cache_row.get("compliance")
            compliance = c_comp if isinstance(c_comp, dict) else (json.loads(c_comp) if c_comp else None)
        except Exception as e:
            print(f"⚠️ Error parsing cache JSON: {e}")
        print(f"⚡ Cache Hit! cms_status={cms_status}")
    
    # If cache is missing or explicitly marked not_matched/error, try re-running
    if not cache_row or cms_status in ("not_matched", "pending"):
        print("🔍 Cache Miss or Stale. Running Matching Engine...")
        try:
            from app.models.provider import OCRPayload
            from app.services.matching import MatchingEngine
            ocr_payload = OCRPayload(
                npi="", license_number=license_number, first_name=raw_first, last_name=raw_last,
                state=state, city=city, zip_code=zip_code, tax_id=tax_id_val, dea_number=dea_number,
                malpractice_policy=mal_policy, board_certification=board_cert, training_certifications=[]
            )
            engine = MatchingEngine()
            result = await engine.process_ocr_data(ocr_payload)
            if result.status == "success" and result.provider_profile:
                cms_profile = result.provider_profile.dict() if hasattr(result.provider_profile, "dict") else vars(result.provider_profile)
                compliance  = result.compliance.dict() if result.compliance and hasattr(result.compliance, "dict") else (vars(result.compliance) if result.compliance else None)
                cms_status  = "verified"
            elif "error" in result.status: cms_status = result.status
            
            # Update cache in the background
            from app.services.db import update_verification_cache
            asyncio.create_task(update_verification_cache(doctor_id))
        except Exception as ex:
            cms_status = f"error: {ex}"
    
    print(f"🏁 Profile assembly complete in {round((time.time()-start_all)*1000, 2)}ms")

    ocr_details = {
        "first_name":          raw_first,
        "last_name":           raw_last,
        "provider_name":       lic.get("provider_name") if lic else (dea.get("provider_name") if dea else (tax.get("provider_name") if tax else None)),
        "tax_id_ein":          tax.get("ein")                   if tax   else None,
        "business_name":       tax.get("business_name")         if tax   else None,
        "license_number":      lic.get("license_number")        if lic   else None,
        "license_status":      lic.get("license_status")        if lic   else None,
        "date_of_birth":       lic.get("date_of_birth")         if lic   else None,
        "gender":              lic.get("gender")                 if lic   else None,
        "lic_issue_date":      lic.get("issue_date")             if lic   else None,
        "lic_expiry_date":     lic.get("expiration_date")       if lic   else None,
        "dea_number":          dea.get("dea_number")             if dea   else None,
        "dea_issue_date":      dea.get("issue_date")             if dea   else None,
        "dea_expiry_date":     dea.get("expiration_date")       if dea   else None,
        "dea_schedules":       dea.get("schedules")              if dea   else None,
        "insurer_name":        mal.get("insurer_name")           if mal   else None,
        "policy_number":       mal.get("policy_number")          if mal   else None,
        "mal_effective_date":  mal.get("effective_date")         if mal   else None,
        "mal_expiry_date":     mal.get("expiration_date")        if mal   else None,
        "coverage_per_claim":  mal.get("coverage_per_claim")     if mal   else None,
        "coverage_aggregate":  mal.get("coverage_aggregate")     if mal   else None,
        "board_cert_type":     board.get("certification_type")   if board else None,
        "board_cert_id":       board.get("certification_id")     if board else None,
        "certifying_board":    board.get("certifying_board")     if board else None,
        "board_status":        board.get("status")               if board else None,
        "board_specialty":     board.get("specialty_code")       if board else None,
        "board_issue_date":    board.get("issue_date")           if board else None,
        "board_expiry_date":   board.get("expiration_date")      if board else None,
        "address_street":      lic.get("address_street")         if lic   else None,
        "address_city":        lic.get("address_city")           if lic   else None,
        "address_state":       lic.get("address_state")          if lic   else None,
        "address_zip":         lic.get("address_zip")            if lic   else None,
        "contact_phone":       lic.get("contact_phone")          if lic   else None,
        "contact_email":       lic.get("contact_email")          if lic   else None,
    }

    source_map = {
        "tax_id": tax.get("id") if tax else None,
        "state_medical_license": lic.get("id") if lic else None,
        "malpractice_insurance": mal.get("id") if mal else None,
        "dea_certificate": dea.get("id") if dea else None,
        "board_certification": board.get("id") if board else None
    }

    return {
        "status":           "success",
        "cms_status":       cms_status,
        "cms_profile":      cms_profile,
        "compliance":       compliance,
        "ocr_details":      ocr_details,
        "source_map":       source_map,
        "document_statuses": doc_statuses,
    }


@router.put("/providers/{doctor_id}/{table}/{record_id}")
async def update_provider_field(
    doctor_id: str,
    table: str,
    record_id: int,
    req: UpdateFieldRequest,
    authorization: Optional[str] = Header(None)
):
    """Update a single field in any document table record."""
    _require_admin(authorization)

    if table not in DOCUMENT_TABLES:
        raise HTTPException(status_code=400, detail=f"Invalid table: {table}")

    EDITABLE = {
        "tax_id": ["ein", "provider_name", "first_name", "last_name", "business_name",
                   "issue_date", "form_type", "address_street", "address_city",
                   "address_state", "address_zip", "address_country"],
        "state_medical_license": ["license_number", "provider_name", "first_name", "last_name",
                                   "date_of_birth", "gender", "license_status", "issue_date",
                                   "expiration_date", "address_street", "address_city",
                                   "address_state", "address_zip", "address_country",
                                   "contact_phone", "contact_email"],
        "malpractice_insurance": ["policy_number", "provider_name", "insurer_name",
                                   "effective_date", "expiration_date", "specialty",
                                   "coverage_per_claim", "coverage_aggregate",
                                   "address_street", "address_city", "address_state",
                                   "address_zip", "address_country", "contact_phone", "contact_email"],
        "dea_certificate": ["dea_number", "provider_name", "first_name", "last_name",
                             "business_activity", "issue_date", "expiration_date",
                             "schedules", "fee_paid", "address_street", "address_city",
                             "address_state", "address_zip"],
        "board_certification": ["provider_name", "certification_type", "certifying_board",
                                  "certification_id", "issue_date", "expiration_date",
                                  "status", "specialty_code"],
    }

    if req.field not in EDITABLE.get(table, []):
        raise HTTPException(status_code=400, detail=f"Field '{req.field}' is not editable.")

    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            f"UPDATE {SCHEMA}.{table} SET {req.field} = %s WHERE id = %s AND doctor_id = %s",
            (req.value, record_id, doctor_id)
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Record not found.")
        conn.commit()
        return {"status": "success", "message": f"Updated '{req.field}' in {table}#{record_id}"}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()


# ─── NEW: Approve Documents ────────────────────────────────────────────────────

@router.post("/providers/{doctor_id}/approve")
async def approve_documents(
    doctor_id: str,
    req: ApproveRequest,
    authorization: Optional[str] = Header(None)
):
    """Approve one or more document types for a provider."""
    _require_admin(authorization)

    # Validate doc types
    invalid = [d for d in req.document_types if d not in DOCUMENT_TABLES]
    if invalid:
        raise HTTPException(status_code=400, detail=f"Invalid document types: {invalid}")

    from app.services.db import upsert_document_status, create_notification

    conn = get_conn()
    provider_name = _get_doctor_name(conn, doctor_id)
    conn.close()

    approved_labels = []
    for doc_type in req.document_types:
        upsert_document_status(
            doctor_id=doctor_id,
            document_type=doc_type,
            status="approved",
            rejection_reason=None,
            rejection_fields=None,
            reviewed_by="admin"
        )
        label = DOCUMENT_LABELS.get(doc_type, doc_type)
        approved_labels.append(label)

        # Notify provider
        create_notification(
            role="provider",
            recipient_id=doctor_id,
            notification_type="doc_approved",
            message=f"✅ Your {label} has been approved by the administrator.",
            metadata={"document_type": doc_type, "label": label}
        )

    # Notify admin log (self-log for traceability)
    create_notification(
        role="admin",
        recipient_id=ADMIN_RECIPIENT_ID,
        notification_type="admin_approved",
        message=f"Admin approved {', '.join(approved_labels)} for provider {provider_name}.",
        metadata={"doctor_id": doctor_id, "document_types": req.document_types}
    )

    return {
        "status": "success",
        "message": f"Approved {len(req.document_types)} document(s) for {provider_name}.",
        "approved": req.document_types
    }


# ─── NEW: Reject Document ─────────────────────────────────────────────────────

@router.post("/providers/{doctor_id}/reject")
async def reject_document(
    doctor_id: str,
    req: RejectRequest,
    authorization: Optional[str] = Header(None)
):
    """Reject a specific document type with reason and optional field mapping."""
    _require_admin(authorization)

    if req.document_type not in DOCUMENT_TABLES:
        raise HTTPException(status_code=400, detail=f"Invalid document type: {req.document_type}")

    if not req.rejection_reason or not req.rejection_reason.strip():
        raise HTTPException(status_code=400, detail="Rejection reason is required.")

    from app.services.db import upsert_document_status, create_notification

    fields_str = ", ".join(req.rejection_fields) if req.rejection_fields else None
    label = DOCUMENT_LABELS.get(req.document_type, req.document_type)

    conn = get_conn()
    provider_name = _get_doctor_name(conn, doctor_id)
    conn.close()

    upsert_document_status(
        doctor_id=doctor_id,
        document_type=req.document_type,
        status="rejected",
        rejection_reason=req.rejection_reason,
        rejection_fields=fields_str,
        reviewed_by="admin"
    )

    # Notify provider with full details
    provider_msg = (
        f"❌ Your {label} was rejected. "
        f"Reason: {req.rejection_reason}"
    )
    if fields_str:
        provider_msg += f" | Fields needing correction: {fields_str}"

    create_notification(
        role="provider",
        recipient_id=doctor_id,
        notification_type="doc_rejected",
        message=provider_msg,
        metadata={
            "document_type": req.document_type,
            "label": label,
            "rejection_reason": req.rejection_reason,
            "rejection_fields": req.rejection_fields or []
        }
    )

    # Notify admin log
    create_notification(
        role="admin",
        recipient_id=ADMIN_RECIPIENT_ID,
        notification_type="admin_rejected",
        message=f"Admin rejected {label} for provider {provider_name}. Reason: {req.rejection_reason}",
        metadata={"doctor_id": doctor_id, "document_type": req.document_type}
    )

    return {
        "status": "success",
        "message": f"Rejected {label} for {provider_name}.",
        "document_type": req.document_type,
        "rejection_reason": req.rejection_reason,
        "rejection_fields": req.rejection_fields
    }


# ─── NEW: Reupload Decision ───────────────────────────────────────────────────

@router.post("/providers/{doctor_id}/reupload-decision")
async def reupload_decision(
    doctor_id: str,
    req: ReuUploadDecisionRequest,
    authorization: Optional[str] = Header(None)
):
    """Admin approves or rejects a provider's re-upload request."""
    _require_admin(authorization)

    if req.document_type not in DOCUMENT_TABLES:
        raise HTTPException(status_code=400, detail=f"Invalid document type: {req.document_type}")

    from app.services.db import get_status_for_doc, upsert_document_status, create_notification

    current = get_status_for_doc(doctor_id, req.document_type)
    if not current or current.get("status") != "reupload_requested":
        raise HTTPException(
            status_code=400,
            detail="No pending re-upload request found for this document type."
        )

    conn = get_conn()
    provider_name = _get_doctor_name(conn, doctor_id)
    conn.close()

    label = DOCUMENT_LABELS.get(req.document_type, req.document_type)

    if req.approved:
        upsert_document_status(
            doctor_id=doctor_id,
            document_type=req.document_type,
            status="reupload_approved",
            reviewed_by="admin"
        )
        provider_msg = f"✅ Your re-upload request for {label} has been approved. You may now upload a new document."
        notif_type = "reupload_granted"
        admin_msg = f"Approved re-upload request for {label} by provider {provider_name}."
    else:
        upsert_document_status(
            doctor_id=doctor_id,
            document_type=req.document_type,
            status="approved",  # revert to approved (request denied)
            reviewed_by="admin"
        )
        reason_part = f" Reason: {req.reason}" if req.reason else ""
        provider_msg = f"❌ Your re-upload request for {label} was denied.{reason_part}"
        notif_type = "reupload_denied"
        admin_msg = f"Denied re-upload request for {label} by provider {provider_name}."

    create_notification(
        role="provider",
        recipient_id=doctor_id,
        notification_type=notif_type,
        message=provider_msg,
        metadata={"document_type": req.document_type, "label": label, "approved": req.approved}
    )

    create_notification(
        role="admin",
        recipient_id=ADMIN_RECIPIENT_ID,
        notification_type="admin_reupload_decision",
        message=admin_msg,
        metadata={"doctor_id": doctor_id, "document_type": req.document_type}
    )

    return {
        "status": "success",
        "decision": "approved" if req.approved else "denied",
        "document_type": req.document_type,
        "new_status": "reupload_approved" if req.approved else "approved"
    }


# ─── NEW: Admin Notifications ─────────────────────────────────────────────────

@router.get("/notifications")
async def get_admin_notifications(
    unread: bool = False,
    authorization: Optional[str] = Header(None)
):
    """Fetch admin notifications."""
    _require_admin(authorization)
    from app.services.db import get_notifications, get_unread_notification_count

    notifications = get_notifications("admin", ADMIN_RECIPIENT_ID, unread_only=unread)
    unread_count = get_unread_notification_count("admin", ADMIN_RECIPIENT_ID)
    return {
        "status": "success",
        "unread_count": unread_count,
        "notifications": notifications
    }


@router.put("/notifications/{notification_id}/read")
async def mark_admin_notification_read(
    notification_id: int,
    authorization: Optional[str] = Header(None)
):
    """Mark a specific admin notification as read."""
    _require_admin(authorization)
    from app.services.db import mark_notification_read

    updated = mark_notification_read(notification_id)
    if not updated:
        raise HTTPException(status_code=404, detail="Notification not found.")
    return {"status": "success", "message": "Notification marked as read."}


@router.put("/notifications/read-all")
async def mark_all_admin_notifications_read(
    authorization: Optional[str] = Header(None)
):
    """Mark all admin notifications as read."""
    _require_admin(authorization)
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            f"UPDATE {SCHEMA}.notifications SET is_read = TRUE WHERE recipient_role = 'admin' AND recipient_id = %s",
            (ADMIN_RECIPIENT_ID,)
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
async def clear_all_admin_notifications(
    authorization: Optional[str] = Header(None)
):
    """Delete all admin notifications."""
    _require_admin(authorization)
    from app.services.db import delete_all_notifications
    try:
        delete_all_notifications("admin", ADMIN_RECIPIENT_ID)
        return {"status": "success", "message": "All notifications cleared."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



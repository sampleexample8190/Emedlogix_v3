"""
Provider Chatbot Endpoint
POST /api/chatbot/query  — JWT-authenticated. Fetches provider's live DB data
                           (document statuses, profile, CMS data) and injects it
                           into the AI assistant's context before answering.
"""

import os
import jwt
from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel
from typing import Dict, Any, Optional
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env", override=True)

from app.services.chatbot import chatbot_service

router = APIRouter()

JWT_SECRET = os.getenv("JWT_SECRET")
JWT_ALG    = "HS256"


class ChatQuery(BaseModel):
    message: str
    context: Optional[Dict[str, Any]] = {}   # frontend context (optional, DB always wins)


def _decode_provider_token(authorization: Optional[str]) -> Optional[str]:
    """Decode JWT and return doctor_id if it's a provider token, else None."""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization.split(" ", 1)[1]
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
        return payload.get("doctor_id") or payload.get("sub")
    except Exception:
        return None


def _build_db_context(doctor_id: str) -> Dict[str, Any]:
    """Fetch all relevant provider data from DB to enrich chatbot context."""
    from app.services.db import (
        get_document_statuses, get_documents_by_doctor,
        get_cms_provider_data, get_provider_identity
    )

    ctx: Dict[str, Any] = {}

    # 1. Document statuses
    try:
        statuses = get_document_statuses(doctor_id)
        ctx["document_statuses"] = statuses

        # Aggregate counts
        all_statuses = [v.get("status") for v in statuses.values()]
        ctx["docs_total"]     = len(all_statuses)
        ctx["docs_approved"]  = all_statuses.count("approved")
        ctx["docs_rejected"]  = all_statuses.count("rejected")
        ctx["docs_pending"]   = all_statuses.count("pending")
        ctx["docs_reupload_requested"] = all_statuses.count("reupload_requested")
        ctx["docs_reupload_approved"]  = all_statuses.count("reupload_approved")

        # Which are approved / rejected by name
        ctx["approved_docs"] = [k for k, v in statuses.items() if v.get("status") == "approved"]
        ctx["rejected_docs"] = [
            {"doc": k, "reason": v.get("rejection_reason"), "fields": v.get("rejection_fields")}
            for k, v in statuses.items() if v.get("status") == "rejected"
        ]
        ctx["pending_docs"]  = [k for k, v in statuses.items() if v.get("status") == "pending"]
    except Exception as e:
        ctx["document_statuses"] = {}

    # 2. CMS / NPI data
    try:
        cms = get_cms_provider_data(doctor_id)
        if cms:
            ctx["npi"]            = cms.get("npi_number")
            ctx["npi_status"]     = cms.get("npi_status")
            ctx["practice_city"]  = cms.get("practice_city")
            ctx["practice_state"] = cms.get("practice_state")
            ctx["phone"]          = cms.get("phone")
    except Exception:
        pass

    # 3. Provider identity (name, license, specialty)
    try:
        identity = get_provider_identity(doctor_id)
        if identity:
            ctx["first_name"]          = identity.get("first_name")
            ctx["last_name"]           = identity.get("last_name")
            ctx["gender"]              = identity.get("gender")
            ctx["license_number"]      = identity.get("license_number")
            ctx["license_state"]       = identity.get("license_state")
            ctx["primary_specialty"]   = identity.get("primary_specialty")
            ctx["taxonomy_code"]       = identity.get("primary_taxonomy_code")
    except Exception:
        pass

    # 4. OCR document data (latest records from each table)
    try:
        docs = get_documents_by_doctor(doctor_id)
        tax   = docs.get("tax_id",               [{}])[0] if docs.get("tax_id")               else {}
        lic   = docs.get("state_medical_license", [{}])[0] if docs.get("state_medical_license") else {}
        dea   = docs.get("dea_certificate",       [{}])[0] if docs.get("dea_certificate")       else {}
        mal   = docs.get("malpractice_insurance", [{}])[0] if docs.get("malpractice_insurance") else {}
        board = docs.get("board_certification",   [{}])[0] if docs.get("board_certification")   else {}

        def _fv(*vals):
            for v in vals:
                if v and str(v).strip() and str(v).lower() != "none":
                    return str(v).strip()
            return None

        ctx.setdefault("first_name",    _fv(lic.get("first_name"), dea.get("first_name"), tax.get("first_name")))
        ctx.setdefault("last_name",     _fv(lic.get("last_name"),  dea.get("last_name"),  tax.get("last_name")))
        ctx["tax_ein"]            = _fv(tax.get("ein"))
        ctx["business_name"]      = _fv(tax.get("legal_business_name"), tax.get("business_name"))
        ctx["license_number"]     = ctx.get("license_number") or _fv(lic.get("license_number"))
        ctx["license_status"]     = _fv(lic.get("license_status"))
        ctx["license_expiry"]     = _fv(lic.get("expiration_date"))
        ctx["dea_number"]         = _fv(dea.get("dea_number"))
        ctx["dea_expiry"]         = _fv(dea.get("dea_expiration"))
        ctx["malpractice_policy"] = _fv(mal.get("policy_number"))
        ctx["malpractice_insurer"]= _fv(mal.get("malpractice_insurer"))
        ctx["mal_expiry"]         = _fv(mal.get("policy_expiration"))
        ctx["board_cert_type"]    = _fv(board.get("certification_type"))
        ctx["board_cert_status"]  = _fv(board.get("board_cert_status"))
        ctx["certifying_board"]   = _fv(board.get("certifying_board"))
    except Exception:
        pass

    return ctx


@router.post("/query")
async def chat_with_assistant(
    query: ChatQuery,
    authorization: Optional[str] = Header(None)
):
    """Provider chatbot — JWT-auth optional. Merges DB context with frontend context."""
    print(f"🤖 Chatbot Query: {query.message}")

    # Start with frontend-provided context
    merged_ctx: Dict[str, Any] = dict(query.context or {})

    # If authenticated provider → enrich with live DB data (DB wins over frontend)
    doctor_id = _decode_provider_token(authorization)
    if doctor_id:
        db_ctx = _build_db_context(doctor_id)
        merged_ctx.update(db_ctx)   # DB data overrides stale frontend values
        merged_ctx["doctor_id"] = doctor_id
        print(f"   ✅ DB context loaded for {doctor_id[:8]}... | docs approved: {db_ctx.get('docs_approved', 0)}")
    else:
        print("   ℹ️ No valid JWT — using frontend context only")

    try:
        response = await chatbot_service.query(query.message, merged_ctx)
        return {"status": "success", "response": response}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

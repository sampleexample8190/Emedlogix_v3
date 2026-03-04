"""
Chatbot Service — Emedlogix Provider AI Assistant
Uses Llama 3.1 via HuggingFace router to answer questions about:
- The provider's own credentialing status (DB-fetched, live)
- Document approval/rejection/pending counts
- General medical credentialing knowledge
- Basic conversational messages
"""

import json
import os
import httpx
import logging
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv

load_dotenv(override=True)

logger = logging.getLogger(__name__)

MODEL_NAME = "meta-llama/Llama-3.1-8B-Instruct"
API_URL    = "https://router.huggingface.co/v1/chat/completions"

DOCUMENT_LABELS = {
    "tax_id":               "Tax ID / IRS Letter",
    "state_medical_license":"State Medical License",
    "malpractice_insurance":"Malpractice Insurance",
    "dea_certificate":      "Federal DEA Certificate",
    "board_certification":  "Board Certification",
}


class ChatbotService:
    def __init__(self):
        self._token = self._get_hf_token()

    def _get_hf_token(self):
        token = os.getenv("HF_TOKEN") or os.getenv("HF_API_TOKEN") or os.getenv("HUGGING_FACE_HUB_TOKEN")
        if token:
            token = token.strip().replace("\r", "").replace("\n", "").replace(" ", "")
        return token

    def _build_system_prompt(self, ctx: Dict[str, Any]) -> str:
        """Build a rich system prompt from DB-fetched provider context."""

        def _val(v, default="Not yet available"):
            return str(v).strip() if v and str(v).strip() and str(v).lower() != "none" else default

        # ── Personal Info ─────────────────────────────────────────────────────
        name  = f"{_val(ctx.get('first_name'), '')} {_val(ctx.get('last_name'), '')}".strip() or "Provider"
        npi   = _val(ctx.get("npi"), "Not yet matched")
        lic   = _val(ctx.get("license_number"), "Not uploaded")
        lic_state   = _val(ctx.get("license_state"), "")
        lic_status  = _val(ctx.get("license_status"), "Unknown")
        lic_expiry  = _val(ctx.get("license_expiry"), "Unknown")
        dea   = _val(ctx.get("dea_number"), "Not uploaded")
        dea_exp     = _val(ctx.get("dea_expiry"), "Unknown")
        mal   = _val(ctx.get("malpractice_policy"), "Not uploaded")
        insurer     = _val(ctx.get("malpractice_insurer"), "")
        mal_exp     = _val(ctx.get("mal_expiry"), "Unknown")
        board = _val(ctx.get("board_cert_type"), "Not uploaded")
        board_stat  = _val(ctx.get("board_cert_status"), "")
        tax_ein     = _val(ctx.get("tax_ein"), "Not uploaded")
        biz_name    = _val(ctx.get("business_name"), "")
        specialty   = _val(ctx.get("primary_specialty") or ctx.get("specialty"), "Not available")


        # Build compact status lines (one per doc)

        raw_statuses = ctx.get("document_statuses", {})
        status_lines = "; ".join(
            f"{DOCUMENT_LABELS.get(doc, doc)}={info.get('status','?').upper()}"
            + (f"[reason:{info['rejection_reason']}]" if info.get("rejection_reason") else "")
            for doc, info in raw_statuses.items()
        ) or "none"

        approved = ctx.get("docs_approved", 0)
        rejected = ctx.get("docs_rejected", 0)
        pending  = ctx.get("docs_pending", 0)
        reu_req  = ctx.get("docs_reupload_requested", 0)
        reu_app  = ctx.get("docs_reupload_approved", 0)
        approved_str = ", ".join(DOCUMENT_LABELS.get(n, n) for n in ctx.get("approved_docs", [])) or "none"
        pending_str  = ", ".join(DOCUMENT_LABELS.get(n, n) for n in ctx.get("pending_docs",  [])) or "none"
        rejected_str = "; ".join(
            f"{DOCUMENT_LABELS.get(r['doc'], r['doc'])}" + (f"({r['reason']})" if r.get("reason") else "")
            for r in ctx.get("rejected_docs", [])
        ) or "none"

        system_prompt = (
            "You are the Emedlogix AI Assistant for medical provider credentialing. "
            "Answer questions using the provider's live data below. "
            "For general questions (NPI, DEA, CAQH, credentialing process) use your built-in knowledge. "
            "For greetings or small talk respond warmly and briefly.\n\n"
            "PROVIDER DATA:\n"
            f"Name: {name} | NPI: {npi} | Specialty: {specialty}\n"
            f"License: {lic} ({lic_state}) Status:{lic_status} Exp:{lic_expiry}\n"
            f"DEA: {dea} Exp:{dea_exp}\n"
            f"Malpractice: {mal} ({insurer}) Exp:{mal_exp}\n"
            f"Tax EIN: {tax_ein} Biz: {biz_name}\n"
            f"Board Cert: {board} Status:{board_stat}\n\n"
            "DOCUMENT STATUS:\n"
            f"Approved:{approved} Pending:{pending} Rejected:{rejected} "
            f"ReuploadRequested:{reu_req} ReuploadApproved:{reu_app}\n"
            f"Approved docs: {approved_str}\n"
            f"Pending docs: {pending_str}\n"
            f"Rejected docs: {rejected_str}\n"
            f"Detail: {status_lines}\n\n"
            "RULES: Use the data above for personal questions. "
            "If a value is 'Not uploaded' tell the provider to upload that document. "
            "Be concise, professional, and helpful."
        )

        return system_prompt

    async def query(self, message: str, context: Dict[str, Any]) -> str:
        """Query the AI assistant with message and provider context."""
        if not self._token:
            return "I'm currently unavailable — the Hugging Face API token is missing. Please contact your administrator."

        system_prompt = self._build_system_prompt(context)

        payload = {
            "model": MODEL_NAME,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": message}
            ],
            "max_tokens": 600,
            "temperature": 0.3
        }

        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "x-wait-for-model": "true"
        }

        try:
            async with httpx.AsyncClient(timeout=90.0) as client:
                response = await client.post(API_URL, json=payload, headers=headers)

            if response.status_code != 200:
                err_text = response.text[:400]
                logger.error(f"HF Error {response.status_code}: {err_text}")
                return f"I ran into an issue connecting to the AI service ({response.status_code}). Please try again in a moment."

            result = response.json()
            return result["choices"][0]["message"]["content"]

        except httpx.TimeoutException:
            logger.error("Chatbot request timed out")
            return "The AI service took too long to respond. Please try again."
        except Exception as e:
            logger.error(f"Chatbot Exception: {e}")
            return f"Something went wrong: {str(e)}"


# Singleton
chatbot_service = ChatbotService()

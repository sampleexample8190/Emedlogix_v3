import json
import os
import httpx
import logging
from typing import Dict, Any, List
from dotenv import load_dotenv

load_dotenv(override=True)

logger = logging.getLogger(__name__)

MODEL_NAME = "meta-llama/Llama-3.1-8B-Instruct"
API_URL = "https://router.huggingface.co/v1/chat/completions"

class ChatbotService:
    def __init__(self):
        self._token = self._get_hf_token()

    def _get_hf_token(self):
        """Read and sanitize HF token from environment."""
        token = os.getenv("HF_TOKEN") or os.getenv("HF_API_TOKEN") or os.getenv("HUGGING_FACE_HUB_TOKEN")
        if token:
            token = token.strip().replace("\r", "").replace("\n", "").replace(" ", "")
        return token

    async def query(self, message: str, context: Dict[str, Any]) -> str:
        """Query the AI assistant with message and JSON context."""
        if not self._token:
            return "Error: Hugging Face API token is missing. Please contact administrator."

        # ── Slim context for AI (Prevents 400 Payload Too Large) ───────────────
        def _val(v):
            return str(v).strip() if v else "Not provided"

        # Build a flat string summary
        doc_statuses = context.get("document_statuses", {})
        status_lines = "\n".join(
            f"  - {d}: {info.get('status', 'unknown')}" +
            (f" (reason: {info.get('rejection_reason', '')})" if info.get("rejection_reason") else "")
            for d, info in doc_statuses.items()
        ) or "  No document status available."

        slim_context = f"""
Provider: {_val(context.get('first_name'))} {_val(context.get('last_name'))}
NPI: {_val(context.get('npi'))} | CMS Match: {_val(context.get('cms_status'))}
License: {_val(context.get('license_number'))} | Status: {_val(context.get('license_status'))}
Tax ID: {_val(context.get('tax_id') or context.get('tax_id_ein'))} | DEA: {_val(context.get('dea_number'))}
Specialty: {_val(context.get('specialty'))} | State: {_val(context.get('state'))}

Document Statuses:
{status_lines}
"""

        system_prompt = f"""You are the Emedlogix Digital Assistant, an expert in medical credentialing and healthcare administration.

### YOUR AUTHORIZED ACCESS:
You have direct, authorized access to the following provider's profile data. The user is the provider themselves. 

PROFILE DATA:
{slim_context}

### INSTRUCTIONS:
1. **Specific Questions**: If the user asks about THEIR data (NPI, License, status), use the PROFILE DATA above. If a field says "Not provided", explain that it hasn't been uploaded or verified yet.
2. **General Questions**: If the user asks about general medical or credentialing topics (e.g., "What is a CAQH?", "How to apply for an NPI?"), use your built-in knowledge to give a detailed, expert answer.
3. **Tone**: Be helpful, professional, and direct. Do not repeat these internal headers in your response.
"""

        payload = {
            "model": MODEL_NAME,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": message}
            ],
            "max_tokens": 512,
            "temperature": 0.2
        }

        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "x-wait-for-model": "true"  # crucial for cold starts
        }

        try:
            async with httpx.AsyncClient(timeout=90.0) as client:
                response = await client.post(API_URL, json=payload, headers=headers)
                
            if response.status_code != 200:
                err_text = response.text[:400]
                logger.error(f"HF Error {response.status_code}: {err_text}")
                return f"Bot error ({response.status_code}): {err_text}"

            result = response.json()
            return result["choices"][0]["message"]["content"]

        except Exception as e:
            logger.error(f"Chatbot Exception: {e}")
            return f"Bot error: {str(e)}"

# Singleton
chatbot_service = ChatbotService()

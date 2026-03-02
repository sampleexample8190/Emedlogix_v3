import json
import os
import httpx
import logging
from typing import Dict, Any, List
from dotenv import load_dotenv

load_dotenv(override=True)

logger = logging.getLogger(__name__)

MODEL_NAME = "Qwen/Qwen2.5-7B-Instruct"
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

        system_prompt = f"""You are a professional Medical Credentialing Assistant. 
Your goal is to help users understand provider data, NPI records, and document verification statuses.

CONTEXT DATA (Current Provider):
{json.dumps(context, indent=2)}

TECHNICAL GUIDE:
- 'npi': Found in 'profile' or 'verification_results.cms_profile'.
- 'verification_results': Contains live CMS/SAM compliance status.
- 'document_statuses': Current review status for specific files.
- If 'npi' is null, it means the automated search is still processing or requires more uploaded documents (like a State License).

RULES:
1. Base your answers PRIMARILY on the provided Context Data. 
2. Be proactive: If an NPI is 'null', check 'document_statuses'. If most documents are 'not_uploaded', explain that verification will begin once more files are provided.
3. For general credentialing process questions, you may use your internal knowledge.
4. Be concise, professional, and explain 'REJECTED' reasons if they exist.
5. Use a helpful, supportive tone.
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
            "Content-Type": "application/json"
        }

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(API_URL, json=payload, headers=headers)
                
            if response.status_code != 200:
                logger.error(f"HF API Error: {response.text}")
                return f"Bot error: Received {response.status_code} from AI service."

            result = response.json()
            return result["choices"][0]["message"]["content"]

        except Exception as e:
            logger.error(f"Chatbot Exception: {e}")
            return f"Bot error: {str(e)}"

# Singleton
chatbot_service = ChatbotService()

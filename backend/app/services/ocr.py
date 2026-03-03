import json
import io
import time
import os
import fitz  # PyMuPDF
from PIL import Image
from typing import Dict, Any
from fastapi import HTTPException, UploadFile
from dotenv import load_dotenv

# Load .env file (override=True ensures .env always takes precedence)
load_dotenv(override=True)

# --- CONFIGURATION ---
MODEL_NAME = "Qwen/Qwen2.5-VL-7B-Instruct"


def _get_hf_token():
    """Read and sanitize HF token from environment."""
    token = os.getenv("HF_TOKEN") or os.getenv("HF_API_TOKEN") or os.getenv("HUGGING_FACE_HUB_TOKEN")
    if token:
        # Strip whitespace/newlines that Windows .env files can add
        token = token.strip().replace("\r", "").replace("\n", "").replace(" ", "")
    return token


SYSTEM_PROMPT = """You are a Strict Data Extraction AI for Medical Credentialing.
        
YOUR MISSION:
1. Analyze the document image.
2. Classify it into one of the 5 Supported Types below.
3. Extract data EXACTLY according to the corresponding JSON Schema.

---

SUPPORTED TYPES & SCHEMAS:

1. STATE MEDICAL LICENSE
Schema:
{
  "document_type": "state_medical_license",
  "license_number": "string",
  "npi_number": "string (10-digit if found)",
  "provider_name": "string",
  "first_name": "string",
  "last_name": "string",
  "date_of_birth": "string (MM/DD/YYYY)",
  "gender": "string (M/F)",
  "license_status": "string",
  "issue_date": "string (MM/DD/YYYY)",
  "expiration_date": "string (MM/DD/YYYY)",
  "address": { "street": "string", "city": "string", "state": "string (2-letter)", "zip_code": "string (5-digit)", "country": "string" },
  "contact": { "phone_number": "string", "email": "string" }
}

2. TAX ID / IRS LETTER
Schema:
{
  "document_type": "tax_id",
  "ein": "string (XX-XXXXXXX)",
  "provider_name": "string",
  "first_name": "string",
  "last_name": "string",
  "business_name": "string",
  "issue_date": "string (MM-DD-YYYY)",
  "form_type": "string",
  "address": { "street": "string", "city": "string", "state": "string (2-letter)", "zip_code": "string" }
}

3. FEDERAL DEA CERTIFICATE
Schema:
{
  "document_type": "dea_certificate",
  "dea_number": "string (XX#######)",
  "npi_number": "string (10-digit if found)",
  "provider_name": "string",
  "first_name": "string",
  "last_name": "string",
  "business_activity": "string",
  "issue_date": "string (MM-DD-YYYY)",
  "expiration_date": "string (MM-DD-YYYY)",
  "schedules": ["string", "string"],
  "fee_paid": "string",
  "address": { "street": "string", "city": "string", "state": "string", "zip_code": "string" }
}

4. MALPRACTICE INSURANCE
Schema:
{
  "document_type": "malpractice_insurance",
  "policy_number": "string",
  "provider_name": "string",
  "insurer_name": "string",
  "effective_date": "string (MM/DD/YYYY)",
  "expiration_date": "string (MM/DD/YYYY)",
  "specialty": "string",
  "coverage_limits": { "per_claim": "string", "aggregate": "string" },
  "address": { "street": "string", "city": "string", "state": "string", "zip_code": "string" }
}

5. BOARD CERTIFICATION
Schema:
{
  "document_type": "board_certification",
  "provider_name": "string",
  "certification_type": "string",
  "certifying_board": "string",
  "certification_id": "string",
  "issue_date": "string",
  "expiration_date": "string",
  "status": "string",
  "specialty_code": "string"
}

---

CRITICAL RULES:
1. Output ONLY valid JSON. Do not use Markdown code blocks.
2. If a field is found, extract it exactly.
3. If a field is NOT found or unreadable, set it to null. Do NOT omit the key.
4. Split addresses into city/state/zip if possible.
5. Do not invent document types. If it fits none, return { "document_type": "unknown" }.
"""


class OCRService:
    def __init__(self):
        self._token = None
        self._api_url = "https://router.huggingface.co/v1/chat/completions"

    def _get_token(self):
        """Get the cleaned HF token."""
        if self._token is None:
            self._token = _get_hf_token()
            if not self._token:
                raise RuntimeError("HF_TOKEN not set! Add your Hugging Face token to backend/.env")
            print(f"✅ HF Token ready (starts with: {self._token[:8]}...)")
        return self._token

    def process_pdf_bytes(self, file_bytes: bytes) -> Image.Image:
        """Converts PDF bytes to Image for OCR. Uses 2x zoom for a good
        quality/speed tradeoff (3x produced images too large for the HF API)."""
        try:
            doc = fitz.open(stream=file_bytes, filetype="pdf")
            page = doc.load_page(0)  # Extract first page

            # 2x zoom ≈ 144 DPI — clear enough for OCR, half the bytes of 3x
            mat = fitz.Matrix(2.0, 2.0)
            pix = page.get_pixmap(matrix=mat)

            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            doc.close()
            return img
        except Exception as e:
            print(f"❌ PDF Error: {e}")
            return None

    async def extract_data(self, file: UploadFile) -> Dict[str, Any]:
        """Main method to process file and call the Qwen2.5-VL model via HF Inference API."""
        print(f"📥 Processing Upload: {file.filename}")
        print(f"📄 Content-Type: {file.content_type}")
        start_time = time.time()
        
        try:
            file_bytes = await file.read()
            print(f"✅ File read successfully: {len(file_bytes)} bytes")
            
            filename = file.filename.lower()
            image = None

            # 1. Convert File to Image
            if filename.endswith(".pdf"):
                print("🔄 Converting PDF to Image...")
                image = self.process_pdf_bytes(file_bytes)
            elif filename.endswith((".png", ".jpg", ".jpeg", ".tiff", ".bmp")):
                print("🖼️ Opening Image file...")
                image = Image.open(io.BytesIO(file_bytes))
            else:
                print(f"❌ Unsupported file type: {filename}")
                raise HTTPException(400, "Unsupported file type. Use PDF or Image.")

            if not image:
                print("❌ Image conversion failed.")
                raise HTTPException(500, "Failed to process image file.")

            # Ensure RGB
            if image.mode in ("RGBA", "P"):
                image = image.convert("RGB")

            # Cap at 1024px — large enough for Qwen VL to read all text clearly
            image.thumbnail((1024, 1024))

            print(f"✅ Image Ready ({image.width}x{image.height}). Sending to HuggingFace Inference API...")

            # 2. Run OCR via HF Inference API
            extracted_data = await self.run_inference(image, file.filename)
            
            # 3. Handle Errors
            if "error" in extracted_data:
                print(f"❌ API Error: {extracted_data['error']}")
                raise HTTPException(500, detail=extracted_data["error"])

            processing_time = round(time.time() - start_time, 2)
            print(f"✅ OCR Success! Time: {processing_time}s")
            
            doc_type = extracted_data.get("document_type", "Unknown Document")

            return {
                "success": True,
                "filename": file.filename,
                "document_type": doc_type,
                "processing_time_seconds": processing_time,
                "extracted_data": extracted_data
            }

        except HTTPException:
            raise
        except Exception as e:
            import traceback
            print(f"❌ Server Error: {e}")
            traceback.print_exc()
            raise HTTPException(500, str(e))

    async def run_inference(self, image: Image.Image, filename: str) -> Dict[str, Any]:
        """Run Qwen2.5-VL inference via direct HTTP call to HuggingFace router."""
        import base64
        import httpx

        try:
            token = self._get_token()

            # Compress image — quality 85 is indistinguishable on text docs
            # and can cut payload size by 30-40% vs quality 95
            buffered = io.BytesIO()
            image.save(buffered, format="JPEG", quality=85, optimize=True)
            img_bytes = buffered.getvalue()
            img_base64 = base64.b64encode(img_bytes).decode("utf-8")
            img_data_url = f"data:image/jpeg;base64,{img_base64}"
            print(f"📦 Image payload: {len(img_bytes) / 1024:.0f} KB base64")

            # Build request payload
            payload = {
                "model": MODEL_NAME,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": img_data_url}},
                            {"type": "text", "text": f"Extract all data from this document: {filename}"}
                        ]
                    }
                ],
                "max_tokens": 2048,
                "temperature": 0.0
            }

            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "x-wait-for-model": "true"
            }

            print(f"🤖 Calling HF Inference API with {MODEL_NAME}...")

            async with httpx.AsyncClient(timeout=180.0) as client:
                response = await client.post(self._api_url, json=payload, headers=headers)

            if response.status_code != 200:
                error_msg = response.text[:500]
                print(f"❌ API returned {response.status_code}: {error_msg}")
                return {"error": f"HF API error ({response.status_code}): {error_msg}"}

            result = response.json()
            generated_text = result["choices"][0]["message"]["content"]
            print(f"📝 Raw API output: {generated_text[:200]}...")

            # Clean up potential Markdown wrappers and parse JSON
            clean_json = generated_text.replace("```json", "").replace("```", "").strip()
            return json.loads(clean_json)

        except json.JSONDecodeError as e:
            return {"error": f"Failed to parse API output as JSON: {str(e)}", "raw_output": generated_text}
        except Exception as e:
            import traceback
            traceback.print_exc()
            return {"error": str(e)}


# Singleton instance
ocr_service = OCRService()


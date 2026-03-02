from pathlib import Path
from dotenv import load_dotenv
import os

# ✅ Explicitly load backend/.env (fixes HF_TOKEN issue)
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

from fastapi import FastAPI, Header, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from app.api.endpoints import router as api_router
from app.api.auth_endpoints import router as auth_router
from app.api.admin_endpoints import router as admin_router
from app.api.provider_endpoints import router as provider_router
from app.api.chatbot_endpoints import router as chatbot_router
from app.services.db import (
    init_db, save_document, get_documents_by_doctor,
    get_status_for_doc, upsert_document_status,
    get_documents_by_doctor, archive_document, create_notification
)
from app.services.ocr import ocr_service
import uvicorn
from typing import Optional

app = FastAPI(title="Provider Onboarding API", version="1.0.0")

# CORS
origins = [
    "http://localhost",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://localhost:5500",
    "http://127.0.0.1:5500",
    "https://0tr3phdv-8000.inc1.devtunnels.ms",
    "*"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize DB on startup
@app.on_event("startup")
async def startup():
    init_db()

# ─── Document type table mapping ──────────────────────────────────────────────
DOC_TYPE_TO_TABLE = {
    "tax_id": "tax_id",
    "state_medical_license": "state_medical_license",
    "malpractice_insurance": "malpractice_insurance",
    "dea_certificate": "dea_certificate",
    "board_certification": "board_certification",
}

@app.post("/api/upload")
async def upload_document(
    file: UploadFile = File(...),
    x_doctor_id: Optional[str] = Header(None)
):
    """OCR Endpoint — extract data, check upload restrictions, and auto-save to DB."""
    print(f"📥 Upload request - Doctor ID from header: '{x_doctor_id}'")

    result = await ocr_service.extract_data(file)

    if x_doctor_id and "extracted_data" in result:
        doc_type = result.get("document_type", "unknown")
        filename = result.get("filename", file.filename)

        # ─── Upload Restriction Check ──────────────────────────────────────────
        current_status = get_status_for_doc(x_doctor_id, doc_type)

        if current_status:
            status = current_status.get("status")

            if status == "approved":
                return {
                    **result,
                    "upload_blocked": True,
                    "block_reason": "approved",
                    "block_message": (
                        f"Your {doc_type.replace('_', ' ').title()} has already been approved. "
                        "You must request re-upload permission from the administrator before uploading again."
                    )
                }

            if status == "reupload_requested":
                return {
                    **result,
                    "upload_blocked": True,
                    "block_reason": "reupload_requested",
                    "block_message": (
                        "Your re-upload request is pending admin approval. "
                        "Please wait until admin approves your request."
                    )
                }

            if status == "reupload_approved":
                # Archive old document before saving new one
                try:
                    table_name = DOC_TYPE_TO_TABLE.get(doc_type)
                    if table_name:
                        import psycopg2
                        import psycopg2.extras
                        from app.services.db import get_connection, SCHEMA
                        conn = get_connection()
                        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                        try:
                            cur.execute(
                                f"SELECT id FROM {SCHEMA}.{table_name} WHERE doctor_id = %s ORDER BY uploaded_at DESC LIMIT 1",
                                (x_doctor_id,)
                            )
                            old_row = cur.fetchone()
                            if old_row:
                                archive_document(x_doctor_id, doc_type, table_name, old_row["id"])
                        finally:
                            cur.close()
                            conn.close()
                except Exception as arch_err:
                    print(f"⚠️ Archive warning (non-blocking): {arch_err}")

        # ─── Save document ─────────────────────────────────────────────────────
        try:
            extracted = result.get("extracted_data", {})
            print(f"💾 Saving {doc_type} ({filename}) → doctor: {x_doctor_id[:8]}...")
            print(f"   extracted_data keys: {list(extracted.keys()) if extracted else 'EMPTY'}")
            save_document(x_doctor_id, filename, doc_type, extracted)

            # Update/create status to 'pending' after upload
            upsert_document_status(
                doctor_id=x_doctor_id,
                document_type=doc_type,
                status="pending",
                rejection_reason=None,
                rejection_fields=None
            )

            # If this was a reupload_approved → notify admin of new submission
            if current_status and current_status.get("status") == "reupload_approved":
                create_notification(
                    role="admin",
                    recipient_id="admin",
                    notification_type="doc_resubmitted",
                    message=f"🔄 Provider has submitted a new {doc_type.replace('_', ' ').title()} after re-upload approval.",
                    metadata={"doctor_id": x_doctor_id, "document_type": doc_type}
                )
            print(f"✅ DB save successful for {doc_type}")

        except Exception as e:
            import traceback
            err_detail = traceback.format_exc()
            print(f"❌ DB save FAILED for {doc_type}: {e}\n{err_detail}")
            # Surface the error in the response so the frontend can show it
            result["db_save_warning"] = f"Document processed but could not be saved to DB: {str(e)}"

    elif not x_doctor_id:
        print("⚠️ No X-Doctor-Id header — document NOT saved to DB")

    return result


@app.get("/api/documents")
async def get_my_documents(doctor_id: str):
    """Fetch all documents for a doctor from the DB."""
    try:
        docs = get_documents_by_doctor(doctor_id)
        return {"status": "success", "count": len(docs), "documents": docs}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

app.include_router(api_router, prefix="/api")
app.include_router(auth_router, prefix="/api")
app.include_router(admin_router, prefix="/api")
app.include_router(provider_router, prefix="/api")
app.include_router(chatbot_router, prefix="/api/chatbot")

@app.get("/")
def health_check():
    return {"status": "ok", "system": "Provider Onboarding Backend"}

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
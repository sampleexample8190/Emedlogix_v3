"""
Database Service - PostgreSQL connection and CRUD for OCR documents and doctors.
Schema: ocr_document
5 separate tables for each document type with explicit columns.
Extended with: document_status, notifications, document_versions tables.
"""

import psycopg2
import psycopg2.extras
import json
import os
from typing import Optional, Dict, Any, List
from dotenv import load_dotenv
import asyncio
import logging

logger = logging.getLogger(__name__)

load_dotenv(override=True)

# PG Connection Config
DB_CONFIG = {
    "host": os.getenv("PG_HOST", "localhost"),
    "port": int(os.getenv("PG_PORT", "5432")),
    "database": os.getenv("PG_DATABASE", "postgres"),
    "user": os.getenv("PG_USER", "postgres"),
    "password": os.getenv("PG_PASSWORD", ""),
}

SCHEMA = "ocr_document"


def get_connection():
    """Get a new PG connection."""
    return psycopg2.connect(**DB_CONFIG)


def init_db():
    """Create schema and all tables if they don't exist."""
    conn = get_connection()
    cur = conn.cursor()

    try:
        cur.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")

        # ===== DOCTORS TABLE =====
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {SCHEMA}.doctors (
                doctor_id UUID PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                email VARCHAR(255) UNIQUE NOT NULL,
                password_hash VARCHAR(255) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # ===== TAX ID / IRS LETTER =====
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {SCHEMA}.tax_id (
                id SERIAL PRIMARY KEY,
                doctor_id UUID NOT NULL REFERENCES {SCHEMA}.doctors(doctor_id),
                filename VARCHAR(500),
                document_type VARCHAR(100) DEFAULT 'tax_id',
                ein VARCHAR(50),
                provider_name VARCHAR(255),
                first_name VARCHAR(255),
                last_name VARCHAR(255),
                business_name VARCHAR(255),
                issue_date VARCHAR(100),
                form_type VARCHAR(50),
                address_street VARCHAR(500),
                address_city VARCHAR(255),
                address_state VARCHAR(100),
                address_zip VARCHAR(50),
                address_country VARCHAR(100),
                uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # ===== STATE MEDICAL LICENSE =====
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {SCHEMA}.state_medical_license (
                id SERIAL PRIMARY KEY,
                doctor_id UUID NOT NULL REFERENCES {SCHEMA}.doctors(doctor_id),
                filename VARCHAR(500),
                document_type VARCHAR(100) DEFAULT 'state_medical_license',
                license_number VARCHAR(100),
                provider_name VARCHAR(255),
                first_name VARCHAR(255),
                last_name VARCHAR(255),
                date_of_birth VARCHAR(100),
                gender VARCHAR(50),
                license_status VARCHAR(255),
                issue_date VARCHAR(100),
                expiration_date VARCHAR(100),
                address_street VARCHAR(500),
                address_city VARCHAR(255),
                address_state VARCHAR(100),
                address_zip VARCHAR(50),
                address_country VARCHAR(255),
                contact_phone VARCHAR(100),
                contact_email VARCHAR(255),
                uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # ===== MALPRACTICE INSURANCE =====
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {SCHEMA}.malpractice_insurance (
                id SERIAL PRIMARY KEY,
                doctor_id UUID NOT NULL REFERENCES {SCHEMA}.doctors(doctor_id),
                filename VARCHAR(500),
                document_type VARCHAR(100) DEFAULT 'malpractice_insurance',
                policy_number VARCHAR(100),
                provider_name VARCHAR(255),
                insurer_name VARCHAR(255),
                effective_date VARCHAR(100),
                expiration_date VARCHAR(100),
                specialty VARCHAR(255),
                coverage_per_claim VARCHAR(100),
                coverage_aggregate VARCHAR(100),
                address_street VARCHAR(500),
                address_city VARCHAR(255),
                address_state VARCHAR(100),
                address_zip VARCHAR(50),
                address_country VARCHAR(100),
                contact_phone VARCHAR(100),
                contact_email VARCHAR(255),
                uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # ===== FEDERAL DEA =====
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {SCHEMA}.dea_certificate (
                id SERIAL PRIMARY KEY,
                doctor_id UUID NOT NULL REFERENCES {SCHEMA}.doctors(doctor_id),
                filename VARCHAR(500),
                document_type VARCHAR(100) DEFAULT 'dea_certificate',
                dea_number VARCHAR(100),
                provider_name VARCHAR(255),
                first_name VARCHAR(255),
                last_name VARCHAR(255),
                business_activity VARCHAR(255),
                issue_date VARCHAR(100),
                expiration_date VARCHAR(100),
                schedules TEXT,
                fee_paid VARCHAR(50),
                address_street VARCHAR(500),
                address_city VARCHAR(255),
                address_state VARCHAR(100),
                address_zip VARCHAR(50),
                uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # ===== BOARD CERTIFICATION =====
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {SCHEMA}.board_certification (
                id SERIAL PRIMARY KEY,
                doctor_id UUID NOT NULL REFERENCES {SCHEMA}.doctors(doctor_id),
                filename VARCHAR(500),
                document_type VARCHAR(100) DEFAULT 'board_certification',
                provider_name VARCHAR(255),
                certification_type VARCHAR(500),
                certifying_board VARCHAR(255),
                certification_id VARCHAR(100),
                issue_date VARCHAR(100),
                expiration_date VARCHAR(100),
                status VARCHAR(255),
                specialty_code VARCHAR(255),
                uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # ===== DOCUMENT STATUS TABLE =====
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {SCHEMA}.document_status (
                id SERIAL PRIMARY KEY,
                doctor_id UUID NOT NULL REFERENCES {SCHEMA}.doctors(doctor_id),
                document_type VARCHAR(100) NOT NULL,
                status VARCHAR(50) DEFAULT 'pending',
                rejection_reason TEXT,
                rejection_fields TEXT,
                reviewed_at TIMESTAMP,
                reviewed_by VARCHAR(100),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (doctor_id, document_type)
            )
        """)

        # ===== NOTIFICATIONS TABLE =====
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {SCHEMA}.notifications (
                id SERIAL PRIMARY KEY,
                recipient_role VARCHAR(20) NOT NULL,
                recipient_id VARCHAR(100) NOT NULL,
                notification_type VARCHAR(100) NOT NULL,
                message TEXT NOT NULL,
                metadata TEXT DEFAULT '{{}}',
                is_read BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # ===== DOCUMENT VERSIONS TABLE =====
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {SCHEMA}.document_versions (
                id SERIAL PRIMARY KEY,
                doctor_id UUID NOT NULL REFERENCES {SCHEMA}.doctors(doctor_id),
                document_type VARCHAR(100) NOT NULL,
                original_table VARCHAR(100) NOT NULL,
                original_record_id INTEGER NOT NULL,
                archived_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # ===== VERIFICATION CACHE TABLE =====
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {SCHEMA}.verification_cache (
                doctor_id UUID PRIMARY KEY REFERENCES {SCHEMA}.doctors(doctor_id),
                cms_profile JSONB,
                compliance JSONB,
                cms_status VARCHAR(50) DEFAULT 'pending',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # ===== ADD raw_ocr_json COLUMN (idempotent — safe to run on existing DBs) =====
        for tbl in ['tax_id', 'state_medical_license', 'malpractice_insurance', 'dea_certificate', 'board_certification']:
            cur.execute(f"""
                ALTER TABLE {SCHEMA}.{tbl}
                ADD COLUMN IF NOT EXISTS raw_ocr_json JSONB
            """)

        # Indexes for fast doctor lookups
        for table in ['tax_id', 'state_medical_license', 'malpractice_insurance', 'dea_certificate', 'board_certification']:
            cur.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_{table}_doctor_id
                ON {SCHEMA}.{table}(doctor_id)
            """)

        cur.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_doc_status_doctor
            ON {SCHEMA}.document_status(doctor_id)
        """)

        cur.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_notifications_recipient
            ON {SCHEMA}.notifications(recipient_role, recipient_id)
        """)

        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_vc_doctor ON {SCHEMA}.verification_cache(doctor_id)")

        conn.commit()
        print("✅ Database initialized: schema 'ocr_document' with all tables ready (status/notifications/versions)")

    except Exception as e:
        conn.rollback()
        print(f"❌ DB Init Error: {e}")
        raise
    finally:
        cur.close()
        conn.close()


# ===== DOCTOR CRUD =====

def save_doctor(doctor_id: str, name: str, email: str, password_hash: str):
    """Insert a new doctor into the DB."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f"""INSERT INTO {SCHEMA}.doctors (doctor_id, name, email, password_hash)
                VALUES (%s, %s, %s, %s)""",
            (doctor_id, name, email, password_hash)
        )
        conn.commit()
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        raise ValueError("A doctor with this email already exists.")
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cur.close()
        conn.close()


def get_doctor_by_email(email: str) -> Optional[Dict[str, Any]]:
    """Fetch a doctor by email."""
    conn = get_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute(
            f"SELECT doctor_id, name, email, password_hash, created_at FROM {SCHEMA}.doctors WHERE email = %s",
            (email,)
        )
        row = cur.fetchone()
        if row:
            return dict(row)
        return None
    finally:
        cur.close()
        conn.close()


# ===== DOCUMENT SAVE (routes to correct table based on document_type) =====

def _get_addr(data, key="address"):
    """Extract flattened address fields from nested address dict."""
    addr = data.get(key, {}) or {}
    return {
        "street": addr.get("street") or addr.get("address_line_1"),
        "city": addr.get("city"),
        "state": addr.get("state"),
        "zip": addr.get("zip_code") or addr.get("zip"),
        "country": addr.get("country"),
    }


def _get_contact(data):
    """Extract contact fields."""
    contact = data.get("contact", {}) or {}
    return {
        "phone": contact.get("phone_number"),
        "email": contact.get("email"),
    }


def save_document(doctor_id: str, filename: str, document_type: str, extracted_data: Dict[str, Any]):
    """Save OCR-extracted document to its specific table based on document_type."""
    conn = get_connection()
    cur = conn.cursor()

    try:
        d = extracted_data  # shorthand
        addr = _get_addr(d)
        contact = _get_contact(d)

        raw_json = json.dumps(extracted_data)  # full OCR JSON for audit / reprocessing

        if document_type == "tax_id":
            cur.execute(f"""
                INSERT INTO {SCHEMA}.tax_id
                (doctor_id, filename, ein, provider_name, first_name, last_name,
                 business_name, issue_date, form_type,
                 address_street, address_city, address_state, address_zip, address_country,
                 raw_ocr_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id""",
                (doctor_id, filename, d.get("ein"), d.get("provider_name"),
                 d.get("first_name"), d.get("last_name"), d.get("business_name"),
                 d.get("issue_date"), d.get("form_type"),
                 addr["street"], addr["city"], addr["state"], addr["zip"], addr["country"],
                 raw_json)
            )

        elif document_type == "state_medical_license":
            cur.execute(f"""
                INSERT INTO {SCHEMA}.state_medical_license
                (doctor_id, filename, license_number, provider_name, first_name, last_name,
                 date_of_birth, gender, license_status, issue_date, expiration_date,
                 address_street, address_city, address_state, address_zip, address_country,
                 contact_phone, contact_email, raw_ocr_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id""",
                (doctor_id, filename, d.get("license_number"), d.get("provider_name"),
                 d.get("first_name"), d.get("last_name"), d.get("date_of_birth"),
                 d.get("gender"), d.get("license_status"), d.get("issue_date"),
                 d.get("expiration_date"),
                 addr["street"], addr["city"], addr["state"], addr["zip"], addr["country"],
                 contact["phone"], contact["email"], raw_json)
            )

        elif document_type == "malpractice_insurance":
            limits = d.get("coverage_limits", {}) or {}
            cur.execute(f"""
                INSERT INTO {SCHEMA}.malpractice_insurance
                (doctor_id, filename, policy_number, provider_name, insurer_name,
                 effective_date, expiration_date, specialty,
                 coverage_per_claim, coverage_aggregate,
                 address_street, address_city, address_state, address_zip, address_country,
                 contact_phone, contact_email, raw_ocr_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id""",
                (doctor_id, filename, d.get("policy_number"), d.get("provider_name"),
                 d.get("insurer_name"), d.get("effective_date"), d.get("expiration_date"),
                 d.get("specialty"), limits.get("per_claim"), limits.get("aggregate"),
                 addr["street"], addr["city"], addr["state"], addr["zip"], addr["country"],
                 contact["phone"], contact["email"], raw_json)
            )

        elif document_type == "dea_certificate":
            schedules_val = d.get("schedules")
            if isinstance(schedules_val, list):
                schedules_val = ", ".join(schedules_val)
            cur.execute(f"""
                INSERT INTO {SCHEMA}.dea_certificate
                (doctor_id, filename, dea_number, provider_name, first_name, last_name,
                 business_activity, issue_date, expiration_date, schedules, fee_paid,
                 address_street, address_city, address_state, address_zip, raw_ocr_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id""",
                (doctor_id, filename, d.get("dea_number"), d.get("provider_name"),
                 d.get("first_name"), d.get("last_name"), d.get("business_activity"),
                 d.get("issue_date"), d.get("expiration_date"),
                 schedules_val, d.get("fee_paid"),
                 addr["street"], addr["city"], addr["state"], addr["zip"], raw_json)
            )

        elif document_type == "board_certification":
            cur.execute(f"""
                INSERT INTO {SCHEMA}.board_certification
                (doctor_id, filename, provider_name, certification_type, certifying_board,
                 certification_id, issue_date, expiration_date, status, specialty_code,
                 raw_ocr_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id""",
                (doctor_id, filename, d.get("provider_name"), d.get("certification_type"),
                 d.get("certifying_board"), d.get("certification_id"),
                 d.get("issue_date"), d.get("expiration_date"),
                 d.get("status"), d.get("specialty_code"), raw_json)
            )

        else:
            print(f"⚠️ Unknown document type: {document_type}, skipping DB save")
            return None

        doc_id = cur.fetchone()[0]
        conn.commit()
        print(f"SUCCESS: Saved to {SCHEMA}.{document_type}: #{doc_id} for doctor {doctor_id[:8]}...")
        
        # Trigger cache update in the background
        try:
            from app.services.db import update_verification_cache
            asyncio.create_task(update_verification_cache(doctor_id))
        except Exception as cache_err:
            print(f"WARNING: Failed to trigger verification cache update: {cache_err}")

        return doc_id

    except Exception as e:
        conn.rollback()
        print(f"❌ DB Save Error ({document_type}): {e}")
        import traceback
        traceback.print_exc()
        raise e
    finally:
        cur.close()
        conn.close()


# ===== FETCH ALL DOCUMENTS FOR A DOCTOR =====

def get_documents_by_doctor(doctor_id: str) -> Dict[str, List[Dict]]:
    """Fetch all documents for a doctor across all 5 tables."""
    conn = get_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    result = {}

    tables = ['tax_id', 'state_medical_license', 'malpractice_insurance', 'dea_certificate', 'board_certification']

    try:
        for table in tables:
            cur.execute(
                f"SELECT * FROM {SCHEMA}.{table} WHERE doctor_id = %s ORDER BY uploaded_at DESC",
                (doctor_id,)
            )
            rows = cur.fetchall()
            result[table] = [dict(r) for r in rows]

        return result
    finally:
        cur.close()
        conn.close()


# ===== DOCUMENT STATUS CRUD =====

def upsert_document_status(
    doctor_id: str,
    document_type: str,
    status: str,
    rejection_reason: Optional[str] = None,
    rejection_fields: Optional[str] = None,
    reviewed_by: Optional[str] = None
) -> None:
    """Insert or update the status record for a doctor+document_type pair."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(f"""
            INSERT INTO {SCHEMA}.document_status
                (doctor_id, document_type, status, rejection_reason, rejection_fields, reviewed_at, reviewed_by, updated_at)
            VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (doctor_id, document_type)
            DO UPDATE SET
                status = EXCLUDED.status,
                rejection_reason = EXCLUDED.rejection_reason,
                rejection_fields = EXCLUDED.rejection_fields,
                reviewed_at = CURRENT_TIMESTAMP,
                reviewed_by = EXCLUDED.reviewed_by,
                updated_at = CURRENT_TIMESTAMP
        """, (doctor_id, document_type, status, rejection_reason, rejection_fields, reviewed_by))
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cur.close()
        conn.close()


def get_document_statuses(doctor_id: str) -> Dict[str, Dict]:
    """Return all document status rows for a doctor, keyed by document_type."""
    conn = get_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute(
            f"SELECT * FROM {SCHEMA}.document_status WHERE doctor_id = %s",
            (doctor_id,)
        )
        rows = cur.fetchall()
        result = {}
        for row in rows:
            r = dict(row)
            r["doctor_id"] = str(r["doctor_id"])
            for ts_field in ["reviewed_at", "created_at", "updated_at"]:
                if r.get(ts_field):
                    r[ts_field] = r[ts_field].isoformat()
            result[r["document_type"]] = r
        return result
    finally:
        cur.close()
        conn.close()


def get_status_for_doc(doctor_id: str, document_type: str) -> Optional[Dict]:
    """Return single document status row for a doctor+doc_type, or None if not exists."""
    conn = get_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute(
            f"SELECT * FROM {SCHEMA}.document_status WHERE doctor_id = %s AND document_type = %s",
            (doctor_id, document_type)
        )
        row = cur.fetchone()
        if row:
            r = dict(row)
            r["doctor_id"] = str(r["doctor_id"])
            for ts_field in ["reviewed_at", "created_at", "updated_at"]:
                if r.get(ts_field):
                    r[ts_field] = r[ts_field].isoformat()
            return r
        return None
    finally:
        cur.close()
        conn.close()


# ===== NOTIFICATIONS CRUD =====

def create_notification(
    role: str,
    recipient_id: str,
    notification_type: str,
    message: str,
    metadata: Optional[Dict] = None
) -> int:
    """Create a new notification. Returns the new notification id."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        meta_str = json.dumps(metadata or {})
        cur.execute(f"""
            INSERT INTO {SCHEMA}.notifications
                (recipient_role, recipient_id, notification_type, message, metadata)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
        """, (role, recipient_id, notification_type, message, meta_str))
        nid = cur.fetchone()[0]
        conn.commit()
        return nid
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cur.close()
        conn.close()


def get_notifications(role: str, recipient_id: str, unread_only: bool = False) -> List[Dict]:
    """Fetch notifications for a given role+recipient."""
    conn = get_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        query = f"""
            SELECT * FROM {SCHEMA}.notifications
            WHERE recipient_role = %s AND recipient_id = %s
        """
        params = [role, recipient_id]
        if unread_only:
            query += " AND is_read = FALSE"
        query += " ORDER BY created_at DESC"
        cur.execute(query, params)
        rows = cur.fetchall()
        result = []
        for row in rows:
            r = dict(row)
            if r.get("created_at"):
                r["created_at"] = r["created_at"].isoformat()
            try:
                r["metadata"] = json.loads(r.get("metadata") or "{}")
            except Exception:
                r["metadata"] = {}
            result.append(r)
        return result
    finally:
        cur.close()
        conn.close()


def mark_notification_read(notification_id: int) -> bool:
    """Mark a notification as read. Returns True if a row was updated."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f"UPDATE {SCHEMA}.notifications SET is_read = TRUE WHERE id = %s",
            (notification_id,)
        )
        updated = cur.rowcount > 0
        conn.commit()
        return updated
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cur.close()
        conn.close()


def get_unread_notification_count(role: str, recipient_id: str) -> int:
    """Return count of unread notifications for a role+recipient."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f"SELECT COUNT(*) FROM {SCHEMA}.notifications WHERE recipient_role = %s AND recipient_id = %s AND is_read = FALSE",
            (role, recipient_id)
        )
        return cur.fetchone()[0]
    finally:
        cur.close()
        conn.close()


def delete_all_notifications(role: str, recipient_id: str) -> bool:
    """Delete all notifications for a role+recipient. Returns True if rows were deleted."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f"DELETE FROM {SCHEMA}.notifications WHERE recipient_role = %s AND recipient_id = %s",
            (role, recipient_id)
        )
        deleted = cur.rowcount > 0
        conn.commit()
        return deleted
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cur.close()
        conn.close()


# ===== DOCUMENT VERSIONS (ARCHIVE) CRUD =====

def archive_document(doctor_id: str, document_type: str, original_table: str, original_record_id: int) -> int:
    """Archive a document version before re-upload. Returns archive record id."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(f"""
            INSERT INTO {SCHEMA}.document_versions
                (doctor_id, document_type, original_table, original_record_id)
            VALUES (%s, %s, %s, %s)
            RETURNING id
        """, (doctor_id, document_type, original_table, original_record_id))
        vid = cur.fetchone()[0]
        conn.commit()
        print(f"📦 Archived {document_type} record #{original_record_id} → version #{vid}")
        return vid
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cur.close()
        conn.close()
# ===== VERIFICATION CACHE (PERFORMANCE) =====

async def update_verification_cache(doctor_id: str):
    """Pre-compute verification results and store in verification_cache."""
    print(f"INFO: Updating verification cache for doctor {doctor_id[:8]}...")
    conn = get_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    
    try:
        # 1. Fetch latest documents
        tables = ['tax_id', 'state_medical_license', 'malpractice_insurance', 'dea_certificate', 'board_certification']
        docs = {}
        for table in tables:
            cur.execute(
                f"SELECT * FROM {SCHEMA}.{table} WHERE doctor_id = %s ORDER BY uploaded_at DESC LIMIT 1",
                (doctor_id,)
            )
            row = cur.fetchone()
            docs[table] = dict(row) if row else None

        # 2. Extract fields for OCRPayload (minimal merging logic)
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

        # 3. Call Matching Engine
        from app.models.provider import OCRPayload
        from app.services.matching import MatchingEngine
        
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

        # 4. Save to Cache
        cms_profile = None
        compliance  = None
        cms_status  = "not_matched"

        if result.status == "success" and result.provider_profile:
            cms_profile = result.provider_profile.dict() if hasattr(result.provider_profile, "dict") else vars(result.provider_profile)
            compliance  = result.compliance.dict() if result.compliance and hasattr(result.compliance, "dict") else (vars(result.compliance) if result.compliance else None)
            cms_status  = "verified"
        elif "error" in result.status:
            cms_status = result.status

        cur.execute(f"""
            INSERT INTO {SCHEMA}.verification_cache (doctor_id, cms_profile, compliance, cms_status, updated_at)
            VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (doctor_id) DO UPDATE SET
                cms_profile = EXCLUDED.cms_profile,
                compliance = EXCLUDED.compliance,
                cms_status = EXCLUDED.cms_status,
                updated_at = EXCLUDED.updated_at
        """, (doctor_id, json.dumps(cms_profile), json.dumps(compliance), cms_status))
        conn.commit()
        print(f"SUCCESS: Verification cache updated for doctor {doctor_id[:8]}...")

    except Exception as e:
        print(f"ERROR: Cache Update Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        cur.close()
        conn.close()

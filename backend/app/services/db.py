"""
Database Service - PostgreSQL connection and CRUD for OCR documents and doctors.
Schema: provider_credentialing
Tables: doctors, provider_identity, cms_provider_data, documents,
        tax_id, state_medical_license, dea_certificate, malpractice_insurance,
        board_certification, document_reviews, notifications,
        document_versions, processing_tasks
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

SCHEMA = "provider_credentialing"


def get_connection():
    """Get a new PG connection with schema search_path so ENUM types are visible."""
    config = {
        **DB_CONFIG,
        "options": f"-c search_path={SCHEMA},public"
    }
    return psycopg2.connect(**config)


def init_db():
    """Create schema, ENUM types, and all tables if they don't exist."""
    conn = get_connection()
    cur = conn.cursor()

    try:
        cur.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
        cur.execute(f"SET search_path TO {SCHEMA}")

        # ===== ENUM TYPES =====
        cur.execute("""
            DO $$ BEGIN
                CREATE TYPE document_status_enum AS ENUM (
                    'pending', 'approved', 'rejected', 'reupload_requested', 'reupload_approved'
                );
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$;
        """)

        # ── Safe migration: add reupload_approved if this is an existing DB ──
        cur.execute("""
            DO $$ BEGIN
                ALTER TYPE document_status_enum ADD VALUE IF NOT EXISTS 'reupload_approved';
            EXCEPTION WHEN others THEN NULL;
            END $$;
        """)

        cur.execute("""
            DO $$ BEGIN
                CREATE TYPE task_status_enum AS ENUM (
                    'pending', 'running', 'completed', 'failed'
                );
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$;
        """)

        cur.execute("""
            DO $$ BEGIN
                CREATE TYPE task_type_enum AS ENUM (
                    'OCR', 'CMS_LOOKUP', 'DOCUMENT_REVIEW', 'COMPLIANCE_CHECK'
                );
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$;
        """)

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
        cur.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_doctors_email
            ON {SCHEMA}.doctors(email)
        """)

        # ===== PROVIDER IDENTITY =====
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {SCHEMA}.provider_identity (
                doctor_id UUID PRIMARY KEY,
                first_name VARCHAR(255),
                last_name VARCHAR(255),
                gender VARCHAR(50),
                date_of_birth DATE,
                license_number VARCHAR(100),
                license_state VARCHAR(50),
                primary_taxonomy_code VARCHAR(50),
                primary_specialty VARCHAR(255),
                CONSTRAINT fk_provider_identity_doctor
                    FOREIGN KEY (doctor_id)
                    REFERENCES {SCHEMA}.doctors(doctor_id)
                    ON DELETE CASCADE
            )
        """)

        # ===== CMS PROVIDER DATA =====
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {SCHEMA}.cms_provider_data (
                doctor_id UUID PRIMARY KEY,
                npi_number VARCHAR(20),
                enumeration_type VARCHAR(50),
                sole_proprietor BOOLEAN,
                practice_address_line1 VARCHAR(255),
                practice_address_line2 VARCHAR(255),
                practice_city VARCHAR(100),
                practice_state VARCHAR(50),
                practice_zip VARCHAR(20),
                practice_country VARCHAR(50),
                mailing_address_line1 VARCHAR(255),
                mailing_address_line2 VARCHAR(255),
                mailing_city VARCHAR(100),
                mailing_state VARCHAR(50),
                mailing_zip VARCHAR(20),
                mailing_country VARCHAR(50),
                phone VARCHAR(50),
                fax VARCHAR(50),
                npi_status VARCHAR(50),
                last_updated TIMESTAMP,
                cms_raw_json JSONB,
                CONSTRAINT fk_cms_provider_doctor
                    FOREIGN KEY (doctor_id)
                    REFERENCES {SCHEMA}.doctors(doctor_id)
                    ON DELETE CASCADE
            )
        """)
        cur.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_cms_npi
            ON {SCHEMA}.cms_provider_data(npi_number)
        """)

        # ===== DOCUMENTS (central tracking table) =====
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {SCHEMA}.documents (
                document_id SERIAL PRIMARY KEY,
                doctor_id UUID,
                document_type VARCHAR(100),
                filename VARCHAR(500),
                uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status document_status_enum DEFAULT 'pending',
                reviewed_at TIMESTAMP,
                reviewed_by VARCHAR(100),
                CONSTRAINT fk_documents_doctor
                    FOREIGN KEY (doctor_id)
                    REFERENCES {SCHEMA}.doctors(doctor_id)
                    ON DELETE CASCADE
            )
        """)
        cur.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_documents_doctor
            ON {SCHEMA}.documents(doctor_id)
        """)
        cur.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_documents_type
            ON {SCHEMA}.documents(document_type)
        """)

        # ===== TAX ID / IRS LETTER =====
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {SCHEMA}.tax_id (
                id SERIAL PRIMARY KEY,
                doctor_id UUID,
                ein VARCHAR(50),
                legal_business_name VARCHAR(255),
                provider_name VARCHAR(255),
                first_name VARCHAR(255),
                last_name VARCHAR(255),
                business_name VARCHAR(255),
                issue_date VARCHAR(100),
                form_type VARCHAR(50),
                address_street VARCHAR(255),
                address_city VARCHAR(100),
                address_state VARCHAR(50),
                address_zip VARCHAR(20),
                address_country VARCHAR(50),
                filename VARCHAR(500),
                raw_ocr_json JSONB,
                uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT fk_tax_id_doctor
                    FOREIGN KEY (doctor_id)
                    REFERENCES {SCHEMA}.doctors(doctor_id)
                    ON DELETE CASCADE
            )
        """)
        cur.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_tax_doctor
            ON {SCHEMA}.tax_id(doctor_id)
        """)

        # ===== STATE MEDICAL LICENSE =====
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {SCHEMA}.state_medical_license (
                id SERIAL PRIMARY KEY,
                doctor_id UUID,
                license_number VARCHAR(100),
                provider_name VARCHAR(255),
                first_name VARCHAR(255),
                last_name VARCHAR(255),
                date_of_birth VARCHAR(100),
                gender VARCHAR(50),
                license_status VARCHAR(100),
                issue_date DATE,
                expiration_date DATE,
                address_street VARCHAR(500),
                address_city VARCHAR(255),
                address_state VARCHAR(100),
                address_zip VARCHAR(50),
                address_country VARCHAR(255),
                contact_phone VARCHAR(50),
                contact_email VARCHAR(255),
                filename VARCHAR(500),
                raw_ocr_json JSONB,
                uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT fk_state_license_doctor
                    FOREIGN KEY (doctor_id)
                    REFERENCES {SCHEMA}.doctors(doctor_id)
                    ON DELETE CASCADE
            )
        """)
        cur.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_license_doctor
            ON {SCHEMA}.state_medical_license(doctor_id)
        """)

        # ===== FEDERAL DEA CERTIFICATE =====
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {SCHEMA}.dea_certificate (
                id SERIAL PRIMARY KEY,
                doctor_id UUID,
                dea_number VARCHAR(50),
                dea_expiration DATE,
                provider_name VARCHAR(255),
                first_name VARCHAR(255),
                last_name VARCHAR(255),
                business_activity VARCHAR(255),
                issue_date VARCHAR(100),
                schedules TEXT,
                fee_paid VARCHAR(50),
                address_street VARCHAR(500),
                address_city VARCHAR(255),
                address_state VARCHAR(100),
                address_zip VARCHAR(50),
                filename VARCHAR(500),
                raw_ocr_json JSONB,
                uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT fk_dea_doctor
                    FOREIGN KEY (doctor_id)
                    REFERENCES {SCHEMA}.doctors(doctor_id)
                    ON DELETE CASCADE
            )
        """)
        cur.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_dea_doctor
            ON {SCHEMA}.dea_certificate(doctor_id)
        """)

        # ===== MALPRACTICE INSURANCE =====
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {SCHEMA}.malpractice_insurance (
                id SERIAL PRIMARY KEY,
                doctor_id UUID,
                policy_number VARCHAR(100),
                malpractice_insurer VARCHAR(255),
                provider_name VARCHAR(255),
                effective_date VARCHAR(100),
                policy_expiration DATE,
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
                filename VARCHAR(500),
                raw_ocr_json JSONB,
                uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT fk_malpractice_doctor
                    FOREIGN KEY (doctor_id)
                    REFERENCES {SCHEMA}.doctors(doctor_id)
                    ON DELETE CASCADE
            )
        """)
        cur.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_malpractice_doctor
            ON {SCHEMA}.malpractice_insurance(doctor_id)
        """)

        # ===== BOARD CERTIFICATION =====
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {SCHEMA}.board_certification (
                id SERIAL PRIMARY KEY,
                doctor_id UUID,
                provider_name VARCHAR(255),
                certification_type VARCHAR(500),
                certifying_board VARCHAR(255),
                certification_id VARCHAR(100),
                issue_date DATE,
                expiration_date DATE,
                board_cert_status VARCHAR(100),
                specialty_code VARCHAR(255),
                filename VARCHAR(500),
                raw_ocr_json JSONB,
                uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT fk_board_cert_doctor
                    FOREIGN KEY (doctor_id)
                    REFERENCES {SCHEMA}.doctors(doctor_id)
                    ON DELETE CASCADE
            )
        """)
        cur.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_board_doctor
            ON {SCHEMA}.board_certification(doctor_id)
        """)

        # ===== DOCUMENT REVIEWS =====
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {SCHEMA}.document_reviews (
                id SERIAL PRIMARY KEY,
                document_id INTEGER,
                status document_status_enum,
                rejection_reason TEXT,
                rejection_fields TEXT,
                reviewed_by VARCHAR(100),
                reviewed_at TIMESTAMP,
                CONSTRAINT fk_document_reviews_doc
                    FOREIGN KEY (document_id)
                    REFERENCES {SCHEMA}.documents(document_id)
                    ON DELETE CASCADE
            )
        """)

        # ===== DOCUMENT STATUS (backward-compat layer on top of documents) =====
        # Kept as a view-like helper table for existing endpoint compatibility
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
        cur.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_doc_status_doctor
            ON {SCHEMA}.document_status(doctor_id)
        """)

        # ===== NOTIFICATIONS =====
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {SCHEMA}.notifications (
                id SERIAL PRIMARY KEY,
                recipient_role VARCHAR(20),
                recipient_id VARCHAR(100),
                notification_type VARCHAR(100),
                message TEXT,
                metadata TEXT DEFAULT '{{}}',
                is_read BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_notifications_recipient
            ON {SCHEMA}.notifications(recipient_role, recipient_id)
        """)

        # ===== DOCUMENT VERSIONS =====
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {SCHEMA}.document_versions (
                id SERIAL PRIMARY KEY,
                doctor_id UUID,
                document_type VARCHAR(100),
                original_table VARCHAR(100),
                original_record_id INTEGER,
                archived_filename VARCHAR(500),
                archived_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT fk_document_versions_doctor
                    FOREIGN KEY (doctor_id)
                    REFERENCES {SCHEMA}.doctors(doctor_id)
                    ON DELETE CASCADE
            )
        """)

        # ===== PROCESSING TASKS =====
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {SCHEMA}.processing_tasks (
                task_id SERIAL PRIMARY KEY,
                doctor_id UUID,
                task_type task_type_enum,
                document_type VARCHAR(100),
                status task_status_enum DEFAULT 'pending',
                started_at TIMESTAMP,
                completed_at TIMESTAMP,
                triggered_by VARCHAR(50),
                error_message TEXT,
                metadata_json JSONB,
                CONSTRAINT fk_processing_tasks_doctor
                    FOREIGN KEY (doctor_id)
                    REFERENCES {SCHEMA}.doctors(doctor_id)
                    ON DELETE CASCADE
            )
        """)
        cur.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_tasks_doctor
            ON {SCHEMA}.processing_tasks(doctor_id)
        """)
        cur.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_tasks_status
            ON {SCHEMA}.processing_tasks(status)
        """)

        # ===== VERIFICATION CACHE (legacy compat for update_verification_cache) =====
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {SCHEMA}.verification_cache (
                doctor_id UUID PRIMARY KEY REFERENCES {SCHEMA}.doctors(doctor_id),
                cms_profile JSONB,
                compliance JSONB,
                cms_status VARCHAR(50) DEFAULT 'pending',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # ===== IDEMPOTENT MIGRATIONS for existing DBs (safe to re-run) =====
        # Add archived_filename if missing
        cur.execute(f"""
            ALTER TABLE {SCHEMA}.document_versions
            ADD COLUMN IF NOT EXISTS archived_filename VARCHAR(500)
        """)

        conn.commit()
        print(f"✅ Database initialized: schema '{SCHEMA}' with all tables ready")

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


# ===== PROVIDER IDENTITY CRUD =====

def upsert_provider_identity(doctor_id: str, data: Dict[str, Any]) -> None:
    """Insert or update the provider_identity row for a doctor."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(f"""
            INSERT INTO {SCHEMA}.provider_identity
                (doctor_id, first_name, last_name, gender, date_of_birth,
                 license_number, license_state, primary_taxonomy_code, primary_specialty)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (doctor_id) DO UPDATE SET
                first_name = EXCLUDED.first_name,
                last_name = EXCLUDED.last_name,
                gender = EXCLUDED.gender,
                date_of_birth = EXCLUDED.date_of_birth,
                license_number = EXCLUDED.license_number,
                license_state = EXCLUDED.license_state,
                primary_taxonomy_code = EXCLUDED.primary_taxonomy_code,
                primary_specialty = EXCLUDED.primary_specialty
        """, (
            doctor_id,
            data.get("first_name"), data.get("last_name"),
            data.get("gender"), data.get("date_of_birth"),
            data.get("license_number"), data.get("license_state"),
            data.get("primary_taxonomy_code"), data.get("primary_specialty")
        ))
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cur.close()
        conn.close()


def get_provider_identity(doctor_id: str) -> Optional[Dict[str, Any]]:
    """Return the provider_identity row for a doctor, or None."""
    conn = get_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute(
            f"SELECT * FROM {SCHEMA}.provider_identity WHERE doctor_id = %s",
            (doctor_id,)
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        cur.close()
        conn.close()


# ===== CMS PROVIDER DATA CRUD =====

def upsert_cms_provider_data(doctor_id: str, data: Dict[str, Any]) -> None:
    """Insert or update CMS provider data (replaces verification_cache for structured data)."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(f"""
            INSERT INTO {SCHEMA}.cms_provider_data
                (doctor_id, npi_number, enumeration_type, sole_proprietor,
                 practice_address_line1, practice_address_line2,
                 practice_city, practice_state, practice_zip, practice_country,
                 mailing_address_line1, mailing_address_line2,
                 mailing_city, mailing_state, mailing_zip, mailing_country,
                 phone, fax, npi_status, last_updated, cms_raw_json)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,CURRENT_TIMESTAMP,%s)
            ON CONFLICT (doctor_id) DO UPDATE SET
                npi_number = EXCLUDED.npi_number,
                enumeration_type = EXCLUDED.enumeration_type,
                sole_proprietor = EXCLUDED.sole_proprietor,
                practice_address_line1 = EXCLUDED.practice_address_line1,
                practice_address_line2 = EXCLUDED.practice_address_line2,
                practice_city = EXCLUDED.practice_city,
                practice_state = EXCLUDED.practice_state,
                practice_zip = EXCLUDED.practice_zip,
                practice_country = EXCLUDED.practice_country,
                mailing_address_line1 = EXCLUDED.mailing_address_line1,
                mailing_address_line2 = EXCLUDED.mailing_address_line2,
                mailing_city = EXCLUDED.mailing_city,
                mailing_state = EXCLUDED.mailing_state,
                mailing_zip = EXCLUDED.mailing_zip,
                mailing_country = EXCLUDED.mailing_country,
                phone = EXCLUDED.phone,
                fax = EXCLUDED.fax,
                npi_status = EXCLUDED.npi_status,
                last_updated = CURRENT_TIMESTAMP,
                cms_raw_json = EXCLUDED.cms_raw_json
        """, (
            doctor_id,
            data.get("npi_number"), data.get("enumeration_type"), data.get("sole_proprietor"),
            data.get("practice_address_line1"), data.get("practice_address_line2"),
            data.get("practice_city"), data.get("practice_state"),
            data.get("practice_zip"), data.get("practice_country"),
            data.get("mailing_address_line1"), data.get("mailing_address_line2"),
            data.get("mailing_city"), data.get("mailing_state"),
            data.get("mailing_zip"), data.get("mailing_country"),
            data.get("phone"), data.get("fax"), data.get("npi_status"),
            json.dumps(data.get("cms_raw_json") or data)
        ))
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cur.close()
        conn.close()


def get_cms_provider_data(doctor_id: str) -> Optional[Dict[str, Any]]:
    """Return CMS data row for a doctor, or None."""
    conn = get_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute(
            f"SELECT * FROM {SCHEMA}.cms_provider_data WHERE doctor_id = %s",
            (doctor_id,)
        )
        row = cur.fetchone()
        if row:
            r = dict(row)
            r["doctor_id"] = str(r["doctor_id"])
            if r.get("last_updated"):
                r["last_updated"] = r["last_updated"].isoformat()
            return r
        return None
    finally:
        cur.close()
        conn.close()


# ===== PROCESSING TASKS CRUD =====

def create_processing_task(
    doctor_id: str,
    task_type: str,
    document_type: Optional[str] = None,
    triggered_by: Optional[str] = None,
    metadata_json: Optional[Dict] = None
) -> int:
    """Create a new processing task. Returns task_id."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(f"""
            INSERT INTO {SCHEMA}.processing_tasks
                (doctor_id, task_type, document_type, status, started_at, triggered_by, metadata_json)
            VALUES (%s, %s::task_type_enum, %s, 'running'::task_status_enum, CURRENT_TIMESTAMP, %s, %s)
            RETURNING task_id
        """, (
            doctor_id, task_type, document_type, triggered_by,
            json.dumps(metadata_json or {})
        ))
        task_id = cur.fetchone()[0]
        conn.commit()
        return task_id
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cur.close()
        conn.close()


def complete_processing_task(task_id: int, success: bool = True, error_message: Optional[str] = None) -> None:
    """Mark a processing task as completed or failed."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        status = "completed" if success else "failed"
        cur.execute(f"""
            UPDATE {SCHEMA}.processing_tasks
            SET status = %s::task_status_enum,
                completed_at = CURRENT_TIMESTAMP,
                error_message = %s
            WHERE task_id = %s
        """, (status, error_message, task_id))
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cur.close()
        conn.close()


# ===== DOCUMENT SAVE (routes to correct table) =====

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


def _parse_date(val):
    """Normalize OCR date strings to ISO YYYY-MM-DD for DATE columns.
    Handles: MM/DD/YYYY, MM-DD-YYYY, YYYY-MM-DD, DD/MM/YYYY, Month DD YYYY, etc.
    Returns None if blank or unparseable to avoid DB crash."""
    from datetime import datetime
    if not val:
        return None
    val = str(val).strip()
    if not val or val.lower() in ("none", "n/a", "-", ""):
        return None
    for fmt in ("%m/%d/%Y", "%m-%d-%Y", "%Y-%m-%d", "%d/%m/%Y",
                "%B %d, %Y", "%b %d, %Y", "%d-%b-%Y", "%Y/%m/%d",
                "%m/%d/%y", "%m-%d-%y"):
        try:
            return datetime.strptime(val, fmt).date().isoformat()
        except ValueError:
            continue
    logger.warning(f"Could not parse date: '{val}' — storing as NULL")
    return None


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
                (doctor_id, filename, ein, legal_business_name, provider_name, first_name, last_name,
                 business_name, issue_date, form_type,
                 address_street, address_city, address_state, address_zip, address_country,
                 raw_ocr_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id""",
                (doctor_id, filename,
                 d.get("ein"), d.get("business_name") or d.get("legal_business_name"),
                 d.get("provider_name"), d.get("first_name"), d.get("last_name"),
                 d.get("business_name"), d.get("issue_date"), d.get("form_type"),
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
                 d.get("gender"), d.get("license_status"),
                 _parse_date(d.get("issue_date")),
                 _parse_date(d.get("expiration_date")),
                 addr["street"], addr["city"], addr["state"], addr["zip"], addr["country"],
                 contact["phone"], contact["email"], raw_json)
            )

        elif document_type == "malpractice_insurance":
            limits = d.get("coverage_limits", {}) or {}
            cur.execute(f"""
                INSERT INTO {SCHEMA}.malpractice_insurance
                (doctor_id, filename, policy_number, malpractice_insurer, provider_name,
                 effective_date, policy_expiration, specialty,
                 coverage_per_claim, coverage_aggregate,
                 address_street, address_city, address_state, address_zip, address_country,
                 contact_phone, contact_email, raw_ocr_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id""",
                (doctor_id, filename, d.get("policy_number"),
                 d.get("insurer_name") or d.get("malpractice_insurer"),
                 d.get("provider_name"),
                 d.get("effective_date") or None,
                 _parse_date(d.get("expiration_date") or d.get("policy_expiration")),
                 d.get("specialty"),
                 limits.get("per_claim") or d.get("coverage_per_claim"),
                 limits.get("aggregate") or d.get("coverage_aggregate"),
                 addr["street"], addr["city"], addr["state"], addr["zip"], addr["country"],
                 contact["phone"], contact["email"], raw_json)
            )

        elif document_type == "dea_certificate":
            schedules_val = d.get("schedules")
            if isinstance(schedules_val, list):
                schedules_val = ", ".join(schedules_val)
            cur.execute(f"""
                INSERT INTO {SCHEMA}.dea_certificate
                (doctor_id, filename, dea_number, dea_expiration, provider_name,
                 first_name, last_name, business_activity, issue_date, schedules, fee_paid,
                 address_street, address_city, address_state, address_zip, raw_ocr_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id""",
                (doctor_id, filename, d.get("dea_number"),
                 _parse_date(d.get("expiration_date") or d.get("dea_expiration")),
                 d.get("provider_name"), d.get("first_name"), d.get("last_name"),
                 d.get("business_activity"), d.get("issue_date"),
                 schedules_val, d.get("fee_paid"),
                 addr["street"], addr["city"], addr["state"], addr["zip"], raw_json)
            )

        elif document_type == "board_certification":
            cur.execute(f"""
                INSERT INTO {SCHEMA}.board_certification
                (doctor_id, filename, provider_name, certification_type, certifying_board,
                 certification_id, issue_date, expiration_date, board_cert_status, specialty_code,
                 raw_ocr_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id""",
                (doctor_id, filename, d.get("provider_name"), d.get("certification_type"),
                 d.get("certifying_board"), d.get("certification_id"),
                 _parse_date(d.get("issue_date")),
                 _parse_date(d.get("expiration_date")),
                 d.get("status") or d.get("board_cert_status"),
                 d.get("specialty_code"), raw_json)
            )

        else:
            print(f"⚠️ Unknown document type: {document_type}, skipping DB save")
            return None

        doc_id = cur.fetchone()[0]

        # Also upsert into central documents table for tracking
        cur.execute(f"""
            INSERT INTO {SCHEMA}.documents (doctor_id, document_type, filename)
            VALUES (%s, %s, %s)
        """, (doctor_id, document_type, filename))

        conn.commit()
        print(f"SUCCESS: Saved to {SCHEMA}.{document_type}: #{doc_id} for doctor {doctor_id[:8]}...")

        # Trigger cache update in the background
        try:
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

        # Also update the central documents table status (cast handled by search_path)
        cur.execute(f"""
            UPDATE {SCHEMA}.documents
            SET status = %s::document_status_enum,
                reviewed_at = CURRENT_TIMESTAMP,
                reviewed_by = %s
            WHERE doctor_id = %s AND document_type = %s
        """, (status if status in ('pending', 'approved', 'rejected', 'reupload_requested') else 'pending',
              reviewed_by, doctor_id, document_type))

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


# ===== DOCUMENT REVIEWS CRUD =====

def create_document_review(
    document_id: int,
    status: str,
    rejection_reason: Optional[str] = None,
    rejection_fields: Optional[str] = None,
    reviewed_by: Optional[str] = None
) -> int:
    """Create a document review entry. Returns review id."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(f"""
            INSERT INTO {SCHEMA}.document_reviews
                (document_id, status, rejection_reason, rejection_fields, reviewed_by, reviewed_at)
            VALUES (%s, %s::document_status_enum, %s, %s, %s, CURRENT_TIMESTAMP)
            RETURNING id
        """, (document_id, status, rejection_reason, rejection_fields, reviewed_by))
        review_id = cur.fetchone()[0]
        conn.commit()
        return review_id
    except Exception as e:
        conn.rollback()
        raise e
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

def archive_document(doctor_id: str, document_type: str, original_table: str, original_record_id: int, archived_filename: Optional[str] = None) -> int:
    """Archive a document version before re-upload. Returns archive record id."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(f"""
            INSERT INTO {SCHEMA}.document_versions
                (doctor_id, document_type, original_table, original_record_id, archived_filename)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
        """, (doctor_id, document_type, original_table, original_record_id, archived_filename))
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
    """Pre-compute verification results and store in verification_cache and cms_provider_data."""
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

        # 2. Extract fields for OCRPayload
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
            zip_code=first_val(lic.get("address_zip") if lic else None),
            tax_id=first_val(tax.get("ein") if tax else None),
            dea_number=first_val(dea.get("dea_number") if dea else None),
            malpractice_policy=first_val(mal.get("policy_number") if mal else None),
            board_certification=first_val(board.get("certification_type") if board else None),
            training_certifications=[]
        )

        engine = MatchingEngine()
        result = await engine.process_ocr_data(ocr_payload)

        # 4. Save to verification_cache (legacy)
        cms_profile = None
        compliance  = None
        cms_status  = "not_matched"

        if result.status == "success" and result.provider_profile:
            cms_profile = result.provider_profile.dict() if hasattr(result.provider_profile, "dict") else vars(result.provider_profile)
            compliance  = result.compliance.dict() if result.compliance and hasattr(result.compliance, "dict") else (vars(result.compliance) if result.compliance else None)
            cms_status  = "verified"

            # Also persist to structured cms_provider_data table
            try:
                upsert_cms_provider_data(doctor_id, {
                    "npi_number": cms_profile.get("npi") or cms_profile.get("number"),
                    "enumeration_type": cms_profile.get("enumeration_type"),
                    "npi_status": cms_profile.get("status"),
                    "practice_address_line1": cms_profile.get("address_1"),
                    "practice_city": cms_profile.get("city"),
                    "practice_state": cms_profile.get("state"),
                    "practice_zip": cms_profile.get("postal_code"),
                    "practice_country": cms_profile.get("country_code"),
                    "phone": cms_profile.get("telephone_number"),
                    "cms_raw_json": cms_profile,
                })
            except Exception as cms_err:
                print(f"WARNING: Could not persist cms_provider_data: {cms_err}")

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

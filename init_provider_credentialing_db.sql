-- =============================================
-- Provider Credentialing Database Initialization
-- =============================================

CREATE SCHEMA IF NOT EXISTS provider_credentialing;
SET search_path TO provider_credentialing;

-- =============================================
-- ENUM TYPES
-- =============================================

CREATE TYPE document_status_enum AS ENUM (
'pending',
'approved',
'rejected',
'reupload_requested'
);

CREATE TYPE task_status_enum AS ENUM (
'pending',
'running',
'completed',
'failed'
);

CREATE TYPE task_type_enum AS ENUM (
'OCR',
'CMS_LOOKUP',
'DOCUMENT_REVIEW',
'COMPLIANCE_CHECK'
);

-- =============================================
-- DOCTORS
-- =============================================

CREATE TABLE doctors (
    doctor_id UUID PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_doctors_email
ON doctors(email);

-- =============================================
-- PROVIDER IDENTITY
-- =============================================

CREATE TABLE provider_identity (
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
    REFERENCES doctors(doctor_id)
    ON DELETE CASCADE
);

-- =============================================
-- CMS PROVIDER DATA
-- =============================================

CREATE TABLE cms_provider_data (
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
    REFERENCES doctors(doctor_id)
    ON DELETE CASCADE
);

CREATE INDEX idx_cms_npi
ON cms_provider_data(npi_number);

-- =============================================
-- DOCUMENTS
-- =============================================

CREATE TABLE documents (
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
    REFERENCES doctors(doctor_id)
    ON DELETE CASCADE
);

CREATE INDEX idx_documents_doctor
ON documents(doctor_id);

CREATE INDEX idx_documents_type
ON documents(document_type);

-- =============================================
-- TAX ID
-- =============================================

CREATE TABLE tax_id (
    id SERIAL PRIMARY KEY,
    doctor_id UUID,

    ein VARCHAR(50),
    legal_business_name VARCHAR(255),

    address_street VARCHAR(255),
    address_city VARCHAR(100),
    address_state VARCHAR(50),
    address_zip VARCHAR(20),
    address_country VARCHAR(50),

    raw_ocr_json JSONB,

    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_tax_id_doctor
    FOREIGN KEY (doctor_id)
    REFERENCES doctors(doctor_id)
    ON DELETE CASCADE
);

CREATE INDEX idx_tax_doctor
ON tax_id(doctor_id);

-- =============================================
-- STATE MEDICAL LICENSE
-- =============================================

CREATE TABLE state_medical_license (
    id SERIAL PRIMARY KEY,
    doctor_id UUID,

    license_status VARCHAR(100),

    issue_date DATE,
    expiration_date DATE,

    contact_phone VARCHAR(50),
    contact_email VARCHAR(255),

    raw_ocr_json JSONB,

    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_state_license_doctor
    FOREIGN KEY (doctor_id)
    REFERENCES doctors(doctor_id)
    ON DELETE CASCADE
);

CREATE INDEX idx_license_doctor
ON state_medical_license(doctor_id);

-- =============================================
-- DEA CERTIFICATE
-- =============================================

CREATE TABLE dea_certificate (
    id SERIAL PRIMARY KEY,
    doctor_id UUID,

    dea_number VARCHAR(50),
    dea_expiration DATE,

    business_activity VARCHAR(255),

    raw_ocr_json JSONB,

    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_dea_doctor
    FOREIGN KEY (doctor_id)
    REFERENCES doctors(doctor_id)
    ON DELETE CASCADE
);

CREATE INDEX idx_dea_doctor
ON dea_certificate(doctor_id);

-- =============================================
-- MALPRACTICE INSURANCE
-- =============================================

CREATE TABLE malpractice_insurance (
    id SERIAL PRIMARY KEY,
    doctor_id UUID,

    malpractice_insurer VARCHAR(255),

    policy_number VARCHAR(100),

    policy_expiration DATE,

    coverage_per_claim VARCHAR(100),

    coverage_aggregate VARCHAR(100),

    raw_ocr_json JSONB,

    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_malpractice_doctor
    FOREIGN KEY (doctor_id)
    REFERENCES doctors(doctor_id)
    ON DELETE CASCADE
);

CREATE INDEX idx_malpractice_doctor
ON malpractice_insurance(doctor_id);

-- =============================================
-- BOARD CERTIFICATION
-- =============================================

CREATE TABLE board_certification (
    id SERIAL PRIMARY KEY,
    doctor_id UUID,

    certifying_board VARCHAR(255),

    certification_id VARCHAR(100),

    issue_date DATE,

    expiration_date DATE,

    board_cert_status VARCHAR(100),

    raw_ocr_json JSONB,

    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_board_cert_doctor
    FOREIGN KEY (doctor_id)
    REFERENCES doctors(doctor_id)
    ON DELETE CASCADE
);

CREATE INDEX idx_board_doctor
ON board_certification(doctor_id);

-- =============================================
-- DOCUMENT REVIEWS
-- =============================================

CREATE TABLE document_reviews (
    id SERIAL PRIMARY KEY,

    document_id INTEGER,

    status document_status_enum,

    rejection_reason TEXT,

    rejection_fields TEXT,

    reviewed_by VARCHAR(100),

    reviewed_at TIMESTAMP,

    CONSTRAINT fk_document_reviews_doc
    FOREIGN KEY (document_id)
    REFERENCES documents(document_id)
    ON DELETE CASCADE
);

-- =============================================
-- NOTIFICATIONS
-- =============================================

CREATE TABLE notifications (
    id SERIAL PRIMARY KEY,

    recipient_role VARCHAR(20),

    recipient_id VARCHAR(100),

    notification_type VARCHAR(100),

    message TEXT,

    metadata TEXT DEFAULT '{}',

    is_read BOOLEAN DEFAULT FALSE,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_notifications_recipient
ON notifications(recipient_role, recipient_id);

-- =============================================
-- DOCUMENT VERSIONS
-- =============================================

CREATE TABLE document_versions (
    id SERIAL PRIMARY KEY,

    doctor_id UUID,

    document_type VARCHAR(100),

    original_record_id INTEGER,

    archived_filename VARCHAR(500),

    archived_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_document_versions_doctor
    FOREIGN KEY (doctor_id)
    REFERENCES doctors(doctor_id)
    ON DELETE CASCADE
);

-- =============================================
-- PROCESSING TASKS
-- =============================================

CREATE TABLE processing_tasks (
    task_id SERIAL PRIMARY KEY,

    doctor_id UUID,

    task_type task_type_enum,

    document_type VARCHAR(100),

    status task_status_enum,

    started_at TIMESTAMP,

    completed_at TIMESTAMP,

    triggered_by VARCHAR(50),

    error_message TEXT,

    metadata_json JSONB,

    CONSTRAINT fk_processing_tasks_doctor
    FOREIGN KEY (doctor_id)
    REFERENCES doctors(doctor_id)
    ON DELETE CASCADE
);

CREATE INDEX idx_tasks_doctor
ON processing_tasks(doctor_id);

CREATE INDEX idx_tasks_status
ON processing_tasks(status);
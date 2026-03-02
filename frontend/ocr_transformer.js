/**
 * OCR Transformer - Converts raw OCR response into a flat structure
 * that the backend matching engine can consume.
 */

// Credentials that should NOT be treated as names
const CREDENTIAL_PATTERNS = ['dr.', 'dr', 'md', 'do', 'phd', 'dds', 'dmd', 'rn', 'np', 'pa', 'pa-c'];

function isCredential(value) {
    if (!value) return false;
    return CREDENTIAL_PATTERNS.includes(value.trim().toLowerCase());
}

function extractName(data, result) {
    // Try explicit first_name / last_name first
    if (data.first_name && !isCredential(data.first_name)) {
        result.first_name = result.first_name || data.first_name;
    }
    if (data.last_name && !isCredential(data.last_name)) {
        result.last_name = result.last_name || data.last_name;
    }

    // Fallback: split provider_name
    if (!result.first_name && data.provider_name) {
        const name = data.provider_name.trim();
        // Remove leading credentials like "Dr. "
        const cleaned = name.replace(/^(Dr\.?\s+|MD\s+|DO\s+)/i, '');
        const parts = cleaned.split(/\s+/);
        if (parts.length >= 2) {
            result.first_name = result.first_name || parts[0];
            result.last_name = result.last_name || parts.slice(1).join(' ');
        } else if (parts.length === 1 && !result.last_name) {
            result.last_name = parts[0];
        }
    }
}

function extractAddress(data, result) {
    if (data.address) {
        result.city = result.city || data.address.city || null;
        result.state = result.state || data.address.state || null;
        result.zip_code = result.zip_code || data.address.zip_code || null;
        result.address_line_1 = result.address_line_1 || data.address.address_line_1 || data.address.street || null;
    }
    // Top-level fallbacks
    if (data.city) result.city = result.city || data.city;
    if (data.state) result.state = result.state || data.state;
    if (data.zip_code) result.zip_code = result.zip_code || data.zip_code;
}

function transformOCRResponse(ocrData) {
    const result = {};

    // Unwrap nested response from backend
    const data = ocrData.extracted_data || ocrData.data || ocrData;
    const docType = data.document_type || ocrData.document_type;

    console.log('📄 Transforming OCR data, document_type:', docType);
    console.log('📄 Raw data keys:', Object.keys(data));

    // --- STATE MEDICAL LICENSE ---
    if (docType === 'state_medical_license' || data.license_number) {
        console.log('Processing: State Medical License');
        result.license_number = data.license_number || null;
        extractName(data, result);
        extractAddress(data, result);

        if (data.contact) {
            result.phone = data.contact.phone_number ? data.contact.phone_number.replace(/[^0-9]/g, '') : null;
        }
        if (data.date_of_birth) result.date_of_birth = data.date_of_birth;
    }

    // --- TAX ID / IRS LETTER ---
    if (docType === 'tax_id' || data.ein) {
        console.log('Processing: Tax ID / IRS Letter');
        result.tax_id = data.ein || data.tax_id || null;
        extractName(data, result);
        if (data.business_name) result.business_name = data.business_name;
        extractAddress(data, result);
    }

    // --- FEDERAL DEA CERTIFICATE ---
    if (docType === 'dea_certificate' || data.dea_number) {
        console.log('Processing: DEA Certificate');
        result.dea_number = data.dea_number || null;
        result.dea_expiration = data.expiration_date || null;
        extractName(data, result);
        extractAddress(data, result);
    }

    // --- MALPRACTICE INSURANCE ---
    if (docType === 'malpractice_insurance' || data.policy_number) {
        console.log('Processing: Malpractice Insurance');
        result.malpractice_policy = data.policy_number || null;
        result.insurer_name = data.insurer_name || null;
        result.malpractice_expiration = data.expiration_date || null;
        extractName(data, result);
        extractAddress(data, result);
    }

    // --- BOARD CERTIFICATION ---
    if (docType === 'board_certification' || data.certifying_board) {
        console.log('Processing: Board Certification');
        result.board_certification = data.certification_type || data.status || null;
        result.board_status = data.status || null;
        extractName(data, result);
    }

    // NPI if present anywhere
    if (data.npi) result.npi = data.npi;

    console.log('✅ Transformed result:', result);
    return result;
}

/**
 * Smart merge - prefers non-empty, non-credential values.
 * Use this instead of Object.assign to avoid overwriting good data.
 */
function smartMerge(target, source) {
    for (const [key, value] of Object.entries(source)) {
        if (value === null || value === undefined || value === '') continue;

        // For name fields, skip credential values
        if ((key === 'first_name' || key === 'last_name') && isCredential(value)) continue;

        // Only set if target doesn't have a value, or target has a credential
        if (!target[key] || target[key] === '' ||
            ((key === 'first_name' || key === 'last_name') && isCredential(target[key]))) {
            target[key] = value;
        }
    }
    return target;
}

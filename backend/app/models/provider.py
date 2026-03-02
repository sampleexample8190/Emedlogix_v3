from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any

class OCRPayload(BaseModel):
    # Identity fields
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    state: Optional[str] = None
    city: Optional[str] = None
    zip_code: Optional[str] = None
    
    # Strong identifiers
    npi: Optional[str] = None
    license_number: Optional[str] = None
    
    # Business & credentialing information (from 7 documents)
    tax_id: Optional[str] = None
    dea_number: Optional[str] = None
    malpractice_policy: Optional[str] = None
    board_certification: Optional[str] = None
    training_certifications: Optional[List[str]] = []
    
    # Metadata
    raw_text: Optional[str] = None

class ComplianceStatus(BaseModel):
    is_excluded: bool = False
    exclusion_details: Optional[str] = None
    sam_status: str = "Unknown"
    verification_source: str = "SAM.gov"

class ProviderProfile(BaseModel):
    npi: str
    first_name: str
    last_name: str
    credential: Optional[str] = None
    gender: Optional[str] = None
    sole_proprietor: Optional[str] = None
    status: Optional[str] = None # A=Active, etc.
    enumeration_date: Optional[str] = None
    last_updated: Optional[str] = None
    
    specialty: Optional[str] = None
    taxonomy_code: Optional[str] = None
    license_number: Optional[str] = None
    
    phone: Optional[str] = None
    fax_number: Optional[str] = None
    
    address_line_1: Optional[str] = None
    address_line_2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    place_of_birth: Optional[str] = None
    zip_code: Optional[str] = None
    country_name: Optional[str] = None
    
    enumeration_type: Optional[str] = None
    license_state: Optional[str] = None

    # Mailing Address
    mailing_address_1: Optional[str] = None
    mailing_address_2: Optional[str] = None
    mailing_city: Optional[str] = None
    mailing_state: Optional[str] = None
    mailing_zip: Optional[str] = None
    mailing_country: Optional[str] = None
    
    addresses: List[Dict[str, Any]] = []
    practice_locations: List[Dict[str, Any]] = []
    taxonomy_description: Optional[str] = None

class AutoFillResponse(BaseModel):
    status: str
    match_confidence: str # HIGH, MEDIUM, LOW
    provider_profile: Optional[ProviderProfile] = None
    compliance: Optional[ComplianceStatus] = None
    business_details: Optional[Dict[str, Any]] = None  # Business information from OCR
    reason: Optional[str] = None
    suggested_matches: Optional[List[ProviderProfile]] = None

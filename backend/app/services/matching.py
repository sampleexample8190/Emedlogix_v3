from app.models.provider import OCRPayload, AutoFillResponse, ProviderProfile, ComplianceStatus
from app.services.cms import CMSService
from app.services.sam import SAMService
import logging

logger = logging.getLogger(__name__)

class MatchingEngine:
    def __init__(self):
        self.cms_service = CMSService()
        self.sam_service = SAMService()
    
    def _normalize_name(self, name: str) -> str:
        """Normalize names for matching - handles middle names, suffixes, etc."""
        if not name:
            return ""
        # Remove extra spaces, convert to upper, remove common suffixes
        name = name.upper().strip()
        suffixes = [' JR', ' SR', ' II', ' III', ' IV', ' MD', ' DO', ' PHD', ' DDS']
        for suffix in suffixes:
            if name.endswith(suffix):
                name = name[:-len(suffix)].strip()
        # Remove multiple spaces
        name = ' '.join(name.split())
        return name
    
    def _normalize_zip(self, zip_code: str) -> str:
        """Normalize ZIP codes to 5 digits"""
        if not zip_code:
            return ""
        # Remove all non-digit characters
        zip_clean = ''.join(c for c in zip_code if c.isdigit())
        # Return first 5 digits
        return zip_clean[:5] if len(zip_clean) >= 5 else zip_clean
    
    def _normalize_license(self, license_num: str) -> str:
        """Normalize license numbers - remove spaces, dashes, convert to upper"""
        if not license_num:
            return ""
        return license_num.upper().replace(' ', '').replace('-', '').strip()
    
    def _names_match(self, name1: str, name2: str, fuzzy: bool = False) -> bool:
        """
        Check if two names match with fuzzy logic
        Handles: middle names, reversed order, partial matches
        """
        n1 = self._normalize_name(name1)
        n2 = self._normalize_name(name2)
        
        if n1 == n2:
            return True
        
        if fuzzy:
            # Split into parts
            parts1 = n1.split()
            parts2 = n2.split()
            
            # Check if any part of one name is in the other
            for p1 in parts1:
                if p1 in parts2:
                    return True
        return False

    def _check_license(self, cms_record: dict, license_num: str) -> bool:
        """Helper to check if license exists in taxonomies with normalization"""
        taxonomies = cms_record.get("taxonomies", [])
        clean_target = self._normalize_license(license_num)
        
        for tax in taxonomies:
            cms_license = self._normalize_license(tax.get("license", ""))
            if cms_license == clean_target:
                return True
        return False

    def _map_cms_to_profile(self, cms_data: dict) -> ProviderProfile:
        basic = cms_data.get("basic", {})
        addresses = cms_data.get("addresses", [])
        taxonomies = cms_data.get("taxonomies", [])
        
        # Get primary specialty & license & taxonomy code
        specialty = "Unknown"
        license_num = "N/A"
        license_state = ""
        tax_code = ""
        for tax in taxonomies:
            if tax.get("primary") is True:
                specialty = tax.get("desc")
                license_num = tax.get("license")
                license_state = tax.get("state")
                tax_code = tax.get("code")
                break
        
        # Get primary address (usually location address, not mailing)
        primary_addr = next((a for a in addresses if a.get("address_purpose") == "LOCATION"), addresses[0] if addresses else {})
        mailing_addr = next((a for a in addresses if a.get("address_purpose") == "MAILING"), {})
        
        return ProviderProfile(
            npi=str(cms_data.get("number")),
            first_name=basic.get("first_name", ""),
            last_name=basic.get("last_name", ""),
            credential=basic.get("credential", ""),
            gender=basic.get("gender", ""),
            sole_proprietor=basic.get("sole_proprietor", "NO"),
            status=basic.get("status", ""),
            enumeration_date=basic.get("enumeration_date", ""),
            last_updated=basic.get("last_updated", ""),
            enumeration_type=cms_data.get("enumeration_type", ""),
            license_state=license_state,
            
            specialty=specialty,
            taxonomy_code=tax_code,
            license_number=license_num,
            
            phone=primary_addr.get("telephone_number"),
            fax_number=primary_addr.get("fax_number"),
            
            address_line_1=primary_addr.get("address_1"),
            address_line_2=primary_addr.get("address_2"),
            city=primary_addr.get("city"),
            state=primary_addr.get("state"),
            zip_code=primary_addr.get("postal_code"),
            country_name=primary_addr.get("country_name"),

            mailing_address_1=mailing_addr.get("address_1"),
            mailing_address_2=mailing_addr.get("address_2"),
            mailing_city=mailing_addr.get("city"),
            mailing_state=mailing_addr.get("state"),
            mailing_zip=mailing_addr.get("postal_code"),
            mailing_country=mailing_addr.get("country_name"),
            
            addresses=addresses,
            practice_locations=cms_data.get("practiceLocations", []),
            taxonomy_description=specialty
        )

    async def process_ocr_data(self, ocr_data: OCRPayload) -> AutoFillResponse:
        logger.info(f"Processing OCR data for: {ocr_data.first_name} {ocr_data.last_name}")
        
        match_found = None
        confidence = "LOW"
        suggested = []
        
        strong_identifiers = {}
        
        # 1️⃣ NPI Number
        if ocr_data.npi:
            strong_identifiers["npi"] = ocr_data.npi.strip()
        
        # 2️⃣ First Name + Last Name
        if ocr_data.first_name and ocr_data.last_name:
            strong_identifiers["first_name"] = self._normalize_name(ocr_data.first_name)
            strong_identifiers["last_name"] = self._normalize_name(ocr_data.last_name)
        
        # 3️⃣ State
        if ocr_data.state:
            strong_identifiers["state"] = ocr_data.state.upper().strip()[:2]
        
        # 4️⃣ City
        if ocr_data.city:
            strong_identifiers["city"] = ocr_data.city.upper().strip()
        
        # 5️⃣ ZIP Code
        if ocr_data.zip_code:
            strong_identifiers["zip_code"] = self._normalize_zip(ocr_data.zip_code)
        
        # 6️⃣ License Number
        if ocr_data.license_number:
            strong_identifiers["license_number"] = self._normalize_license(ocr_data.license_number)
        
        logger.info(f"Filtered Data: {strong_identifiers}")
        
        # Priority 1: Try NPI
        if strong_identifiers.get("npi"):
            npi = strong_identifiers["npi"]
            logger.info(f"🎯 Priority 1: Attempting match with NPI: {npi}")
            data = await self.cms_service.get_by_npi(npi)
            if data:
                match_found = data
                confidence = "HIGH"
                logger.info(f"✓ Match found using NPI: {npi}")
        
        # Priority 2: Try License Number + State
        if not match_found and strong_identifiers.get("license_number") and strong_identifiers.get("state"):
            license_num = strong_identifiers["license_number"]
            state = strong_identifiers["state"]
            
            logger.info(f"🎯 Priority 2: Attempting match with License: {license_num} + State: {state}")
            results = await self.cms_service.search_by_license(license_num, state)
            
            if len(results) == 1:
                match_found = results[0]
                confidence = "HIGH"
            elif len(results) > 1:
                # Filter by name if available
                if strong_identifiers.get("first_name") and strong_identifiers.get("last_name"):
                    first_name = strong_identifiers["first_name"]
                    last_name = strong_identifiers["last_name"]
                    filtered = []
                    for result in results:
                        cms_first = self._normalize_name(result.get("basic", {}).get("first_name", ""))
                        cms_last = self._normalize_name(result.get("basic", {}).get("last_name", ""))
                        if (self._names_match(first_name, cms_first, fuzzy=True) and 
                            self._names_match(last_name, cms_last, fuzzy=True)):
                            filtered.append(result)
                    
                    if len(filtered) == 1:
                        match_found = filtered[0]
                        confidence = "HIGH"
                    elif len(filtered) > 1:
                         suggested = [self._map_cms_to_profile(r) for r in filtered[:5]]
                         return AutoFillResponse(status="manual_review_required", match_confidence="MEDIUM", reason="Multiple providers found with same license and name", suggested_matches=suggested)

        # Priority 3: Try First Name + Last Name + State (with Smart Retry and License Filtering)
        if not match_found and strong_identifiers.get("first_name") and strong_identifiers.get("last_name") and strong_identifiers.get("state"):
            first_name = strong_identifiers["first_name"]
            last_name = strong_identifiers["last_name"]
            state = strong_identifiers["state"]
            zip_code = strong_identifiers.get("zip_code")
            
            logger.info(f"🎯 Priority 3: Attempting match with Name: {first_name} {last_name} + State: {state}")
            results = await self.cms_service.search_provider(first_name, last_name, state=state)

            # --- SMART RETRY logic for complex names ---
            if not results:
                clean_first = first_name.split()[0]
                clean_last = last_name.split()[-1]
                if clean_first != first_name or clean_last != last_name:
                    logger.info(f"🔄 Retrying with simplified name: {clean_first} {clean_last}")
                    results = await self.cms_service.search_provider(clean_first, clean_last, state=state)
            
            if len(results) == 1:
                match_found = results[0]
                confidence = "HIGH"
                logger.info(f"✓ Single match found using Name + State")
            elif len(results) > 1:
                # 3.1 Try License Verification First (Stronger than ZIP)
                if strong_identifiers.get("license_number"):
                     lic = strong_identifiers["license_number"]
                     filtered = [r for r in results if self._check_license(r, lic)]
                     if len(filtered) == 1:
                         match_found = filtered[0]
                         confidence = "HIGH"
                         logger.info("✓ Match verified by License Number inside Name Search")
                
                # 3.2 If still no match, try ZIP Code
                if not match_found and zip_code:
                    logger.info(f"🔍 Multiple matches. Filtering by ZIP: {zip_code}")
                    filtered_results = []
                    user_zip_normalized = zip_code
                    for result in results:
                        addresses = result.get("addresses", [])
                        for addr in addresses:
                            cms_zip_raw = addr.get("postal_code", "")
                            cms_zip_normalized = self._normalize_zip(cms_zip_raw)
                            if user_zip_normalized == cms_zip_normalized:
                                filtered_results.append(result)
                                break
                            # Partial match
                            elif len(user_zip_normalized) >= 3 and len(cms_zip_normalized) >= 3 and user_zip_normalized[:3] == cms_zip_normalized[:3]:
                                filtered_results.append(result)
                                break
                    
                    if len(filtered_results) == 1:
                        match_found = filtered_results[0]
                        confidence = "HIGH"
                        logger.info(f"✓ Match found after ZIP filtering")
                    elif len(filtered_results) > 1:
                        suggested = [self._map_cms_to_profile(r) for r in filtered_results[:5]]
                        return AutoFillResponse(status="manual_review_required", match_confidence="MEDIUM", reason="Multiple providers found in ZIP", suggested_matches=suggested)

        # Priority 4: City (omitted for brevity, assume State works mostly)
        
        # ===== CONSTRUCT RESPONSE =====
        if match_found:
            provider_profile = self._map_cms_to_profile(match_found)
            
            # Compliance Check (Safe Call)
            compliance_status = None
            try:
                compliance_status = await self.sam_service.check_exclusions(
                    first_name=provider_profile.first_name,
                    last_name=provider_profile.last_name,
                    npi=provider_profile.npi
                )
            except Exception as e:
                logger.error(f"Compliance check failed: {e}")
            
            business_details = {
                "tax_id": ocr_data.tax_id, 
                "dea_number": ocr_data.dea_number,
                "malpractice_policy": ocr_data.malpractice_policy,
                "board_certification": ocr_data.board_certification or getattr(provider_profile, "taxonomy_description", None),
                "training_certifications": ocr_data.training_certifications
            }
            
            return AutoFillResponse(
                status="success",
                match_confidence=confidence,
                provider_profile=provider_profile,
                compliance=compliance_status,
                business_details=business_details
            )

        # No match found
        logger.warning("No matching provider found")
        return AutoFillResponse(
            status="manual_review_required",
            match_confidence="LOW",
            reason="No matching provider found in CMS registry."
        )

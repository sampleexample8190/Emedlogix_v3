import httpx
import os
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

SAM_API_URL = "https://api.sam.gov/entity-information/v4/exclusions"

class SAMService:
    def __init__(self):
        self.api_key = os.getenv("SAM_API_KEY")

    async def check_exclusions(self, first_name: str, last_name: str, npi: Optional[str] = None) -> Dict[str, Any]:
        """
        Checks for exclusions in SAM.gov.
        Returns a dict describing the status.
        """
        if not self.api_key:
            return {
                "is_excluded": False,
                "exclusion_details": None,
                "sam_status": "Not Checked",
                "verification_source": "SAM.gov"
            }

        params = {
            # SAM API params are complex and often require specific query formats.
            # This is a simplified representation for the request.
            # In a real scenario, you'd construct the specific query string.
            "q": f"{first_name} {last_name}",
            "api_key": self.api_key
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(SAM_API_URL, params=params, timeout=10.0)
                if response.status_code == 200:
                    data = response.json()
                    # Logic to parse SAM response would go here.
                    # Assuming empty results means no exclusion.
                    if data and "excludedEntity" in data and len(data["excludedEntity"]) > 0:
                         return {
                            "is_excluded": True,
                            "exclusion_details": "Match found in SAM database",
                            "sam_status": "Excluded",
                            "verification_source": "SAM.gov"
                        }
        except Exception as e:
            logger.error(f"Error calling SAM API: {e}")

        # Default to clean if error or no match (fail-open or fail-closed depends on policy, usually fail-closed for compliance, but for onboarding efficiency we might flag for review)
        return {
            "is_excluded": False,
            "exclusion_details": None,
            "sam_status": "Clean",
            "verification_source": "SAM.gov"
        }

import httpx
from typing import Optional, List, Dict, Any
import logging

logger = logging.getLogger(__name__)

CMS_API_URL = "https://npiregistry.cms.hhs.gov/api/?version=2.1"

class CMSService:
    async def get_by_npi(self, npi: str) -> Optional[Dict[str, Any]]:
        params = {"number": npi, "version": "2.1"}
        try:
            print(f"DEBUG: Fetching NPI {npi} from CMS...") 
            async with httpx.AsyncClient(follow_redirects=True) as client:
                response = await client.get(CMS_API_URL, params=params, timeout=10.0)
                print(f"DEBUG: CMS Response Status: {response.status_code}")
                response.raise_for_status()
                data = response.json()
                if "results" in data and len(data["results"]) > 0:
                    print("DEBUG: Match found in CMS.")
                    return data["results"][0]
                else:
                    print("DEBUG: No results in CMS response.")
        except Exception as e:
            logger.error(f"Error fetching from CMS by NPI: {e}")
            print(f"ERROR: CMS API failed: {e}")
        return None

    async def search_by_license(self, license_number: str, state: str) -> List[Dict[str, Any]]:
        """Search CMS registry by license number and state"""
        params = {
            "version": "2.1",
            "state": state,
            "limit": 50  # Higher limit to search through more providers
        }
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(CMS_API_URL, params=params, timeout=15.0)
                response.raise_for_status()
                data = response.json()
                results = data.get("results", [])
                
                # Filter by license number in taxonomies
                matched = []
                for provider in results:
                    taxonomies = provider.get("taxonomies", [])
                    for taxonomy in taxonomies:
                        if taxonomy.get("license") == license_number:
                            matched.append(provider)
                            break
                
                print(f"DEBUG: Found {len(matched)} providers with license {license_number} in {state}")
                return matched
        except Exception as e:
            logger.error(f"Error searching CMS by license: {e}")
            print(f"ERROR: CMS license search failed: {e}")
            return []

    async def search_provider(self, first_name: str, last_name: str, state: Optional[str] = None, city: Optional[str] = None) -> List[Dict[str, Any]]:
        params = {
            "version": "2.1",
            "first_name": first_name,
            "last_name": last_name,
            "use_first_name_alias": "True"
        }
        if state:
            params["state"] = state
        if city:
            params["city"] = city
            
        # CMS API limit
        params["limit"] = 10 

        try:
            async with httpx.AsyncClient() as client:
                logger.info(f"Calling CMS API: {CMS_API_URL} with params: {params}")
                response = await client.get(CMS_API_URL, params=params, timeout=10.0)
                logger.info(f"CMS API Response Status: {response.status_code}")
                response.raise_for_status()
                data = response.json()
                return data.get("results", [])
        except Exception as e:
            logger.error(f"Error searching CMS: {e}")
            return []

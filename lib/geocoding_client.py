import json
import requests
from typing import Dict, Any, Optional


class GeocodingClient:
    """
    Client for reverse geocoding coordinates (lat, lon) into human-readable place names
    and location details required by Polarsteps /api/locations.
    """

    def __init__(
        self,
        base_url: str = "https://nominatim.openstreetmap.org/reverse",
        user_agent: str = "PolarstepsImporter/1.0",
        verbose: bool = False
    ):
        self.base_url = base_url
        self.verbose = verbose
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": user_agent,
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9"
        })

    def get_location_details(self, lat: float, lon: float, timeout: int = 10) -> Dict[str, Any]:
        """
        Retrieves location details based on latitude and longitude (Reverse Geocoding).
        Returns fields structured for Polarsteps /api/locations:
          - country_code (e.g. 'ES')
          - name (e.g. 'Sant Feliu de Llobregat')
          - detail (e.g. 'Spain')
          - full_detail (e.g. 'Sant Feliu de Llobregat, Barcelona, Spain')
        """
        params = {
            "lat": lat,
            "lon": lon,
            "format": "jsonv2",
            "addressdetails": 1
        }
        
        print(f"🌍 [GeocodingAPI] Fetching details for coordinates: lat={lat}, lon={lon}...")
        if self.verbose:
            print(f"[RQ Payload] GET {self.base_url}:\n{json.dumps(params, indent=2)}")

        try:
            response = self.session.get(self.base_url, params=params, timeout=timeout)
            if self.verbose:
                print(f"[RS Payload] {response.status_code} {self.base_url}:\n{response.text}\n")
            response.raise_for_status()
            data = response.json()
            
            address = data.get("address", {})
            country_code = address.get("country_code", "").upper()
            country = address.get("country", "")
            
            # Extract city / town / village name
            place_name = (
                address.get("city")
                or address.get("town")
                or address.get("village")
                or address.get("municipality")
                or address.get("suburb")
                or data.get("name")
                or (data.get("display_name", "").split(",")[0] if data.get("display_name") else "")
                or f"Location ({lat:.4f}, {lon:.4f})"
            )
            
            # Region or province
            region = (
                address.get("province")
                or address.get("state")
                or address.get("county")
                or ""
            )

            # Build full_detail matching Polarsteps format: "Place, Region, Country"
            detail_components = [place_name]
            if region and region != place_name:
                detail_components.append(region)
            if country and country not in detail_components:
                detail_components.append(country)
            full_detail = ", ".join(detail_components) if detail_components else data.get("display_name", "")

            result = {
                "lat": lat,
                "lon": lon,
                "name": place_name,
                "resolved_name": place_name,
                "country_code": country_code,
                "detail": country,
                "country": country,
                "full_detail": full_detail,
                "raw": data
            }
            print(f"✅ [GeocodingAPI] Resolved: {place_name}, {full_detail}")
            return result

        except Exception as err:
            print(f"❌ [GeocodingAPI] Failed to fetch external geocoding details for ({lat}, {lon}): {err}")
            raise

import json
import os
import requests
from typing import Dict, Any, Optional

class PolarstepsAPIClient:
    """
    Client handling REST API requests to Polarsteps (browser-emulated).
    """
    def __init__(
        self,
        remember_token: Optional[str] = None,
        session_cookie: Optional[str] = None,
        username: Optional[str] = None,
        verbose: bool = False
    ):
        self.base_url = "https://www.polarsteps.com"
        self.verbose = verbose
        
        # Load from environment if not explicitly passed
        self.remember_token = remember_token or os.getenv("POLARSTEPS_REMEMBER_TOKEN")
        self.session_cookie = session_cookie or os.getenv("POLARSTEPS_SESSION")
        self.username = username or os.getenv("POLARSTEPS_USERNAME", "NikodemJura")
        
        self.session = requests.Session()
        
        # Set required request headers
        self.session.headers.update({
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Content-Type": "application/json",
            "Origin": self.base_url,
            "Referer": f"{self.base_url}/{self.username}",
            "polarsteps-api-version": "68",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
        })
        
        # Inject required cookies into the session jar
        if self.remember_token:
            self.session.cookies.set("remember_token", self.remember_token, domain="www.polarsteps.com")
        if self.session_cookie:
            self.session.cookies.set("session", self.session_cookie, domain="www.polarsteps.com")

    def _send_request(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None
    ) -> requests.Response:
        """Sends an HTTP request with optional verbose logging of RQ/RS payloads."""
        if self.verbose:
            payload = json_data or params
            if payload:
                print(f"[RQ Payload] {method} {url}:\n{json.dumps(payload, indent=2)}")

        response = self.session.request(method, url, headers=headers, params=params, json=json_data)

        if self.verbose:
            print(f"[RS Payload] {response.status_code} {url}:\n{response.text}\n")

        return response

    def _handle_response(self, response: requests.Response) -> Dict[str, Any]:
        """Helper to process HTTP responses and catch errors."""
        try:
            response.raise_for_status()
            return response.json() if response.content else {}
        except requests.exceptions.HTTPError as e:
            print(f"❌ Polarsteps HTTP Error: {e} | Response: {response.text}")
            raise
        except ValueError:
            print("❌ Error: Response body is not valid JSON.")
            return {"raw_text": response.text}

    def create_trip(self, trip_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Creates a new trip using the /api/trips endpoint.
        """
        endpoint = f"{self.base_url}/api/trips"
        self.session.headers["polarsteps-api-version"] = "68"
        
        print(f"➡️ [Polarsteps] Sending POST request to create trip: '{trip_data.get('name')}'")
        response = self._send_request("POST", endpoint, json_data=trip_data)
        return self._handle_response(response)

    def create_location_object(
        self,
        location_data: Dict[str, Any],
        trip_id: Optional[int] = None,
        trip_slug: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Creates a location object in Polarsteps via POST /api/locations.
        Uses polarsteps-api-version: 13 as required by this endpoint.
        """
        endpoint = f"{self.base_url}/api/locations"
        headers = {"polarsteps-api-version": "13"}
        
        slug = trip_slug or "summer-alps-expedition"
        if trip_id:
            self.session.headers["Referer"] = f"{self.base_url}/{self.username}/{trip_id}-{slug}?mode=track"

        print(f"➡️ [Polarsteps] Creating location object: '{location_data.get('name')}' at lat={location_data.get('lat')}, lon={location_data.get('lon')}")
        response = self._send_request("POST", endpoint, headers=headers, json_data=location_data)
        return self._handle_response(response)

    def get_timezone(self, lat: float, lon: float) -> Dict[str, Any]:
        """
        Fetches timezone metadata via GET /api/timezone.
        Uses polarsteps-api-version: 0 as required by this endpoint.
        """
        endpoint = f"{self.base_url}/api/timezone"
        params = {"lat": lat, "lon": lon}
        headers = {"polarsteps-api-version": "0"}
        
        print(f"➡️ [Polarsteps] Fetching timezone for lat={lat}, lon={lon}")
        response = self._send_request("GET", endpoint, headers=headers, params=params)
        return self._handle_response(response)

    def create_step(self, step_data: Dict[str, Any], trip_slug: Optional[str] = None) -> Dict[str, Any]:
        """
        Creates a new step in Polarsteps via POST /api/steps.
        Sets the appropriate Referer header containing the trip slug.
        Uses polarsteps-api-version: 68.
        """
        endpoint = f"{self.base_url}/api/steps"
        
        # Copy to avoid mutating original dictionary
        payload = dict(step_data)
        slug = trip_slug or payload.pop("trip_slug", None) or "summer-alps-expedition"
        
        trip_id = payload.get("trip_id")
        if trip_id:
            self.session.headers["Referer"] = f"{self.base_url}/{self.username}/{trip_id}-{slug}?mode=track"

        self.session.headers["polarsteps-api-version"] = "68"
        print(f"➡️ [Polarsteps] Creating step: '{payload.get('name')}' for trip ID: {trip_id}")
        response = self._send_request("POST", endpoint, json_data=payload)
        return self._handle_response(response)

import os
import requests
from typing import Dict, Any, Optional

class PolarstepsAPIClient:
    """
    Client wrapper matching exact browser headers and cookies for Polarsteps.
    """
    def __init__(self, remember_token: Optional[str] = None, session_cookie: Optional[str] = None, username: str = "NikodemJura"):
        self.base_url = "https://www.polarsteps.com"
        
        # Load from environment if not explicitly passed
        self.remember_token = remember_token or os.getenv("POLARSTEPS_REMEMBER_TOKEN")
        self.session_cookie = session_cookie or os.getenv("POLARSTEPS_SESSION")
        self.username = username or os.getenv("POLARSTEPS_USERNAME")
        
        self.session = requests.Session()
        
        # Set exact browser headers found in your working curl command
        self.session.headers.update({
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Content-Type": "application/json",
            "Origin": self.base_url,
            "Referer": f"{self.base_url}/{self.username}",
            "polarsteps-api-version": "69",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
        })
        
        # Inject required cookies into the session jar
        if self.remember_token:
            self.session.cookies.set("remember_token", self.remember_token, domain="www.polarsteps.com")
        if self.session_cookie:
            self.session.cookies.set("session", self.session_cookie, domain="www.polarsteps.com")

    def _handle_response(self, response: requests.Response) -> Dict[str, Any]:
        """Helper to process HTTP responses and catch errors."""
        try:
            response.raise_for_status()
            return response.json() if response.content else {}
        except requests.exceptions.HTTPError as e:
            print(f"HTTP Error: {e} | Response: {response.text}")
            raise
        except ValueError:
            print("Error: Response body is not valid JSON.")
            return {"raw_text": response.text}

    def create_trip(self, trip_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Creates a new trip using the correct /api/trips endpoint.
        """
        endpoint = f"{self.base_url}/api/trips"
        
        print(f"Sending POST request to create trip: '{trip_data.get('name')}'")
        response = self.session.post(endpoint, json=trip_data)
        
        return self._handle_response(response)
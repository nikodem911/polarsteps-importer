import json
import os
import time
import requests
from typing import Dict, Any, List, Optional


def _headers_to_har(headers: dict) -> List[Dict[str, str]]:
    """Convert a dict (or CaseInsensitiveDict) to HAR name/value list."""
    return [{"name": k, "value": v} for k, v in headers.items()]


def _cookies_to_har(cookie_jar) -> List[Dict[str, str]]:
    """Convert a requests CookieJar to HAR cookie list."""
    return [{"name": c.name, "value": c.value} for c in cookie_jar]


class PolarstepsAPIClient:
    """
    Client handling REST API requests to Polarsteps (browser-emulated).
    """
    def __init__(
        self,
        remember_token: Optional[str] = None,
        session_cookie: Optional[str] = None,
        username: Optional[str] = None,
        verbose: bool = False,
        dump: Optional[str] = None
    ):
        self.base_url = "https://www.polarsteps.com"
        self.verbose = verbose
        self.dump_path = dump          # path to write .har file, or None
        self._har_entries: List[Dict[str, Any]] = []
        
        self._har_entries: List[Dict[str, Any]] = []

        # Load from environment if not explicitly passed
        self.remember_token = remember_token or os.getenv("POLARSTEPS_REMEMBER_TOKEN")
        self.session_cookie = session_cookie or os.getenv("POLARSTEPS_SESSION")
        self.username = username or os.getenv("POLARSTEPS_USERNAME")

        self.session = requests.Session()
        print(f"🔑 Initialized PolarstepsAPIClient for user: {self.username}"
              )
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
        """Sends an HTTP request with optional verbose logging and HAR dumping."""
        if self.verbose:
            payload = json_data or params
            if payload:
                print(f"[RQ Payload] {method} {url}:\n{json.dumps(payload, indent=2)}")

        started = time.time()
        response = self.session.request(method, url, headers=headers, params=params, json=json_data)
        elapsed_ms = int((time.time() - started) * 1000)

        if self.verbose:
            print(f"[RS Payload] {response.status_code} {url}:\n{response.text}\n")

        if self.dump_path is not None:
            self._record_har_entry(response, elapsed_ms, json_data)

        return response

    def _record_har_entry(
        self,
        response: requests.Response,
        elapsed_ms: int,
        json_data: Optional[Dict[str, Any]]
    ) -> None:
        """Appends a HAR-format entry for the completed request/response pair."""
        req = response.request

        # Request body
        post_data: Optional[Dict[str, Any]] = None
        if json_data is not None:
            post_data = {
                "mimeType": "application/json",
                "text": json.dumps(json_data)
            }
        elif req.body:
            body_text = req.body if isinstance(req.body, str) else req.body.decode("utf-8", errors="replace")
            post_data = {"mimeType": "application/octet-stream", "text": body_text}

        # Response body
        try:
            rs_body = response.text
        except Exception:
            rs_body = ""

        entry = {
            "startedDateTime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "time": elapsed_ms,
            "request": {
                "method": req.method,
                "url": req.url,
                "httpVersion": "HTTP/1.1",
                "headers": _headers_to_har(req.headers),
                "cookies": _cookies_to_har(self.session.cookies),
                "queryString": [
                    {"name": k, "value": v}
                    for k, v in (
                        [pair.split("=", 1) if "=" in pair else (pair, "")
                         for pair in req.url.split("?", 1)[1].split("&")]
                        if "?" in req.url else []
                    )
                ],
                "postData": post_data,
                "headersSize": -1,
                "bodySize": len(req.body) if req.body else 0,
            },
            "response": {
                "status": response.status_code,
                "statusText": response.reason or "",
                "httpVersion": "HTTP/1.1",
                "headers": _headers_to_har(response.headers),
                "cookies": [],
                "content": {
                    "size": len(rs_body),
                    "mimeType": response.headers.get("Content-Type", "application/json"),
                    "text": rs_body,
                },
                "redirectURL": "",
                "headersSize": -1,
                "bodySize": len(rs_body),
            },
            "timings": {"send": 0, "wait": elapsed_ms, "receive": 0},
        }
        self._har_entries.append(entry)

    def flush_har(self) -> None:
        """Writes all recorded HAR entries to the configured dump path."""
        if not self.dump_path or not self._har_entries:
            return

        har = {
            "log": {
                "version": "1.2",
                "creator": {"name": "polarsteps-importer", "version": "1.0"},
                "entries": self._har_entries,
            }
        }
        with open(self.dump_path, "w", encoding="utf-8") as f:
            json.dump(har, f, indent=2)
        print(f"📄 HAR dump written to: {self.dump_path}")

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

    def sign_s3(
        self,
        s3_object_name: str,
        s3_object_type: str = "image/jpeg",
        media_type: str = "full_res_step_image",
        trip_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Requests an S3 signed PUT URL from Polarsteps via GET /sign_s3.
        Uses polarsteps-api-version: 54.
        """
        endpoint = f"{self.base_url}/sign_s3"
        params = {
            "s3_object_name": s3_object_name,
            "s3_object_type": s3_object_type,
            "type": media_type
        }
        headers = {"polarsteps-api-version": "54"}
        if trip_id:
            self.session.headers["Referer"] = f"{self.base_url}/{self.username}/{trip_id}"

        print(f"➡️ [Polarsteps] Requesting S3 signature for '{media_type}': {s3_object_name}")
        response = self._send_request("GET", endpoint, headers=headers, params=params)
        return self._handle_response(response)

    def upload_to_s3(
        self,
        signed_url: str,
        data: bytes,
        content_type: str = "image/jpeg"
    ) -> requests.Response:
        """
        Uploads raw binary bytes directly to AWS S3 using the signed PUT URL.
        """
        headers = {
            "Content-Type": content_type,
            "x-amz-acl": "public-read"
        }
        if self.verbose:
            print(f"[RQ Payload] PUT S3 ({len(data)} bytes) to:\n{signed_url[:120]}...")

        started = time.time()
        response = requests.put(signed_url, data=data, headers=headers)
        elapsed_ms = int((time.time() - started) * 1000)

        if self.verbose:
            print(f"[RS Payload] {response.status_code} S3 PUT:\n{response.text}\n")

        if self.dump_path is not None:
            self._record_har_entry(response, elapsed_ms, None)

        response.raise_for_status()
        return response

    def add_media_to_step(
        self,
        trip_id: int,
        step_id: int,
        media_infos: list
    ) -> Dict[str, Any]:
        """
        Links uploaded S3 media items to a step via POST /api/trips/{trip_id}/steps/{step_id}/media.
        Uses polarsteps-api-version: 71.
        """
        endpoint = f"{self.base_url}/api/trips/{trip_id}/steps/{step_id}/media"
        headers = {"polarsteps-api-version": "71"}
        self.session.headers["Referer"] = f"{self.base_url}/{self.username}/{trip_id}"

        payload = {
            "type": "image",
            "media_infos": media_infos
        }
        print(f"➡️ [Polarsteps] Linking {len(media_infos)} media item(s) to step {step_id} (trip {trip_id})")
        response = self._send_request("POST", endpoint, headers=headers, json_data=payload)
        return self._handle_response(response)

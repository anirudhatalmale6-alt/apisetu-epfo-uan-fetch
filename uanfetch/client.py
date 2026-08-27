"""HTTP client for the EPFO UAN Card endpoint on APISetu.

Endpoint contract, from EPFO's published OpenAPI 3.0.0 document:

    POST https://apisetu.gov.in/epfindia/v3/uncrd/certificate
    X-APISETU-APIKEY:   <api key>
    X-APISETU-CLIENTID: <client id>
    {
      "txnId":  "<uuid4>",
      "format": "pdf",
      "certificateParameters": {"UAN": "<12 digits>", "DOB": "DD-MM-YYYY"},
      "consentArtifact": { ... optional ... }
    }

    200 -> application/pdf, body is the certificate itself
"""

import threading
import time
import uuid
from dataclasses import dataclass

import requests

from .config import Config
from .consent import build_consent_artifact
from .records import Employee


class RateLimiter:
    """Simple thread-safe spacing between calls.

    EPFO have not published a rate limit for this endpoint. The safe assumption
    is that one exists and is lower than you would like, so requests are spaced
    rather than fired off in parallel.
    """

    def __init__(self, per_second: float):
        self._interval = 1.0 / per_second if per_second > 0 else 0.0
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    def wait(self) -> None:
        if self._interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            sleep_for = self._next_allowed - now
            if sleep_for > 0:
                time.sleep(sleep_for)
                now = time.monotonic()
            self._next_allowed = now + self._interval


@dataclass
class FetchResult:
    ok: bool
    status_code: int | None
    txn_id: str
    body: bytes = b""
    content_type: str = ""
    error: str = ""
    attempts: int = 1
    consent_id: str | None = None


# Statuses where trying again might genuinely help. A 400 or 404 will fail
# identically every time, so retrying only wastes the rate limit.
RETRYABLE_STATUSES = {429, 500, 502, 503, 504}

_STATUS_MEANINGS = {
    400: "bad request - usually a malformed UAN or a date not in DD-MM-YYYY",
    401: "unauthorised - check APISETU_API_KEY and APISETU_CLIENT_ID, and that "
    "the calling server's IP is on the whitelist for this key",
    403: "forbidden - the subscription to this API may not be approved yet",
    404: "not found - no UAN Card matches this UAN and date of birth pair",
    429: "rate limited by the gateway",
    500: "EPFO server error",
    502: "bad gateway between APISetu and EPFO",
    503: "EPFO service unavailable",
    504: "timeout between APISetu and EPFO",
}


def explain_status(status: int) -> str:
    return _STATUS_MEANINGS.get(status, f"unexpected HTTP {status}")


class UanCardClient:
    def __init__(self, cfg: Config, session: requests.Session | None = None):
        self.cfg = cfg
        self.session = session or requests.Session()
        self.limiter = RateLimiter(cfg.requests_per_second)

    def _headers(self) -> dict:
        return {
            "X-APISETU-APIKEY": self.cfg.api_key,
            "X-APISETU-CLIENTID": self.cfg.client_id,
            "Content-Type": "application/json",
            "Accept": "application/pdf",
        }

    def _payload(self, employee: Employee) -> tuple[dict, str | None]:
        payload = {
            "txnId": str(uuid.uuid4()),
            "format": "pdf",
            "certificateParameters": {"UAN": employee.uan, "DOB": employee.dob},
        }
        consent_id = None
        if self.cfg.send_consent:
            artifact = build_consent_artifact(employee, self.cfg)
            payload["consentArtifact"] = artifact
            consent_id = artifact["consent"]["consentId"]
        return payload, consent_id

    def fetch(self, employee: Employee) -> FetchResult:
        """Fetch one UAN card, retrying only where a retry could plausibly help."""
        payload, consent_id = self._payload(employee)
        txn_id = payload["txnId"]
        last_error = ""
        last_status: int | None = None
        used_attempts = 0

        for attempt in range(1, self.cfg.max_retries + 1):
            used_attempts = attempt
            self.limiter.wait()
            try:
                response = self.session.post(
                    self.cfg.endpoint,
                    json=payload,
                    headers=self._headers(),
                    timeout=self.cfg.timeout_seconds,
                )
            except requests.Timeout:
                last_error = f"request timed out after {self.cfg.timeout_seconds}s"
                last_status = None
            except requests.RequestException as exc:
                last_error = f"network error: {exc}"
                last_status = None
            else:
                last_status = response.status_code
                if response.status_code == 200:
                    return FetchResult(
                        ok=True,
                        status_code=200,
                        txn_id=txn_id,
                        body=response.content,
                        content_type=response.headers.get("Content-Type", ""),
                        attempts=attempt,
                        consent_id=consent_id,
                    )

                last_error = explain_status(response.status_code)
                detail = self._error_detail(response)
                if detail:
                    last_error = f"{last_error} ({detail})"

                if response.status_code not in RETRYABLE_STATUSES:
                    break

            if attempt < self.cfg.max_retries:
                # Exponential backoff. Anything the gateway is struggling with is
                # made worse by hammering it.
                time.sleep(self.cfg.backoff_seconds * (2 ** (attempt - 1)))

        return FetchResult(
            ok=False,
            status_code=last_status,
            txn_id=txn_id,
            error=last_error or "request failed",
            attempts=used_attempts,
            consent_id=consent_id,
        )

    @staticmethod
    def _error_detail(response: requests.Response) -> str:
        """Pull a human-readable detail out of an error response if there is one.

        EPFO's spec documents the error statuses but not their bodies, so this
        stays defensive and simply reports whatever came back.
        """
        content_type = response.headers.get("Content-Type", "").lower()
        try:
            if "json" in content_type:
                data = response.json()
                if isinstance(data, dict):
                    for key in ("error_description", "errorDescription", "message", "error"):
                        if data.get(key):
                            return str(data[key])[:200]
                return str(data)[:200]
            text = response.text.strip()
            return text[:200] if text else ""
        except Exception:  # noqa: BLE001 - detail extraction must never mask the real error
            return ""

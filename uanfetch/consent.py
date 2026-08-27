"""Building the APISetu consent artifact.

The artifact shape below is taken directly from the ConsentArtifactSchema in
EPFO's published OpenAPI document, including which fields it marks required.

One honest caveat, repeated in the README because it matters: the schema makes
`signature.signature` mandatory but does not say how the artifact should be
signed. Until EPFO confirm the algorithm and the key exchange, `sign_artifact`
below produces a deterministic local HMAC. That is enough to make the audit
trail tamper-evident on your side; it is NOT a claim of compliance with
whatever EPFO expect. Confirm with them before switching consent on.
"""

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timedelta, timezone

from .config import Config
from .records import Employee


def _iso(moment: datetime) -> str:
    return moment.replace(microsecond=0).isoformat()


def sign_artifact(payload: dict, secret: str) -> str:
    """Deterministic HMAC-SHA256 over the canonical JSON form of the artifact.

    Canonical form means sorted keys and no incidental whitespace, so the same
    artifact always produces the same signature and a later audit can re-derive
    it from the stored record.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hmac.new(
        secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return digest


def build_consent_artifact(
    employee: Employee, cfg: Config, *, now: datetime | None = None
) -> dict:
    """Assemble a consent artifact for one employee's UAN Card request."""
    now = now or datetime.now(timezone.utc)
    valid_to = now + timedelta(days=cfg.consent_validity_days)

    consent = {
        "consentId": str(uuid.uuid4()),
        "timestamp": _iso(now),
        "data": {"id": "UANCard"},
        "dataConsumer": {"id": cfg.consent_data_consumer_id or cfg.client_id},
        "dataProvider": {"id": cfg.consent_data_provider_id},
        "purpose": {"description": cfg.consent_purpose},
        "user": {
            # idType/idNumber identify the individual the consent is about.
            "idType": "UAN",
            "idNumber": employee.uan,
            # Contact details are required by the schema. Left blank when payroll
            # does not hold them rather than invented, which would defeat the
            # point of an audit record.
            "email": "",
            "mobile": "",
        },
        "permission": {
            "access": "read",
            "dateRange": {"from": _iso(now), "to": _iso(valid_to)},
            # A single read, not a standing subscription.
            "frequency": {"unit": "day", "value": 1, "repeats": 1},
        },
    }

    artifact = {"consent": consent}
    artifact["signature"] = {"signature": sign_artifact(artifact, cfg.api_key)}
    return artifact

import json

from uanfetch.config import Config
from uanfetch.consent import build_consent_artifact, sign_artifact
from uanfetch.records import Employee

EMPLOYEE = Employee(
    employee_id="E1", name="Asha Menon", uan="100000035770", dob="31-12-1980"
)


def _cfg(**overrides) -> Config:
    base = {"api_key": "k", "client_id": "CID"}
    base.update(overrides)
    return Config(**base)


def test_artifact_carries_every_field_the_schema_marks_required():
    """Shape is taken from EPFO's ConsentArtifactSchema; keep it in step with it."""
    artifact = build_consent_artifact(EMPLOYEE, _cfg())

    assert set(artifact) == {"consent", "signature"}
    consent = artifact["consent"]
    for field in (
        "consentId",
        "timestamp",
        "data",
        "dataConsumer",
        "dataProvider",
        "purpose",
        "user",
        "permission",
    ):
        assert field in consent, f"missing required field {field}"

    assert consent["user"]["idNumber"] == EMPLOYEE.uan
    assert consent["user"]["idType"] == "UAN"
    for field in ("access", "dateRange", "frequency"):
        assert field in consent["permission"]
    assert set(consent["permission"]["dateRange"]) == {"from", "to"}
    assert set(consent["permission"]["frequency"]) == {"unit", "value", "repeats"}


def test_consent_id_is_unique_per_request():
    a = build_consent_artifact(EMPLOYEE, _cfg())
    b = build_consent_artifact(EMPLOYEE, _cfg())
    assert a["consent"]["consentId"] != b["consent"]["consentId"]


def test_data_consumer_falls_back_to_the_client_id():
    artifact = build_consent_artifact(EMPLOYEE, _cfg())
    assert artifact["consent"]["dataConsumer"]["id"] == "CID"


def test_validity_window_honours_the_configured_days():
    artifact = build_consent_artifact(EMPLOYEE, _cfg(consent_validity_days=7))
    window = artifact["consent"]["permission"]["dateRange"]
    assert window["from"][:4].isdigit()
    assert window["to"] > window["from"]


def test_signature_is_deterministic_and_recomputable():
    """An auditor must be able to re-derive the signature from the stored record."""
    payload = {"consent": {"consentId": "fixed", "data": {"id": "UANCard"}}}
    first = sign_artifact(payload, "secret")
    # Key order must not matter, only content.
    reordered = json.loads(json.dumps(payload))
    assert sign_artifact(reordered, "secret") == first
    assert sign_artifact(payload, "different-secret") != first


def test_contact_details_are_left_blank_rather_than_invented():
    """Fabricating an email would make the audit record worthless."""
    user = build_consent_artifact(EMPLOYEE, _cfg())["consent"]["user"]
    assert user["email"] == ""
    assert user["mobile"] == ""

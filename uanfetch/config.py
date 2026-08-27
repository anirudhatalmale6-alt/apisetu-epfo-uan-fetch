"""Configuration, loaded from environment variables (or a .env file).

Every value that differs between the mock and the live APISetu endpoint lives
here, so switching over is a config change and not a code change.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

# APISetu's published base URL for the EPFO collection, v3.
DEFAULT_BASE_URL = "https://apisetu.gov.in/epfindia/v3"

# Path of the UAN Card endpoint within that collection.
UAN_CARD_PATH = "/uncrd/certificate"


def _load_dotenv(path: Path) -> None:
    """Minimal .env reader so the tool has no dependency on python-dotenv.

    Existing environment variables always win, which means a value exported in
    the shell overrides the file rather than the other way round.
    """
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a whole number, got {raw!r}") from exc


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


class ConfigError(RuntimeError):
    """Raised when the environment is missing or contradicts itself."""


@dataclass
class Config:
    api_key: str
    client_id: str
    base_url: str = DEFAULT_BASE_URL

    # Consent artifact. The UAN Card schema marks only txnId and format as
    # required, so consent is off by default: the artifact carries a mandatory
    # cryptographic signature whose algorithm EPFO have not published, and
    # sending an unsigned one risks rejection where omitting it is schema-valid.
    # Every request is written to the local audit log either way. Confirm with
    # EPFO whether they require it, then set APISETU_SEND_CONSENT=true.
    send_consent: bool = False
    consent_data_consumer_id: str = ""
    consent_data_provider_id: str = "EPFO"
    consent_validity_days: int = 30
    consent_purpose: str = "Employer verification of employee UAN records"

    # Throughput. Deliberately conservative — EPFO have not published a rate
    # limit, so the safe assumption is that one exists and is low.
    requests_per_second: float = 2.0
    max_retries: int = 3
    backoff_seconds: float = 2.0
    timeout_seconds: int = 60

    # PDF acceptance rules.
    max_pdf_bytes: int = 10 * 1024 * 1024
    min_pdf_bytes: int = 512
    normalise_orientation: bool = True

    output_dir: Path = field(default_factory=lambda: Path("output"))

    @property
    def endpoint(self) -> str:
        return self.base_url.rstrip("/") + UAN_CARD_PATH

    @classmethod
    def from_env(cls, env_file: Path | None = None) -> "Config":
        _load_dotenv(env_file or Path(".env"))

        api_key = os.environ.get("APISETU_API_KEY", "").strip()
        client_id = os.environ.get("APISETU_CLIENT_ID", "").strip()
        missing = [
            name
            for name, value in (
                ("APISETU_API_KEY", api_key),
                ("APISETU_CLIENT_ID", client_id),
            )
            if not value
        ]
        if missing:
            raise ConfigError(
                "Missing required environment variable(s): "
                + ", ".join(missing)
                + ". Copy .env.example to .env and fill them in. "
                "Both are issued by the APISetu Partners portal."
            )

        cfg = cls(
            api_key=api_key,
            client_id=client_id,
            base_url=os.environ.get("APISETU_BASE_URL", DEFAULT_BASE_URL).strip()
            or DEFAULT_BASE_URL,
            send_consent=_env_bool("APISETU_SEND_CONSENT", False),
            consent_data_consumer_id=os.environ.get(
                "CONSENT_DATA_CONSUMER_ID", ""
            ).strip()
            or client_id,
            consent_data_provider_id=os.environ.get(
                "CONSENT_DATA_PROVIDER_ID", "EPFO"
            ).strip(),
            consent_validity_days=_env_int("CONSENT_VALIDITY_DAYS", 30),
            consent_purpose=os.environ.get(
                "CONSENT_PURPOSE",
                "Employer verification of employee UAN records",
            ).strip(),
            requests_per_second=_env_float("REQUESTS_PER_SECOND", 2.0),
            max_retries=_env_int("MAX_RETRIES", 3),
            backoff_seconds=_env_float("BACKOFF_SECONDS", 2.0),
            timeout_seconds=_env_int("TIMEOUT_SECONDS", 60),
            max_pdf_bytes=_env_int("MAX_PDF_BYTES", 10 * 1024 * 1024),
            min_pdf_bytes=_env_int("MIN_PDF_BYTES", 512),
            normalise_orientation=_env_bool("NORMALISE_ORIENTATION", True),
            output_dir=Path(os.environ.get("OUTPUT_DIR", "output").strip() or "output"),
        )

        if cfg.requests_per_second <= 0:
            raise ConfigError("REQUESTS_PER_SECOND must be greater than zero")
        if cfg.min_pdf_bytes >= cfg.max_pdf_bytes:
            raise ConfigError("MIN_PDF_BYTES must be smaller than MAX_PDF_BYTES")
        return cfg

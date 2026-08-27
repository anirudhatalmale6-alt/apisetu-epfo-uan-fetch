"""End to end test through the mock APISetu endpoint.

The mock server is started on a free port inside the test process, so the whole
suite runs with no setup and no leftover state between runs.
"""

import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from mockserver import server as mock
from uanfetch.client import UanCardClient
from uanfetch.config import Config
from uanfetch.records import load_employees
from uanfetch.runner import run_batch

SAMPLE = Path(__file__).resolve().parent.parent / "employees.sample.csv"


@pytest.fixture()
def mock_server():
    # Reset the per-UAN attempt counter so the retry case behaves identically
    # every run rather than depending on what a previous test did.
    mock._attempts.clear()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), mock.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/epfindia/v3"
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture()
def cfg(mock_server, tmp_path):
    return Config(
        api_key="test-key",
        client_id="TESTCLIENT",
        base_url=mock_server,
        requests_per_second=50,
        backoff_seconds=0.01,
        output_dir=tmp_path / "out",
    )


def test_full_batch_classifies_every_case(cfg):
    employees, rejected = load_employees(SAMPLE)
    outcome = run_batch(employees, rejected, cfg, resume=False)

    t = outcome.totals
    assert t.downloaded == 4
    assert t.failed == 1        # the 404
    assert t.invalid_pdf == 2   # HTML-as-PDF, and the truncated one
    assert t.rejected == 3      # bad UAN, bad DOB, duplicate

    # Only genuine PDFs reached the disk.
    written = sorted(p.name for p in (cfg.output_dir / "pdfs").glob("*.pdf"))
    assert len(written) == 4
    for path in (cfg.output_dir / "pdfs").glob("*.pdf"):
        assert path.read_bytes().startswith(b"%PDF-")

    # No half-written temporary files survive.
    assert not list((cfg.output_dir / "pdfs").glob("*.part"))


def test_retryable_error_is_retried_and_permanent_error_is_not(cfg):
    employees, rejected = load_employees(SAMPLE)
    outcome = run_batch(employees, rejected, cfg, resume=False)
    by_id = {r["employee_id"]: r for r in outcome.rows}

    # UAN ending 3 fails twice with 503 then succeeds.
    assert by_id["EMP004"]["status"] == "OK"
    assert by_id["EMP004"]["attempts"] == 3

    # UAN ending 2 returns 404, which retrying cannot fix, so it must stop at one.
    assert by_id["EMP003"]["status"] == "FAILED"
    assert by_id["EMP003"]["attempts"] == 1
    assert by_id["EMP003"]["http_status"] == 404


def test_landscape_response_is_filed_upright(cfg):
    employees, rejected = load_employees(SAMPLE)
    outcome = run_batch(employees, rejected, cfg, resume=False)
    row = next(r for r in outcome.rows if r["employee_id"] == "EMP002")
    assert row["status"] == "OK"
    assert row["rotated"] == "yes"


def test_resume_skips_what_is_already_downloaded(cfg):
    employees, rejected = load_employees(SAMPLE)
    run_batch(employees, rejected, cfg, resume=False)
    second = run_batch(employees, rejected, cfg, resume=True)

    assert second.totals.downloaded == 0
    assert second.totals.skipped == 4


def test_audit_log_records_every_request_without_the_api_key(cfg):
    employees, rejected = load_employees(SAMPLE)
    run_batch(employees, rejected, cfg, resume=False)

    raw = cfg.output_dir.joinpath("audit.jsonl").read_text()
    entries = [json.loads(line) for line in raw.splitlines()]

    assert len(entries) == 7  # every employee that was actually sent
    assert all(e["txn_id"] for e in entries)
    assert cfg.api_key not in raw


def test_missing_credentials_are_rejected_by_the_gateway(cfg):
    employees, _ = load_employees(SAMPLE)
    cfg.api_key = ""
    result = UanCardClient(cfg).fetch(employees[0])
    assert not result.ok
    assert result.status_code == 401


def test_consent_artifact_is_accepted_when_enabled(cfg):
    cfg.send_consent = True
    cfg.consent_data_consumer_id = "TESTCLIENT"
    employees, _ = load_employees(SAMPLE)

    result = UanCardClient(cfg).fetch(employees[0])
    assert result.ok
    assert result.consent_id  # recorded so the request can be traced later

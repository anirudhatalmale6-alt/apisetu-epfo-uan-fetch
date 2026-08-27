from mockserver.server import build_pdf
from uanfetch.pdfcheck import inspect_pdf, normalise_to_portrait

LIMITS = {"min_bytes": 512, "max_bytes": 10 * 1024 * 1024}


def test_valid_pdf_passes_and_is_described():
    report = inspect_pdf(build_pdf("100000035770", "31-12-1980"), "application/pdf", **LIMITS)
    assert report.ok
    assert report.page_count == 1
    assert report.orientation == "portrait"


def test_landscape_is_detected():
    body = build_pdf("100000035771", "31-12-1980", landscape=True)
    assert inspect_pdf(body, "application/pdf", **LIMITS).orientation == "landscape"


def test_html_body_mislabelled_as_pdf_is_rejected():
    """The important one: HTTP 200, content-type application/pdf, HTML body.

    Without the magic-byte check this lands on disk as an unopenable .pdf and
    nobody notices until someone tries to read it.
    """
    body = b"<!doctype html><html><body>Gateway maintenance</body></html>"
    report = inspect_pdf(body, "application/pdf", **LIMITS)
    assert not report.ok
    assert "not a PDF" in report.reason


def test_json_error_body_is_rejected_with_a_useful_reason():
    report = inspect_pdf(b'{"error":"nope"}', "application/json", **LIMITS)
    assert not report.ok
    assert "JSON error document" in report.reason


def test_empty_body_is_rejected():
    assert not inspect_pdf(b"", "application/pdf", **LIMITS).ok


def test_truncated_pdf_is_rejected_by_size_floor():
    full = build_pdf("100000035770", "31-12-1980")
    report = inspect_pdf(full[: len(full) // 2], "application/pdf", **LIMITS)
    assert not report.ok


def test_pdf_missing_eof_trailer_is_rejected():
    """Truncation large enough to clear the size floor must still be caught."""
    full = build_pdf("100000035770", "31-12-1980")
    truncated = full[: -len(b"%%EOF\n")]
    report = inspect_pdf(truncated, "application/pdf", min_bytes=10, max_bytes=10**7)
    assert not report.ok
    assert "%%EOF" in report.reason


def test_oversize_pdf_is_rejected():
    body = build_pdf("100000035770", "31-12-1980")
    report = inspect_pdf(body, "application/pdf", min_bytes=10, max_bytes=100)
    assert not report.ok
    assert "ceiling" in report.reason


def test_landscape_is_rotated_upright_and_stays_readable():
    body = build_pdf("100000035771", "31-12-1980", landscape=True)
    rotated, changed = normalise_to_portrait(body)
    assert changed
    after = inspect_pdf(rotated, "application/pdf", **LIMITS)
    assert after.ok
    assert after.orientation == "portrait"


def test_portrait_is_left_untouched():
    body = build_pdf("100000035770", "31-12-1980")
    out, changed = normalise_to_portrait(body)
    assert not changed
    assert out is body


def test_rotation_never_returns_a_broken_file():
    """Given something unparseable, hand back the original rather than a ruin."""
    junk = b"%PDF-1.4 this is not really a pdf"
    out, changed = normalise_to_portrait(junk)
    assert out == junk
    assert not changed

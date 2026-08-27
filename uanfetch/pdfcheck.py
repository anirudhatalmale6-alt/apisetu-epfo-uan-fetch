"""Validating and normalising the returned PDF.

The failure mode worth designing against is not a clean HTTP error. It is a
200 response whose body is an HTML error page, a maintenance notice or a
zero-byte file. Written straight to disk with a .pdf extension that produces a
directory of files that look fine in a listing and are all unopenable, and
nobody notices until an auditor asks. So every response is checked for the PDF
magic bytes before it is allowed anywhere near the output folder.
"""

from dataclasses import dataclass

PDF_MAGIC = b"%PDF-"
PDF_EOF = b"%%EOF"


@dataclass
class PdfReport:
    ok: bool
    reason: str = ""
    byte_size: int = 0
    page_count: int | None = None
    orientation: str | None = None  # "portrait", "landscape" or "mixed"
    was_rotated: bool = False


def _describe_orientation(pages) -> str | None:
    seen = set()
    for page in pages:
        box = page.mediabox
        width = float(box.width)
        height = float(box.height)
        # A page rotated by /Rotate 90 or 270 presents transposed on screen even
        # though the mediabox itself is unchanged, so fold the rotation in.
        rotation = (page.get("/Rotate") or 0) % 360
        if rotation in (90, 270):
            width, height = height, width
        seen.add("landscape" if width > height else "portrait")
    if not seen:
        return None
    if len(seen) > 1:
        return "mixed"
    return seen.pop()


def inspect_pdf(body: bytes, content_type: str, *, min_bytes: int, max_bytes: int) -> PdfReport:
    """Decide whether this response body is a usable PDF."""
    size = len(body)

    if size == 0:
        return PdfReport(False, "response body was empty", size)

    # Content-Type is checked, but it is advisory only: the magic bytes below are
    # what actually decides, because a misconfigured gateway can label anything.
    normalised_type = (content_type or "").split(";")[0].strip().lower()

    if not body.startswith(PDF_MAGIC):
        preview = body[:120].decode("utf-8", errors="replace").replace("\n", " ")
        if normalised_type in {"application/json", "text/json"}:
            hint = "the gateway returned a JSON error document"
        elif normalised_type.startswith("text/html"):
            hint = "the gateway returned an HTML page, usually an error or a login redirect"
        else:
            hint = f"content-type was {normalised_type or 'absent'}"
        return PdfReport(
            False,
            f"not a PDF - {hint}. First bytes: {preview!r}",
            size,
        )

    if size < min_bytes:
        return PdfReport(
            False,
            f"PDF is only {size} bytes, below the {min_bytes} byte floor - "
            "almost certainly truncated",
            size,
        )

    if size > max_bytes:
        return PdfReport(
            False,
            f"PDF is {size} bytes, above the {max_bytes} byte ceiling",
            size,
        )

    # A PDF whose trailer is missing was cut off in transit. Some writers pad
    # after %%EOF, so look in the tail rather than requiring it to be last.
    if PDF_EOF not in body[-2048:]:
        return PdfReport(
            False,
            "PDF has no %%EOF trailer in its final bytes - the download was truncated",
            size,
        )

    report = PdfReport(True, "", size)

    # Page geometry is a bonus rather than a gate: if pypdf is not installed the
    # file is still perfectly usable, we just cannot describe it.
    try:
        import io

        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(body))
        report.page_count = len(reader.pages)
        report.orientation = _describe_orientation(reader.pages)
    except ImportError:
        pass
    except Exception as exc:  # noqa: BLE001 - a malformed PDF must not crash the batch
        return PdfReport(False, f"PDF failed to parse: {exc}", size)

    return report


def normalise_to_portrait(body: bytes) -> tuple[bytes, bool]:
    """Rotate any landscape page upright so every file opens the same way round.

    Returns (possibly rewritten bytes, whether anything changed). If pypdf is
    unavailable or the rewrite fails, the original bytes are returned untouched -
    an un-rotated PDF is a cosmetic problem, a corrupted one is not.
    """
    try:
        import io

        from pypdf import PdfReader, PdfWriter
    except ImportError:
        return body, False

    try:
        reader = PdfReader(io.BytesIO(body))
        writer = PdfWriter()
        changed = False

        for page in reader.pages:
            box = page.mediabox
            width, height = float(box.width), float(box.height)
            rotation = (page.get("/Rotate") or 0) % 360
            if rotation in (90, 270):
                width, height = height, width
            if width > height:
                page.rotate(90)
                changed = True
            writer.add_page(page)

        if not changed:
            return body, False

        buffer = io.BytesIO()
        writer.write(buffer)
        return buffer.getvalue(), True
    except Exception:  # noqa: BLE001 - never trade a working file for a broken one
        return body, False

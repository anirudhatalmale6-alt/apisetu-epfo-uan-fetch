"""Batch orchestration: read the list, fetch each card, file it, report."""

import csv
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .client import UanCardClient
from .config import Config
from .pdfcheck import inspect_pdf, normalise_to_portrait
from .records import Employee

REPORT_COLUMNS = [
    "employee_id",
    "name",
    "uan",
    "dob",
    "status",
    "detail",
    "file",
    "bytes",
    "pages",
    "orientation",
    "rotated",
    "http_status",
    "attempts",
    "txn_id",
    "consent_id",
    "fetched_at",
]


@dataclass
class Totals:
    downloaded: int = 0
    skipped: int = 0
    rejected: int = 0
    failed: int = 0
    invalid_pdf: int = 0

    @property
    def attempted(self) -> int:
        return self.downloaded + self.failed + self.invalid_pdf


@dataclass
class BatchOutcome:
    totals: Totals = field(default_factory=Totals)
    rows: list[dict] = field(default_factory=list)
    report_path: Path | None = None
    audit_path: Path | None = None
    pdf_dir: Path | None = None


class AuditLog:
    """Append-only JSONL record of every request made.

    Written per line and flushed immediately so that an interrupted run still
    leaves a complete record of what was already sent. Deliberately records the
    transaction and consent ids but never the API key.
    """

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = path.open("a", encoding="utf-8")

    def write(self, entry: dict) -> None:
        self._handle.write(json.dumps(entry, sort_keys=True) + "\n")
        self._handle.flush()

    def close(self) -> None:
        self._handle.close()

    def __enter__(self) -> "AuditLog":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def run_batch(
    employees: list[Employee],
    rejected_rows: list[dict],
    cfg: Config,
    *,
    client: UanCardClient | None = None,
    resume: bool = True,
    progress=None,
) -> BatchOutcome:
    """Fetch a UAN card for every valid employee.

    Existing files are left alone when `resume` is set, so a run that stops
    halfway can simply be started again without re-fetching what it already has.
    """
    client = client or UanCardClient(cfg)
    outcome = BatchOutcome()

    pdf_dir = cfg.output_dir / "pdfs"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    outcome.pdf_dir = pdf_dir
    outcome.report_path = cfg.output_dir / "report.csv"
    outcome.audit_path = cfg.output_dir / "audit.jsonl"

    # Rows that never made it as far as a request still belong in the report,
    # otherwise the totals will not reconcile against the input file.
    for row in rejected_rows:
        outcome.totals.rejected += 1
        outcome.rows.append(
            {
                "employee_id": row.get("employee_id", ""),
                "name": row.get("name", ""),
                "uan": row.get("uan", ""),
                "dob": row.get("dob", ""),
                "status": "REJECTED",
                "detail": f"line {row.get('line')}: {row.get('reason')}",
            }
        )

    with AuditLog(outcome.audit_path) as audit:
        for index, employee in enumerate(employees, start=1):
            target = pdf_dir / f"{employee.slug}.pdf"

            if resume and target.exists() and target.stat().st_size > 0:
                outcome.totals.skipped += 1
                outcome.rows.append(
                    {
                        "employee_id": employee.employee_id,
                        "name": employee.name,
                        "uan": employee.uan,
                        "dob": employee.dob,
                        "status": "SKIPPED",
                        "detail": "already downloaded",
                        "file": str(target),
                        "bytes": target.stat().st_size,
                    }
                )
                if progress:
                    progress(index, len(employees), employee, "SKIPPED")
                continue

            result = client.fetch(employee)
            fetched_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

            row = {
                "employee_id": employee.employee_id,
                "name": employee.name,
                "uan": employee.uan,
                "dob": employee.dob,
                "http_status": result.status_code or "",
                "attempts": result.attempts,
                "txn_id": result.txn_id,
                "consent_id": result.consent_id or "",
                "fetched_at": fetched_at,
            }

            if not result.ok:
                outcome.totals.failed += 1
                row.update({"status": "FAILED", "detail": result.error})
                outcome.rows.append(row)
                audit.write({**row, "outcome": "failed"})
                if progress:
                    progress(index, len(employees), employee, "FAILED")
                continue

            report = inspect_pdf(
                result.body,
                result.content_type,
                min_bytes=cfg.min_pdf_bytes,
                max_bytes=cfg.max_pdf_bytes,
            )

            if not report.ok:
                # Nothing is written to disk. A rejected body saved as .pdf is
                # exactly the silent corruption this check exists to prevent.
                outcome.totals.invalid_pdf += 1
                row.update({"status": "INVALID_PDF", "detail": report.reason,
                            "bytes": report.byte_size})
                outcome.rows.append(row)
                audit.write({**row, "outcome": "invalid_pdf"})
                if progress:
                    progress(index, len(employees), employee, "INVALID_PDF")
                continue

            body = result.body
            rotated = False
            if cfg.normalise_orientation and report.orientation in {"landscape", "mixed"}:
                body, rotated = normalise_to_portrait(body)

            # Write to a temporary name and move into place, so an interrupted
            # write never leaves a half-file that a resume would then skip.
            temp = target.with_suffix(".pdf.part")
            temp.write_bytes(body)
            temp.replace(target)

            outcome.totals.downloaded += 1
            row.update(
                {
                    "status": "OK",
                    "detail": "",
                    "file": str(target),
                    "bytes": len(body),
                    "pages": report.page_count if report.page_count is not None else "",
                    "orientation": report.orientation or "",
                    "rotated": "yes" if rotated else "no",
                }
            )
            outcome.rows.append(row)
            audit.write({**row, "outcome": "ok"})
            if progress:
                progress(index, len(employees), employee, "OK")

    _write_report(outcome.report_path, outcome.rows)
    return outcome


def _write_report(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=REPORT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in REPORT_COLUMNS})

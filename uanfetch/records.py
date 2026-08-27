"""Reading and validating the employee list.

Nearly every failure in a batch like this is a bad row rather than a bad API
call, so rows are validated up front and the invalid ones are reported without
ever being sent to EPFO. That keeps the error report meaningful and avoids
burning rate limit on requests that cannot succeed.
"""

import csv
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# EPFO's Universal Account Number is always 12 digits.
UAN_RE = re.compile(r"^\d{12}$")

# The API requires DD-MM-YYYY. Anything a spreadsheet is likely to hand us gets
# normalised into that. Day-first orderings are tried before month-first because
# that is the Indian convention and the source data is Indian payroll.
_DATE_FORMATS = (
    "%d-%m-%Y",
    "%d/%m/%Y",
    "%d.%m.%Y",
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d-%b-%Y",
    "%d %b %Y",
    "%d-%B-%Y",
    "%d %B %Y",
)

API_DATE_FORMAT = "%d-%m-%Y"


class RowError(ValueError):
    """A row that cannot be turned into a valid request."""


@dataclass
class Employee:
    employee_id: str
    name: str
    uan: str
    dob: str  # always normalised to DD-MM-YYYY

    @property
    def slug(self) -> str:
        """Filename stem. Employee id first so files sort the way payroll thinks."""
        safe_id = re.sub(r"[^A-Za-z0-9_-]+", "_", self.employee_id).strip("_")
        return f"{safe_id}_{self.uan}" if safe_id else self.uan


def normalise_uan(raw: str) -> str:
    """Strip spaces and separators, then confirm it is 12 digits.

    Spreadsheets love to render a UAN as '1000 0003 577' or to drop a leading
    zero after treating it as a number, so both are worth catching explicitly.
    """
    cleaned = re.sub(r"[\s-]+", "", (raw or "").strip())
    if not cleaned:
        raise RowError("UAN is empty")
    if not cleaned.isdigit():
        raise RowError(f"UAN must be digits only, got {raw!r}")
    if len(cleaned) < 12:
        raise RowError(
            f"UAN {cleaned!r} is only {len(cleaned)} digits. A UAN is 12 digits - "
            "this usually means a spreadsheet dropped a leading zero by storing "
            "the column as a number. Reformat that column as text and re-export."
        )
    if not UAN_RE.match(cleaned):
        raise RowError(f"UAN must be exactly 12 digits, got {len(cleaned)}")
    return cleaned


def normalise_dob(raw: str) -> str:
    """Parse a date in any common layout and return it as DD-MM-YYYY."""
    value = (raw or "").strip()
    if not value:
        raise RowError("date of birth is empty")

    # Excel sometimes exports a date as a serial number of days since 1899-12-30.
    if value.isdigit() and 10_000 <= int(value) <= 60_000:
        raise RowError(
            f"date of birth {value!r} looks like an Excel date serial number, not a "
            "date. Format that column as text or as DD-MM-YYYY and re-export."
        )

    for fmt in _DATE_FORMATS:
        try:
            parsed = datetime.strptime(value, fmt)
        except ValueError:
            continue
        if parsed.year < 1900 or parsed > datetime.now():
            raise RowError(f"date of birth {value!r} is outside a plausible range")
        return parsed.strftime(API_DATE_FORMAT)

    raise RowError(
        f"could not read date of birth {value!r}. Use DD-MM-YYYY, for example 31-12-1980"
    )


def _pick(row: dict, *names: str) -> str:
    """Fetch a column by any of several accepted header spellings."""
    for name in names:
        for key, value in row.items():
            if key and key.strip().lower().replace(" ", "_") == name:
                return (value or "").strip()
    return ""


def load_employees(path: Path) -> tuple[list[Employee], list[dict]]:
    """Read the CSV. Returns (valid employees, rejected rows).

    Rejected rows carry the reason so the operator can fix the source data
    rather than guess at what went wrong.
    """
    if not path.is_file():
        raise FileNotFoundError(f"employee file not found: {path}")

    employees: list[Employee] = []
    rejected: list[dict] = []
    seen_uans: dict[str, int] = {}

    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"{path} has no header row")

        for line_no, row in enumerate(reader, start=2):
            if not any((v or "").strip() for v in row.values()):
                continue  # blank line

            employee_id = _pick(row, "employee_id", "employeeid", "empid", "id")
            name = _pick(row, "name", "employee_name", "full_name", "fullname")
            raw_uan = _pick(row, "uan", "uan_number", "uannumber", "uan_no")
            raw_dob = _pick(row, "dob", "date_of_birth", "dateofbirth", "birth_date")

            try:
                uan = normalise_uan(raw_uan)
                dob = normalise_dob(raw_dob)
            except RowError as exc:
                rejected.append(
                    {
                        "line": line_no,
                        "employee_id": employee_id,
                        "name": name,
                        "uan": raw_uan,
                        "dob": raw_dob,
                        "reason": str(exc),
                    }
                )
                continue

            if uan in seen_uans:
                rejected.append(
                    {
                        "line": line_no,
                        "employee_id": employee_id,
                        "name": name,
                        "uan": uan,
                        "dob": dob,
                        "reason": f"duplicate UAN, already present on line {seen_uans[uan]}",
                    }
                )
                continue
            seen_uans[uan] = line_no

            employees.append(
                Employee(employee_id=employee_id, name=name, uan=uan, dob=dob)
            )

    return employees, rejected

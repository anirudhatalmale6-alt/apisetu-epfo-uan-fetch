"""Command line entry point.

    python -m uanfetch fetch --input employees.csv
    python -m uanfetch check --input employees.csv    (validate only, no calls)
"""

import argparse
import sys
from pathlib import Path

from .config import Config, ConfigError
from .records import Employee, load_employees
from .runner import run_batch


def _print_progress(index: int, total: int, employee: Employee, status: str) -> None:
    label = {
        "OK": "ok",
        "FAILED": "FAILED",
        "INVALID_PDF": "INVALID PDF",
        "SKIPPED": "skipped",
    }.get(status, status)
    name = employee.name or employee.employee_id or employee.uan
    print(f"  [{index}/{total}] {employee.uan}  {name[:28]:<28} {label}", flush=True)


def _summarise(outcome, employees_count: int) -> None:
    totals = outcome.totals
    print("\nSummary")
    print(f"  downloaded    {totals.downloaded}")
    if totals.skipped:
        print(f"  skipped       {totals.skipped}  (already present)")
    if totals.failed:
        print(f"  failed        {totals.failed}")
    if totals.invalid_pdf:
        print(f"  invalid PDF   {totals.invalid_pdf}  (response was not a usable PDF)")
    if totals.rejected:
        print(f"  bad rows      {totals.rejected}  (never sent - fix the input file)")
    print(f"\n  PDFs    {outcome.pdf_dir}")
    print(f"  report  {outcome.report_path}")
    print(f"  audit   {outcome.audit_path}")

    if totals.failed or totals.invalid_pdf or totals.rejected:
        print("\n  Open the report and filter the status column for anything "
              "that is not OK.")


def cmd_check(args) -> int:
    """Validate the input file without making a single API call."""
    employees, rejected = load_employees(Path(args.input))
    print(f"Readable rows : {len(employees)}")
    print(f"Rejected rows : {len(rejected)}")
    for row in rejected[: args.show]:
        print(f"  line {row['line']}: {row['reason']}")
    if len(rejected) > args.show:
        print(f"  ... and {len(rejected) - args.show} more")
    return 1 if rejected else 0


def cmd_fetch(args) -> int:
    cfg = Config.from_env(Path(args.env) if args.env else None)
    if args.output:
        cfg.output_dir = Path(args.output)

    employees, rejected = load_employees(Path(args.input))

    if not employees:
        print("No valid rows to fetch. Run the check command to see why.")
        return 1

    print(f"Endpoint : {cfg.endpoint}")
    print(f"Client   : {cfg.client_id}")
    print(f"Consent  : {'attached' if cfg.send_consent else 'not sent'}")
    print(f"Employees: {len(employees)} valid, {len(rejected)} rejected")
    if args.limit:
        employees = employees[: args.limit]
        print(f"Limit    : first {len(employees)} only")
    print()

    outcome = run_batch(
        employees,
        rejected,
        cfg,
        resume=not args.no_resume,
        progress=None if args.quiet else _print_progress,
    )
    _summarise(outcome, len(employees))
    return 0 if outcome.totals.failed == 0 and outcome.totals.invalid_pdf == 0 else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="uanfetch",
        description="Fetch EPFO UAN Cards in bulk from APISetu.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="validate the employee file, make no calls")
    check.add_argument("--input", "-i", required=True, help="employee CSV")
    check.add_argument("--show", type=int, default=20, help="how many problems to list")
    check.set_defaults(func=cmd_check)

    fetch = sub.add_parser("fetch", help="fetch UAN cards")
    fetch.add_argument("--input", "-i", required=True, help="employee CSV")
    fetch.add_argument("--output", "-o", help="output directory (default from OUTPUT_DIR)")
    fetch.add_argument("--env", help="path to the .env file (default ./.env)")
    fetch.add_argument("--limit", type=int, help="only process the first N rows")
    fetch.add_argument("--no-resume", action="store_true",
                       help="re-fetch even where a PDF already exists")
    fetch.add_argument("--quiet", "-q", action="store_true", help="suppress per-row output")
    fetch.set_defaults(func=cmd_fetch)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"Configuration problem:\n  {exc}", file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        print(f"{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted. Re-run the same command to carry on where it stopped.",
              file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

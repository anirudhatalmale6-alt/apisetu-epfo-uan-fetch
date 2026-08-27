import pytest

from uanfetch.records import RowError, load_employees, normalise_dob, normalise_uan


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("100000035770", "100000035770"),
        ("1000 0003 5770", "100000035770"),
        ("1000-0003-5770", "100000035770"),
        ("  100000035770  ", "100000035770"),
    ],
)
def test_uan_accepts_common_spreadsheet_spacing(raw, expected):
    assert normalise_uan(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "abcdefghijkl", "10000003577X"])
def test_uan_rejects_junk(raw):
    with pytest.raises(RowError):
        normalise_uan(raw)


def test_uan_short_error_names_the_leading_zero_cause():
    """A dropped leading zero is the most common cause, so say so."""
    with pytest.raises(RowError, match="leading zero"):
        normalise_uan("10000003577")


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("31-12-1980", "31-12-1980"),
        ("31/12/1980", "31-12-1980"),
        ("31.12.1980", "31-12-1980"),
        ("1980-12-31", "31-12-1980"),
        ("17 Jun 1990", "17-06-1990"),
        ("05-Jul-1988", "05-07-1988"),
    ],
)
def test_dob_normalises_to_api_format(raw, expected):
    assert normalise_dob(raw) == expected


def test_dob_prefers_day_first_for_ambiguous_dates():
    """03-04-1990 is 3 April in Indian payroll data, not 4 March."""
    assert normalise_dob("03-04-1990") == "03-04-1990"


def test_dob_rejects_excel_serial_number():
    with pytest.raises(RowError, match="serial number"):
        normalise_dob("29221")


def test_dob_rejects_future_date():
    with pytest.raises(RowError, match="plausible range"):
        normalise_dob("01-01-2099")


def test_load_employees_separates_good_from_bad(tmp_path):
    csv_file = tmp_path / "e.csv"
    csv_file.write_text(
        "employee_id,name,uan,dob\n"
        "E1,Good One,100000035770,31-12-1980\n"
        "E2,Bad Uan,123,01-01-1980\n"
        "E3,Dupe,100000035770,31-12-1980\n"
        "\n"  # blank line must be ignored, not rejected
        "E4,Good Two,100000035771,1975-03-19\n",
        encoding="utf-8",
    )
    employees, rejected = load_employees(csv_file)

    assert [e.employee_id for e in employees] == ["E1", "E4"]
    assert len(rejected) == 2
    assert any("duplicate" in r["reason"] for r in rejected)


def test_load_employees_tolerates_alternative_headers(tmp_path):
    csv_file = tmp_path / "e.csv"
    csv_file.write_text(
        "Emp ID,Full Name,UAN Number,Date Of Birth\n"
        "E1,Asha Menon,100000035770,31-12-1980\n",
        encoding="utf-8",
    )
    employees, rejected = load_employees(csv_file)
    assert not rejected
    assert employees[0].name == "Asha Menon"


def test_slug_is_filesystem_safe():
    from uanfetch.records import Employee

    e = Employee(employee_id="EMP/001 A", name="X", uan="100000035770", dob="31-12-1980")
    assert "/" not in e.slug
    assert e.slug == "EMP_001_A_100000035770"

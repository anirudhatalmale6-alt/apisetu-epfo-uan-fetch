# uanfetch — bulk EPFO UAN Cards via APISetu

Fetches EPFO **UAN Card** PDFs in bulk from the APISetu gateway, validates every
response is genuinely a usable PDF, files them per employee and writes a report
plus an audit trail.

Runs end to end against a bundled mock server, so you can see exactly what it
does before your APISetu credentials arrive.

---

## Read this first: what APISetu actually offers

I checked EPFO's published OpenAPI document rather than going from memory. Their
entire collection on APISetu is **three endpoints**:

| Document | Path | Lookup parameters |
|---|---|---|
| UAN Card | `POST /uncrd/certificate` | `UAN`, `DOB` |
| Pension Certificate | `POST /pecer/certificate` | `PPONO`, `DOB` |
| Scheme Certificate | `POST /epfsc/certificate` | `SCNO`, `FullName`, `DOB` |

Base URL `https://apisetu.gov.in/epfindia/v3`. All three return `application/pdf`.

**There is no ECR API and no challan API**, for EPFO or ESIC, and nothing for
professional tax. That is structural rather than an oversight: APISetu and
DigiLocker are built around *citizen* documents, fetched using a citizen's own
identifiers and with their consent. ECR files and challans are *establishment*
records — they belong to the employer, cover many employees at once, and there
is no single citizen whose consent the request could hang on.

Those documents have to come from the EPFO Unified Employer Portal, the ESIC
employer portal and each state's professional tax portal, using your own logins.
That is a separate piece of work with its own caveats (EPFO put captcha and OTP
in that path, and portals change without notice).

---

## Getting credentials

From NeGD's consumer manual. Private organisations are explicitly eligible —
the wording covers "any entity, government department, private organisation, or
developer".

1. Register and log in at [apisetu.gov.in](https://apisetu.gov.in), then
   **Consume APIs**.
2. Pick the service category and submit your **use case** (minimum 250
   characters). A human reviews this. Vague submissions get delayed or rejected,
   so describe what your organisation does, why you need EPFO data and how you
   will use it.
3. Once approved: **API Directory** → search EPFO → **Subscribe** → select the
   UAN Card API.
   **You must upload an API Permission / Authorisation Letter issued by EPFO at
   this step, and it is mandatory.** You obtain it from EPFO directly. In
   practice this letter, not any of the code, is the long pole on the project —
   start chasing it early.
4. **Consume APIs → API Keys → Create API Keys.** You whitelist the server IPs
   and domain that will make the calls, so this tool needs to run from a fixed
   IP. **The key is displayed exactly once** — copy it there and then.
5. Your **Client ID** (also labelled Issuer ID) is bottom-left of the portal
   sidebar.

Publisher approval of your subscription is separate from APISetu approval; you
have no live access until the publisher approves.

---

## Install

```bash
pip install -r requirements.txt      # requests; pypdf is optional but recommended
cp .env.example .env                 # then fill in the two credentials
```

`pypdf` is optional. Without it the tool still works and still validates PDFs —
it just cannot report page count or orientation, or auto-rotate.

## Try it without credentials

Two terminals:

```bash
python -m mockserver.server                              # terminal 1
```

```bash
# terminal 2 — point .env at the mock:
#   APISETU_BASE_URL=http://127.0.0.1:8099/epfindia/v3
python -m uanfetch fetch --input employees.sample.csv
```

The sample file is built to exercise every branch. Behaviour is keyed off the
last digit of the UAN:

| UAN ends | What the mock does | What you should see |
|---|---|---|
| most digits | valid portrait PDF | `OK` |
| `1` | landscape PDF | `OK`, `rotated=yes` |
| `2` | HTTP 404 | `FAILED`, **1 attempt** — retrying cannot fix a 404 |
| `3` | 503 twice, then succeeds | `OK` after **3 attempts** |
| `4` | HTTP **200**, `Content-Type: application/pdf`, HTML body | `INVALID_PDF` |
| `5` | truncated PDF | `INVALID_PDF` |

Case `4` is the one that matters most. A gateway error page arriving with a 200
and a PDF content-type, written straight to disk, gives you a folder of files
that look fine in a listing and are all unopenable — and nobody finds out until
an auditor asks. Content-Type is treated as advisory; the `%PDF-` magic bytes
and the `%%EOF` trailer are what actually decide.

## Usage

```bash
python -m uanfetch check --input employees.csv    # validate the file, make no calls
python -m uanfetch fetch --input employees.csv
python -m uanfetch fetch -i employees.csv --limit 5   # try a few before the full run
```

Always run `check` first. It catches bad rows without spending rate limit on
requests that cannot succeed.

Options: `--limit N`, `--no-resume`, `--quiet`, `--output DIR`, `--env PATH`.

Exit codes: `0` all good, `1` configuration or input problem, `2` completed with
failures (see the report).

### Input

CSV with `employee_id`, `name`, `uan`, `dob`. Header spellings are flexible
(`Emp ID`, `UAN Number`, `Date Of Birth` all work).

Dates are accepted in any common layout and normalised to the `DD-MM-YYYY` the
API requires. Ambiguous dates are read **day-first**, per Indian convention.

Two spreadsheet traps are caught explicitly and named in the error rather than
left for you to work out:
- a UAN under 12 digits, which almost always means Excel stored the column as a
  number and dropped a leading zero
- a date exported as an Excel serial number (e.g. `29221`)

Duplicate UANs are reported rather than fetched twice.

### Output

```
output/
  pdfs/EMP001_100000035770.pdf
  report.csv        one row per input row, including rejected ones
  audit.jsonl       append-only, flushed per line, never contains the API key
```

`report.csv` status is one of `OK`, `SKIPPED`, `FAILED`, `INVALID_PDF` or
`REJECTED`. The totals reconcile against the input file, so nothing goes
missing silently.

Re-running **resumes**: anything already downloaded is skipped. Use
`--no-resume` to force a refetch. PDFs are written to a `.part` file and moved
into place, so an interrupted write never leaves a half-file that a later resume
would mistake for a complete one.

## Configuration

All settings live in `.env` — see `.env.example` for the full annotated list.
The ones worth knowing:

| Variable | Default | Notes |
|---|---|---|
| `APISETU_API_KEY` | — | required |
| `APISETU_CLIENT_ID` | — | required |
| `APISETU_BASE_URL` | live EPFO v3 | point at the mock to rehearse |
| `REQUESTS_PER_SECOND` | `2` | EPFO publish no rate limit; deliberately gentle |
| `MAX_RETRIES` | `3` | only for 429/5xx — a 400 or 404 is never retried |
| `NORMALISE_ORIENTATION` | `true` | rotate landscape pages upright |
| `MIN_PDF_BYTES` / `MAX_PDF_BYTES` | `512` / `10 MB` | acceptance window |
| `APISETU_SEND_CONSENT` | `false` | see below |

### On the consent artifact

Off by default, deliberately. The endpoint requires only `txnId` and `format`,
and while EPFO's schema makes `signature.signature` mandatory *inside* the
artifact, it does not publish how the artifact should be signed. Sending an
unsigned or wrongly-signed artifact risks rejection, where omitting it is
schema-valid.

The builder is complete and matches their `ConsentArtifactSchema` field for
field, and signs with a deterministic local HMAC so your audit trail is
tamper-evident. That is **not** a claim of compliance with whatever EPFO
expect. **Ask EPFO whether they require a consent artifact and how they want it
signed**, then set `APISETU_SEND_CONSENT=true`.

Every request is written to `audit.jsonl` regardless of this setting.

## Postman

`postman/APISetu-EPFO.postman_collection.json` covers all three EPFO endpoints.
Set the `baseUrl`, `apiKey` and `clientId` collection variables. Use **Send and
Download**, since the response body is the PDF itself.

The UAN Card request carries tests that assert the response really starts with
`%PDF-`, not merely that the status was 200.

## Tests

```bash
python -m pytest tests/ -q      # 45 tests, no network, no setup
```

The integration tests start the mock server on a free port inside the test
process and reset its state per test, so the suite gives the same result on
every run.

## Security notes

- `.env` is gitignored. Never commit it.
- The API key is never written to the report or the audit log.
- The key is shown once at creation in the portal; if lost, generate a new one.
- API keys are IP-whitelisted, so decide early where this will run.

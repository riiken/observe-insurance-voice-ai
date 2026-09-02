# Google Sheets setup

Two integrations, two spreadsheets:

| | Integration #1 — customers & claims | Integration #2 — interaction log |
| --- | --- | --- |
| Direction | read | **write** |
| Credential | API key | service account |
| Sharing | anyone with the link (viewer) | shared with the service account only |
| Sections | [1](#1-create-the-spreadsheet)–[6](#6-verify) below | [Integration #2](#integration-2--the-interaction-log) |

**They are deliberately separate files.** The write credential must not be able
to edit customer records — that is the whole reason for the split, and the
service warns at startup if you point both at the same spreadsheet.

## Integration #1 — customers and claims

> **Use synthetic data only.** Read access here is via an API key, which requires
> the sheet to be link-shared. Everything in `scripts/seed_data/` is invented.
> Never put real customer data in a link-shared sheet — see
> [Security note](#security-note).

---

## 1. Create the spreadsheet

Create one spreadsheet with two tabs named exactly **`Customers`** and
**`Claims`**. Row 1 of each is the header.

Column *order* does not matter — the adapter maps by header name — but the names
below must be present and spelled as shown.

### `Customers`

| Column | Meaning | Example |
| ------ | ------- | ------- |
| `customer_id` | Stable identifier, joins to `Claims` | `CUST-1001` |
| `full_name` | Caller's name, used for the greeting and the post-call record | `Maria Alvarez` |
| `phone_number` | Any readable format; normalised to E.164 on both sides of the match | `+1 555 010 1234` |
| `verification_value` | The shared secret proving identity — date of birth here | `1985-04-12` |

### `Claims`

| Column | Meaning | Example |
| ------ | ------- | ------- |
| `claim_id` | Claim reference | `CLM-88401` |
| `customer_id` | Joins to `Customers` | `CUST-1001` |
| `status` | One of the five supported statuses | `Documents Required` |
| `required_documents` | Separated by `;`, `,` or `\|`. Blank unless status is Documents Required | `Police report; Repair estimate` |
| `last_updated` | `YYYY-MM-DD` (also accepts `DD/MM/YYYY`, `MM/DD/YYYY`) | `2026-08-30` |

Recognised statuses: `Submitted`, `Under Review`, `Approved`, `Rejected`,
`Documents Required`. Case, spaces and hyphens are all tolerated. **An
unrecognised status is skipped, never guessed** — telling a caller their claim is
approved because a cell said something unparseable is the worst outcome this
system could produce.

## 2. Load the demo data

Paste [`scripts/seed_data/customers.csv`](../scripts/seed_data/customers.csv) and
[`scripts/seed_data/claims.csv`](../scripts/seed_data/claims.csv) into the
matching tabs (File → Import → Upload → *Replace current sheet*, comma
separated).

## 3. Share the sheet

**Share → General access → Anyone with the link → Viewer.**

An API key can only read a link-shared sheet. Without this the API returns 403,
which surfaces as `INTEGRATION_ERROR` — correctly, not as "customer not found".

## 4. Create an API key

1. <https://console.cloud.google.com/> → create or select a project
2. **APIs & Services → Library →** enable **Google Sheets API**
3. **APIs & Services → Credentials → Create credentials → API key**
4. Restrict it: **API restrictions → Google Sheets API**. Do this — an
   unrestricted key works against every API enabled on the project.

## 5. Configure the service

The spreadsheet id is the long token in the sheet URL:

```
https://docs.google.com/spreadsheets/d/1AbC...XyZ/edit
                                       ^^^^^^^^^^^ this
```

In `.env`:

```bash
GOOGLE_SHEETS_SPREADSHEET_ID=1AbC...XyZ
GOOGLE_SHEETS_API_KEY=AIza...
```

## 6. Verify

```bash
curl -s localhost:8000/ready | jq
```

```json
{ "status": "ready",
  "dependencies": [ { "name": "customers", "healthy": true, "duration_ms": 5.0 },
                    { "name": "claims",    "healthy": true, "duration_ms": 5.8 } ] }
```

`/ready` returns **503** with `"healthy": false` if the sheet is unreachable,
not shared, or missing a required column. `/health` stays **200** throughout —
liveness never depends on an upstream.

Leaving both variables blank is supported: the service starts, `/ready` reports
no dependencies, and the integration is simply absent. Unit tests never read
these values.

---

## Integration #2 — the interaction log

One row per completed call. Written with a service account, because an API key
cannot write.

### 1. Create the spreadsheet

A **second, separate** spreadsheet with one tab named `Interactions`. Row 1 must
be exactly these headers, in this order:

```
call_id | timestamp | caller_name | caller_phone | customer_id | claim_id |
authenticated | resolution | escalated | escalation_reason | sentiment | call_summary
```

The service verifies this header on startup; `/ready` reports `interactions` as
unhealthy if a column is missing.

### 2. Create a service account

1. <https://console.cloud.google.com/> → **IAM & Admin → Service Accounts →
   Create**
2. No project roles are needed — access is granted by sharing the sheet, not by
   IAM.
3. **Keys → Add key → JSON.** Download it.

### 3. Share the sheet with it

Copy the `client_email` from the JSON (`…@….iam.gserviceaccount.com`) and share
the **Interactions spreadsheet only** with it as **Editor**.

Do not share the customer spreadsheet with this account. It has write scope.

### 4. Configure

```bash
GOOGLE_INTERACTIONS_SPREADSHEET_ID=1XyZ...
GOOGLE_SERVICE_ACCOUNT_JSON={"type":"service_account","client_email":"...","private_key":"-----BEGIN PRIVATE KEY-----\n..."}
```

The JSON goes in as one line. Keep the `\n` escapes in `private_key` intact —
it is a JSON string, so they are literal backslash-n, not newlines.

Leave both blank and calls still complete: the record is built and written to
the logs instead of the sheet, and `/ready` simply omits `interactions`.

### 5. Verify

Make a call, hang up, and check the sheet. You should see exactly one row.
Hanging up twice, or a webhook redelivery, still produces one row — `call_id`
is the idempotency key.

```bash
docker compose logs api | jq 'select(.event == "postcall.persisted")'
```

---

## Demo data

Six customers and six claims, covering every mandatory scenario:

| Scenario | Phone | Verification | Outcome |
| -------- | ----- | ------------ | ------- |
| **Happy path** | `+1 555 010 1234` (Maria Alvarez) | `1985-04-12` | `CLM-88401`, Under Review |
| **Documents required** | `+1 555 010 2345` (James Okonkwo) | `1979-11-30` | `CLM-88402`, needs police report + repair estimate |
| **Customer not found** | `+1 555 010 9999` | — | `CUSTOMER_NOT_FOUND` |
| **Authentication failure** | `+1 555 010 1234` | any wrong date | `VERIFICATION_FAILED` |
| Approved claim | `+1 555 010 3456` (Priya Raman) | `1992-07-08` | `CLM-88403`, Approved |
| Rejected claim | `+1 555 010 5678` (Aisha Bello) | `1990-09-15` | `CLM-88405`, Rejected |
| Customer with no claim | `+1 555 010 6789` (Thomas Lindqvist) | `1974-12-03` | `CLAIM_NOT_FOUND` |

James Okonkwo deliberately holds **two** claims (`CLM-88402` and the older
`CLM-88406`) so the "most recently updated claim wins" rule is exercised by the
demo and not only by a unit test.

---

## Security note

**The verification value never leaves the repository.** `Customer` has no field
for it, so no service, tool, prompt, log line or API response can carry it even
by accident. Comparison is constant-time (`hmac.compare_digest`) after folding
case and spacing, since a caller saying "S W 1 A, 1 A A" should not fail on
whitespace.

**Customer lookup returns no claim information.** Lookup happens *before*
authentication, so everything it returns must be safe to disclose to an
unauthenticated caller. A test asserts the exact field list, so widening it
fails loudly.

**The API-key approach is a demo trade-off.** It requires the sheet to be
link-shared, which is fine for synthetic data and not acceptable for real
records. A service account (no link sharing, and the write access Integration #2
needs) is the production path — deferred and tracked in
[DEFERRED.md](DEFERRED.md).

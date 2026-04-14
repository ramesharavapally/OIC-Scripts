# OIC Gen3 Factory API — Extraction & Audit Scripts

Python scripts to extract and audit Oracle Integration Cloud (OIC) Generation 3 artifacts via the OIC Factory REST API. Outputs formatted Excel workbooks and CSV audit reports.

## Verified API Endpoints

All endpoints validated against official Oracle documentation:
https://docs.oracle.com/en/cloud/paas/application-integration/rest-api/

| Operation | Method | Path | Notes |
|---|---|---|---|
| List integrations | GET | `/ic/api/integration/v1/integrations` | Supports `q`, `limit`, `offset`, `expand`, `orderBy` |
| Get integration detail | GET | `/ic/api/integration/v1/integrations/{id}` | `{id}` = `CODE%7Cversion` (pipe URL-encoded) |
| Export integration IAR | GET | `/ic/api/integration/v1/integrations/{id}/archive` | Returns `application/octet-stream` |
| List connections | GET | `/ic/api/integration/v1/connections` | Same pagination pattern |
| Get connection detail | GET | `/ic/api/integration/v1/connections/{id}` | `{id}` = connection identifier |
| List lookups | GET | `/ic/api/integration/v1/lookups` | Same pagination pattern |
| Get lookup detail | GET | `/ic/api/integration/v1/lookups/{name}` | Uses `{name}` NOT `{id}` |

### Critical: `integrationInstance` Query Parameter

**Every single API call requires** the `integrationInstance` query parameter. This is the service instance name found on the OIC About page → "Service instance" field. Injected automatically by `OICClient._get()`.

### Integration ID Format

Integration IDs are composite: `CODE|VERSION` (e.g., `HELLO_WORLD|01.02.0000`).
In URL paths, the pipe must be URL-encoded: `HELLO_WORLD%7C01.02.0000`.

### Response JSON Field Names

The API schema documentation shows hyphenated names (`has-more`, `end-points`, `created-by`)
but the actual JSON responses use **camelCase** (`hasMore`, `endPoints`, `createdBy`).
All scripts handle both formats via fallback `.get()` lookups.

## Prerequisites

```bash
# Requires Python 3.14+ and uv
uv sync
```

## Configuration

Copy `.env.sample` to `.env` and fill in your values:

```env
OIC_HOST=https://design.integration.<region>.ocp.oraclecloud.com
INTEGRATION_INSTANCE=<your-service-instance-name>
OIC_CLIENT_ID=<your-oauth2-client-id>
OIC_CLIENT_SECRET=<your-oauth2-client-secret>
OIC_TOKEN_URL=https://idcs-<guid>.identity.oraclecloud.com/oauth2/v1/token
OIC_TOKEN_SCOPE=https://<scope>  # optional
```

### How to get these values

1. **OIC_HOST** — OIC Console URL (design-time). Found on OCI Console → Application Integration → Instances.
2. **INTEGRATION_INSTANCE** — OIC Console → About → "Service instance" field.
3. **OIC_CLIENT_ID / SECRET** — Create a Confidential Application in OCI IAM (IDCS):
   - Resources → Add OIC scope
   - Grant Type → Client Credentials
   - Grant the app `ServiceAdministrator` or `ServiceDeveloper` role on the OIC instance.
4. **OIC_TOKEN_URL** — IDCS domain → OAuth Configuration → Token Endpoint
5. **OIC_TOKEN_SCOPE** — The OIC resource scope from the IDCS app configuration (leave blank if not required)

## Authentication

OIC Gen3 factory APIs require **OAuth2 Client Credentials** grant. Basic Auth is NOT supported.

The `OICClient` class handles token acquisition and auto-refresh automatically.

## Usage

### API Export Scripts (require network + `.env`)

```bash
python 01_integrations_export.py   # Export all integrations + download IAR backups → Excel
python 02_connections_export.py    # Export all connections + usage mapping → Excel
python 03_lookups_export.py        # Export all lookups + data + usage mapping → Excel
```

### IAR Audit Scripts (offline — no API calls)

These scripts read `.iar` zip archives previously downloaded by `01_integrations_export.py`.
Set `IAR_FOLDER` at the top of each script to point to your backup directory before running.

```bash
python scan_hardcodes.py           # Detect hardcoded values in Assign variables and mappers → CSV
python scan_notifications.py       # Extract notification From/To/CC/Subject fields → CSV
python scan_child_integrations.py  # Map parent→child integration dependencies → CSV + Excel
```

## Output Files

All outputs are written to the `exports/` directory (created automatically).

| Script | Output File | Sheets / Format |
|---|---|---|
| `01_integrations_export.py` | `exports/OIC_Integrations_Report-latest.xlsx` | Integrations, Summary |
| `02_connections_export.py` | `exports/OIC_Connections_Report.xlsx` | Connections, Connection Usage, Summary |
| `03_lookups_export.py` | `exports/OIC_Lookups_Report.xlsx` | Lookups, Lookup Data, Lookup Usage, Summary |
| `scan_hardcodes.py` | `exports/hardcodes_audit.csv` | CSV |
| `scan_notifications.py` | `exports/notifications_audit.csv` | CSV |
| `scan_child_integrations.py` | `exports/child_integrations_audit.csv`, `exports/child_integrations_report.xlsx` | CSV + Excel (Detail, Summary) |

IAR backup archives are saved to `integrations/integration_backups_<timestamp>/` by `01_integrations_export.py`.

## IAR Audit Details

### scan_hardcodes.py

Detects four categories of hardcoded values inside `.iar` files:

| Category | Source | Description |
|---|---|---|
| `REPORT_ASSIGN` | `expr.properties` | BIP/BI Publisher report path (`/Custom…` or `…xdo`) in an Assign variable |
| `ASSIGN_LITERAL` | `expr.properties` | Any other hardcoded string literal in an Assign variable |
| `REPORT_MAPPER` | `.xsl` files | BIP report path embedded inline in an XSL mapper file |
| `STITCH_ASSIGN` | `stitch.json` | Hardcoded string literal in a Data Stitch Assign activity |

Set `REPORTS_ONLY = True` to limit output to BIP report path findings only.

### scan_notifications.py

Extracts From / To / CC / Subject fields from every notification activity. Classifies each field as:
- `HARDCODED` — a literal string value (e.g. `'oic-noreply@oracle.com'`)
- `DYNAMIC` — bound to a variable/XPath (e.g. `$gv_emails`)
- `EMPTY` — explicitly set to empty
- `MISSING` — no PARAM file found

Set `HARDCODED_ONLY = True` to report only notifications with at least one hardcoded field.

### scan_child_integrations.py

Scans `.jca` files inside each IAR for `collocatedics` adapter references to identify which
child integrations are invoked from each parent. Derives parent code + version from the IAR
filename (`CODE_VV.VV.VVVV.iar`) — the naming convention used by `01_integrations_export.py`.

## Lookup Deep Scan (Optional)

By default, `03_lookups_export.py` maps lookups to integrations using the `dependencies.lookups[]`
array from the integration list API. This catches explicitly declared dependencies.

To also detect lookups embedded in XSLT mappers (via `dvm:lookup()` calls), set
`ENABLE_IAR_SCAN = True` in `03_lookups_export.py`. This downloads each integration's IAR
archive in-memory and scans XSL/XML files — significantly slower but more thorough.

## Files

```
oic_scripts/
├── oic_client.py                  # Shared OIC client (OAuth2, pagination, ID encoding)
├── 01_integrations_export.py      # Integrations + IAR backups → Excel
├── 02_connections_export.py       # Connections + usage mapping → Excel
├── 03_lookups_export.py           # Lookups + data + usage mapping → Excel
├── scan_hardcodes.py              # IAR audit: hardcoded values → CSV
├── scan_notifications.py          # IAR audit: notification fields → CSV
├── scan_child_integrations.py     # IAR audit: parent→child dependencies → CSV + Excel
├── .env.sample                    # Environment variable template
├── exports/                       # All script output (auto-created)
├── integrations/                  # IAR backup archives (auto-created by script 01)
└── misc/                          # Standalone throwaway scripts (not part of main workflow)
```

# OIC Gen3 Factory API — Extraction Scripts

Python scripts to extract Integrations, Connections, and Lookups from Oracle Integration Cloud (OIC) Generation 3 into formatted Excel workbooks.

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

**Every single API call requires** the `integrationInstance` query parameter. This is the service instance name found on the OIC About page → "Service instance" field.

### Integration ID Format

Integration IDs are composite: `CODE|VERSION` (e.g., `HELLO_WORLD|01.02.0000`).
In URL paths, the pipe must be URL-encoded: `HELLO_WORLD%7C01.02.0000`.

### Response JSON Field Names

The API schema documentation shows hyphenated names (`has-more`, `end-points`, `created-by`)
but the actual JSON responses use **camelCase** (`hasMore`, `endPoints`, `createdBy`).
The scripts handle both formats via fallback lookups.

## Prerequisites

```bash
pip install requests pandas openpyxl
```

## Configuration

Edit `oic_client.py` — fill in these values:

```python
OIC_HOST             = "https://design.integration.<region>.ocp.oraclecloud.com"
INTEGRATION_INSTANCE = "<your-service-instance-name>"
OIC_CLIENT_ID        = "<your-oauth2-client-id>"
OIC_CLIENT_SECRET    = "<your-oauth2-client-secret>"
OIC_TOKEN_URL        = "https://idcs-<guid>.identity.oraclecloud.com/oauth2/v1/token"
OIC_SCOPE            = "https://<scope>"
```

### How to get these values

1. **OIC_HOST** — OIC Console URL (design-time). Found on OIC instance page in OCI Console.
2. **INTEGRATION_INSTANCE** — OIC Console → About → "Service instance" field.
3. **OIC_CLIENT_ID / SECRET** — Create a Confidential Application in OCI IAM (IDCS):
   - Resources → Add OIC scope
   - Grant Type → Client Credentials
4. **OIC_TOKEN_URL** — IDCS domain → OAuth Configuration → Token Endpoint
5. **OIC_SCOPE** — The OIC resource scope from the IDCS app configuration

## Authentication

OIC Gen3 factory APIs require **OAuth2 Client Credentials** grant. Basic Auth is NOT supported.

The `OICClient` class handles token acquisition and auto-refresh automatically.

## Usage

```bash
# Export all integrations
python 01_integrations_export.py

# Export all connections with integration-usage mapping
python 02_connections_export.py

# Export all lookups with integration-usage mapping
python 03_lookups_export.py
```

## Output Files

| Script | Output File | Sheets |
|---|---|---|
| 01 | `OIC_Integrations_Report.xlsx` | Integrations, Summary |
| 02 | `OIC_Connections_Report.xlsx` | Connections, Connection Usage, Summary |
| 03 | `OIC_Lookups_Report.xlsx` | Lookups, Lookup Data, Lookup Usage, Summary |

## Lookup Deep Scan (Optional)

By default, lookup-to-integration mapping uses the `dependencies.lookups[]` array from the
integration list API. This catches explicitly declared dependencies.

To also detect lookups embedded in XSLT mappers (via `dvm:lookup()` calls), set
`ENABLE_IAR_SCAN = True` in `03_lookups_export.py`. This downloads each integration's IAR
archive and scans XSL/XML files — significantly slower but more thorough.

## Files

```
oic_gen3_scripts/
├── oic_client.py              # Shared OIC client (OAuth2, pagination, API calls)
├── 01_integrations_export.py  # Integrations → Excel
├── 02_connections_export.py   # Connections + usage → Excel
├── 03_lookups_export.py       # Lookups + data + usage → Excel
└── README.md                  # This file
```

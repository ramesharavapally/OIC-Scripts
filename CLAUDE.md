# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Python scripts for extracting and auditing Oracle Integration Cloud (OIC) Generation 3 artifacts via the OIC Factory REST API. Outputs formatted Excel workbooks and CSV audit reports.

## Setup & Running

Uses `uv` with Python 3.14. Dependencies are managed in `pyproject.toml` / `uv.lock`.

```bash
uv sync                            # install dependencies
python 01_integrations_export.py   # export integrations + IAR backups → Excel
python 02_connections_export.py    # export connections + usage mapping → Excel
python 03_lookups_export.py        # export lookups + data + usage mapping → Excel
python scan_hardcodes.py           # scan IAR files for hardcoded values → CSV
python scan_notifications.py       # scan IAR files for notification details → CSV
python scan_child_integrations.py  # scan IAR files for child integration calls → CSV + Excel
```

Configuration lives in `.env` (loaded via `dotenv`). Copy `.env.sample` to `.env` and fill in values. Required env vars: `OIC_HOST`, `INTEGRATION_INSTANCE`, `OIC_CLIENT_ID`, `OIC_CLIENT_SECRET`, `OIC_TOKEN_URL`, and optionally `OIC_TOKEN_SCOPE`.

Scanner scripts (`scan_*.py`) write output to the `exports/` directory (created automatically). Export scripts (`01`–`03`) write Excel workbooks to the project root.

## Architecture

**`oic_client.py`** — Shared API client (`OICClient` class). Handles OAuth2 Client Credentials token lifecycle, auto-injects `integrationInstance` query param on every request, implements offset/limit pagination, and encodes composite integration IDs (`CODE|VERSION` → `CODE%7CVERSION`). All export scripts instantiate `OICClient` and call its methods.

**Numbered export scripts (01–03)** — Each is a standalone script that fetches data via `OICClient`, builds pandas DataFrames, then writes a multi-sheet Excel workbook using openpyxl with consistent formatting (header styles, alternating row fills, auto-width, freeze panes, auto-filter). Each script contains its own copy of the `_write_df_to_sheet` Excel formatting helper.

**IAR scanner scripts (`scan_hardcodes.py`, `scan_notifications.py`, `scan_child_integrations.py`)** — Offline tools that read `.iar` zip archives (downloaded by `01_integrations_export.py`). They don't call the OIC API. Configure them by editing `IAR_FOLDER` at the top of each file to point to a backup directory. Output goes to `exports/`. `scan_child_integrations.py` parses `.jca` files inside IARs for `collocatedics` adapter references to map parent→child integration dependencies.

**`misc/`** — Standalone throwaway scripts not part of the main OIC workflow (e.g. `generate_sample_pdfs.py`). Not imported by any other script.

**`main.py`** — Unused stub; ignore it.

## Key API Quirks

- The OIC API schema docs show hyphenated field names (`has-more`, `created-by`) but actual JSON uses camelCase (`hasMore`, `createdBy`). All scripts handle both via fallback `.get()` calls — maintain this pattern.
- Every OIC API call requires the `integrationInstance` query parameter (injected automatically by `OICClient._get()`).
- Lookup detail endpoint uses `{name}` in the path, not `{id}` — unlike integrations and connections.
- `03_lookups_export.py` has an optional `ENABLE_IAR_SCAN` flag for deep XSLT scanning via `dvm:lookup()` pattern matching inside IAR archives.

## Conventions

- No CLI argument parsing — scripts use module-level constants for configuration (`OUTPUT_FILE`, `BACKUP_DIR`, `IAR_FOLDER`, `REPORTS_ONLY`, etc.).
- `scan_hardcodes.py` deliberately skips files with "PARAM" in the path (those belong to `scan_notifications.py`).
- `scan_child_integrations.py` derives parent code + version from the IAR filename (`CODE_VV.VV.VVVV.iar`) — the IAR export naming from `01_integrations_export.py` must be preserved for this to work.
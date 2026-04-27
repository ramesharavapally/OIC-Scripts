# OCI Functions-Based OIC Integration Midnight Backup — Implementation Plan

## Overview

This document describes how to deploy an OCI Function that runs at midnight UTC daily, downloads all Oracle Integration Cloud (OIC) integration IAR backup files, uploads them to an OCI Object Storage bucket, and cleans up backups older than a configurable retention window.

The solution reuses the existing `OICClient` class (`oic_client.py`) with minimal, backward-compatible changes, and introduces a new `oic_backup_function/` directory as a self-contained OCI Functions deployment package.

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [OCI Infrastructure Setup](#2-oci-infrastructure-setup)
3. [Function Code Structure](#3-function-code-structure)
4. [Changes to `oic_client.py`](#4-changes-to-oic_clientpy)
5. [Module Implementations](#5-module-implementations)
6. [OCI Scheduler Setup](#6-oci-scheduler-setup)
7. [IAM Dynamic Group and Policies](#7-iam-dynamic-group-and-policies)
8. [Deployment Steps](#8-deployment-steps)
9. [Retention and Cleanup](#9-retention-and-cleanup)
10. [Testing Approach](#10-testing-approach)
11. [Key Design Decisions](#11-key-design-decisions)

---

## 1. Architecture Overview

```
OCI Resource Scheduler (cron: 0 0 * * * UTC)
        │
        ▼
OCI Function: oic-backup-fn (Python 3.11, 512 MB, 300s)
        │
        ├─► OCI Vault ──────────────► Load OIC credentials (6 secrets)
        │
        ├─► OIC REST API ────────────► get_all_integrations() → list
        │        │
        │        └─► export_integration_to_memory(id) → IAR bytes (×N)
        │
        └─► OCI Object Storage ─────► put_object() backups/{date}/{CODE}_{VER}.iar
                │
                └─► Lifecycle Policy → auto-delete objects after 30 days
```

**Backup object path pattern:**
```
backups/2026-04-14/INTEGRATION_CODE_01.00.0000.iar
backups/2026-04-14/ANOTHER_CODE_02.01.0000.iar
```

---

## 2. OCI Infrastructure Setup

### 2.1 Object Storage Bucket

| Setting | Value |
|---|---|
| Name | `oic-integration-backups` |
| Visibility | Private (no public access) |
| Storage Tier | Standard |
| Versioning | Disabled (date-prefixed paths serve as history) |
| Encryption | Oracle-managed keys (default) |

**Create via OCI CLI:**
```bash
oci os bucket create \
  --compartment-id <compartment-ocid> \
  --name oic-integration-backups \
  --namespace <tenancy-namespace> \
  --public-access-type NoPublicAccess
```

### 2.2 OCI Vault and Secrets

**Vault name:** `oic-backup-vault`  
**Vault type:** Default (software-protected)

Create one secret per OIC credential:

| Secret Name | Value to Store |
|---|---|
| `oic-host` | `https://design.integration.<region>.ocp.oraclecloud.com` |
| `oic-integration-instance` | `<service-instance-name>` (from OIC About page) |
| `oic-client-id` | OAuth2 Confidential App client ID |
| `oic-client-secret` | OAuth2 client secret |
| `oic-token-url` | `https://idcs-<guid>.identity.oraclecloud.com/oauth2/v1/token` |
| `oic-token-scope` | OAuth2 scope string (or empty string if not required) |

**Create a secret via OCI CLI:**
```bash
# Base64-encode the value first
echo -n "your-secret-value" | base64

oci vault secret create-base64 \
  --compartment-id <compartment-ocid> \
  --secret-name oic-client-secret \
  --vault-id <vault-ocid> \
  --key-id <master-key-ocid> \
  --secret-content-content "<base64-encoded-value>"
```

Save each secret's OCID after creation — these are stored as Function config variables (not the secret values themselves).

### 2.3 Functions Application

**Application name:** `oic-backup-app`  
**Subnet:** Private subnet with a Service Gateway route to OCI services (Object Storage, Vault) and a NAT Gateway or FastConnect route to reach the OIC host.

**Function configuration variables** (non-sensitive metadata and secret OCIDs):

| Key | Value |
|---|---|
| `BUCKET_NAME` | `oic-integration-backups` |
| `BUCKET_NAMESPACE` | `<tenancy-object-storage-namespace>` |
| `BACKUP_RETENTION_DAYS` | `30` |
| `SECRET_OCID_OIC_HOST` | `ocid1.vaultsecret.oc1...` |
| `SECRET_OCID_INTEGRATION_INSTANCE` | `ocid1.vaultsecret.oc1...` |
| `SECRET_OCID_CLIENT_ID` | `ocid1.vaultsecret.oc1...` |
| `SECRET_OCID_CLIENT_SECRET` | `ocid1.vaultsecret.oc1...` |
| `SECRET_OCID_TOKEN_URL` | `ocid1.vaultsecret.oc1...` |
| `SECRET_OCID_TOKEN_SCOPE` | `ocid1.vaultsecret.oc1...` |

**Function resource settings:**
- Memory: **512 MB**
- Timeout: **300 seconds** (increase to 540s if >200 integrations exist)

---

## 3. Function Code Structure

```
oic_backup_function/
├── func.py                  # OCI Function entrypoint (handler)
├── oic_client.py            # Copy of existing client + 2 additive changes
├── backup_uploader.py       # OCI Object Storage: upload, list, delete
├── secret_loader.py         # OCI Vault: retrieve and decode secrets
├── requirements.txt         # Python dependencies
├── func.yaml                # OCI Fn metadata (name, runtime, memory, timeout)
└── local_test.py            # Local integration test (uses .env + ~/.oci/config)
```

---

## 4. Changes to `oic_client.py`

The existing `oic_client.py` works correctly for local scripts. Two **additive, backward-compatible** changes are needed for the OCI Function.

### Change 1: Optional constructor parameters

The current constructor reads credentials from `os.environ` (populated via `.env`). The Function receives credentials from OCI Vault at runtime and must inject them explicitly.

**Before:**
```python
class OICClient:
    def __init__(self):
        self.host = OIC_HOST.rstrip("/")
        self.instance = INTEGRATION_INSTANCE
        self._client_id = OIC_CLIENT_ID
        self._client_secret = OIC_CLIENT_SECRET
        self._token_url = OIC_TOKEN_URL
        self._scope = OIC_SCOPE
```

**After** (backward-compatible — all args default to `None`, falling back to module-level constants):
```python
class OICClient:
    def __init__(self, host=None, instance=None, client_id=None,
                 client_secret=None, token_url=None, scope=None):
        self.host = (host or OIC_HOST).rstrip("/")
        self.instance = instance or INTEGRATION_INSTANCE
        self._client_id = client_id or OIC_CLIENT_ID
        self._client_secret = client_secret or OIC_CLIENT_SECRET
        self._token_url = token_url or OIC_TOKEN_URL
        self._scope = scope if scope is not None else OIC_SCOPE
```

Existing scripts that call `OICClient()` with no arguments continue to work unchanged via `.env` module-level constants.

### Change 2: Add `export_integration_to_memory()`

The existing `export_integration(id, output_path)` writes to a file path. The Function needs in-memory bytes to upload directly to Object Storage.

**New method** (original method untouched):
```python
def export_integration_to_memory(self, integration_id: str) -> bytes:
    """
    Stream the IAR archive into memory and return raw bytes.
    Used by OCI Function to avoid filesystem I/O.
    """
    self._ensure_token()
    url = f"{self.host}{BASE_PATH}/integrations/{integration_id}/archive"
    params = {"integrationInstance": self.instance}
    resp = self.session.get(
        url,
        params=params,
        headers={"Accept": "application/octet-stream"},
        stream=True,
        timeout=120,
    )
    resp.raise_for_status()
    buffer = io.BytesIO()
    for chunk in resp.iter_content(chunk_size=8192):
        buffer.write(chunk)
    return buffer.getvalue()
```

---

## 5. Module Implementations

### 5.1 `secret_loader.py`

Reads secret OCIDs from Function config env vars, fetches their base64-encoded values from OCI Vault, and returns a dict ready to unpack into `OICClient(**credentials)`.

```python
import base64
import logging
import os
import oci

logger = logging.getLogger(__name__)


def _get_secrets_client():
    """Resource Principal inside OCI; falls back to ~/.oci/config locally."""
    try:
        signer = oci.auth.signers.get_resource_principals_signer()
        return oci.secrets.SecretsClient(config={}, signer=signer)
    except Exception:
        return oci.secrets.SecretsClient(oci.config.from_file())


def get_secret_value(client, secret_ocid: str) -> str:
    response = client.get_secret_bundle(secret_id=secret_ocid)
    content = response.data.secret_bundle_content
    return base64.b64decode(content.content).decode("utf-8")


def load_oic_credentials() -> dict:
    """
    Returns a dict of OIC credentials loaded from OCI Vault.
    Keys match OICClient constructor kwargs: host, instance, client_id,
    client_secret, token_url, scope.
    """
    client = _get_secrets_client()
    secret_map = {
        "host":          os.environ["SECRET_OCID_OIC_HOST"],
        "instance":      os.environ["SECRET_OCID_INTEGRATION_INSTANCE"],
        "client_id":     os.environ["SECRET_OCID_CLIENT_ID"],
        "client_secret": os.environ["SECRET_OCID_CLIENT_SECRET"],
        "token_url":     os.environ["SECRET_OCID_TOKEN_URL"],
        "scope":         os.environ.get("SECRET_OCID_TOKEN_SCOPE", ""),
    }
    credentials = {}
    for param, ocid in secret_map.items():
        if not ocid:
            credentials[param] = ""
            continue
        credentials[param] = get_secret_value(client, ocid)
        logger.info("Loaded secret for '%s'", param)
    return credentials
```

### 5.2 `backup_uploader.py`

Encapsulates all OCI Object Storage interactions.

```python
import io
import logging
import oci

logger = logging.getLogger(__name__)


def get_object_storage_client():
    """Resource Principal inside OCI; falls back to ~/.oci/config locally."""
    try:
        signer = oci.auth.signers.get_resource_principals_signer()
        return oci.object_storage.ObjectStorageClient(config={}, signer=signer)
    except Exception:
        return oci.object_storage.ObjectStorageClient(oci.config.from_file())


def upload_iar(client, namespace: str, bucket: str,
               object_name: str, iar_bytes: bytes) -> None:
    client.put_object(
        namespace_name=namespace,
        bucket_name=bucket,
        object_name=object_name,
        put_object_body=io.BytesIO(iar_bytes),
        content_type="application/octet-stream",
    )
    logger.info("Uploaded %s (%d bytes)", object_name, len(iar_bytes))


def list_backup_prefixes(client, namespace: str, bucket: str) -> list[str]:
    """Returns date-level prefixes like ['backups/2026-01-01/', ...]"""
    response = client.list_objects(
        namespace_name=namespace,
        bucket_name=bucket,
        prefix="backups/",
        delimiter="/",
    )
    return response.data.prefixes or []


def delete_objects_under_prefix(client, namespace: str,
                                 bucket: str, prefix: str) -> int:
    """Deletes all objects under prefix. Returns count of deleted objects."""
    deleted = 0
    next_start = None
    while True:
        kwargs = dict(namespace_name=namespace, bucket_name=bucket,
                      prefix=prefix, limit=1000)
        if next_start:
            kwargs["start"] = next_start
        response = client.list_objects(**kwargs)
        objects = response.data.objects
        if not objects:
            break
        for obj in objects:
            client.delete_object(namespace_name=namespace,
                                 bucket_name=bucket, object_name=obj.name)
            deleted += 1
        if response.data.next_start_with:
            next_start = response.data.next_start_with
        else:
            break
    return deleted
```

### 5.3 `func.py`

The OCI Function handler — orchestrates all steps.

```python
"""
func.py — OCI Function entrypoint for OIC Integration Backup.
Triggered nightly at midnight UTC by OCI Resource Scheduler.
"""
import io
import json
import logging
import os
from datetime import date, timedelta

import fdk.response

from oic_client import OICClient
from backup_uploader import (
    get_object_storage_client, upload_iar,
    list_backup_prefixes, delete_objects_under_prefix,
)
from secret_loader import load_oic_credentials

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
logger = logging.getLogger(__name__)


def handler(ctx, data: io.BytesIO = None):
    run_date = date.today().isoformat()
    prefix = f"backups/{run_date}/"

    bucket_name      = os.environ["BUCKET_NAME"]
    bucket_namespace = os.environ["BUCKET_NAMESPACE"]
    retention_days   = int(os.environ.get("BACKUP_RETENTION_DAYS", "30"))

    results = {"date": run_date, "total": 0,
               "succeeded": 0, "failed": 0,
               "errors": [], "deleted_prefixes": []}

    # 1. Load OIC credentials from Vault
    try:
        credentials = load_oic_credentials()
    except Exception as exc:
        logger.error("FATAL: Vault credential load failed: %s", exc)
        results["errors"].append(str(exc))
        return fdk.response.Response(ctx, response_data=json.dumps(results),
                                     status_code=500,
                                     headers={"Content-Type": "application/json"})

    # 2. Initialize clients
    oic = OICClient(**credentials)
    os_client = get_object_storage_client()

    # 3. Fetch all integrations
    try:
        integrations = oic.get_all_integrations(limit=100, order_by="name")
    except Exception as exc:
        logger.error("FATAL: Integration list retrieval failed: %s", exc)
        results["errors"].append(str(exc))
        return fdk.response.Response(ctx, response_data=json.dumps(results),
                                     status_code=500,
                                     headers={"Content-Type": "application/json"})

    results["total"] = len(integrations)
    logger.info("Found %d integrations to back up.", len(integrations))

    # 4. Download each IAR and upload to Object Storage
    for intg in integrations:
        intg_id = intg.get("id", "")
        code    = intg.get("code", intg_id.split("|")[0] if "|" in intg_id else intg_id)
        version = intg.get("version", intg_id.split("|")[1] if "|" in intg_id else "")

        if not code or not version:
            results["failed"] += 1
            results["errors"].append(f"Missing code/version: id={intg_id}")
            continue

        safe_code   = code.replace("/", "_").replace("\\", "_")
        safe_ver    = version.replace("/", "_").replace("\\", "_")
        object_name = f"{prefix}{safe_code}_{safe_ver}.iar"
        enc_id      = OICClient.encode_integration_id(code, version)

        try:
            iar_bytes = oic.export_integration_to_memory(enc_id)
            upload_iar(os_client, bucket_namespace, bucket_name,
                       object_name, iar_bytes)
            results["succeeded"] += 1
        except Exception as exc:
            results["failed"] += 1
            results["errors"].append(f"{code}|{version}: {exc}")
            logger.warning("Failed to back up %s|%s: %s", code, version, exc)

    logger.info("Backup complete: %d succeeded, %d failed.",
                results["succeeded"], results["failed"])

    # 5. Retention cleanup
    try:
        cutoff = date.today() - timedelta(days=retention_days)
        for dated_prefix in list_backup_prefixes(os_client, bucket_namespace, bucket_name):
            try:
                prefix_date = date.fromisoformat(dated_prefix.rstrip("/").split("/")[-1])
                if prefix_date < cutoff:
                    count = delete_objects_under_prefix(
                        os_client, bucket_namespace, bucket_name, dated_prefix)
                    results["deleted_prefixes"].append(
                        {"prefix": dated_prefix, "objects_deleted": count})
                    logger.info("Cleaned up %s (%d objects)", dated_prefix, count)
            except ValueError:
                pass  # Non-date prefix — skip
    except Exception as exc:
        logger.warning("Retention cleanup error: %s", exc)
        results["errors"].append(f"Cleanup warning: {exc}")

    status_code = 200 if results["failed"] == 0 else 207
    return fdk.response.Response(ctx, response_data=json.dumps(results, indent=2),
                                 status_code=status_code,
                                 headers={"Content-Type": "application/json"})
```

### 5.4 `requirements.txt`

```
requests>=2.32.5
oci>=2.125.0
fdk>=0.1.75
python-dotenv>=0.9.9
```

Note: `pandas`, `openpyxl`, and `reportlab` are excluded — the Function only downloads IARs and uploads to OCI. Lean dependencies reduce cold-start time.

### 5.5 `func.yaml`

```yaml
schema_version: 20180708
name: oic-backup-fn
version: 0.0.1
runtime: python3.11
build_image: fnproject/python:3.11-dev
run_image: fnproject/python:3.11
entrypoint: /python/bin/fdk /function/func.py handler
memory: 512
timeout: 300
```

> **Note:** Use Python 3.11 — OCI Functions does not yet support Python 3.14 (the version used by the existing local scripts). The `oic_client.py` copy in `oic_backup_function/` uses no 3.14-specific syntax, so this is transparent.

---

## 6. OCI Scheduler Setup

### Option A: OCI Resource Scheduler (Recommended)

In OCI Console → Platform Services → Resource Scheduler:

1. Create a new **Schedule**.
2. **Trigger type:** Time-based.
3. **Cron expression:** `0 0 * * *` (midnight UTC, every day).
4. **Action:** Invoke OCI Function → select `oic-backup-app` / `oic-backup-fn`.
5. **Payload:** `{}` (the handler ignores the input body).

**Via OCI CLI:**
```bash
oci resource-scheduler schedule create \
  --compartment-id <compartment-ocid> \
  --display-name "OIC Daily Midnight Backup" \
  --cron-expression "0 0 * * *" \
  --time-zone "UTC" \
  --action-type "invoke_oci_function" \
  --function-ocid <function-ocid>
```

### Option B: Manual test invoke (pre-scheduler validation)

```bash
fn invoke oic-backup-app oic-backup-fn
# or
oci fn function invoke \
  --function-id <function-ocid> \
  --file "-" \
  --body '{}'
```

---

## 7. IAM Dynamic Group and Policies

### Step 1 — Create Dynamic Group

```
Name: oic-backup-fn-dynamic-group
Matching rule:
  ALL {resource.type = 'fnfunc', resource.compartment.id = '<compartment-ocid>'}
```

### Step 2 — Create IAM Policy

```
# Read OIC credentials from Vault
Allow dynamic-group oic-backup-fn-dynamic-group to read secret-bundles in compartment <compartment-name>
Allow dynamic-group oic-backup-fn-dynamic-group to use vaults in compartment <compartment-name>
Allow dynamic-group oic-backup-fn-dynamic-group to use keys in compartment <compartment-name>

# Write IAR files to Object Storage
Allow dynamic-group oic-backup-fn-dynamic-group to manage objects in compartment <compartment-name>
  where target.bucket.name='oic-integration-backups'

# List prefixes during retention cleanup
Allow dynamic-group oic-backup-fn-dynamic-group to read buckets in compartment <compartment-name>
  where target.bucket.name='oic-integration-backups'
```

---

## 8. Deployment Steps

### 8.1 Prerequisites

```bash
# Install OCI CLI
bash -c "$(curl -L https://raw.githubusercontent.com/oracle/oci-cli/master/scripts/install/install.sh)"

# Install Fn CLI
curl -LSs https://raw.githubusercontent.com/fnproject/cli/master/install | sh

# Configure OCI CLI
oci setup config

# Configure Fn context
fn create context <region-key> --provider oracle
fn use context <region-key>
fn update context oracle.compartment-id <compartment-ocid>
fn update context api-url https://functions.<region>.oraclecloud.com
fn update context registry <region-key>.ocir.io/<tenancy-namespace>/<repo-prefix>
```

### 8.2 OCIR Login

```bash
# Create auth token: OCI Console → User Settings → Auth Tokens
docker login <region-key>.ocir.io \
  --username "<tenancy-namespace>/<username>" \
  --password "<auth-token>"
```

### 8.3 Create Function Application

```bash
fn create app oic-backup-app \
  --annotation oracle.com/oci/subnetIds='["<private-subnet-ocid>"]'
```

### 8.4 Set Function Configuration Variables

```bash
fn config app oic-backup-app BUCKET_NAME oic-integration-backups
fn config app oic-backup-app BUCKET_NAMESPACE <tenancy-namespace>
fn config app oic-backup-app BACKUP_RETENTION_DAYS 30
fn config app oic-backup-app SECRET_OCID_OIC_HOST <secret-ocid>
fn config app oic-backup-app SECRET_OCID_INTEGRATION_INSTANCE <secret-ocid>
fn config app oic-backup-app SECRET_OCID_CLIENT_ID <secret-ocid>
fn config app oic-backup-app SECRET_OCID_CLIENT_SECRET <secret-ocid>
fn config app oic-backup-app SECRET_OCID_TOKEN_URL <secret-ocid>
fn config app oic-backup-app SECRET_OCID_TOKEN_SCOPE <secret-ocid>
```

### 8.5 Deploy

```bash
cd oic_backup_function/
fn deploy --app oic-backup-app
```

### 8.6 Enable OCI Logging

In OCI Console → Observability & Management → Logging → Log Groups:
- Create a log group `oic-backup-logs`
- Enable **invoke** service logs for `oic-backup-fn`

---

## 9. Retention and Cleanup

### Approach 1: OCI Object Lifecycle Policy (safety net — always recommended)

Apply a lifecycle rule that auto-deletes objects under `backups/` after 30 days. This runs independently of the Function.

```bash
oci os object-lifecycle-policy put \
  --namespace <namespace> \
  --bucket-name oic-integration-backups \
  --items '[{
    "name": "delete-old-backups",
    "action": "DELETE",
    "timeAmount": 30,
    "timeUnit": "DAYS",
    "objectNameFilter": {"inclusionPrefixes": ["backups/"]},
    "isEnabled": true
  }]'
```

### Approach 2: Function-based cleanup (in `func.py`)

At the end of each invocation, `func.py` lists `backups/` date prefixes, parses dates, and explicitly deletes prefixes older than `BACKUP_RETENTION_DAYS`. This provides log visibility and same-day cleanup confirmation.

**Recommendation:** Use both. The lifecycle policy acts as a safety net even when the Function fails or is paused.

---

## 10. Testing Approach

### 10.1 Local Unit Tests

```
oic_backup_function/tests/
├── test_oic_client.py       # Test export_integration_to_memory with mock requests
├── test_backup_uploader.py  # Test upload_iar with mock OCI SDK
└── test_secret_loader.py    # Test load_oic_credentials with mock Vault client
```

Use `unittest.mock.patch` to mock `requests.Session.get` and `oci.secrets.SecretsClient`.

### 10.2 Local Integration Test (`local_test.py`)

Runs with real OIC credentials from `.env` and uploads to a `backups/test-local/` prefix using `~/.oci/config`. Tests 2 integrations to confirm the full download → upload path works before deploying.

```bash
cd oic_backup_function/
cp ../.env .env          # reuse existing OIC credentials
python local_test.py     # requires ~/.oci/config to be configured
```

### 10.3 OCI End-to-End Test (post-deploy, pre-scheduler)

```bash
# Invoke manually
fn invoke oic-backup-app oic-backup-fn

# Verify
# 1. OCI Console → Object Storage → oic-integration-backups → backups/<today>/
# 2. OCI Console → Functions → oic-backup-app → oic-backup-fn → Metrics
# 3. OCI Console → Logging → oic-backup-logs → check per-integration entries
```

Confirm the JSON response shows `succeeded > 0` and `failed == 0` before enabling the scheduler.

---

## 11. Key Design Decisions

| Decision | Choice | Reason |
|---|---|---|
| IAR I/O | In-memory `bytes` | Avoids `/tmp` overhead; typical IAR files are 10–200 KB each |
| Credential storage | OCI Vault Secrets | No secrets in Function config, Docker image, or environment |
| OCI auth | Resource Principal | No API keys to rotate; secure by default inside OCI Functions |
| `oic_client.py` changes | Additive only | Backward-compatible; existing local scripts unchanged |
| Partial failures | Continue and log | One failed IAR should not abort all others |
| Python runtime | 3.11 | OCI Functions does not yet support Python 3.14 |
| Scheduler | OCI Resource Scheduler | Native OCI service, no extra infrastructure |
| Retention | Lifecycle policy + Function cleanup | Policy is the safety net; Function cleanup provides log visibility |
| `oic_backup_function/` isolation | Separate directory | Does not pollute the existing project; self-contained deployment unit |

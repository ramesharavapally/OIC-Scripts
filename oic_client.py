"""
OIC Gen3 Factory REST API Client
=================================
OAuth2-only client for Oracle Integration Cloud Generation 3.

API Base Path:  /ic/api/integration/v1
Auth:           OAuth2 Client Credentials (IDCS/OCI IAM)

Official API Reference:
  https://docs.oracle.com/en/cloud/paas/application-integration/rest-api/

IMPORTANT — integrationInstance Query Parameter:
  Every OIC Gen3 factory API call REQUIRES the 'integrationInstance' query param.
  This is the service instance name (found on OIC About page → Service instance field).
"""

import os
import time
import logging
import urllib.parse
import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
#  Configuration — loaded from .env
# ─────────────────────────────────────────────
OIC_HOST             = os.environ["OIC_HOST"]
INTEGRATION_INSTANCE = os.environ["INTEGRATION_INSTANCE"]
OIC_CLIENT_ID        = os.environ["OIC_CLIENT_ID"]
OIC_CLIENT_SECRET    = os.environ["OIC_CLIENT_SECRET"]
OIC_TOKEN_URL        = os.environ["OIC_TOKEN_URL"]
OIC_SCOPE            = os.environ.get("OIC_TOKEN_SCOPE", "")


# ─────────────────────────────────────────────
#  API Base Path (do NOT change)
# ─────────────────────────────────────────────
BASE_PATH = "/ic/api/integration/v1"


class OICClient:
    """
    HTTP client for OIC Gen3 Factory REST APIs.

    Handles:
      - OAuth2 token acquisition and auto-refresh
      - integrationInstance param injection on every request
      - Paginated list retrieval (offset/limit pattern)
      - Integration ID encoding (CODE|VERSION → CODE%7CVERSION)
    """

    def __init__(self):
        self.host = OIC_HOST.rstrip("/")
        self.instance = INTEGRATION_INSTANCE
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
        })
        self._token = None
        self._token_expiry = 0

    # ── OAuth2 ──────────────────────────────

    def _ensure_token(self):
        if self._token and time.time() < self._token_expiry - 60:
            return
        logger.info("Acquiring OAuth2 token...")
        resp = requests.post(
            OIC_TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "scope": OIC_SCOPE,
            },
            auth=(OIC_CLIENT_ID, OIC_CLIENT_SECRET),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        data = resp.json()
        self._token = data["access_token"]
        self._token_expiry = time.time() + data.get("expires_in", 3600)
        self.session.headers["Authorization"] = f"Bearer {self._token}"
        logger.info("OAuth2 token acquired (expires in %ds)", data.get("expires_in", 3600))

    # ── Core HTTP ───────────────────────────

    def _get(self, path, params=None, stream=False):
        """GET with auto-token refresh and integrationInstance injection."""
        self._ensure_token()
        url = f"{self.host}{BASE_PATH}{path}"
        if params is None:
            params = {}
        params["integrationInstance"] = self.instance
        resp = self.session.get(url, params=params, stream=stream, timeout=120)
        resp.raise_for_status()
        return resp

    # ── Integration ID helper ───────────────

    @staticmethod
    def encode_integration_id(code, version):
        """
        Build the composite integration ID for path params.
        Format: CODE|VERSION  →  URL-encoded as CODE%7CVERSION
        Example: HELLO_WORLD|01.02.0000 → HELLO_WORLD%7C01.02.0000
        """
        return urllib.parse.quote(f"{code}|{version}", safe="")

    # ─────────────────────────────────────────
    #  INTEGRATIONS
    # ─────────────────────────────────────────

    def get_all_integrations(self, limit=100, q=None, expand=None, order_by=None):
        """
        GET /ic/api/integration/v1/integrations
        Retrieves all integrations with pagination (offset/limit).

        Params:
            limit    — page size (max records per call)
            q        — filter string, e.g. {status: 'ACTIVATED'}
            expand   — 'connection' or 'connection.adapter'
            order_by — 'name' or 'time'

        Returns: list of all integration dicts
        """
        all_items = []
        offset = 0
        while True:
            params = {"limit": limit, "offset": offset}
            if q:
                params["q"] = q
            if expand:
                params["expand"] = expand
            if order_by:
                params["orderBy"] = order_by

            resp = self._get("/integrations", params=params)
            data = resp.json()

            items = data.get("items", [])
            all_items.extend(items)

            has_more = data.get("hasMore", data.get("has-more", False))
            if not has_more or len(items) == 0:
                break
            offset += limit
            logger.info("Fetched %d integrations so far...", len(all_items))

        logger.info("Total integrations retrieved: %d", len(all_items))
        return all_items

    def get_integration_detail(self, integration_id, expand=None):
        """
        GET /ic/api/integration/v1/integrations/{id}
        Retrieves detailed info for a single integration.

        Param integration_id:
            Already-encoded composite ID (CODE%7CVERSION).
            Use encode_integration_id() to build it.
        """
        params = {}
        if expand:
            params["expand"] = expand
        resp = self._get(f"/integrations/{integration_id}", params=params)
        return resp.json()

    def export_integration(self, integration_id, output_path):
        """
        GET /ic/api/integration/v1/integrations/{id}/archive
        Downloads the integration IAR file.

        Returns: path to saved .iar file
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
        with open(output_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        logger.info("Exported integration to %s", output_path)
        return output_path

    # ─────────────────────────────────────────
    #  CONNECTIONS
    # ─────────────────────────────────────────

    def get_all_connections(self, limit=100, q=None, expand=None):
        """
        GET /ic/api/integration/v1/connections
        Retrieves all connections with pagination.
        """
        all_items = []
        offset = 0
        while True:
            params = {"limit": limit, "offset": offset}
            if q:
                params["q"] = q
            if expand:
                params["expand"] = expand

            resp = self._get("/connections", params=params)
            data = resp.json()

            items = data.get("items", [])
            all_items.extend(items)

            has_more = data.get("hasMore", data.get("has-more", False))
            if not has_more or len(items) == 0:
                break
            offset += limit

        logger.info("Total connections retrieved: %d", len(all_items))
        return all_items

    def get_connection_detail(self, connection_id, expand=None):
        """
        GET /ic/api/integration/v1/connections/{id}
        Retrieves detailed info for a single connection.
        """
        params = {}
        if expand:
            params["expand"] = expand
        resp = self._get(f"/connections/{connection_id}", params=params)
        return resp.json()

    # ─────────────────────────────────────────
    #  LOOKUPS
    # ─────────────────────────────────────────

    def get_all_lookups(self, limit=100):
        """
        GET /ic/api/integration/v1/lookups
        Retrieves all lookups with pagination.
        """
        all_items = []
        offset = 0
        while True:
            params = {"limit": limit, "offset": offset}
            resp = self._get("/lookups", params=params)
            data = resp.json()

            items = data.get("items", [])
            all_items.extend(items)

            has_more = data.get("hasMore", data.get("has-more", False))
            if not has_more or len(items) == 0:
                break
            offset += limit

        logger.info("Total lookups retrieved: %d", len(all_items))
        return all_items

    def get_lookup_detail(self, lookup_name, expand=None):
        """
        GET /ic/api/integration/v1/lookups/{name}
        Retrieves detailed info for a single lookup BY NAME (not by ID).

        IMPORTANT: The lookup endpoint uses {name} in the path, not {id}.
        """
        params = {}
        if expand:
            params["expand"] = expand
        encoded_name = urllib.parse.quote(lookup_name, safe="")
        resp = self._get(f"/lookups/{encoded_name}", params=params)
        return resp.json()

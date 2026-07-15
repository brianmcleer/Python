"""
Script Name: unregister_feature_service_replicas.py
Version: 1.0.0
License: Apache-2.0

Description:
    Audits every feature service (sync) replica across one or more
    ArcGIS Enterprise geodatabases and performs rolling age-based
    cleanup. Each run:

        1. Scans each configured SDE geodatabase with
           arcpy.da.ListReplicas and merges the results, tagging every
           replica with its source database
        2. Extracts the unique feature service URLs from the replicas
           and queries each service's REST /replicas endpoint, then
           each replica's info endpoint (this is where replicaOwner
           lives - it is NOT in the list response)
        3. Joins SDE-side and REST-side data by replicaID into one
           combined record per replica
        4. Unregisters replicas older than UNREGISTER_AGE_DAYS via the
           service's unRegisterReplica REST operation (rolling cleanup)
        5. Exports the surviving replicas to replicas.json and
           replicas.js for a monitoring dashboard, including a manifest
           of scanned databases so zero-replica databases still render
        6. Reports duplicate replicas (same owner + same service +
           different creation dates), a common symptom of offline
           clients that re-downloaded areas without syncing

    Why this matters: every Field Maps / offline area download
    registers a sync replica against the geodatabase. Abandoned
    replicas pin geodatabase versions, which blocks compress from
    trimming state lineage and slowly degrades performance. Nothing
    cleans them up automatically - this script does.

Authentication:
    - Generates an ArcGIS token from Portal (or Server admin) and uses
      it for all REST calls
    - Caches the token and refreshes automatically shortly before
      expiry, with a one-shot retry on token errors (codes 498/499)
    - client=referer token mode; set the REFERER to match your
      security configuration (or switch to client=ip in TokenManager)

Setup:
    1. Copy secrets.example.py to secrets.py and set the password for
       your automation account. secrets.py is gitignored.
    2. Fill in the CONFIGURATION section: SDE connection files, portal
       URL, username, output directory, age threshold.
    3. Run with DRY_RUN = True first: it audits everything and reports
       exactly which replicas WOULD be unregistered, without touching
       anything.
    4. Set DRY_RUN = False and schedule it daily.

Dependencies:
    - Python 3.8+ in an ArcGIS Pro / Server environment (arcpy)
    - requests
"""

from __future__ import annotations

import os
import sys
import json
import time
import logging
import datetime as dt
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import requests

import smtplib
from email.mime.text import MIMEText

# --- LOCAL SECRETS IMPORT -------------------------------------------------------
# Pull the automation account password from a secrets.py living in the SAME
# folder as this script. The script's own directory is inserted onto sys.path
# so the import resolves regardless of the working directory the scheduler
# launches the job from. secrets.py is gitignored - never commit it, and
# restrict filesystem permissions on this folder.
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)
from secrets import ARCGIS_PASSWORD as PASSWORD

# --- LOGGING SETUP ------------------------------------------------------------

def setup_logging() -> tuple:
    """Configure logging with timestamped log file.

    Returns:
        tuple: (logger, log_path) - Logger instance and path to log file
    """
    log_dir = "Log"

    # Ensure log directory exists
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    # Create timestamped log filename
    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_filename = f"unregister_feature_service_replicas_{timestamp}.log"
    log_path = os.path.join(log_dir, log_filename)

    # Create logger
    logger = logging.getLogger("UnregisterReplicas")
    logger.setLevel(logging.INFO)

    # File handler
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.INFO)

    # Console handler (for PyCharm output)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)

    # Formatter
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)

    # Add handlers
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    logger.info(f"Logging initialized. Log file: {log_path}")

    return logger, log_path

# Initialize logger
logger, LOG_FILE_PATH = setup_logging()

# --- CONFIG -------------------------------------------------------------------

# SDE workspaces to audit for feature service replicas. Each connection file is
# scanned via arcpy.da.ListReplicas and the results are merged into one list.
# Replicas are keyed by replicaID / serviceName downstream, so the source
# geodatabase does not affect the REST join, age-based cleanup, or export.
# Add another line here to bring an additional geodatabase into the audit.
SDE_WORKSPACES = [
    r"C:\path\to\dbConnections\mygdb1.sde",
    r"C:\path\to\dbConnections\mygdb2.sde",
]

# True = audit and export only; log which replicas WOULD be unregistered
# without unregistering anything. Set False for the production run.
DRY_RUN = True
VERIFY_SSL = True

REQUEST_TIMEOUT = 60
SLEEP_BETWEEN_REQUESTS_SEC = 0.05

# Auth config
AUTH_MODE = "PORTAL"  # "PORTAL" or "SERVER_ADMIN"

# For AUTH_MODE="PORTAL":
PORTAL_BASE = "https://your-portal.example.com/portal"  # includes /portal in most Enterprise installs
# Token URL will be: {PORTAL_BASE}/sharing/rest/generateToken

# For AUTH_MODE="SERVER_ADMIN" (only if you truly need it):
SERVER_ADMIN_BASE = "https://your-server.example.com:6443/arcgis"
# Token URL will be: {SERVER_ADMIN_BASE}/admin/generateToken

# Credentials
# Username stays inline; password is imported from the local secrets.py (see
# the LOCAL SECRETS IMPORT block near the top of this file).
ARCGIS_USERNAME = "your_automation_user"
ARCGIS_PASSWORD = PASSWORD

# Required for client="referer" token mode (common/best). Must match your security config.
REFERER = os.environ.get("ARCGIS_REFERER", "https://your-app.example.com").strip()

# Token generation options
TOKEN_EXPIRATION_MINUTES = 60  # request lifetime; server may clamp
TOKEN_REFRESH_SAFETY_SEC = 120  # refresh this many seconds before expiry

# Replica age threshold for automatic unregistration (days)
# Replicas older than this will be unregistered regardless of owner
UNREGISTER_AGE_DAYS = 45

# Output directory for the replicas.json / replicas.js dashboard files.
# Defaults to the bundled dashboard folder so replica-viewer.html picks up
# fresh data automatically. Point it at your web server folder in production.
OUTPUT_DIR = os.path.join(_SCRIPT_DIR, "dashboard")

# Keep the log file after successful runs? (Always kept on failure.)
DELETE_LOG_ON_SUCCESS = False

logger.info("Configuration loaded")
logger.info(f"SDE_WORKSPACES ({len(SDE_WORKSPACES)}): {SDE_WORKSPACES}")
logger.info(f"AUTH_MODE: {AUTH_MODE}")
logger.info(f"PORTAL_BASE: {PORTAL_BASE}")
logger.info(f"ARCGIS_USERNAME: {ARCGIS_USERNAME}")
logger.info(f"VERIFY_SSL: {VERIFY_SSL}")
logger.info(f"REQUEST_TIMEOUT: {REQUEST_TIMEOUT}s")
logger.info(f"TOKEN_EXPIRATION_MINUTES: {TOKEN_EXPIRATION_MINUTES}")
logger.info(f"UNREGISTER_AGE_DAYS: {UNREGISTER_AGE_DAYS}")
logger.info(f"DRY_RUN: {DRY_RUN}")
logger.info(f"OUTPUT_DIR: {OUTPUT_DIR}")

# --- DATA TYPES ---------------------------------------------------------------

@dataclass
class CombinedReplica:
    replica_id: str
    service_url: str

    # Name of the SDE geodatabase the replica was found in, derived from the
    # connection file name in SDE_WORKSPACES (e.g. "mygdb1").
    source_database: Optional[str] = None

    sde_name: Optional[str] = None
    sde_owner: Optional[str] = None
    sde_creation_date: Optional[dt.datetime] = None
    sde_replica_date: Optional[dt.datetime] = None
    sde_type: Optional[str] = None
    sde_role: Optional[str] = None
    sde_version: Optional[str] = None
    sde_last_send: Optional[dt.datetime] = None
    sde_last_receive: Optional[dt.datetime] = None
    sde_target_type: Optional[str] = None
    sde_has_conflicts: Optional[bool] = None

    rest_replica_name: Optional[str] = None
    rest_replica_owner: Optional[str] = None
    rest_creation_date: Optional[dt.datetime] = None
    rest_last_sync_date: Optional[dt.datetime] = None
    rest_sync_model: Optional[str] = None
    rest_sync_direction: Optional[str] = None
    rest_target_type: Optional[str] = None

# --- HELPERS ------------------------------------------------------------------

def parse_epoch_millis(millis: Any) -> Optional[dt.datetime]:
    if millis is None:
        return None
    try:
        ms = int(millis)
        result = dt.datetime.fromtimestamp(ms / 1000.0, tz=dt.timezone.utc)
        logger.info(f"Parsed epoch millis {millis} to {result.isoformat()}")
        return result
    except Exception as ex:
        logger.info(f"Failed to parse epoch millis {millis}: {ex}")
        return None

def safe_getattr(obj: Any, attr: str) -> Any:
    try:
        return getattr(obj, attr)
    except Exception:
        return None

def normalize_feature_server_url(service_url: str) -> str:
    if not service_url:
        return service_url
    u = service_url.strip().split("?", 1)[0].rstrip("/")
    return u

def service_replicas_list_url(feature_server_url: str) -> str:
    return f"{feature_server_url.rstrip('/')}/replicas"

def service_replica_info_url(feature_server_url: str, replica_id: str) -> str:
    return f"{feature_server_url.rstrip('/')}/replicas/{replica_id}"

def fmt_dt(d: Optional[dt.datetime]) -> str:
    return "None" if not d else d.isoformat()

# --- TOKEN MANAGEMENT ---------------------------------------------------------

class TokenManager:
    def __init__(self) -> None:
        self._token: Optional[str] = None
        self._expires_at_epoch: int = 0  # seconds since epoch
        logger.info("TokenManager initialized")

    def _token_url(self) -> str:
        if AUTH_MODE.upper() == "PORTAL":
            url = f"{PORTAL_BASE.rstrip('/')}/sharing/rest/generateToken"
            logger.info(f"Using Portal token URL: {url}")
            return url
        if AUTH_MODE.upper() == "SERVER_ADMIN":
            url = f"{SERVER_ADMIN_BASE.rstrip('/')}/admin/generateToken"
            logger.info(f"Using Server Admin token URL: {url}")
            return url
        raise ValueError(f"Unsupported AUTH_MODE: {AUTH_MODE}")

    def _need_refresh(self) -> bool:
        if not self._token:
            logger.info("Token refresh needed: no existing token")
            return True
        now = int(time.time())
        needs_refresh = now >= (self._expires_at_epoch - TOKEN_REFRESH_SAFETY_SEC)
        if needs_refresh:
            logger.info(f"Token refresh needed: current time {now} >= expiry threshold {self._expires_at_epoch - TOKEN_REFRESH_SAFETY_SEC}")
        return needs_refresh

    def get_token(self) -> str:
        logger.info("get_token() called")

        if not ARCGIS_USERNAME or not ARCGIS_PASSWORD:
            logger.info("ERROR: Missing credentials - ARCGIS_USERNAME or ARCGIS_PASSWORD not set")
            raise RuntimeError(
                "Missing credentials. Set env vars ARCGIS_USERNAME and ARCGIS_PASSWORD."
            )

        if not self._need_refresh():
            logger.info("Using cached token (still valid)")
            return self._token  # type: ignore[return-value]

        url = self._token_url()
        logger.info(f"Requesting new token from: {url}")

        # Most Enterprise deployments accept client=referer. If yours uses IP-based tokens,
        # swap to client="ip" and provide "ip" param instead.
        data = {
            "f": "json",
            "username": ARCGIS_USERNAME,
            "password": ARCGIS_PASSWORD,
            "client": "referer",
            "referer": REFERER,
            "expiration": str(TOKEN_EXPIRATION_MINUTES),
        }

        logger.info(f"Token request parameters: username={ARCGIS_USERNAME}, client=referer, expiration={TOKEN_EXPIRATION_MINUTES}")

        resp = requests.post(url, data=data, verify=VERIFY_SSL, timeout=REQUEST_TIMEOUT)
        logger.info(f"Token request HTTP status: {resp.status_code}")
        resp.raise_for_status()
        tok = resp.json()

        if "error" in tok:
            logger.info(f"Token error received: {tok['error']}")
            raise RuntimeError(f"Token error from {url}: {tok['error']}")

        token = tok.get("token")
        expires = tok.get("expires")  # usually epoch millis for Portal; sometimes epoch millis for Server too

        if not token:
            logger.info(f"Token response missing 'token' field: {tok}")
            raise RuntimeError(f"Token response missing 'token': {tok}")

        # Normalize expiry
        expires_at = 0
        if expires is not None:
            try:
                exp_int = int(expires)
                # If it's millis, convert to seconds
                expires_at = exp_int // 1000 if exp_int > 10_000_000_000 else exp_int
            except Exception:
                expires_at = 0

        # Fallback if server didn't return expires: assume requested expiration
        if not expires_at:
            expires_at = int(time.time()) + TOKEN_EXPIRATION_MINUTES * 60
            logger.info(f"No expiry in response, using fallback: {expires_at}")

        self._token = str(token)
        self._expires_at_epoch = expires_at

        expiry_dt = dt.datetime.fromtimestamp(expires_at, tz=dt.timezone.utc)
        logger.info(f"Token acquired successfully, expires at: {expiry_dt.isoformat()}")

        return self._token

TOKEN_MANAGER = TokenManager()

# --- REST GET -----------------------------------------------------------------

def rest_get(url: str, params: Dict[str, Any]) -> Any:
    """
    GET helper that:
    - injects token automatically
    - retries once on token-related errors (refreshing token)
    """
    logger.info(f"REST GET request to: {url}")

    def _do(params_local: Dict[str, Any]) -> Any:
        headers = {"User-Agent": "replica-audit/1.0"}
        if REFERER:
            headers["Referer"] = REFERER

        logger.info(f"Executing HTTP GET with params: {list(params_local.keys())}")
        resp = requests.get(url, params=params_local, verify=VERIFY_SSL, timeout=REQUEST_TIMEOUT, headers=headers)
        logger.info(f"HTTP response status: {resp.status_code}")
        resp.raise_for_status()
        data = resp.json()
        return data

    params = dict(params)
    params.setdefault("f", "json")

    # First attempt with current/refresh token
    params["token"] = TOKEN_MANAGER.get_token()
    data = _do(params)

    # Handle ArcGIS REST error shape
    if isinstance(data, dict) and "error" in data:
        err = data.get("error", {})
        msg = (err.get("message") or "").lower()
        code = err.get("code")
        is_token_problem = ("token" in msg) or (code in (498, 499))
        if is_token_problem:
            logger.info(f"Token problem detected (code={code}, msg={msg}), refreshing token and retrying")
            # Force refresh and retry once
            TOKEN_MANAGER._token = None
            params["token"] = TOKEN_MANAGER.get_token()
            data = _do(params)

    if isinstance(data, dict) and "error" in data:
        logger.info(f"REST error: {data['error']}")
        raise RuntimeError(f"REST error calling {url}: {data['error']}")

    logger.info(f"REST GET successful for: {url}")
    return data

def rest_post(url: str, data: Dict[str, Any]) -> Any:
    """
    POST helper that:
    - injects token automatically
    - retries once on token-related errors (refreshing token)
    """
    logger.info(f"REST POST request to: {url}")

    def _do(data_local: Dict[str, Any]) -> Any:
        headers = {"User-Agent": "replica-audit/1.0"}
        if REFERER:
            headers["Referer"] = REFERER

        logger.info(f"Executing HTTP POST with data keys: {list(data_local.keys())}")
        resp = requests.post(url, data=data_local, verify=VERIFY_SSL, timeout=REQUEST_TIMEOUT, headers=headers)
        logger.info(f"HTTP response status: {resp.status_code}")
        resp.raise_for_status()
        result = resp.json()
        return result

    data = dict(data)
    data.setdefault("f", "json")

    # First attempt with current/refresh token
    data["token"] = TOKEN_MANAGER.get_token()
    result = _do(data)

    # Handle ArcGIS REST error shape
    if isinstance(result, dict) and "error" in result:
        err = result.get("error", {})
        msg = (err.get("message") or "").lower()
        code = err.get("code")
        is_token_problem = ("token" in msg) or (code in (498, 499))
        if is_token_problem:
            logger.info(f"Token problem detected (code={code}, msg={msg}), refreshing token and retrying")
            # Force refresh and retry once
            TOKEN_MANAGER._token = None
            data["token"] = TOKEN_MANAGER.get_token()
            result = _do(data)

    if isinstance(result, dict) and "error" in result:
        logger.info(f"REST error: {result['error']}")
        raise RuntimeError(f"REST error calling {url}: {result['error']}")

    logger.info(f"REST POST successful for: {url}")
    return result

def unregister_replica(feature_server_url: str, replica_id: str) -> bool:
    """
    Unregister a replica from a feature service.
    Returns True on success, False on failure.
    """
    base = normalize_feature_server_url(feature_server_url)
    unregister_url = f"{base}/unRegisterReplica"

    logger.info(f"Unregistering replica {replica_id} from {base}")

    try:
        result = rest_post(unregister_url, {"replicaID": replica_id})

        # Check for success response
        if isinstance(result, dict):
            success = result.get("success", False)
            if success:
                logger.info(f"Successfully unregistered replica {replica_id}")
                return True
            else:
                logger.info(f"Unregister returned success=False for replica {replica_id}: {result}")
                return False

        logger.info(f"Unexpected response format for unregister: {result}")
        return False

    except Exception as ex:
        logger.info(f"Failed to unregister replica {replica_id}: {ex}")
        return False

# --- CORE LOGIC ---------------------------------------------------------------

def load_sde_replicas(workspace: str) -> List[Any]:
    logger.info(f"Loading SDE replicas from workspace: {workspace}")
    import arcpy  # ArcGIS Pro Python env
    arcpy.env.workspace = workspace
    logger.info("arcpy.env.workspace set")

    logger.info("Calling arcpy.da.ListReplicas()...")
    reps = arcpy.da.ListReplicas(workspace, True)
    result = reps or []
    logger.info(f"arcpy.da.ListReplicas() returned {len(result)} replicas")
    return result

def extract_service_urls(sde_replicas: List[Tuple[Any, str]]) -> List[str]:
    logger.info(f"Extracting service URLs from {len(sde_replicas)} SDE replicas")
    urls = set()
    for r, _db in sde_replicas:
        u = safe_getattr(r, "serviceName")
        if u:
            normalized = normalize_feature_server_url(u)
            urls.add(normalized)
            logger.info(f"Found service URL: {normalized}")

    sorted_urls = sorted(urls)
    logger.info(f"Extracted {len(sorted_urls)} unique service URLs")
    return sorted_urls

def fetch_rest_replica_infos_for_service(feature_server_url: str) -> Dict[str, Dict[str, Any]]:
    logger.info(f"Fetching REST replica info for service: {feature_server_url}")
    base = normalize_feature_server_url(feature_server_url)
    infos: Dict[str, Dict[str, Any]] = {}

    # 1) list replicas to get IDs
    list_url = service_replicas_list_url(base)
    logger.info(f"Requesting replica list from: {list_url}")

    list_params = {
        "f": "json",
        "returnVersion": "true",
        "returnLastSyncDate": "true",
    }
    lst = rest_get(list_url, list_params)

    # Some servers return an array; others wrap
    if isinstance(lst, list):
        replica_summaries = lst
        logger.info(f"Replica list returned as array with {len(replica_summaries)} items")
    elif isinstance(lst, dict) and isinstance(lst.get("replicas"), list):
        replica_summaries = lst["replicas"]
        logger.info(f"Replica list returned as dict with {len(replica_summaries)} replicas")
    else:
        logger.info(f"WARNING: Unexpected replicas list shape from {list_url}: {type(lst)}")
        replica_summaries = []

    replica_ids: List[str] = []
    for item in replica_summaries:
        if not isinstance(item, dict):
            continue
        rid = item.get("replicaID") or item.get("replicaId") or item.get("replicaGuid")
        if rid:
            replica_ids.append(str(rid))
            logger.info(f"Found replica ID: {rid}")

    logger.info(f"Found {len(replica_ids)} replica IDs to fetch details for")

    # 2) per replica info (owner lives here as replicaOwner)
    for rid in replica_ids:
        info_url = service_replica_info_url(base, rid)
        logger.info(f"Fetching replica info: {info_url}")
        try:
            info = rest_get(info_url, {"f": "json"})
            if isinstance(info, dict):
                infos[rid] = info
                owner = info.get("replicaOwner") or info.get("owner")
                name = info.get("replicaName") or info.get("name")
                logger.info(f"Replica {rid}: owner={owner}, name={name}")
        except Exception as ex:
            logger.info(f"WARNING: Failed to fetch replica info {info_url}: {ex}")
        time.sleep(SLEEP_BETWEEN_REQUESTS_SEC)

    logger.info(f"Successfully fetched {len(infos)} replica info records for service: {base}")
    return infos

def build_combined_list(
    sde_replicas: List[Tuple[Any, str]],
    rest_infos_by_service: Dict[str, Dict[str, Dict[str, Any]]],
) -> List[CombinedReplica]:
    logger.info(f"Building combined list from {len(sde_replicas)} SDE replicas")
    combined: List[CombinedReplica] = []

    for r, source_db in sde_replicas:
        rid = safe_getattr(r, "replicaID")
        if not rid:
            logger.info("Skipping replica with no replicaID")
            continue
        rid = str(rid)

        service = normalize_feature_server_url(safe_getattr(r, "serviceName") or "")
        rest_info = rest_infos_by_service.get(service, {}).get(rid, {})

        sde_name = safe_getattr(r, "name")
        sde_owner = safe_getattr(r, "owner")
        rest_owner = rest_info.get("replicaOwner") or rest_info.get("owner")

        logger.info(f"Processing replica {rid}: db={source_db}, sde_name={sde_name}, sde_owner={sde_owner}, rest_owner={rest_owner}")

        combined.append(
            CombinedReplica(
                replica_id=rid,
                service_url=service,
                source_database=source_db,
                sde_name=sde_name,
                sde_owner=sde_owner,
                sde_creation_date=safe_getattr(r, "creationDate"),
                sde_replica_date=safe_getattr(r, "replicaDate"),
                sde_type=safe_getattr(r, "type"),
                sde_role=safe_getattr(r, "role"),
                sde_version=safe_getattr(r, "version"),
                sde_last_send=safe_getattr(r, "lastSend"),
                sde_last_receive=safe_getattr(r, "lastReceive"),
                sde_target_type=safe_getattr(r, "targetType"),
                sde_has_conflicts=safe_getattr(r, "hasConflicts"),
                rest_replica_name=rest_info.get("replicaName") or rest_info.get("name"),
                rest_replica_owner=rest_owner,
                rest_creation_date=parse_epoch_millis(rest_info.get("creationDate")),
                rest_last_sync_date=parse_epoch_millis(rest_info.get("lastSyncDate")),
                rest_sync_model=rest_info.get("syncModel"),
                rest_sync_direction=rest_info.get("syncDirection"),
                rest_target_type=rest_info.get("targetType"),
            )
        )

    logger.info(f"Combined list built with {len(combined)} entries")
    return combined

def find_same_owner_same_service_diff_creation(combined: List[CombinedReplica]) -> List[CombinedReplica]:
    logger.info(f"Finding duplicates (same owner + same service + different creation dates) from {len(combined)} entries")

    def owner_key(cr: CombinedReplica) -> str:
        return (cr.rest_replica_owner or "").strip() or (cr.sde_owner or "").strip()

    def creation_key(cr: CombinedReplica) -> Optional[dt.datetime]:
        return cr.rest_creation_date or cr.sde_creation_date or cr.sde_replica_date

    groups: Dict[Tuple[str, str], List[CombinedReplica]] = defaultdict(list)
    for cr in combined:
        o = owner_key(cr)
        s = cr.service_url
        if o and s:
            groups[(o, s)].append(cr)

    logger.info(f"Grouped replicas into {len(groups)} owner+service combinations")

    matches: List[CombinedReplica] = []
    for (owner, service), reps in groups.items():
        if len(reps) < 2:
            continue
        dates = [creation_key(r) for r in reps if creation_key(r) is not None]
        if len(dates) < 2:
            continue
        if len({d.isoformat() for d in dates}) > 1:
            logger.info(f"Found duplicate group: owner={owner}, service={service}, count={len(reps)}")
            reps_sorted = sorted(
                reps,
                key=lambda x: (
                    creation_key(x) is None,
                    creation_key(x) or dt.datetime.min.replace(tzinfo=dt.timezone.utc),
                ),
            )
            matches.extend(reps_sorted)

    logger.info(f"Found {len(matches)} replicas matching duplicate criteria")
    return matches

# --- JSON/HTML EXPORT ---------------------------------------------------------

def export_to_json(combined: List[CombinedReplica]) -> None:
    """Export combined replicas to JSON and JS files for the web viewer."""
    logger.info("Exporting replica data to JSON and JS")

    # Build export data - ONLY feature service replicas (have email owners, not DBO)
    export_data = []
    skipped_db_replicas = 0

    for cr in combined:
        owner = (cr.rest_replica_owner or cr.sde_owner or "").strip()

        # Skip database replicas - they have owners like "DBO", "sde", or no @ symbol
        # Feature service replicas have email addresses as owners
        if not owner or "@" not in owner or owner.upper() in ("DBO", "SDE", "SYSTEM"):
            skipped_db_replicas += 1
            continue

        creation = cr.rest_creation_date or cr.sde_creation_date or cr.sde_replica_date

        # Extract service name from URL
        service_name = ""
        if cr.service_url:
            parts = cr.service_url.rstrip('/').split('/')
            for i, p in enumerate(parts):
                if p.lower() == 'featureserver' and i > 0:
                    service_name = parts[i-1]
                    break

        export_data.append({
            "replicaId": cr.replica_id,
            "name": cr.sde_name or cr.rest_replica_name or "",
            "owner": owner,
            "sourceDatabase": cr.source_database or "",
            "serviceUrl": cr.service_url,
            "serviceName": service_name,
            "creationDate": creation.isoformat() if creation else None,
            "lastSyncDate": cr.rest_last_sync_date.isoformat() if cr.rest_last_sync_date else None,
            "syncModel": cr.rest_sync_model or "",
            "syncDirection": cr.rest_sync_direction or "",
            "sdeType": cr.sde_type or "",
            "sdeRole": cr.sde_role or "",
            "hasConflicts": cr.sde_has_conflicts or False,
        })

    logger.info(f"Filtered out {skipped_db_replicas} database replicas (DBO/system owners)")

    # Per-database export counts (also answers "how many per DB" from the log)
    exported_by_db: Dict[str, int] = defaultdict(int)
    for rec in export_data:
        exported_by_db[rec.get("sourceDatabase") or "unknown"] += 1
    for db, count in sorted(exported_by_db.items()):
        logger.info(f"  Exported from {db}: {count}")

    # Manifest of every scanned database, so the dashboard can show a database
    # with ZERO exported replicas as 0 rather than omitting it entirely.
    scanned_databases = [os.path.splitext(os.path.basename(ws))[0] for ws in SDE_WORKSPACES]

    # Ensure output directory exists
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    json_path = os.path.join(OUTPUT_DIR, "replicas.json")
    js_path = os.path.join(OUTPUT_DIR, "replicas.js")

    # Write replicas.json (standard JSON)
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(export_data, f, indent=2)
    logger.info(f"Exported {len(export_data)} replicas to {json_path}")

    # Write replicas.js (JavaScript variable for browser auto-load)
    # Include timestamp so viewer knows when data was generated, plus the
    # scanned-database manifest for zero-count rendering
    generated_at = dt.datetime.now().isoformat()
    with open(js_path, 'w', encoding='utf-8') as f:
        f.write(f"var REPLICA_GENERATED = \"{generated_at}\";\n")
        f.write("var REPLICA_DATABASES = ")
        json.dump(scanned_databases, f)
        f.write(";\n")
        f.write("var REPLICA_DATA = ")
        json.dump(export_data, f)
        f.write(";")
    logger.info(f"Exported {len(export_data)} replicas to {js_path} (generated: {generated_at}, databases: {scanned_databases})")

# --- MAIN ---------------------------------------------------------------------

# --- Email notification on failure (optional) ----------------------------------
ENABLE_EMAIL = False
SMTP_HOST    = "smtp.example.com"
FROM_EMAIL   = "noreply@example.com"
TO_EMAIL     = "you@example.com"


def send_failure_email(subject: str, body: str) -> None:
    if not ENABLE_EMAIL:
        return
    try:
        msg = MIMEText(body)
        msg['Subject'] = subject
        msg['From'] = FROM_EMAIL
        msg['To'] = TO_EMAIL
        with smtplib.SMTP(SMTP_HOST) as server:
            server.sendmail(FROM_EMAIL, TO_EMAIL, msg.as_string())
        logger.info(f"Failure email sent to {TO_EMAIL}")
    except Exception as e:
        logger.info(f"Failed to send notification email: {e}")


def main() -> int:
    logger.info("=" * 60)
    logger.info("REPLICA UNREGISTER SCRIPT STARTED")
    logger.info("=" * 60)

    # Track start time for elapsed time reporting
    start_time = time.time()

    try:
        # Quick auth sanity check up front
        logger.info("Performing initial token authentication check...")
        try:
            _ = TOKEN_MANAGER.get_token()
            logger.info("Initial token authentication successful")
        except Exception as ex:
            logger.info(f"ERROR: Token generation failed: {ex}")
            raise

        # 1) SDE replicas (merged across all configured workspaces)
        logger.info("-" * 40)
        logger.info(f"STEP 1: Loading SDE replicas from {len(SDE_WORKSPACES)} workspace(s)")
        logger.info("-" * 40)
        sde_reps: List[Tuple[Any, str]] = []
        for ws in SDE_WORKSPACES:
            db_name = os.path.splitext(os.path.basename(ws))[0]
            try:
                ws_reps = load_sde_replicas(ws)
            except Exception as ex:
                logger.info(f"ERROR: Failed to list SDE replicas from {ws}: {ex}")
                raise
            logger.info(f"  {db_name} ({ws}): {len(ws_reps)} replicas")
            sde_reps.extend((r, db_name) for r in ws_reps)

        logger.info(f"Found {len(sde_reps)} total replicas across {len(SDE_WORKSPACES)} SDE workspace(s)")

        # 2) Unique services
        logger.info("-" * 40)
        logger.info("STEP 2: Extracting unique service URLs")
        logger.info("-" * 40)
        services = extract_service_urls(sde_reps)
        logger.info(f"Found {len(services)} unique service URLs via replica.serviceName")

        # 3) REST fetch for each service
        logger.info("-" * 40)
        logger.info("STEP 3: Fetching REST replica info for each service")
        logger.info("-" * 40)
        rest_infos_by_service: Dict[str, Dict[str, Dict[str, Any]]] = {}
        for idx, svc in enumerate(services, 1):
            if not svc:
                continue
            logger.info(f"Processing service {idx}/{len(services)}: {svc}")
            try:
                infos = fetch_rest_replica_infos_for_service(svc)
                rest_infos_by_service[svc] = infos
                logger.info(f"Retrieved {len(infos)} replica info records for service")
            except Exception as ex:
                logger.info(f"WARNING: Failed fetching service {svc}: {ex}")
                rest_infos_by_service[svc] = {}
            time.sleep(SLEEP_BETWEEN_REQUESTS_SEC)

        # 4) Join
        logger.info("-" * 40)
        logger.info("STEP 4: Building combined list")
        logger.info("-" * 40)
        combined = build_combined_list(sde_reps, rest_infos_by_service)
        logger.info(f"Combined list has {len(combined)} entries (joined by replicaID)")

        # 5) Unregister replicas older than threshold (rolling cleanup)
        logger.info("-" * 40)
        logger.info(f"STEP 5: Unregistering replicas older than {UNREGISTER_AGE_DAYS} days (rolling cleanup)")
        if DRY_RUN:
            logger.info("DRY_RUN = True: unregistration will be logged but NOT performed")
        logger.info("-" * 40)

        # Track successfully unregistered replica IDs for filtering
        unregistered_ids: set = set()

        if UNREGISTER_AGE_DAYS and UNREGISTER_AGE_DAYS > 0:
            # Calculate cutoff date
            now = dt.datetime.now(tz=dt.timezone.utc)
            cutoff_date = now - dt.timedelta(days=UNREGISTER_AGE_DAYS)
            logger.info(f"Current date: {now.isoformat()}")
            logger.info(f"Cutoff date ({UNREGISTER_AGE_DAYS} days ago): {cutoff_date.isoformat()}")
            logger.info(f"Replicas created before {cutoff_date.date()} will be unregistered")

            # Find all replicas older than the cutoff
            def get_creation_date(cr: CombinedReplica) -> Optional[dt.datetime]:
                d = cr.rest_creation_date or cr.sde_creation_date or cr.sde_replica_date
                return d

            replicas_to_unregister: List[CombinedReplica] = []
            replicas_to_keep: List[CombinedReplica] = []

            for cr in combined:
                creation = get_creation_date(cr)
                owner = (cr.rest_replica_owner or cr.sde_owner or "").strip()

                # Skip database replicas (no email owner)
                if not owner or "@" not in owner or owner.upper() in ("DBO", "SDE", "SYSTEM"):
                    continue

                if creation is None:
                    # No creation date - keep it (can't determine age)
                    replicas_to_keep.append(cr)
                    logger.info(f"Keeping (no creation date): replicaID={cr.replica_id}, owner={owner}")
                elif creation < cutoff_date:
                    # Older than cutoff - mark for unregistration
                    age_days = (now - creation).days
                    replicas_to_unregister.append(cr)
                    logger.info(f"Will unregister ({age_days} days old): replicaID={cr.replica_id}, owner={owner}, created={fmt_dt(creation)}")
                else:
                    # Newer than cutoff - keep it
                    age_days = (now - creation).days
                    replicas_to_keep.append(cr)

            logger.info(f"Summary: keeping {len(replicas_to_keep)} replicas, unregistering {len(replicas_to_unregister)} replicas older than {UNREGISTER_AGE_DAYS} days")

            if replicas_to_unregister:
                # Group by owner for reporting
                by_owner: Dict[str, List[CombinedReplica]] = defaultdict(list)
                for cr in replicas_to_unregister:
                    owner = (cr.rest_replica_owner or cr.sde_owner or "").strip()
                    by_owner[owner].append(cr)

                logger.info(f"Replicas to unregister by owner:")
                for owner, reps in sorted(by_owner.items()):
                    logger.info(f"  {owner}: {len(reps)} replicas")

                by_db: Dict[str, int] = defaultdict(int)
                for cr in replicas_to_unregister:
                    by_db[cr.source_database or "unknown"] += 1
                logger.info(f"Replicas to unregister by source database:")
                for db, count in sorted(by_db.items()):
                    logger.info(f"  {db}: {count} replicas")

                # Perform unregistration
                unregister_success = 0
                unregister_failed = 0
                success_by_db: Dict[str, int] = defaultdict(int)

                for cr in replicas_to_unregister:
                    creation = get_creation_date(cr)
                    age_days = (now - creation).days if creation else "unknown"
                    owner = (cr.rest_replica_owner or cr.sde_owner or "").strip()
                    logger.info(f"Unregistering: replicaID={cr.replica_id}, db={cr.source_database}, name={cr.sde_name or cr.rest_replica_name}, owner={owner}, age={age_days} days, service={cr.service_url}")

                    if DRY_RUN:
                        logger.info(f"DRY_RUN: would unregister replicaID={cr.replica_id} (skipped)")
                    elif unregister_replica(cr.service_url, cr.replica_id):
                        unregister_success += 1
                        success_by_db[cr.source_database or "unknown"] += 1
                        unregistered_ids.add(cr.replica_id)  # Track successful unregistration
                    else:
                        unregister_failed += 1

                    time.sleep(SLEEP_BETWEEN_REQUESTS_SEC)

                logger.info(f"Unregister results: {unregister_success} succeeded, {unregister_failed} failed")
                for db, count in sorted(success_by_db.items()):
                    logger.info(f"  Unregistered from {db}: {count}")
            else:
                logger.info(f"No replicas older than {UNREGISTER_AGE_DAYS} days found - nothing to unregister")
        else:
            logger.info("Automatic unregistration disabled (UNREGISTER_AGE_DAYS is 0 or None)")

        # 5.5) Export to JSON and HTML for web viewer (AFTER unregistration so counts are accurate)
        logger.info("-" * 40)
        logger.info("STEP 5.5: Exporting to JSON and HTML viewer (post-cleanup)")
        logger.info("-" * 40)

        # Filter out successfully unregistered replicas from combined list
        if unregistered_ids:
            combined_filtered = [cr for cr in combined if cr.replica_id not in unregistered_ids]
            logger.info(f"Filtered out {len(unregistered_ids)} unregistered replicas from export")
            logger.info(f"Exporting {len(combined_filtered)} remaining replicas")
        else:
            combined_filtered = combined
            logger.info(f"No replicas unregistered, exporting all {len(combined_filtered)} replicas")

        try:
            export_to_json(combined_filtered)
        except Exception as ex:
            logger.info(f"WARNING: Failed to export JSON/HTML: {ex}")

        # 6) Filter
        logger.info("-" * 40)
        logger.info("STEP 6: Finding duplicate replicas")
        logger.info("-" * 40)
        matches = find_same_owner_same_service_diff_creation(combined)

        # 7) Print results
        logger.info("-" * 40)
        logger.info("STEP 7: Reporting results")
        logger.info("-" * 40)
        logger.info("=== Replicas with SAME owner + SAME service + DIFFERENT creation dates ===")

        if not matches:
            logger.info("None found.")
            logger.info("=== Summary ===")
            logger.info("Groups matched: 0")
            logger.info("Replicas printed: 0")

            elapsed_time = time.time() - start_time
            logger.info(f"Elapsed time: {elapsed_time:.1f} seconds")

            logger.info("=" * 60)
            logger.info("REPLICA UNREGISTER SCRIPT COMPLETED SUCCESSFULLY")
            logger.info("=" * 60)

            # Delete log file on success (optional)
            if DELETE_LOG_ON_SUCCESS:
                try:
                    for handler in logger.handlers[:]:
                        handler.close()
                        logger.removeHandler(handler)
                    if os.path.exists(LOG_FILE_PATH):
                        os.remove(LOG_FILE_PATH)
                except Exception as cleanup_ex:
                    print(f"Note: Could not delete log file: {cleanup_ex}")

            return 0

        grouped: Dict[Tuple[str, str], List[CombinedReplica]] = defaultdict(list)
        for m in matches:
            owner = (m.rest_replica_owner or m.sde_owner or "").strip()
            grouped[(owner, m.service_url)].append(m)

        total = 0
        for (owner, service), reps in sorted(grouped.items(), key=lambda x: (x[0][0], x[0][1])):
            total += len(reps)
            logger.info(f"Owner: {owner}")
            logger.info(f"Service: {service}")
            for r in reps:
                creation = r.rest_creation_date or r.sde_creation_date or r.sde_replica_date
                logger.info(
                    f"  - replicaID={r.replica_id} | "
                    f"name={r.sde_name or r.rest_replica_name} | "
                    f"creation={fmt_dt(creation)} | "
                    f"lastSync={fmt_dt(r.rest_last_sync_date)} | "
                    f"sdeOwner={r.sde_owner}"
                )
            logger.info("")

        logger.info("=== Summary ===")
        logger.info(f"Groups matched: {len(grouped)}")
        logger.info(f"Replicas printed: {total}")

        elapsed_time = time.time() - start_time
        logger.info(f"Elapsed time: {elapsed_time:.1f} seconds")

        logger.info("=" * 60)
        logger.info("REPLICA UNREGISTER SCRIPT COMPLETED SUCCESSFULLY")
        logger.info("=" * 60)

        # Delete log file on success (optional)
        if DELETE_LOG_ON_SUCCESS:
            try:
                for handler in logger.handlers[:]:
                    handler.close()
                    logger.removeHandler(handler)
                if os.path.exists(LOG_FILE_PATH):
                    os.remove(LOG_FILE_PATH)
            except Exception as cleanup_ex:
                print(f"Note: Could not delete log file: {cleanup_ex}")

        return 0

    except Exception as ex:
        # Log the error
        import traceback
        error_traceback = traceback.format_exc()
        logger.info(f"CRITICAL ERROR: Script failed with exception: {ex}")
        logger.info(f"Traceback:\n{error_traceback}")

        elapsed_time = time.time() - start_time
        logger.info(f"Elapsed time: {elapsed_time:.1f} seconds")

        send_failure_email(
            "unregister_feature_service_replicas.py Script Failure",
            f"The replica audit/cleanup script failed.\n\n"
            f"Error: {ex}\n\nLog file: {LOG_FILE_PATH}\n\n{error_traceback}"
        )

        logger.info("=" * 60)
        logger.info("REPLICA UNREGISTER SCRIPT FAILED")
        logger.info("=" * 60)

        return 2

if __name__ == "__main__":
    raise SystemExit(main())
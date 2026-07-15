"""
Script Name: rebuild_composite_locators.py
Version: 1.0.0
License: MIT

Description:
    Automates the rebuild process for composite geocoding locators on
    ArcGIS Enterprise / ArcGIS Server. For each configured composite:

        1. Checks the geocoding service status via the Admin REST API
        2. Stops the service if it is running (releases file locks)
        3. Rebuilds every participating locator with
           arcpy.geocoding.RebuildAddressLocator
        4. Rebuilds the composite locator itself
        5. Restarts the service if it was running before

    The service restart happens even when rebuild errors occur, so a
    failed rebuild never leaves your geocoding service down. Multiple
    composite configurations are processed sequentially in one run.

Why this exists:
    File-based locators published as geocoding services hold locks
    while the service is running, so scheduled rebuilds must stop the
    service first. Doing that safely (status check, conditional stop,
    guaranteed restart, token refresh before restart in case the
    rebuild ran long) is exactly the kind of orchestration that gets
    hand-rolled badly. This script does it carefully and logs
    everything.

Setup:
    1. Copy secrets.example.py to secrets.py and set your ArcGIS Server
       admin password. secrets.py is gitignored; never commit it.
    2. Fill in the CONFIGURATION section below: server URL, username,
       and your locator configurations.
    3. Locators must live in a folder registered as a data store with
       ArcGIS Server (for example D:\\Locator on the server machine),
       and each composite must already be published as a geocoding
       service.
    4. Run with DRY_RUN = True first to validate the token, service
       status lookups, and locator file paths without touching
       anything.
    5. Set DRY_RUN = False and schedule it (Windows Task Scheduler,
       run whether user is logged on or not). Use the ArcGIS Server or
       Pro Python environment so arcpy is available.

Dependencies:
    - Python 3.x with arcpy (ArcGIS Server or ArcGIS Pro environment)
    - Standard library only beyond arcpy
"""

import os
import sys
import json
import logging
import urllib.request
import urllib.parse
import ssl
import time
import smtplib
import traceback
import arcpy
from email.mime.text import MIMEText
from datetime import datetime

# ============================================================================
# CONFIGURATION
# ============================================================================

# True = validate token, service status, and locator paths only.
# False = full production run (stop, rebuild, restart).
DRY_RUN = True

# --- ArcGIS Server admin connection -----------------------------------------
# Use the machine URL on port 6443; web adaptors typically block /admin.
SERVER_URL       = "https://your-server.example.com:6443/arcgis"
SERVER_ADMIN_URL = f"{SERVER_URL}/admin"
USERNAME         = "your_admin_user"
# Password lives in secrets.py (gitignored). See secrets.example.py.
from secrets import SERVER_ADMIN_PASSWORD
PASSWORD = SERVER_ADMIN_PASSWORD

# Set False only if your server uses a self-signed certificate.
VERIFY_SSL = True

# --- Locator configurations --------------------------------------------------
# One entry per composite locator. service_name is "Folder/Name.GeocodeServer"
# (or just "Name.GeocodeServer" for the root folder). Paths are as seen by
# the machine running this script; locators must be in a registered data
# store folder on the server.
LOCATOR_BASE_PATH = r"D:\Locator"

LOCATOR_CONFIGS = [
    {
        "name": "MyComposite",
        "service_name": "Search/MyComposite.GeocodeServer",
        "composite_locator": os.path.join(LOCATOR_BASE_PATH, "MyComposite.loc"),
        "participating_locators": [
            os.path.join(LOCATOR_BASE_PATH, "MyAddressPoints.loc"),
            os.path.join(LOCATOR_BASE_PATH, "MyStreets.loc"),
        ]
    },
    # Add more composite configurations here as needed.
]

# --- Logging ------------------------------------------------------------------
LOG_DIR = "Log"
DELETE_LOG_ON_SUCCESS = False

# --- Email notifications on failure (optional) --------------------------------
ENABLE_EMAIL = False
SMTP_HOST    = "smtp.example.com"
FROM_EMAIL   = "noreply@example.com"
TO_EMAIL     = "you@example.com"

# ============================================================================
# END CONFIGURATION
# ============================================================================

os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(
    LOG_DIR, f"locator_rebuild_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)


def send_failure_email(subject, body):
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
        logger.error(f"Failed to send notification email: {e}")


# ============================================================================
# SERVER ADMIN FUNCTIONS
# ============================================================================

def get_ssl_context():
    if not VERIFY_SSL:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return None


def get_token(password):
    """Generate an admin token for ArcGIS Server."""
    token_url = f"{SERVER_ADMIN_URL}/generateToken"
    logger.info(f"Requesting admin token from: {token_url}")
    params = {
        'username': USERNAME,
        'password': password,
        'client': 'requestip',
        'expiration': 60,
        'f': 'json'
    }
    try:
        data = urllib.parse.urlencode(params).encode('utf-8')
        request = urllib.request.Request(token_url, data=data)
        response = urllib.request.urlopen(request, context=get_ssl_context())
        result = json.loads(response.read().decode('utf-8'))
        if 'token' in result:
            logger.info("Admin token obtained")
            return result['token']
        logger.error(f"Failed to generate token: "
                     f"{result.get('messages', result.get('error', result))}")
        return None
    except Exception as e:
        logger.error(f"Error generating token: {e}")
        return None


def _service_url(service_name, operation):
    if "/" in service_name:
        folder, service = service_name.rsplit("/", 1)
        return f"{SERVER_ADMIN_URL}/services/{folder}/{service}/{operation}"
    return f"{SERVER_ADMIN_URL}/services/{service_name}/{operation}"


def get_service_status(token, service_name):
    """Return the realTimeState of a service (STARTED, STOPPED, UNKNOWN)."""
    logger.info(f"Checking status of service: {service_name}")
    try:
        url = (_service_url(service_name, "status") + "?" +
               urllib.parse.urlencode({'token': token, 'f': 'json'}))
        response = urllib.request.urlopen(
            urllib.request.Request(url), context=get_ssl_context())
        result = json.loads(response.read().decode('utf-8'))
        status = result.get('realTimeState', 'UNKNOWN')
        logger.info(f"Service real-time state: {status}")
        return status
    except Exception as e:
        logger.error(f"Error getting service status: {e}")
        return 'UNKNOWN'


def _service_operation(token, service_name, operation):
    """Run a stop or start operation against a service."""
    logger.info(f"{operation.upper()} service: {service_name}")
    try:
        data = urllib.parse.urlencode(
            {'token': token, 'f': 'json'}).encode('utf-8')
        request = urllib.request.Request(
            _service_url(service_name, operation), data=data)
        op_start = time.time()
        response = urllib.request.urlopen(request, context=get_ssl_context())
        result = json.loads(response.read().decode('utf-8'))
        logger.info(f"{operation} took {time.time() - op_start:.2f}s, "
                    f"response: {result}")
        if result.get('status') == 'success':
            return True
        logger.error(f"Failed to {operation} service: {result}")
        return False
    except Exception as e:
        logger.error(f"Error during service {operation}: {e}")
        return False


def stop_service(token, service_name):
    return _service_operation(token, service_name, "stop")


def start_service(token, service_name):
    return _service_operation(token, service_name, "start")


# ============================================================================
# LOCATOR REBUILD FUNCTIONS
# ============================================================================

def rebuild_locator(locator_path):
    """Rebuild a single locator with arcpy."""
    locator_name = os.path.basename(locator_path)
    logger.info(f"Rebuilding locator: {locator_name}")
    if not os.path.exists(locator_path):
        logger.error(f"Locator not found: {locator_path}")
        return False
    try:
        rebuild_start = time.time()
        arcpy.geocoding.RebuildAddressLocator(locator_path)
        logger.info(f"Rebuilt {locator_name} in "
                    f"{time.time() - rebuild_start:.2f}s")
        return True
    except arcpy.ExecuteError:
        logger.error(f"ArcPy ExecuteError rebuilding {locator_name}: "
                     f"{arcpy.GetMessages(2)}")
        return False
    except Exception as e:
        logger.error(f"Error rebuilding {locator_name}: {e}")
        return False


def process_locator_config(config, token):
    """Stop service, rebuild participating + composite locators, restart.

    Returns True on full success. The service restart is attempted even
    when rebuilds fail, so the geocoding service is never left down.
    """
    config_name = config['name']
    service_name = config['service_name']
    logger.info("=" * 60)
    logger.info(f"PROCESSING: {config_name} ({service_name})")
    logger.info("=" * 60)

    config_success = True

    # 1. Status check and conditional stop
    service_was_running = (get_service_status(token, service_name) == 'STARTED')
    if service_was_running:
        if not stop_service(token, service_name):
            logger.error(f"Could not stop service for {config_name}; "
                         f"skipping rebuild to avoid lock errors.")
            return False
    else:
        logger.info("Service not running; skipping stop step")

    # 2. Rebuild participating locators, then the composite.
    #    Order matters: the composite must be rebuilt AFTER its
    #    participants or it keeps referencing stale data.
    for locator_path in config['participating_locators']:
        if not rebuild_locator(locator_path):
            config_success = False
    if not rebuild_locator(config['composite_locator']):
        config_success = False

    # 3. Guaranteed restart. Refresh the token first in case the
    #    rebuilds ran longer than the token lifetime.
    if service_was_running:
        new_token = get_token(PASSWORD)
        if not new_token or not start_service(new_token, service_name):
            logger.error(f"Failed to restart service for {config_name}")
            config_success = False
    else:
        logger.info("Service was not running before; skipping restart")

    return config_success


def dry_run_validation(token):
    """Validate everything the production run depends on, change nothing."""
    logger.info("DRY_RUN: validating configuration...")
    ok = True
    for config in LOCATOR_CONFIGS:
        status = get_service_status(token, config['service_name'])
        logger.info(f"{config['name']}: service status = {status}")
        for path in ([config['composite_locator']] +
                     config['participating_locators']):
            exists = os.path.exists(path)
            logger.info(f"  {'OK ' if exists else 'MISSING'} {path}")
            if not exists:
                ok = False
    logger.info(f"DRY_RUN validation {'passed' if ok else 'FAILED'}. "
                f"Set DRY_RUN = False for the production run.")
    return ok


# ============================================================================
# MAIN
# ============================================================================

def main():
    logger.info(f"Composite locator rebuild started "
                f"({len(LOCATOR_CONFIGS)} configuration(s))")
    failed_configs = []

    try:
        token = get_token(PASSWORD)
        if not token:
            raise Exception("Failed to obtain admin token")

        if DRY_RUN:
            return 0 if dry_run_validation(token) else 1

        for config in LOCATOR_CONFIGS:
            if not process_locator_config(config, token):
                failed_configs.append(config['name'])

    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        logger.error(traceback.format_exc())
        failed_configs.append("(fatal error)")

    if failed_configs:
        logger.error(f"Completed with failures: {', '.join(failed_configs)}")
        try:
            with open(LOG_FILE, 'r') as f:
                log_contents = f.read()
        except Exception:
            log_contents = "(log unavailable)"
        send_failure_email(
            "Composite locator rebuild FAILED",
            f"Failed configurations: {', '.join(failed_configs)}\n\n"
            f"--- LOG ---\n{log_contents}"
        )
        return 1

    logger.info("All configurations completed successfully")
    return 0


if __name__ == "__main__":
    exit_code = main()
    for handler in logging.root.handlers[:]:
        handler.close()
        logging.root.removeHandler(handler)
    if exit_code == 0 and DELETE_LOG_ON_SUCCESS and os.path.exists(LOG_FILE):
        os.remove(LOG_FILE)
    sys.exit(exit_code)

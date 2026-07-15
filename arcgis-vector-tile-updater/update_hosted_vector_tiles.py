"""
Script Name: update_hosted_vector_tiles.py
Version: 1.0.0
License: MIT

Description:
    Automates refreshing a hosted vector tile layer on ArcGIS Enterprise
    Portal from an ArcGIS Pro project, using the Replace Layer workflow:

        1. Build a fresh vector tile package (VTPK) from a map in an
           ArcGIS Pro project (.aprx).
        2. Update the package file on a persistent Portal VTPK item.
        3. Publish the package to a NEW temporary vector tile layer
           under a unique timestamped service name.
        4. Swap the temporary layer's content into the production
           hosted tile layer via gis.content.replace_service().
        5. Verify, archive the previous tiles as a rollback, and clean
           up stale temp/archive items from prior runs.

    The production tile layer's item ID and service URL never change,
    so webmaps and apps referencing it are never broken.

Why Replace Layer instead of publish-with-overwrite:
    Overwrite publishing resolves its target by service NAME and by the
    Service2Data relationship, both of which are fragile. If any other
    service shares the name (for example, a hosted feature service),
    Portal fails with "Unable to determine Service Type" or, worse,
    silently targets the wrong service. Publishing to a unique temp
    name and swapping content sidesteps name resolution entirely.

Setup:
    1. Copy secrets.example.py to secrets.py and set your password.
       secrets.py is gitignored; never commit credentials.
    2. Fill in the CONFIGURATION section below.
    3. Run once with DRY_RUN = True to validate connectivity and item
       resolution, then set DRY_RUN = False for production.

Dependencies:
    - ArcGIS Pro Python environment (arcpy + arcgis, Python 3.8+)
    - A Portal account that owns both the VTPK item and the hosted
      tile layer, with publishing privileges

Scheduling:
    Designed to run unattended (Windows Task Scheduler or similar).
    Exits nonzero on failure. Optional email notifications.
"""

import sys
import os
import time
import json
import logging
import smtplib
import traceback
import urllib3
import requests
import arcpy
from email.mime.text import MIMEText
from datetime import datetime
from arcgis.gis import GIS

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ===========================================================================
# CONFIGURATION
# ===========================================================================

# True = validate Portal connectivity and item resolution only.
# False = full production run.
DRY_RUN = True

# --- Portal ---------------------------------------------------------------
PORTAL_URL      = "https://your-portal.example.com/portal"
PORTAL_USERNAME = "your_publishing_user"
# Password lives in secrets.py (gitignored). See secrets.example.py.
from secrets import PORTAL_PASSWORD

# Set False if your Portal uses a certificate your environment trusts.
VERIFY_CERT = False

# --- Portal item IDs (find these on each item's details page) --------------
VTPK_ITEM_ID       = "REPLACE_WITH_VTPK_ITEM_ID"        # Vector Tile Package item
TILE_LAYER_ITEM_ID = "REPLACE_WITH_TILE_LAYER_ITEM_ID"  # Hosted tile layer item

# Item title to pin on the VTPK item. Portal otherwise resets the title
# from the package's internal metadata every time the file is replaced.
VTPK_TITLE = "MyBasemapVectors"

# --- Vector tile package creation -------------------------------------------
CONFIG = {
    'project_path'    : r"C:\path\to\your_project.aprx",
    'vtpk_folder'     : r"C:\path\to\output\folder",
    'map_name'        : "Your Map Name",     # map name inside the .aprx
    'vtpk_filename'   : "your_tiles.vtpk",
    'service_type'    : "EXISTING",          # EXISTING = custom tiling scheme
    'tiling_scheme'   : r"C:\path\to\tiling_scheme.xml",
    'tile_structure'  : "INDEXED",           # or FLAT
    'min_cached_scale': 73957190,
    'max_cached_scale': 141,
    'index_polygons'  : r"C:\path\to\index.gdb\TileIndex",  # for INDEXED
    'summary'         : "Vector tile basemap",
    'tags'            : "basemap",
}

# --- Optional behaviors -----------------------------------------------------
# Remove any styles/root.json resource stored on the tile layer ITEM after
# each replace, so the service style is the single source of truth. Leave
# False if you maintain a deliberate custom item style (for example, one
# saved from the Vector Tile Style Editor).
REMOVE_ITEM_STYLE_OVERLAY = False

# Delete the log file when the run succeeds.
DELETE_LOG_ON_SUCCESS = False

# --- Email notifications (optional) -----------------------------------------
ENABLE_EMAIL = False
SMTP_HOST    = "smtp.example.com"
FROM_EMAIL   = "noreply@example.com"
TO_EMAIL     = "you@example.com"

# ===========================================================================
# END CONFIGURATION
# ===========================================================================

log_directory = "Log"
os.makedirs(log_directory, exist_ok=True)
log_filename = os.path.join(
    log_directory,
    f"update_hosted_vector_tiles_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler(log_filename), logging.StreamHandler()]
)

run_failed = False


def send_email(subject, body_html):
    if not ENABLE_EMAIL:
        return
    try:
        msg = MIMEText(body_html, "html")
        msg['Subject'] = subject
        msg['From'] = FROM_EMAIL
        msg['To'] = TO_EMAIL
        with smtplib.SMTP(SMTP_HOST) as server:
            server.sendmail(FROM_EMAIL, TO_EMAIL, msg.as_string())
        logging.info(f"Email sent: {subject}")
    except Exception as e:
        logging.error(f"Failed to send email '{subject}': {e}")


def connect_to_portal():
    gis = GIS(PORTAL_URL, PORTAL_USERNAME, PORTAL_PASSWORD,
              verify_cert=VERIFY_CERT)
    logging.info(f"Connected to Portal as {gis.properties.user.username}")
    return gis


def resolve_portal_items(gis):
    """Fetch both items by ID and derive the service name from the tile
    layer URL. Fails fast if anything is missing or the wrong type."""
    vtpk_item = gis.content.get(VTPK_ITEM_ID)
    if vtpk_item is None:
        raise Exception(f"VTPK item not found: {VTPK_ITEM_ID}")
    if vtpk_item.type != "Vector Tile Package":
        raise Exception(f"Item {VTPK_ITEM_ID} is '{vtpk_item.type}', "
                        f"expected 'Vector Tile Package'")

    tile_item = gis.content.get(TILE_LAYER_ITEM_ID)
    if tile_item is None:
        raise Exception(f"Tile layer item not found: {TILE_LAYER_ITEM_ID}")

    service_name = None
    if tile_item.url:
        parts = [p for p in tile_item.url.split('/') if p]
        for i, part in enumerate(parts):
            if part.lower() == 'vectortileserver' and i > 0:
                service_name = parts[i - 1]
                break
    if not service_name:
        raise Exception(f"Could not derive service name from URL: "
                        f"{tile_item.url}")

    logging.info(f"VTPK item resolved      : {vtpk_item.title} ({vtpk_item.id})")
    logging.info(f"Tile layer item resolved: {tile_item.title} ({tile_item.id})")
    logging.info(f"Service name            : {service_name}")
    return vtpk_item, tile_item, service_name


def cleanup_stale_working_items(gis, service_name):
    """Delete leftover temp/archive layers from prior runs. The most
    recent archive survives until the NEXT successful run, giving a
    rollback window. Production items are explicitly protected."""
    protected = {VTPK_ITEM_ID, TILE_LAYER_ITEM_ID}
    prefixes = (f"{service_name}_tmp", f"{service_name}_archive")
    try:
        candidates = gis.content.search(
            query=f'owner:{PORTAL_USERNAME}',
            item_type="Vector Tile Service",
            max_items=100
        )
    except Exception as e:
        logging.warning(f"Stale-item search failed (continuing): {e}")
        return
    for item in candidates:
        if item.id in protected:
            continue
        title = item.title or ""
        svc_name = getattr(item, 'name', '') or ""
        if any(title.startswith(p) or svc_name.startswith(p)
               for p in prefixes):
            try:
                if item.delete():
                    logging.info(f"Deleted stale working item: "
                                 f"{title} ({item.id})")
            except Exception as e:
                logging.warning(f"Delete failed for {title} ({item.id}): {e}")


def delete_existing_vtpk(vtpk_path):
    if os.path.exists(vtpk_path):
        os.remove(vtpk_path)
        logging.info(f"Deleted existing VTPK: {vtpk_path}")


def create_vtpk(vtpk_path):
    aprx = arcpy.mp.ArcGISProject(CONFIG['project_path'])
    try:
        maps = aprx.listMaps(CONFIG['map_name'])
        if not maps:
            raise Exception(f"Map '{CONFIG['map_name']}' not found in project")
        arcpy.management.CreateVectorTilePackage(
            in_map           = maps[0],
            output_file      = vtpk_path,
            service_type     = CONFIG['service_type'],
            tiling_scheme    = CONFIG['tiling_scheme'],
            tile_structure   = CONFIG['tile_structure'],
            min_cached_scale = CONFIG['min_cached_scale'],
            max_cached_scale = CONFIG['max_cached_scale'],
            index_polygons   = CONFIG['index_polygons'],
            summary          = CONFIG['summary'],
            tags             = CONFIG['tags']
        )
        logging.info(f"Vector tile package created: {vtpk_path}")
    finally:
        del aprx


def _delete_partial_item(gis, item_id, protected):
    if not item_id or item_id in protected:
        return
    try:
        partial = gis.content.get(item_id)
        if partial is not None and partial.delete():
            logging.info(f"Deleted partial temp item {item_id}")
    except Exception as e:
        logging.warning(f"Could not delete partial item {item_id}: {e}")


def publish_temp_layer(gis, vtpk_item, temp_service_name):
    """Publish the VTPK item to a NEW uniquely named vector tile layer.
    Tries the arcgis library first (it sends the correct request form),
    then falls back to a raw REST publish with verbose job logging so
    real server errors are captured instead of a generic 'Job failed.'"""
    protected = {VTPK_ITEM_ID, TILE_LAYER_ITEM_ID}
    last_error = None

    logging.info("Attempting library publish (auto file type detection)...")
    try:
        temp_item = vtpk_item.publish(
            publish_parameters={'name': temp_service_name}
        )
        if temp_item is not None and temp_item.type == 'Vector Tile Service' \
                and temp_item.id not in protected:
            logging.info(f"Library publish succeeded: {temp_item.title} "
                         f"({temp_item.id})")
            return temp_item
        bad_id = temp_item.id if temp_item is not None else None
        last_error = (f"Library publish produced unexpected result: "
                      f"type={getattr(temp_item, 'type', None)}, id={bad_id}")
        logging.warning(last_error)
        _delete_partial_item(gis, bad_id, protected)
    except Exception as e:
        last_error = f"Library publish raised: {e}"
        logging.warning(last_error)
        logging.warning(traceback.format_exc())
        logging.warning("Falling back to raw REST publish.")

    # REST fallback. Two gotchas learned the hard way:
    # 1. The parameter name is 'fileType' with a capital T; all-lowercase
    #    'filetype' is silently ignored, producing "Unable to determine
    #    Service Type".
    # 2. outputType=VectorTiles is required. Wrong file type values can
    #    silently misroute the publish into the FEATURE service pipeline
    #    and create orphan feature services.
    token = gis._con.token
    logging.info(f"REST publish: fileType=vectortilepackage, "
                 f"name={temp_service_name}")
    pub = requests.post(
        f"{PORTAL_URL}/sharing/rest/content/users/{PORTAL_USERNAME}/publish",
        data={
            'f'                : 'json',
            'token'            : token,
            'itemid'           : vtpk_item.id,
            'fileType'         : 'vectortilepackage',
            'outputType'       : 'VectorTiles',
            'overwrite'        : 'false',
            'buildInitialCache': 'false',
            'publishParameters': json.dumps({'name': temp_service_name}),
        },
        verify=VERIFY_CERT
    ).json()
    logging.info(f"Publish response:\n{json.dumps(pub, indent=2)}")

    if 'error' in pub:
        raise Exception(f"Publish rejected: {pub['error']}. "
                        f"Prior library error: {last_error}")
    services = pub.get('services', [])
    svc = services[0] if services else {}
    if not services or 'error' in svc:
        raise Exception(f"Publish service error: {svc.get('error', pub)}")

    job_id = svc.get('jobId')
    service_item_id = svc.get('serviceItemId')
    if svc.get('type') and svc['type'] != 'Vector Tile Service':
        _delete_partial_item(gis, service_item_id, protected)
        raise Exception(f"Publish misrouted to service type '{svc['type']}' "
                        f"- check fileType/outputType values.")
    if service_item_id in protected:
        raise Exception(f"ABORT: publish resolved to a production item "
                        f"({service_item_id}) despite the unique name.")

    status = 'processing'
    status_resp = {}
    while status in ('processing', 'partial'):
        time.sleep(10)
        status_resp = requests.get(
            f"{PORTAL_URL}/sharing/rest/content/users/{PORTAL_USERNAME}"
            f"/items/{service_item_id}/status",
            params={'jobId': job_id, 'jobType': 'publish',
                    'f': 'json', 'token': token},
            verify=VERIFY_CERT
        ).json()
        status = status_resp.get('status', 'unknown')
        logging.info(f"Job status:\n{json.dumps(status_resp, indent=2)}")

    if status.lower() != 'completed':
        _delete_partial_item(gis, service_item_id, protected)
        raise Exception(f"Publish job failed. statusMessage: "
                        f"{status_resp.get('statusMessage', '(none)')}")

    temp_item = gis.content.get(service_item_id)
    if temp_item is None or temp_item.type != 'Vector Tile Service':
        _delete_partial_item(gis, service_item_id, protected)
        raise Exception("Publish completed but produced an unexpected item.")
    logging.info(f"REST publish succeeded: {temp_item.title} ({temp_item.id})")
    return temp_item


def remove_item_style_overlay(tile_item):
    try:
        tile_item.resources.remove(file='styles/root.json')
        logging.info("Removed item style overlay.")
    except Exception:
        logging.info("No item style overlay present.")


def update_and_replace(gis, vtpk_item, tile_item, vtpk_path, service_name):
    size_mb = os.path.getsize(vtpk_path) / (1024 * 1024)
    logging.info(f"Uploading {os.path.basename(vtpk_path)} "
                 f"({size_mb:.1f} MB) to VTPK item {vtpk_item.id}...")
    if not vtpk_item.update(item_properties={'title': VTPK_TITLE},
                            data=vtpk_path):
        raise Exception("Item.update() returned False for the VTPK item")
    logging.info("VTPK item file updated.")

    ts = datetime.now().strftime('%Y%m%d%H%M')
    temp_service_name = f"{service_name}_tmp{ts}"
    archive_service_name = f"{service_name}_archive{ts}"

    temp_item = publish_temp_layer(gis, vtpk_item, temp_service_name)

    logging.info(f"Replacing production layer content "
                 f"(item {tile_item.id}, URL preserved)...")
    ok = gis.content.replace_service(
        replace_item=tile_item,
        new_item=temp_item,
        replaced_service_name=archive_service_name,
        replace_metadata=False
    )
    if not ok:
        raise Exception(
            f"replace_service returned False. Production untouched; temp "
            f"layer '{temp_service_name}' ({temp_item.id}) left in place."
        )

    check = gis.content.get(TILE_LAYER_ITEM_ID)
    if check is None or 'VectorTileServer' not in (check.url or ''):
        raise Exception(f"Post-replace verification FAILED for "
                        f"{TILE_LAYER_ITEM_ID}. Investigate immediately.")
    logging.info(f"Replace complete and verified: {check.title} "
                 f"({check.id}) at {check.url}")

    if REMOVE_ITEM_STYLE_OVERLAY:
        remove_item_style_overlay(check)

    logging.info(f"Previous tiles archived as '{archive_service_name}' "
                 f"(rollback; auto-deleted at the start of the next run).")


def main():
    global run_failed
    vtpk_path = os.path.join(CONFIG['vtpk_folder'], CONFIG['vtpk_filename'])

    try:
        gis = connect_to_portal()
        vtpk_item, tile_item, service_name = resolve_portal_items(gis)
    except Exception as e:
        logging.error(f"Portal connection/item resolution failed: {e}")
        run_failed = True
        send_email("Vector tile update FAILED",
                   f"<html lang='en'><body><p>Portal step failed:</p>"
                   f"<pre>{e}</pre></body></html>")
        return

    if DRY_RUN:
        logging.info("DRY_RUN: validation successful. Would rebuild "
                     f"{vtpk_path}, update item {vtpk_item.id}, and "
                     f"replace service '{service_name}' "
                     f"(item {tile_item.id}). Set DRY_RUN = False to run.")
        return

    try:
        delete_existing_vtpk(vtpk_path)
        create_vtpk(vtpk_path)
        cleanup_stale_working_items(gis, service_name)
        update_and_replace(gis, vtpk_item, tile_item, vtpk_path, service_name)
    except Exception as e:
        logging.error(f"Run failed: {e}")
        logging.error(traceback.format_exc())
        run_failed = True
        send_email("Vector tile update FAILED",
                   f"<html lang='en'><body><p>Run failed:</p>"
                   f"<pre>{e}</pre></body></html>")
        return

    logging.info("Vector tile update completed successfully.")
    send_email("Vector tile update succeeded",
               "<html lang='en'><body><p>The hosted vector tile layer "
               "was refreshed successfully.</p></body></html>")


if __name__ == "__main__":
    try:
        main()
    finally:
        for handler in logging.root.handlers[:]:
            handler.close()
            logging.root.removeHandler(handler)
        if not run_failed and DELETE_LOG_ON_SUCCESS:
            os.remove(log_filename)
        sys.exit(1 if run_failed else 0)

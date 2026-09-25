"""
Script Name: publish_print_service.py
Project: arcgis-print-service-publisher
Version: 1.0.0

Description:
    Publishes an Export Web Map (print) geoprocessing service to ArcGIS Server
    using a folder of ArcGIS Pro layout files (.pagx) as the templates. Works
    against a server federated with Portal for ArcGIS (sign in, publish by URL)
    or a stand-alone ArcGIS Server (.ags connection file).

    Re-running overwrites the existing service in place, so adding or editing a
    layout is: drop the .pagx in the folder, run the script again.

    The published service prints whatever web map the client sends. Layers and
    basemap come from the user's map at print time. Nothing is baked in.

    All configuration lives in config.py. Copy config.example.py to config.py first.

Why this exists:
    ArcGIS Online charges credits for every print that uses an organization
    Layout item (5 credits per print at the time of writing). A print service on
    your own ArcGIS Server with the same layouts costs nothing per print and can
    reach services that Esri's hosted print service cannot. Publishing it by hand
    in ArcGIS Pro is a dozen clicks that nobody remembers a year later.

Usage:
    python publish_print_service.py

    With ArcGIS Pro's Python on Windows, Command Prompt, regular:
      "C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\envs\\arcgispro-py3\\python.exe" publish_print_service.py

    Requires arcpy, so run it in the ArcGIS Pro (or ArcGIS Server) Python
    environment on a machine that can reach the target server.

Exit Codes:
    - 0: Completed. In DRY_RUN this means the draft analyzed clean.
    - 2: Failed. See the log.

Known limitation:
    The Export Web Map tool always publishes MAP_ONLY as the default value of
    the Layout_Template parameter, and the parameter definition is not exposed
    in the service definition draft, so this script cannot change it. Map
    Viewer falls back to the first template in the list, which is alphabetical
    by file name. Name your files so the layout you want first sorts first.
"""

import os
import sys
import glob
import json
import logging
import importlib.util
from datetime import datetime

import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Empty web map used once so the tool can run for publishing. No layers, no basemap.
WEBMAP_JSON = json.dumps({
    "mapOptions": {
        "extent": {
            "xmin": -12000000, "ymin": 4700000, "xmax": -11990000, "ymax": 4710000,
            "spatialReference": {"wkid": 102100}
        },
        "scale": 50000
    },
    "operationalLayers": [],
    "exportOptions": {"dpi": 96, "outputSize": [800, 600]},
    "layoutOptions": {"titleText": "Publish test"}
})


# ----------------------------------------------------------------------
# Config and logging
# ----------------------------------------------------------------------
def load_config():
    path = os.path.join(SCRIPT_DIR, "config.py")
    if not os.path.exists(path):
        print("config.py not found. Copy config.example.py to config.py and fill it in.")
        sys.exit(2)
    spec = importlib.util.spec_from_file_location("config", path)
    cfg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cfg)
    return cfg


def resolve(path):
    return path if os.path.isabs(path) else os.path.join(SCRIPT_DIR, path)


def setup_logging(log_dir):
    os.makedirs(log_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(log_dir, f"publish_print_service_{stamp}.log")
    fmt = logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")
    log = logging.getLogger("publish_print_service")
    log.setLevel(logging.INFO)
    for h in (logging.StreamHandler(sys.stdout), logging.FileHandler(log_path, encoding="utf-8")):
        h.setFormatter(fmt)
        log.addHandler(h)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    return log, log_path


def remove_if_exists(*paths):
    for p in paths:
        if os.path.exists(p):
            os.remove(p)


# ----------------------------------------------------------------------
# Verification
# ----------------------------------------------------------------------
def task_url(cfg):
    if cfg.PUBLISH_MODE.upper() == "FEDERATED":
        base = cfg.FED_SERVER_URL.rstrip("/")
    else:
        base = None
    if base is None:
        return None
    return f"{base}/rest/services/{cfg.SERVER_FOLDER}/{cfg.SERVICE_NAME}/GPServer/Export%20Web%20Map"


def verify(cfg, log, layouts, url):
    """Anonymous GET of the task. Returns (reachable, layouts_match)."""
    try:
        data = requests.get(url, params={"f": "json"}, timeout=30).json()
    except Exception as e:
        log.error("Could not reach %s: %s", url, e)
        return False, False
    if "error" in data:
        log.error("Anonymous access failed (%s). If public access is required, share the item to Everyone.",
                  data["error"].get("message"))
        return False, False
    param = next((p for p in data.get("parameters", []) if p["name"] == "Layout_Template"), None)
    if not param:
        log.error("Layout_Template parameter missing from the published task")
        return True, False
    choices = [c for c in param.get("choiceList", []) if c != "MAP_ONLY"]
    log.info("Service reachable. Templates: %s", ", ".join(choices))
    if sorted(choices) != sorted(layouts):
        log.error("Template list on the server does not match %s", cfg.PAGX_DIR)
        return True, False
    return True, True


def set_portal_print_service(cfg, log, url):
    """Point the Portal's Printing utility service at the new task."""
    from arcgis.gis import GIS
    gis = GIS(cfg.PORTAL_URL, cfg.PORTAL_USER, cfg.PORTAL_PASSWORD)
    helpers = dict(gis.properties.get("helperServices", {}))
    helpers["printTask"] = {"url": url}
    ok = gis.update_properties({"helperServices": helpers})
    if ok:
        log.info("Portal Printing utility service set to %s", url)
    else:
        log.error("Portal did not accept the helperServices update; set it by hand")
    return bool(ok)


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
def main():
    cfg = load_config()
    log_dir = resolve(cfg.LOG_DIR)
    work_dir = resolve(cfg.WORK_DIR)
    log, log_path = setup_logging(log_dir)
    log.info("Log: %s", log_path)
    log.info("DRY_RUN = %s", cfg.DRY_RUN)

    import arcpy
    arcpy.env.overwriteOutput = True

    mode = cfg.PUBLISH_MODE.upper()
    if mode not in ("FEDERATED", "AGS"):
        log.error("PUBLISH_MODE must be FEDERATED or AGS")
        return 2

    layouts = sorted(os.path.splitext(os.path.basename(f))[0]
                     for f in glob.glob(os.path.join(cfg.PAGX_DIR, "*.pagx")))
    if not layouts:
        log.error("No .pagx files found in %s", cfg.PAGX_DIR)
        return 2
    log.info("Layouts in %s: %s", cfg.PAGX_DIR, ", ".join(layouts))
    if cfg.PUBLISH_LAYOUT not in layouts:
        log.error("PUBLISH_LAYOUT '%s' not found in %s", cfg.PUBLISH_LAYOUT, cfg.PAGX_DIR)
        return 2
    if mode == "AGS" and not os.path.exists(cfg.AGS_FILE):
        log.error("Connection file not found: %s", cfg.AGS_FILE)
        return 2

    os.makedirs(work_dir, exist_ok=True)
    out_pdf = os.path.join(work_dir, "publish_test.pdf")
    sddraft = os.path.join(work_dir, f"{cfg.SERVICE_NAME}.sddraft")
    sd = os.path.join(work_dir, f"{cfg.SERVICE_NAME}.sd")
    remove_if_exists(out_pdf, sddraft, sd)

    try:
        # 0. Sign in (FEDERATED)
        if mode == "FEDERATED":
            log.info("Signing in to %s as %s", cfg.PORTAL_URL, cfg.PORTAL_USER)
            arcpy.SignInToPortal(cfg.PORTAL_URL, cfg.PORTAL_USER, cfg.PORTAL_PASSWORD)

        # 1. Run Export Web Map once. The result carries the layout choice list.
        log.info("Running Export Web Map with layout %s", cfg.PUBLISH_LAYOUT)
        result = arcpy.server.ExportWebMap(
            Web_Map_as_JSON=WEBMAP_JSON,
            Output_File=out_pdf,
            Format="PDF",
            Layout_Templates_Folder=cfg.PAGX_DIR,
            Layout_Template=cfg.PUBLISH_LAYOUT,
        )
        log.info("Tool ran OK: %s", result.getOutput(0))

        # 2. Service definition draft
        log.info("Creating service definition draft")
        if mode == "FEDERATED":
            server_type, connection = "FEDERATED_SERVER", cfg.FED_SERVER_URL
        else:
            server_type, connection = "ARCGIS_SERVER", cfg.AGS_FILE

        analysis = arcpy.CreateGPSDDraft(
            result=result,
            out_sddraft=sddraft,
            service_name=cfg.SERVICE_NAME,
            server_type=server_type,
            connection_file_path=connection,
            copy_data_to_server=True,          # bundles the .pagx folder with the service
            folder_name=cfg.SERVER_FOLDER,
            summary=cfg.SUMMARY,
            tags=cfg.TAGS,
            executionType="Synchronous",
            resultMapServer=False,
            showMessages="INFO",
            maximumRecords=1000,
            minInstances=cfg.MIN_INSTANCES,
            maxInstances=cfg.MAX_INSTANCES,
            maxUsageTime=cfg.MAX_USAGE_TIME,
            maxWaitTime=cfg.MAX_WAIT_TIME,
            maxIdleTime=cfg.MAX_IDLE_TIME,
        )
        for k, v in analysis["errors"].items():
            log.error("Draft error: %s  %s", k, v)
        for k, v in analysis["warnings"].items():
            log.warning("Draft warning: %s  %s", k, v)
        if analysis["errors"]:
            log.error("Fix the draft errors above and re-run")
            return 2

        if cfg.DRY_RUN:
            log.info("DRY_RUN: draft analyzed clean at %s. Nothing published.", sddraft)
            log.info("Set DRY_RUN = False in config.py to publish.")
            return 0

        # 3. Stage and upload. Upload overwrites an existing service of the same name.
        log.info("Staging service")
        arcpy.server.StageService(sddraft, sd)
        log.info("Uploading %s/%s to %s", cfg.SERVER_FOLDER, cfg.SERVICE_NAME, connection)
        if mode == "FEDERATED":
            arcpy.server.UploadServiceDefinition(
                in_sd_file=sd,
                in_server=cfg.FED_SERVER_URL,
                in_override="OVERRIDE_DEFINITION",
                in_my_contents="NO_SHARE_ONLINE",
                in_public="PUBLIC" if cfg.SHARE_EVERYONE else "PRIVATE",
                in_organization="SHARE_ORGANIZATION" if cfg.SHARE_ORGANIZATION else "NO_SHARE_ORGANIZATION",
                in_folder_type="NEW",
                in_folder=cfg.PORTAL_FOLDER,
            )
        else:
            arcpy.server.UploadServiceDefinition(
                in_sd_file=sd,
                in_server=cfg.AGS_FILE,
                in_override="OVERRIDE_DEFINITION",
            )
        log.info("Published")

        # 4. Verify and optionally wire up the Portal
        url = task_url(cfg)
        if url is None:
            log.info("Verify the task in ArcGIS Server Manager under %s/%s", cfg.SERVER_FOLDER, cfg.SERVICE_NAME)
            return 0

        reachable, match = verify(cfg, log, layouts, url)
        if not reachable and cfg.SHARE_EVERYONE:
            return 2
        if reachable and not match:
            return 2

        log.info("Print task URL: %s", url)
        if cfg.SET_PORTAL_PRINT_SERVICE and mode == "FEDERATED":
            if not set_portal_print_service(cfg, log, url):
                return 2
        else:
            log.info("Set this URL under Organization > Settings > Utility services > Printing")
        return 0

    except Exception:
        log.exception("Publish failed")
        return 2


if __name__ == "__main__":
    sys.exit(main())

"""
Script Name: config.example.py
Project: arcgis-print-service-publisher

Description:
    Configuration template. Copy this file to config.py, fill in the values for your
    organization, and keep config.py out of source control. The .gitignore in this
    package already excludes it.

Usage:
    copy config.example.py config.py      (Windows, Command Prompt, regular)
    cp config.example.py config.py        (Linux/macOS, bash)
"""

# ----------------------------------------------------------------------
# Layouts
# ----------------------------------------------------------------------
# Folder holding your ArcGIS Pro layout files (.pagx). Every .pagx in this folder
# becomes a template in the print service. The file name (without .pagx) is the
# template name users see.
#
# Order matters. Map Viewer preselects the first template in the service's list,
# and that list is alphabetical by file name. If you want "Letter Landscape" to be
# the default, make sure it sorts first (a numeric prefix such as "1 - " works).
PAGX_DIR = r"C:\GIS\PrintLayouts"

# Template name (a .pagx file name without the extension) used for the one-time
# publish run. Must exist in PAGX_DIR.
PUBLISH_LAYOUT = "Letter Landscape"

# ----------------------------------------------------------------------
# Target server
#
# PUBLISH_MODE = "FEDERATED"
#     ArcGIS Server federated with a Portal. Signs in to PORTAL_URL and publishes
#     to FED_SERVER_URL. No connection file needed. The Portal item is shared
#     according to the SHARE_* settings below.
#
# PUBLISH_MODE = "AGS"
#     Stand-alone ArcGIS Server. Publishes through an .ags connection file saved
#     from ArcGIS Pro with publisher or administrator credentials.
# ----------------------------------------------------------------------
PUBLISH_MODE = "FEDERATED"

# FEDERATED mode
PORTAL_URL = "https://portal.example.org/portal"
PORTAL_USER = "publisher_account"
PORTAL_PASSWORD = ""
FED_SERVER_URL = "https://gisserver.example.org/arcgis"

# AGS mode
AGS_FILE = r"C:\GIS\connections\gisserver (publisher).ags"

# ----------------------------------------------------------------------
# Service
# ----------------------------------------------------------------------
SERVICE_NAME = "ExportWebMap"
SERVER_FOLDER = "Printing"          # folder on ArcGIS Server, created if missing
PORTAL_FOLDER = "Printing"          # folder in Portal content (FEDERATED mode only)
SUMMARY = "Print service with organization layout templates."
TAGS = "print, layout"

# Sharing (FEDERATED mode only). ArcGIS Online Map Viewer and public apps call the
# service anonymously, so SHARE_EVERYONE must be True for those.
SHARE_EVERYONE = True
SHARE_ORGANIZATION = True

# Instance and timeout settings applied to the GP service.
MIN_INSTANCES = 1
MAX_INSTANCES = 2
MAX_USAGE_TIME = 120        # seconds a single print may run
MAX_WAIT_TIME = 60          # seconds a request may queue
MAX_IDLE_TIME = 1800        # seconds an instance may sit idle

# ----------------------------------------------------------------------
# Optional: point the org's Printing utility service at the new service
#
# After a successful publish, update the Portal (FEDERATED mode) helper service
# so Map Viewer and apps use this print service. Requires an administrator
# account in PORTAL_USER. Leave False to set it by hand under
# Organization > Settings > Utility services > Printing.
# ----------------------------------------------------------------------
SET_PORTAL_PRINT_SERVICE = False

# ----------------------------------------------------------------------
# Run behavior
# ----------------------------------------------------------------------
# True runs the tool and builds the service definition draft, reports analyzer
# messages, and stops before anything reaches the server. Start here.
DRY_RUN = True

# Scratch folder for the .sddraft, .sd, and test PDF. Relative paths are resolved
# against the folder holding the script.
WORK_DIR = "work"

# Log folder. Relative paths are resolved against the folder holding the script.
LOG_DIR = "logs"

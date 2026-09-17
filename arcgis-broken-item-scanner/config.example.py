"""
Script Name: config.example.py
Project: arcgis-broken-item-scanner

Description:
    Configuration template. Copy this file to config.py, fill in the values for your
    organization, and keep config.py out of source control. The .gitignore in this
    package already excludes it.

    Nothing in this file is required to be an email address except ADMIN_EMAIL,
    SUPPORT_EMAIL, and FROM_EMAIL.

Usage:
    copy config.example.py config.py      (Windows, Command Prompt, regular)
    cp config.example.py config.py        (Linux/macOS, bash)
"""

# ----------------------------------------------------------------------
# Organization
# ----------------------------------------------------------------------
ORG_NAME = "Example Organization"

# ----------------------------------------------------------------------
# ArcGIS Enterprise Portal
# Used by scan_portal.py.
# ----------------------------------------------------------------------
PORTAL_URL = "https://portal.example.org/portal"
PORTAL_USER = "portal_scanner"
PORTAL_PASSWORD = ""

# ----------------------------------------------------------------------
# ArcGIS Online
# Used by scan_agol.py.
# ----------------------------------------------------------------------
AGOL_URL = "https://yourorg.maps.arcgis.com"
AGOL_USER = "service_account"
AGOL_PASSWORD = ""

# Many ArcGIS Online items reference federated services hosted on your own Portal.
# Those probes need a Portal token rather than an ArcGIS Online token. Set this to
# True to have scan_agol.py sign in to Portal as well, using the PORTAL_* values
# above. A Portal sign-in failure is logged and the scan continues.
AGOL_USE_FEDERATED_PORTAL = True

# Host suffix that identifies your own Portal and server machines. URLs on these
# hosts are probed with the Portal token.
FEDERATED_HOST_SUFFIX = ".example.org"

# Host suffix for ArcGIS Online. URLs on these hosts are probed with the AGOL token.
AGOL_HOST_SUFFIX = ".arcgis.com"

# ----------------------------------------------------------------------
# Owner resolution
#
# Portal usernames are often the email address already. ArcGIS Online appends the
# org short name, so an owner arrives as "jane.doe@example.org_YourOrgName". Any
# suffix listed here is stripped before the username is treated as an address. If
# the result is still not an address, the email on the user profile is used, and
# failing that the owner's mail is routed to ADMIN_EMAIL.
# ----------------------------------------------------------------------
ORG_USERNAME_SUFFIXES = ["_YourOrgName"]

# Items owned by these accounts are ignored entirely. Esri ships a lot of content
# under its own accounts and none of it is yours to fix.
SKIP_OWNERS = [
    "esri_apps", "esri", "esri_nav", "esri_livingatlas",
    "esri_boundaries", "esri_demographics", "esri_dm", "system_publisher",
]

# Usernames that are service accounts rather than people. Their findings are mailed
# to whoever is listed here instead.
OWNER_EMAIL_OVERRIDES = {
    "portal_scanner": "gis.admin@example.org",
    "siteadmin": "gis.admin@example.org",
    "service_account": "gis.admin@example.org",
}

# ----------------------------------------------------------------------
# Email
# ----------------------------------------------------------------------
SMTP_HOST = "smtp.example.org"
SMTP_PORT = 25
SMTP_USE_TLS = False
SMTP_USER = ""                  # leave empty for an unauthenticated internal relay
SMTP_PASSWORD = ""

FROM_EMAIL = "gis-bot@example.org"
ADMIN_EMAIL = "gis.admin@example.org"      # summary and failure notices land here
SUPPORT_EMAIL = "gis@example.org"          # address owners are told to ask for help

# True means scan, log, and write the CSV, but send nothing. Start here.
DRY_RUN = True

# Set to an address to send every message there instead of to the owners. Useful
# for one live end-to-end test before you turn owners on. Ignored when DRY_RUN.
TEST_REDIRECT = ""

# Business days suggested to the owner in the email.
REVIEW_DAYS = 10

# Broken references listed per item before the email says "and N more".
MAX_REFS_PER_ITEM = 10

# ----------------------------------------------------------------------
# Scan behavior
# ----------------------------------------------------------------------
VERIFY_SSL = True
REQUEST_TIMEOUT = 20            # seconds allowed per URL check
PAGE_SIZE = 100                 # items pulled per search page, 100 is the maximum

# ----------------------------------------------------------------------
# Output locations
# Relative paths are resolved against the folder holding the scripts.
# ----------------------------------------------------------------------
LOG_DIR = "logs"
REPORT_DIR = "reports"
CSV_RETENTION_DAYS = 90
DELETE_LOG_ON_SUCCESS = True

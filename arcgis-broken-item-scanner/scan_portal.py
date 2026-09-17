"""
Script Name: scan_portal.py
Project: arcgis-broken-item-scanner
Version: 1.0.0

Description:
    Scans an ArcGIS Enterprise Portal for items whose backing references no longer
    respond, groups the findings by item owner, and sends each owner one email
    listing all of their broken items. A CSV report and an administrator summary
    are produced on every run that finds something.

    All configuration lives in config.py. Copy config.example.py to config.py first.

Usage:
    python scan_portal.py

    With ArcGIS Pro's Python on Windows, Command Prompt, regular:
      "C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\envs\\arcgispro-py3\\python.exe" scan_portal.py

Exit Codes:
    - 0: Completed, whether or not broken items were found.
    - 2: Fatal error. Failure email sent if email is configured.
"""

import sys

import broken_items


def main():
    cfg = broken_items.load_config()
    profile = {
        "slug": "portal",
        "label": "ArcGIS Enterprise Portal",
        "home_url": cfg.PORTAL_URL,
        "url": cfg.PORTAL_URL,
        "user": cfg.PORTAL_USER,
        "password": cfg.PORTAL_PASSWORD,
        "script_name": "scan_portal.py",
    }
    return broken_items.run(profile)


if __name__ == "__main__":
    sys.exit(main())

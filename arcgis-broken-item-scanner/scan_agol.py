"""
Script Name: scan_agol.py
Project: arcgis-broken-item-scanner
Version: 1.0.0

Description:
    Scans an ArcGIS Online organization for items whose backing references no longer
    respond, groups the findings by item owner, and sends each owner one email listing
    all of their broken items.

    ArcGIS Online organizations commonly hold items that reference federated services
    on an ArcGIS Enterprise Portal. Those probes need a Portal token rather than an
    ArcGIS Online token. When AGOL_USE_FEDERATED_PORTAL is True, this script signs in
    to both and sends the matching token with each probe based on the host in the URL.
    A Portal sign-in failure is logged and the scan continues.

    ArcGIS Online usernames carry the organization short name, for example
    jane.doe@example.org_YourOrgName. Set ORG_USERNAME_SUFFIXES in config.py so those
    resolve to real addresses.

    All configuration lives in config.py. Copy config.example.py to config.py first.

Usage:
    python scan_agol.py

    With ArcGIS Pro's Python on Windows, Command Prompt, regular:
      "C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\envs\\arcgispro-py3\\python.exe" scan_agol.py

Exit Codes:
    - 0: Completed, whether or not broken items were found.
    - 2: Fatal error. Failure email sent if email is configured.
"""

import sys

import broken_items


def main():
    cfg = broken_items.load_config()
    profile = {
        "slug": "agol",
        "label": "ArcGIS Online",
        "home_url": cfg.AGOL_URL,
        "url": cfg.AGOL_URL,
        "user": cfg.AGOL_USER,
        "password": cfg.AGOL_PASSWORD,
        "script_name": "scan_agol.py",
    }
    if getattr(cfg, "AGOL_USE_FEDERATED_PORTAL", False):
        profile["federated"] = {
            "url": cfg.PORTAL_URL,
            "user": cfg.PORTAL_USER,
            "password": cfg.PORTAL_PASSWORD,
        }
    return broken_items.run(profile)


if __name__ == "__main__":
    sys.exit(main())

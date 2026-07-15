# ArcGIS Server Composite Locator Rebuilder

Automates scheduled rebuilds of composite geocoding locators on ArcGIS Enterprise / ArcGIS Server, with safe service stop/restart orchestration via the Admin REST API.

Built and battle-tested by the City of Grand Junction GIS Division.

## The problem this solves

File-based locators published as geocoding services hold file locks while the service is running, so you cannot simply schedule `RebuildAddressLocator` — the rebuild fails on locked files. Doing it correctly means:

1. Check the service status via the Admin REST API
2. Stop the service only if it was running
3. Rebuild every participating locator, **then** the composite (order matters — a composite rebuilt before its participants keeps referencing stale data)
4. Restart the service — **even if rebuilds failed** — so your geocoding service is never left down
5. Refresh the admin token before the restart, in case the rebuilds ran longer than the token lifetime

This script does all of that, for any number of composite configurations in one run, with full logging and optional email notification on failure.

## Requirements

- ArcGIS Server / ArcGIS Enterprise (tested on 11.x)
- Python with `arcpy` (ArcGIS Server or ArcGIS Pro Python environment)
- An ArcGIS Server administrator account
- Access to the Admin REST API — use the machine URL on port 6443; web adaptors typically block `/admin`
- Locators stored in a folder registered as a data store with ArcGIS Server, and each composite published as a geocoding service

## Setup

1. Clone the repo
2. Copy `secrets.example.py` to `secrets.py` and set your server admin password (`secrets.py` is gitignored)
3. Edit the CONFIGURATION section at the top of `rebuild_composite_locators.py`:
   - Server URL (port 6443) and admin username
   - `LOCATOR_CONFIGS` — one entry per composite: the geocoding service name (`Folder/Name.GeocodeServer`), the composite `.loc` path, and its participating `.loc` paths
4. Run with `DRY_RUN = True` first — it validates the admin token, every service status lookup, and every locator file path without stopping or rebuilding anything
5. Set `DRY_RUN = False` and schedule it:
   - Program: your ArcGIS Python environment's `python.exe`
   - Argument: path to `rebuild_composite_locators.py`
   - Run whether user is logged on or not

## Behavior notes

- Multiple composites are processed sequentially; one failing does not stop the others
- A service that was stopped before the run is left stopped after (rebuild still happens)
- Exit code is nonzero when any configuration fails, for scheduler integration
- The log file is kept in `./Log` (set `DELETE_LOG_ON_SUCCESS = True` to remove it on clean runs); on failure with `ENABLE_EMAIL = True`, the full log is included in the notification email

## Troubleshooting

- **Token request fails** — confirm you are using the machine URL on port 6443, not a web adaptor URL, and that the account has administrator privileges
- **Rebuild fails with locked-file errors** — the service stop did not succeed or another service also references the locator; check for additional geocoding services pointing at the same `.loc` files
- **Composite serves stale results after rebuild** — verify the composite is listed after its participants in your configuration; it must be rebuilt last

## License

MIT

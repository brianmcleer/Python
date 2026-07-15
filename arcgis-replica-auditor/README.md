# ArcGIS Feature Service Replica Auditor & Dashboard

A complete replica health system for ArcGIS Enterprise: a scheduled Python script that audits every feature service (sync) replica across one or more enterprise geodatabases and performs rolling age-based cleanup, plus a zero-dependency single-file HTML dashboard that visualizes the results.

Built and battle-tested by the City of Grand Junction GIS Division. Original script by Jackson Trappett and Brian McLeer.

## The problem this solves

Every offline area download (Field Maps, Collector, custom sync-enabled apps) registers a sync replica against your enterprise geodatabase. When users abandon those downloads — new phone, reinstalled app, left the organization — the replicas stay behind forever. Abandoned replicas pin geodatabase versions, which blocks compress from trimming state lineage and slowly degrades performance. Nothing in the platform cleans them up automatically.

This project gives you both visibility (what replicas exist, who owns them, when they last synced, which database they live in) and automated cleanup (unregister anything older than a configurable threshold, on a rolling daily cycle).

## Project structure

```
arcgis-replica-auditor/
├── unregister_feature_service_replicas.py   # scheduled audit + cleanup + export
├── secrets.example.py                        # copy to secrets.py (gitignored)
└── dashboard/
    ├── replica-viewer.html                   # single-file dashboard, no server needed
    └── replicas.js                           # sample data; overwritten by each script run
```

## The script

Each run:

1. **Scans each configured SDE geodatabase** with `arcpy.da.ListReplicas`, merging results and tagging every replica with its source database
2. **Queries each feature service's REST `/replicas` endpoint**, then each replica's individual info endpoint — the owner (`replicaOwner`) only lives in the per-replica info response, not the list response
3. **Joins SDE-side and REST-side data by replicaID** into one combined record per replica
4. **Unregisters replicas older than `UNREGISTER_AGE_DAYS`** (default 45) via each service's `unRegisterReplica` REST operation
5. **Exports the surviving replicas** to `replicas.json` and `replicas.js`, including a manifest of all scanned databases so databases with zero replicas render as 0 instead of disappearing
6. **Reports duplicate replicas** (same owner + same service + different creation dates) — a telltale of offline clients that re-downloaded areas without syncing

### Details worth knowing

- **Owner filtering:** database replicas (owners like `DBO`, `sde`, `SYSTEM`) are excluded from cleanup and export; feature service replicas are identified by email-style owners. If your org's usernames aren't email addresses, adjust the owner filter in `export_to_json` and the STEP 5 loop.
- **Replicas with no creation date are never unregistered** — age can't be determined, so they're kept and logged.
- **Token handling:** a `TokenManager` class caches the token, refreshes it 120 seconds before expiry, and every REST call retries once on token errors (codes 498/499) with a forced refresh. Uses `client=referer`; switch to `client=ip` in `TokenManager.get_token` if your security config requires it.
- **The export runs after cleanup**, and successfully unregistered replicas are filtered out, so the dashboard always reflects the post-cleanup state.
- Throttling between REST calls is configurable (`SLEEP_BETWEEN_REQUESTS_SEC`) to keep load off your hosting server.

## The dashboard

`dashboard/replica-viewer.html` is a self-contained, single-file dashboard — no web server, no build step, no external dependencies. Double-click it and it loads `replicas.js` from the same folder. It ships with sample data so you can explore it before wiring up the script.

Features: KPI cards (total, healthy, stale, never-synced, conflicts) with per-database breakdowns, click-to-filter, a sortable/searchable replica table with owner and service columns, a stale-watch list, CSV/JSON export, print view, light/dark theme, and keyboard shortcuts. WCAG-conscious markup throughout.

The data contract is three JavaScript variables in `replicas.js`, written by the script on every run:

- `REPLICA_GENERATED` — ISO timestamp of the run
- `REPLICA_DATABASES` — manifest of every scanned geodatabase
- `REPLICA_DATA` — array of replica records (id, name, owner, source database, service, creation date, last sync date, sync model/direction, conflict flag)

By default the script exports into the `dashboard/` folder next to itself, so the viewer always shows the latest run. In production, point `OUTPUT_DIR` at a folder served by IIS (or any static host) and put `replica-viewer.html` there.

**Note:** after production runs, `replicas.js`/`replicas.json` contain your real replica data (including owner emails). Don't commit them — `replicas.json` is gitignored, and take care not to commit a production `replicas.js` over the sample.

## Requirements

- Python 3.8+ in an ArcGIS Pro or ArcGIS Server environment (`arcpy`)
- `requests`
- An ArcGIS account with privileges to view and unregister replicas on the target services (typically the service owner or an admin)
- SDE connection files for each geodatabase to audit
- The dashboard needs only a browser

## Setup

1. Clone the repo
2. Copy `secrets.example.py` to `secrets.py` and set your automation account password (`secrets.py` is gitignored)
3. Edit the CONFIGURATION section at the top of `unregister_feature_service_replicas.py`:
   - `SDE_WORKSPACES` — one connection file per geodatabase to audit
   - `PORTAL_BASE` and `ARCGIS_USERNAME`
   - `UNREGISTER_AGE_DAYS` — the rolling cleanup threshold (set to 0 or None to disable cleanup and run audit-only)
   - `OUTPUT_DIR` if you're serving the dashboard from somewhere other than the bundled folder
4. Run with `DRY_RUN = True` first — it performs the full audit and export and logs exactly which replicas WOULD be unregistered, without touching anything; open `dashboard/replica-viewer.html` to see your real data
5. Review the log, then set `DRY_RUN = False` and schedule it daily

## License

Apache License 2.0

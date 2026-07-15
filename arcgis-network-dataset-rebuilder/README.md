# ArcGIS Network Dataset Rebuilder

End-to-end scheduled maintenance for an enterprise geodatabase network dataset used for routing with ArcGIS Network Analyst: refresh source data, populate all network attributes, calculate travel times, rebuild the network, and compress the geodatabase — in one configurable script.

Built and battle-tested by the City of Grand Junction GIS Division.

## What it does

Each run:

1. **Optionally refreshes source feature classes from production** — DeleteRows + Append with automatic field mapping, inside an edit session, safe for versioned data
2. **Adds required network attribute fields if missing** — functional class, speed limit, mode restrictions, hierarchy, travel time, time zone
3. **Populates attributes from your road classification field** — FUNC_CLASS (via a configurable mapping), ROAD_CLASS, HIERARCHY, and pedestrian/auto/bus restrictions
4. **Calculates directional travel times** (FT_Minutes/TF_Minutes) from geometry length and speed, honoring ONE_WAY values, with optional hierarchy time penalties (local roads 1.5x, minor arterials 1.1x) so routes prefer arterials over cutting through neighborhoods — the behavior people expect from commercial routing
5. **Creates the TimeZones table** required to publish routing services to ArcGIS Enterprise Portal
6. **Rebuilds the network dataset** with `arcpy.na.BuildNetwork` (full build), surfacing BuildErrors.txt contents in the log
7. **Optionally compresses the geodatabase** and analyzes system tables to keep version state lineage under control on scheduled runs

Driveways and override junction feature classes are supported as optional inputs and skipped cleanly when not configured.

## Hard-won notes baked in

These each cost real debugging time and are already handled:

1. `TruncateTable` is not supported on versioned tables — the refresh uses `DeleteRows` inside an edit session instead
2. Network dataset feature classes require edit sessions for attribute updates regardless of versioning state
3. Field mapping is built from the intersection of source and target schemas, so the target's extra routing fields survive the refresh (they come back as NULL and are repopulated in the same run)
4. A `TimeZones` table with an `MSTIMEZONE` field is required to register the network dataset with Portal routing services — easy to miss until publish fails
5. Compressing after the build prevents unbounded version state growth when this runs on a schedule

## Requirements

- ArcGIS Pro Python environment (`arcpy`, Python 3.8+)
- ArcGIS Network Analyst extension license
- A network dataset already created over your source feature classes
- The feature dataset registered as versioned (one-time):

  ```python
  arcpy.RegisterAsVersioned_management(dataset_path, "NO_EDITS_TO_BASE")
  ```

- For the optional source refresh: a read-only connection to the production database
- For the optional compress: a connection as the sde or DBO user

No credentials are stored by this script — authentication rides on your `.sde` connection files.

## Setup

1. Clone the repo
2. Edit the CONFIGURATION section at the top of `rebuild_network_dataset.py`:
   - Paths to your centerline FC, network dataset, and SDE connection (driveways/junctions optional)
   - `FUNC_CLASS_MAP` — map **your** road classification values to functional classes 1 (highway) through 5 (local); unmapped values default to 5
   - Behavior toggles: `REFRESH_FROM_SOURCE`, `APPLY_HIERARCHY_PENALTY`, `CREATE_TIMEZONES_TABLE`, `RUN_COMPRESS`
   - `TIME_ZONE_NAME` for your region (Windows time zone name)
3. Run manually once and review the results in ArcGIS Pro
4. Manually refine `SPEED_LIMIT` on known roads and set `HEIGHT_LIMIT_FT` on bridges with clearance restrictions — the script only fills defaults, it never overwrites your curated values on NULL-only passes
5. Test routing, then schedule it (monthly works well); exit code is nonzero on failure, and optional email notification includes the log path

## Behavior notes

- One-way handling: `FT`/`F` and `TF`/`T` values restrict the opposing direction (`-1`); `N` restricts both; anything else is bidirectional
- Records with NULL or zero speed use `DEFAULT_SPEED` and are counted in the log statistics
- Driveways use their own speed field and default (`DRIVEWAY_SPEED_FIELD`, `DRIVEWAY_DEFAULT_SPEED`)
- Every step logs record counts, distributions, and timing; the log is kept in `./Log` (deleted on success if `DELETE_LOG_ON_SUCCESS = True`)

## License

Apache License 2.0

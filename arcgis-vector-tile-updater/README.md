# ArcGIS Enterprise Hosted Vector Tile Updater

Automates refreshing a hosted vector tile layer on ArcGIS Enterprise Portal from an ArcGIS Pro project, using the **Replace Layer workflow**. The production layer's item ID and service URL never change, so webmaps and apps referencing the layer are never broken.

Built and battle-tested by the City of Grand Junction GIS Division.

## What it does

Each run:

1. Builds a fresh vector tile package (VTPK) from a map in your `.aprx` using `arcpy.management.CreateVectorTilePackage`
2. Updates the package file on a persistent Portal VTPK item (chunked upload handles large packages)
3. Publishes the package to a **new temporary vector tile layer** under a unique timestamped name
4. Swaps the temp layer's content into the production layer via `gis.content.replace_service()` — item ID and URL preserved
5. Verifies the production item survived intact
6. Archives the previous tiles under a timestamped name as a one-run rollback window
7. Cleans up temp and archive layers left by prior runs

## Why Replace Layer instead of publish-with-overwrite

Overwrite publishing resolves its target by service **name** and by the Service2Data item relationship. Both are fragile in practice:

- If any other service shares the name (for example, a hosted **feature** service created by an earlier failed publish), Portal fails with the unhelpful error `Unable to determine Service Type`
- Wrong or misread file type values can silently misroute the publish into the feature service pipeline, creating orphan feature services that then poison future name resolution
- The `arcgis` Python library's `Item.publish()` swallows the server's real error behind a generic `Job failed.`

Publishing to a unique temp name and swapping content sidesteps name resolution entirely, and is the same mechanism as Portal's built-in "Replace Layer" feature.

## Hard-won REST API gotchas (baked into the fallback path)

If you ever publish vector tile packages via the raw sharing REST API, know these — each cost real debugging time:

1. The publish parameter is `fileType` with a **capital T**. All-lowercase `filetype` is silently ignored by the endpoint, producing `Unable to determine Service Type`.
2. `outputType=VectorTiles` is required alongside `fileType=vectortilepackage`.
3. The value `vectortiles` (which some library versions send) is not valid and routes the publish into the **feature service** pipeline, failing with `ERROR 000800` and leaving orphan items behind.
4. Poll the job status endpoint yourself and log the full response — `statusMessage` contains the real error the library discards.

The script tries the `arcgis` library first (it sends the correct request form) and falls back to a verbose raw REST publish with these corrections if the library fails.

## Requirements

- ArcGIS Enterprise Portal (tested on 11.x) with a hosting server
- ArcGIS Pro Python environment (`arcpy` + `arcgis` libraries, Python 3.8+)
- A Portal account that **owns both items** (the VTPK item and the hosted tile layer) with publishing privileges
- The hosted tile layer must have been published from the VTPK item at least once (standard publish from the item page works)

## Setup

1. Clone the repo
2. Copy `secrets.example.py` to `secrets.py` and set your Portal password (`secrets.py` is gitignored)
3. Edit the CONFIGURATION section at the top of `update_hosted_vector_tiles.py`:
   - Portal URL and username
   - The two Portal item IDs (from each item's details page URL)
   - Paths to your `.aprx`, tiling scheme XML, output folder, and (for INDEXED caches) index polygons
4. Run with `DRY_RUN = True` first:

   ```
   "C:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe" update_hosted_vector_tiles.py
   ```

   Confirm the log resolves both items and derives the expected service name.
5. Set `DRY_RUN = False` and run for real
6. Schedule it (Windows Task Scheduler or your script controller). The script exits nonzero on failure. Optional email notifications are available via the `ENABLE_EMAIL` settings.

## Steady state

After any successful run, the owning account holds exactly three managed items:

| Item | Type | Notes |
|------|------|-------|
| Production layer | Tile layer (hosted) | Item ID and URL never change |
| Source package | Vector tile package | File replaced each run; title pinned |
| `<name>_archive<timestamp>` | Tile layer (hosted) | Previous tiles; rollback until next run |

If you ever see extra `_tmp` items, a run died mid-flight; the next run's cleanup removes them automatically.

## Rollback

The archive layer holds the previous tiles until the start of the next run. To roll back, use Portal's Replace Layer on the production item, choosing the archive as the replacement.

## Options

- `REMOVE_ITEM_STYLE_OVERLAY` — if True, deletes any `styles/root.json` resource on the production item after each replace, making the service style the single source of truth. Leave False if you maintain a deliberate custom item style (for example, saved from the Vector Tile Style Editor).
- `DELETE_LOG_ON_SUCCESS` — housekeeping for scheduled runs.

## Troubleshooting

- **`Unable to determine Service Type`** — see the gotchas above; also check whether another service (any type) shares your tile layer's service name.
- **Publish creates a Feature Service instead of a tile layer** — wrong `fileType` value reached the server; the script's safety gates detect this, delete the partial item, and abort.
- **Labels or features missing at specific zoom levels in webmaps but fine in Pro** — likely a tiling scheme vs. webmap zoom-stop mismatch. If your cache uses a custom tiling scheme, webmap zoom stops (standard Web Mercator scales) can fall a fraction below your scheme's levels, causing the renderer to draw tiles from one level shallower than expected — and the tile generator materializes label features roughly one level deeper than authored scale ranges suggest. Fix it at the authoring level: extend the relevant layer/label class minimum scale outward by one full level so features materialize into the shallower tiles. Verify by decoding actual tiles rather than trusting the style JSON.

## License

MIT

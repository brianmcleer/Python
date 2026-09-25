# ArcGIS Print Service Publisher

Publishes an Export Web Map (print) geoprocessing service to ArcGIS Server from a folder
of ArcGIS Pro layout files (.pagx). Point it at a federated server or a stand-alone
ArcGIS Server, run it, and every layout in the folder becomes a print template that Map
Viewer, Experience Builder, and Instant Apps can use.

## Why

ArcGIS Online can use organization Layout items as print templates, but every print that
uses one costs credits (5 credits per print when this was written). A print service on
your own ArcGIS Server carries the same layouts, costs nothing per print, and can print
services that Esri's hosted print service cannot reach.

Publishing that service by hand in ArcGIS Pro is a dozen clicks that nobody remembers a
year later when a layout changes. This script makes it a re-runnable job: edit or add a
.pagx, run again, the service is overwritten in place with the same URL.

## What it does

1. Runs the Export Web Map tool once against an empty web map, using your layout folder.
   That run carries the template list into the service definition.
2. Builds and analyzes the service definition draft (`CreateGPSDDraft`). Draft errors
   stop the run.
3. Stages and uploads the service, overwriting an existing service of the same name.
   The .pagx folder is copied to the server with the service, so no data store
   registration is needed.
4. On a federated server, shares the Portal item to your organization and, if
   configured, to Everyone, and files it in a Portal content folder.
5. Requests the task anonymously and confirms the template list on the server matches
   the folder.
6. Optionally sets the Portal's Printing utility service to the new task URL.

The published service prints whatever web map the client sends. Layers and basemap come
from the user's map at print time.

## Requirements

- ArcGIS Pro 3.x Python environment (`arcpy` and `arcgis` are both included)
- `requests`
- A machine that can reach the target server
- Publisher or administrator rights on the server (and Portal, for federated servers)

## Layout files

Author each layout in ArcGIS Pro and export it with Share > Save As > Layout File. For a
layout to work with Export Web Map:

- Name the map frame `WEBMAP_MAP_FRAME` (case-sensitive) if the layout has more than one
  map frame.
- Point scale bars, north arrows, and scale text at that frame.
- Dynamic text for the web map title:
  `<dyn type="layout" property="metadata" attribute="title"/>`
- Dynamic text for the date: `<dyn type="date" format=""/>`

The file name (without `.pagx`) is the template name users see.

**Order matters.** Map Viewer preselects the first template in the service's list, and the
service lists templates alphabetically by file name. The Export Web Map tool always
publishes `MAP_ONLY` as the parameter default and does not expose that in the draft, so
the script cannot change it. If you want `Letter Landscape` chosen by default, make it
sort first (a prefix such as `1 - Letter Landscape.pagx` works). Map Viewer hides
`MAP_ONLY` from the layout list on its own.

## Setup

1. Copy the config template:

   Windows, Command Prompt, regular:
   ```
   copy config.example.py config.py
   ```
   Linux or macOS, bash:
   ```
   cp config.example.py config.py
   ```

2. Fill in `config.py`. At minimum: `PAGX_DIR`, `PUBLISH_LAYOUT`, `PUBLISH_MODE`, and
   the server or Portal connection values for that mode.

3. Leave `DRY_RUN = True` for the first run. The tool runs, the draft is built and
   analyzed, and nothing is sent to the server.

4. Run it. ArcGIS Pro Python, Command Prompt, regular:

   ```
   "C:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe" publish_print_service.py
   ```

5. Read the log. Draft warnings about the layout folder being copied to the server are
   expected. Fix any errors, then set `DRY_RUN = False` and run again.

6. Point your organization at the service. Either set `SET_PORTAL_PRINT_SERVICE = True`
   (federated servers, administrator account) or paste the task URL by hand:

   - **Portal:** Organization > Settings > Utility services > Printing
   - **ArcGIS Online:** Organization > Settings > Utility services > Printing. The service
     must be shared to Everyone, since Map Viewer calls it from the browser without a
     Portal token. Clear the Layout template group on the same page if you had one, or
     users can still pick a credit-consuming template.

   Both settings pages let you drag templates into the order you want shown.

## Federated versus stand-alone

`PUBLISH_MODE = "FEDERATED"` signs in to Portal and publishes to the federated server by
URL. Sharing, Portal folder, and verification all happen automatically.

`PUBLISH_MODE = "AGS"` publishes through an `.ags` connection file saved from ArcGIS Pro
(Insert > Connections > Server > New ArcGIS Server, publisher credentials). Sharing is
handled by the server's own security settings. Verify the task in ArcGIS Server Manager.

## Updating layouts

Edit the layout in Pro, re-export the .pagx over the old file, run the script. Templates
are re-read from the folder and the service is overwritten with the same name and URL.
Nothing downstream needs to change.

## Scheduling

This is normally run on demand, not on a schedule. If you keep the .pagx files in a
shared folder that several people edit, a nightly run keeps the service current:

```
Program:   C:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe
Arguments: publish_print_service.py
Start in:  C:\path\to\arcgis-print-service-publisher
```

Exit code 0 means the run completed. Exit code 2 means it failed; the log says why.

## Security

`config.py` holds a plaintext password and is excluded by `.gitignore`. Keep it that way.
Restrict filesystem permissions on the script folder. For a tighter setup, replace the
password value with a lookup against a secret store such as the `keyring` package or your
platform's credential manager.

## Limitations

- The default template cannot be set by the script (see Layout files above).
- The empty web map used for the publish run assumes Web Mercator. It is never printed by
  users and does not limit what the service can print.
- `copy_data_to_server=True` means the server holds its own copy of the layouts. Editing
  a .pagx on disk does nothing until the script runs again.

## License

Apache License 2.0. Provided as-is with no warranty.

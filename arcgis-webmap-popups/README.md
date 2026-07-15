# ArcGIS Webmap Popup Configurator

Applies one consistent, accessible Arcade popup to every feature layer inside a MapServer group layer in an ArcGIS Enterprise or ArcGIS Online web map — without hand-configuring each sublayer in Map Viewer.

Built and battle-tested by the City of Grand Junction GIS Division.

## The problem this solves

When you add a MapServer to a web map, its sublayers often show up by numeric ID with no popup configured. Setting up popups by hand across many layers — and many web maps — is tedious, and the configurations drift out of sync over time. This script drives every layer from a single Arcade expression, so they all get the same well-formatted, accessible popup, and updating the expression updates everything on the next run.

## What it does

1. Connects to your portal and loads the web map JSON
2. Finds the target group layer (searching nested groups recursively) and reads the real sublayer names from the MapServer REST endpoint
3. Rebuilds the group's `layers` array, enabling a popup on every feature sublayer and disabling it on group sublayers
4. Applies one shared Arcade expression (see below) to each feature sublayer
5. Optionally applies the same popup to one top-level feature layer sitting beside the group (e.g. a `Parcels` layer)
6. Saves the web map in a single update call, so either all changes land or none do

## The Arcade expression

`arcade_popup.js` is a generic, accessible field-listing popup. For each feature it renders every non-excluded field as `Label: value`, one per line, and auto-formats:

- **Dates** → `MM/DD/YYYY`
- **Coded domain values** → the description, not the raw code (resolved before any type detection)
- **Booleans** → True/False or Yes/No (with alias hints for 1/0 fields like `IsActive`)
- **Currency** → `$#,###.##`, with anti-hints so measurement aliases like "Square Feet" are never mistaken for money because "fee" is a substring of "feet"
- **Phone numbers** → `(xxx) xxx-xxxx` as `tel:` links, detected by digit count + punctuation + alias hints, with anti-hints to reject IDs, coordinates, and measurements that happen to have 10 digits
- **Emails** → `mailto:` links
- **Hyperlinks** → absolute `http(s)://` values become links automatically

It produces WCAG-conscious HTML: semantic `<strong>` labels, descriptive link accessible-names via `title` attributes, `(opens in new window)` announcements with `rel="noopener noreferrer"`, and trimmed whitespace so blank fields don't render empty rows.

The expression needs **no editing** to work. Two things you may want to customize are clearly marked at the top of the file:

1. `fieldsToExclude` — system/internal field names to hide
2. The **DOCUMENT LINK SETTINGS** block — if your data stores relative document paths (like `docs/permit123.pdf`) that should become links against your own document server, set `ENABLE_DOC_LINKS = true` and `DOC_BASE_URL`. Off by default; absolute URLs are always linked regardless.

Because the Arcade lives in its own file, you can edit and version it independently, and reuse it in Map Viewer or Experience Builder directly.

## Requirements

- Python 3.7+ with the ArcGIS API for Python (`arcgis`) and `requests` — both included with ArcGIS Pro
- A portal account with rights to edit the target web map
- The target layer must be a MapServer group layer in the web map

## Setup

1. Clone the repo
2. Copy `secrets.example.py` to `secrets.py` and set your portal password (`secrets.py` is gitignored)
3. Edit the CONFIGURATION block at the top of `update_webmap_popups.py`:
   - `PORTAL_URL`, `WEBMAP_ITEM_ID`, `USERNAME`
   - `TARGET_GROUP_LAYER` — the name of the MapServer group layer to process
   - `TOP_LEVEL_LAYER` — optional single top-level layer to also configure, or `None`
4. Run it from an ArcGIS Pro Python environment:

   ```
   python update_webmap_popups.py
   ```

5. Check `webmap_popup_update.log` for a per-layer report

## Running across many web maps

The City of Grand Junction GIS Division runs this across ten web maps (internal and external). The simplest approach is one copy of the config block per map, or a small driver script that imports the functions and loops over a list of `(item_id, group_layer)` pairs. Keeping the Arcade in `arcade_popup.js` means all maps share one expression — edit it once, rerun, and every popup updates.

## Notes and gotchas

- Uses `disablePopup: false` rather than `popupEnabled: true` — the former is what current Map Viewer and Experience Builder honor reliably
- The Arcade uses plain structural HTML (no inline colors); Map Viewer and Experience Builder popup themes override inline color styles on `<strong>`/`<a>`, so colored text renders inconsistently and is best left to the theme
- Group sublayers (those with `subLayerIds`) get popups disabled since they have no features of their own
- If the target group layer or the optional top-level layer isn't found, the script logs the available layer names to help you correct the configuration

## License

Apache License 2.0

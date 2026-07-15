"""
Script Name: update_webmap_popups.py
Version: 1.0.0
License: Apache-2.0

Description:
    Applies a single, consistent, accessible Arcade popup to every
    feature layer inside a MapServer group layer in an ArcGIS
    Enterprise / ArcGIS Online web map - without hand-configuring each
    sublayer in Map Viewer.

    A MapServer added to a web map often shows its sublayers only by
    numeric ID, with no popup configured. This script:

        1. Connects to your portal and loads the web map JSON
        2. Finds the target group layer (recursively) and reads the
           real sublayer names from the MapServer REST endpoint
        3. Rebuilds the group's "layers" array, enabling a popup on
           every feature sublayer and disabling it on group sublayers
        4. Applies one shared Arcade expression that renders every
           non-excluded field as "Label: value", auto-formatting
           dates, booleans, currency, phone numbers, emails, coded
           domain values, and hyperlinks
        5. Optionally applies the same popup to one top-level feature
           layer (e.g. a "Parcels" layer sitting beside the group)
        6. Saves the web map in a single update call

    The bundled Arcade (arcade_popup.js) is generic and needs no
    editing to work. It detects field types from values and aliases.
    A small, clearly marked block at the top lets you turn relative
    field values into hyperlinks against your own document server if
    you want that behavior; it is disabled by default.

Why this exists:
    Configuring popups by hand across many layers and many web maps is
    tedious and drifts out of sync. Driving them all from one Arcade
    expression means every layer gets the same accessible, well
    formatted popup, and updating the expression updates everything on
    the next run.

Accessibility:
    The Arcade produces WCAG-conscious HTML: semantic <strong> labels,
    descriptive link accessible-names via title attributes,
    "(opens in new window)" announcements with rel="noopener
    noreferrer", tel:/mailto: links, and trimmed whitespace so blank
    fields do not render empty rows.

Setup:
    1. Copy secrets.example.py to secrets.py and set your portal
       password. secrets.py is gitignored.
    2. Fill in the CONFIGURATION block: portal URL, web map item ID,
       username, target group layer name, and (optionally) a top-level
       layer name.
    3. Run from an ArcGIS Pro Python environment.
    4. To run the same update across many web maps, copy the config
       block per map or wrap this in a loop / driver script.

Dependencies:
    - Python 3.7+
    - arcgis (ArcGIS API for Python, included with ArcGIS Pro)
    - requests
"""
import os
import logging
import sys
import json
import requests
from typing import List, Dict, Any, Optional, Tuple
from arcgis.gis import GIS

# Configure logging with ASCII-only output for Windows compatibility
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('webmap_popup_update.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# =============================================================================
# CONFIGURATION
# =============================================================================
PORTAL_URL = "https://your-portal.example.com/portal/"
WEBMAP_ITEM_ID = "your_webmap_item_id"
USERNAME = "your_automation_user"

# Name of the MapServer group layer whose sublayers should get popups.
TARGET_GROUP_LAYER = "My Map Service"

# Optional: a single top-level feature layer (sitting beside the group,
# not inside it) to apply the same popup to. Set to None to skip.
TOP_LEVEL_LAYER = None  # e.g. "Parcels"

# Path to the bundled Arcade expression file (same folder by default).
ARCADE_FILE = "arcade_popup.js"

# Password is imported from a local secrets.py (gitignored). See
# secrets.example.py. secrets.py lives in the same folder as this script.
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)
try:
    from secrets import PORTAL_PASSWORD as PASSWORD
except ImportError as exc:
    logger.error("Failed to import PORTAL_PASSWORD from secrets.py")
    logger.error("Copy secrets.example.py to secrets.py and set the password.")
    logger.error("Import error: %s", exc)
    sys.exit(1)

# Load the Arcade expression from its file so it can be edited and reused
# independently of this script.
_ARCADE_PATH = os.path.join(_SCRIPT_DIR, ARCADE_FILE)
try:
    with open(_ARCADE_PATH, "r", encoding="utf-8") as _f:
        ARCADE_CODE = _f.read()
except OSError as exc:
    logger.error("Failed to read Arcade expression from %s", _ARCADE_PATH)
    logger.error("Error: %s", exc)
    sys.exit(1)

def fetch_mapserver_layer_info(service_url: str, gis: GIS) -> Tuple[Dict[int, str], List[Dict[str, Any]]]:
    """
    Fetch layer definitions from a MapServer REST endpoint.

    Args:
        service_url: The MapServer URL (e.g., .../MapServer)
        gis: The GIS connection object for authentication

    Returns:
        Tuple of (layer_id->name mapping, raw layers list from MapServer)
    """
    layer_mapping = {}
    layers_list = []

    try:
        logger.info(f"Fetching MapServer layer definitions from: {service_url}")
        params = {'f': 'json', 'token': gis._con.token}
        response = requests.get(service_url, params=params, timeout=30)
        response.raise_for_status()
        service_info = response.json()

        if 'layers' in service_info:
            layers_list = service_info['layers']
            for layer in layers_list:
                layer_id = layer.get('id')
                layer_name = layer.get('name')
                if layer_id is not None and layer_name:
                    layer_mapping[layer_id] = layer_name
                    logger.info(f"  Mapped Layer ID {layer_id} -> {layer_name}")

        logger.info(f"Successfully fetched {len(layer_mapping)} layer name(s) from MapServer")

    except Exception as e:
        logger.error(f"Error fetching MapServer layers: {str(e)}")
        logger.exception("Full error details:")

    return layer_mapping, layers_list


def create_popup_info(layer_title: str) -> Dict[str, Any]:
    """
    Create a popupInfo structure using inline expressionInfo format.

    This format is proven to work with both Map Viewer and Experience Builder.

    Args:
        layer_title: The title to use for the popup

    Returns:
        Dictionary containing the complete popupInfo configuration
    """
    return {
        "popupElements": [
            {
                "type": "expression",
                "expressionInfo": {
                    "title": layer_title,
                    "expression": ARCADE_CODE
                }
            }
        ],
        "fieldInfos": [],
        "mediaInfos": [],
        "title": layer_title,
        "showAttachments": True
    }


def build_layers_from_mapserver(layers_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Build a layers array structure from MapServer REST metadata.

    Uses 'disablePopup: false' (not 'popupEnabled: true') for compatibility.
    Group layers (those with subLayerIds) get disablePopup: true.
    Feature layers get full popup configuration.

    Args:
        layers_list: Raw layers list from MapServer REST endpoint

    Returns:
        List of layer definition dictionaries ready for webmap
    """
    layers = []

    for layer_info in layers_list:
        layer_id = layer_info.get('id')
        layer_name = layer_info.get('name', f'Layer {layer_id}')
        sub_layer_ids = layer_info.get('subLayerIds')

        if sub_layer_ids is not None:
            # Group layer - disable popup (groups don't have features)
            logger.info(f"  Layer {layer_id} ({layer_name}) is a group layer - disabling popup")
            layer_def = {
                "id": layer_id,
                "disablePopup": True
            }
        else:
            # Feature layer - configure popup with Arcade expression
            logger.info(f"  Layer {layer_id} ({layer_name}) - configuring popup")
            layer_def = {
                "id": layer_id,
                "disablePopup": False,
                "popupInfo": create_popup_info(layer_name)
            }

        layers.append(layer_def)

    return layers


def find_target_group_layer(layers: List[Dict[str, Any]], target_name: str) -> Optional[Dict[str, Any]]:
    """
    Find the target group layer by name, searching recursively if needed.

    Args:
        layers: List of layer dictionaries to search
        target_name: Name of the group layer to find

    Returns:
        The target group layer dictionary if found, None otherwise
    """
    logger.info(f"Searching for target group layer: '{target_name}'")

    for layer in layers:
        layer_title = layer.get("title", "")

        if layer_title == target_name:
            logger.info(f"[SUCCESS] Found target group layer: '{target_name}'")
            return layer

        # If this is a group layer, search its nested layers
        if "layers" in layer and layer["layers"]:
            result = find_target_group_layer(layer["layers"], target_name)
            if result:
                return result

    return None


def find_top_level_layer(layers: List[Dict[str, Any]], target_name: str) -> Optional[Dict[str, Any]]:
    """
    Find a layer by exact title match at the top level only (no recursion).

    Used for finding sibling layers of the main group layer - like the
    "Parcels" layer that sits alongside (not inside) the main group.

    Args:
        layers: Top-level operationalLayers array from the webmap.
        target_name: Title to match.

    Returns:
        The layer dict if found, None otherwise.
    """
    for layer in layers:
        if layer.get("title", "") == target_name:
            return layer
    return None


def configure_top_level_popup(operational_layers: List[Dict[str, Any]]) -> bool:
    """
    Find the optional top-level layer named by TOP_LEVEL_LAYER and apply
    the Arcade popup to it.

    Treated as optional - returns False (with a warning) if TOP_LEVEL_LAYER
    is set but not found, and returns False silently if TOP_LEVEL_LAYER is
    None. Returns True only when a layer was found and configured.

    Args:
        operational_layers: Top-level operationalLayers array (mutated in place).

    Returns:
        True if a top-level layer was configured, False otherwise.
    """
    if not TOP_LEVEL_LAYER:
        return False

    layer = find_top_level_layer(operational_layers, TOP_LEVEL_LAYER)
    if not layer:
        logger.warning("[WARN] No top-level '%s' layer found in this webmap", TOP_LEVEL_LAYER)
        logger.warning("       Skipping top-level popup configuration")
        return False

    logger.info("Configuring popup for top-level '%s' layer", TOP_LEVEL_LAYER)
    layer["disablePopup"] = False
    layer["popupInfo"] = create_popup_info(TOP_LEVEL_LAYER)
    logger.info("[SUCCESS] Top-level '%s' popup configured", TOP_LEVEL_LAYER)
    return True


def main():
    """
    Main execution function.
    """
    logger.info("=" * 80)
    logger.info("Starting ArcGIS Webmap Popup Update Script (v1.0.0)")
    logger.info("=" * 80)

    # Validate password is set in secrets.py
    if not PASSWORD or PASSWORD == "CHANGE_ME":
        logger.error("ERROR: PORTAL_PASSWORD in secrets.py is not set.")
        logger.error("Copy secrets.example.py to secrets.py and set the password.")
        return False

    try:
        # Connect to ArcGIS Enterprise portal
        logger.info(f"Connecting to ArcGIS Enterprise portal: {PORTAL_URL}")
        gis = GIS(PORTAL_URL, USERNAME, PASSWORD)
        logger.info(f"[SUCCESS] Connected as: {gis.properties.user.username}")
        logger.info(f"  Portal version: {gis.version}")

        # Get the webmap item
        logger.info(f"Retrieving webmap with ID: {WEBMAP_ITEM_ID}")
        webmap_item = gis.content.get(WEBMAP_ITEM_ID)

        if not webmap_item:
            logger.error(f"[ERROR] Webmap with ID {WEBMAP_ITEM_ID} not found or not accessible")
            return False

        logger.info(f"[SUCCESS] Retrieved webmap: '{webmap_item.title}'")
        logger.info(f"  Owner: {webmap_item.owner}")
        logger.info(f"  Type: {webmap_item.type}")

        # Get webmap JSON definition
        logger.info("Loading webmap JSON definition...")
        webmap_data = webmap_item.get_data()

        if not webmap_data:
            logger.error("[ERROR] Failed to retrieve webmap data")
            return False

        logger.info("[SUCCESS] Webmap definition loaded successfully")

        # Get operational layers
        operational_layers = webmap_data.get("operationalLayers", [])
        logger.info(f"Found {len(operational_layers)} operational layer(s) in webmap")

        # Find the target group layer
        target_group = find_target_group_layer(operational_layers, TARGET_GROUP_LAYER)

        if not target_group:
            logger.error(f"[ERROR] Target group layer '{TARGET_GROUP_LAYER}' not found in webmap")
            logger.info("Available top-level layers:")
            for layer in operational_layers:
                layer_name = layer.get("title", "No title")
                logger.info(f"  - {layer_name}")
            return False

        # Remove old 'sublayers' or 'layers' array if present (clean slate)
        if "sublayers" in target_group:
            logger.info("Removing old 'sublayers' array")
            del target_group["sublayers"]

        if "layers" in target_group:
            logger.info("Removing old 'layers' array (will rebuild from MapServer)")
            del target_group["layers"]

        # Get MapServer URL and fetch layer info
        service_url = target_group.get("url", "")
        if not service_url or "/MapServer" not in service_url:
            logger.error("[ERROR] No MapServer URL found in target group layer")
            return False

        logger.info(f"Found service URL: {service_url}")
        layer_name_map, layers_list = fetch_mapserver_layer_info(service_url, gis)

        if not layers_list:
            logger.error("[ERROR] No layers fetched from MapServer")
            return False

        # Build new layers array from MapServer metadata
        logger.info("")
        logger.info("=" * 80)
        logger.info("Building 'layers' array from MapServer metadata")
        logger.info("=" * 80)

        layers = build_layers_from_mapserver(layers_list)
        target_group["layers"] = layers

        # Count feature layers (those with popups enabled)
        feature_count = sum(1 for lyr in layers if not lyr.get("disablePopup", True))
        group_count = sum(1 for lyr in layers if lyr.get("disablePopup", False))

        logger.info(f"[SUCCESS] Built {len(layers)} layer definitions")
        logger.info(f"  - Feature layers with popups: {feature_count}")
        logger.info(f"  - Group layers (no popups): {group_count}")

        # Also configure the optional top-level layer (TOP_LEVEL_LAYER) if
        # present. This is optional - a missing layer is a warning, not an error.
        logger.info("")
        logger.info("=" * 80)
        logger.info("Configuring optional top-level layer")
        logger.info("=" * 80)
        top_level_configured = configure_top_level_popup(operational_layers)

        # Save the updated webmap
        logger.info("")
        logger.info("Saving updated webmap...")
        try:
            update_result = webmap_item.update(data=json.dumps(webmap_data))

            if update_result:
                logger.info("[SUCCESS] Successfully saved webmap updates")
            else:
                logger.error("[ERROR] Failed to save webmap updates")
                return False

        except Exception as e:
            logger.error(f"[ERROR] Error saving webmap: {str(e)}")
            logger.error("Changes were not persisted to the portal")
            return False

        logger.info("")
        logger.info("=" * 80)
        logger.info("Script completed successfully!")
        logger.info(f"Total feature layers with popups configured: {feature_count}")
        if top_level_configured:
            logger.info(f"Top-level layer '{TOP_LEVEL_LAYER}': configured")
        elif TOP_LEVEL_LAYER:
            logger.info(f"Top-level layer '{TOP_LEVEL_LAYER}': NOT FOUND (skipped)")
        else:
            logger.info("Top-level layer: not configured (TOP_LEVEL_LAYER is None)")
        logger.info("=" * 80)

        return True

    except Exception as e:
        logger.error(f"[ERROR] Unexpected error occurred: {str(e)}")
        logger.exception("Full traceback:")
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
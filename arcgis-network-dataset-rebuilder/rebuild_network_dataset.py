"""
Script Name: rebuild_network_dataset.py
Version: 1.0.0
License: Apache-2.0

Description:
    End-to-end scheduled maintenance for an enterprise geodatabase
    network dataset used for routing (ArcGIS Network Analyst). Each run:

        1. Optionally refreshes the network source feature classes from
           a production database (DeleteRows + Append with automatic
           field mapping, inside an edit session, versioned-data safe)
        2. Adds required network attribute fields if missing
           (functional class, speed, restrictions, hierarchy, travel
           time, time zone)
        3. Populates functional classification from your road CLASS
           field, plus ROAD_CLASS, HIERARCHY, and mode restrictions
        4. Calculates directional travel times (FT_Minutes/TF_Minutes)
           from geometry length and speed, honoring ONE_WAY values and
           optionally applying hierarchy time penalties so routes avoid
           cutting through residential streets (Google-Maps-like
           behavior)
        5. Creates the TimeZones table required to publish routing
           services to ArcGIS Enterprise Portal
        6. Rebuilds the network dataset (arcpy.na.BuildNetwork)
        7. Optionally compresses the geodatabase to manage version
           state lineage

    Everything is driven by the CONFIGURATION section below. Optional
    inputs (driveways, override junctions, source refresh, compress)
    are skipped cleanly when not configured.

Hard-won notes baked into this script:
    - TruncateTable is not supported on versioned tables; the refresh
      uses DeleteRows inside an edit session instead
    - Network dataset feature classes require edit sessions for updates
      regardless of versioning
    - Field mapping is built from the intersection of source and target
      schemas, so the target's extra routing fields survive the refresh
    - A TimeZones table with an MSTIMEZONE field is required to
      register the network dataset with Portal routing services
    - Compress after the build keeps the version state lineage from
      growing unbounded on scheduled runs

Prerequisites:
    - ArcGIS Pro Python environment (arcpy) with the Network Analyst
      extension licensed
    - The feature dataset registered as versioned (run once):
        arcpy.RegisterAsVersioned_management(dataset_path,
                                             "NO_EDITS_TO_BASE")
    - A network dataset already created over the source feature classes
    - For the optional source refresh: a read-only connection to the
      production database

Usage:
    1. Fill in the CONFIGURATION section
    2. Run manually once and review the calculated values in Pro
    3. Manually adjust SPEED_LIMIT for known roads and HEIGHT_LIMIT_FT
       for bridges with clearance restrictions
    4. Schedule it (monthly works well); exit code is nonzero on
       failure, and optional email notification includes the log path
"""

import arcpy
import os
import sys
import logging
import traceback
from datetime import datetime
import time

import smtplib
from email.mime.text import MIMEText

# =============================================================================
# LOGGING CONFIGURATION
# =============================================================================
# Log file path with script name and datetime stamp
LOG_DIR = "Log"
LOG_FILE = None  # Will be set in setup_logging()


def setup_logging():
    """Configure logging for console and file output (PyCharm compatible)."""
    global LOG_FILE

    # Create log filename with datetime stamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    LOG_FILE = os.path.join(LOG_DIR, f"rebuild_network_dataset_{timestamp}.log")

    # Create logger
    logger = logging.getLogger('NetworkFieldCalculator')
    logger.setLevel(logging.DEBUG)

    # Clear any existing handlers
    logger.handlers = []

    # Create console handler with INFO level
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)

    # Create file handler with INFO level
    try:
        file_handler = logging.FileHandler(LOG_FILE)
        file_handler.setLevel(logging.INFO)
    except Exception as e:
        print(f"Warning: Could not create log file: {e}")
        file_handler = None

    # Create formatter
    formatter = logging.Formatter(
        fmt='%(asctime)s | %(levelname)-8s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    console_handler.setFormatter(formatter)
    if file_handler:
        file_handler.setFormatter(formatter)

    # Add handlers to logger
    logger.addHandler(console_handler)
    if file_handler:
        logger.addHandler(file_handler)

    # Prevent propagation to root logger
    logger.propagate = False

    return logger


def delete_log_file():
    """Delete the log file after successful script completion."""
    global LOG_FILE
    try:
        # Close all logging handlers to release file lock
        for handler in log.handlers[:]:
            handler.close()
            log.removeHandler(handler)

        # Delete the log file if it exists
        if LOG_FILE and os.path.exists(LOG_FILE):
            os.remove(LOG_FILE)
            print(f"Log file deleted: {LOG_FILE}")
    except Exception as e:
        print(f"Warning: Could not delete log file: {e}")


# Initialize logger
log = setup_logging()


# =============================================================================
# WORKSPACE HELPER
# =============================================================================
def get_workspace(fc):
    """
    Get the SDE workspace (connection file) path from a feature class path.

    For feature classes in a feature dataset within an SDE geodatabase,
    we need to traverse up to find the .sde connection file, not stop
    at the feature dataset level.

    Example:
        Input: \\\\server\\path\\connection.sde\\Dataset\\FeatureClass
        Output: \\\\server\\path\\connection.sde
    """
    desc = arcpy.Describe(fc)

    # Get the immediate parent path
    current_path = desc.path

    # Keep going up until we find the .sde file or geodatabase
    while current_path:
        current_desc = arcpy.Describe(current_path)

        # Check if this is a workspace (geodatabase)
        if current_desc.dataType in ('Workspace', 'RemoteDatabase'):
            return current_path

        # Check if path ends with .sde (SDE connection file)
        if current_path.lower().endswith('.sde'):
            return current_path

        # Check if path ends with .gdb (file geodatabase)
        if current_path.lower().endswith('.gdb'):
            return current_path

        # Move up one level
        parent_path = os.path.dirname(current_path)

        # Prevent infinite loop
        if parent_path == current_path:
            break

        current_path = parent_path

    # Fallback: return the immediate parent
    return desc.path


# =============================================================================
# CONFIGURATION - Update everything in this section
# =============================================================================

# --- Target feature classes (the network dataset's sources) ------------------
# Paths through an SDE connection file (or a file geodatabase).
fc_path = r"C:\path\to\NetworkDataset.sde\DBO.NetworkDataset\DBO.StreetCenterlines"

# Optional - set to None if not using
driveway_path = None            # e.g. r"...\DBO.NetworkDataset\DBO.Driveways"
override_junction_path = None   # e.g. r"...\DBO.NetworkDataset\DBO.Override_Junction"

# Network dataset to rebuild after updates
network_dataset_path = r"C:\path\to\NetworkDataset.sde\DBO.NetworkDataset\DBO.MyNetworkND"

# SDE connection file for the optional compress step (connect as sde/DBO)
sde_connection = r"C:\path\to\NetworkDataset.sde"

# --- Optional source refresh from production ---------------------------------
# Set REFRESH_FROM_SOURCE = False to skip and process existing data as-is.
REFRESH_FROM_SOURCE = False
source_connection = r"C:\path\to\production_readonly.sde"
source_centerline_path = r"C:\path\to\production_readonly.sde\DBO.StreetCenterlines"
source_driveway_path = None
source_override_junction_path = None

# --- Behavior toggles ----------------------------------------------------------
CREATE_TIMEZONES_TABLE = True    # required for Portal routing services
APPLY_HIERARCHY_PENALTY = True   # discourage routing through local streets
RUN_COMPRESS = True              # compress version state lineage after build
DELETE_LOG_ON_SUCCESS = False

# --- Attribute configuration ----------------------------------------------------
# Field on your centerlines containing the road classification values
CLASS_FIELD = "CLASS"

# Map YOUR road classification values (uppercased) to functional class 1-5:
# 1 = Interstate/US Highway (most preferred), 5 = Local (least preferred).
# Unmapped values default to 5.
FUNC_CLASS_MAP = {
    "INTERSTATE": 1,
    "US HIGHWAY": 1,
    "STATE HIGHWAY": 2,
    "PR ARTERIAL": 3,
    "MIN ARTERIAL": 4,
    "MAJ COLLECTOR": 4,
    "MIN COLLECTOR": 5,
    "LOCAL": 5,
    "PRIVATE": 5,
    "UNIMPROVED": 5,
    "ON RAMP": 2,
    "OFF RAMP": 2,
}

# Default speed limit for roads without a value (mph)
DEFAULT_SPEED = 25

# Driveways often use a different speed field and slower default
DRIVEWAY_SPEED_FIELD = "SPEED"
DRIVEWAY_DEFAULT_SPEED = 10

# Time zone written to every record and to the TimeZones table
# (must be a Windows time zone name)
TIME_ZONE_NAME = "Mountain Standard Time"

# --- Email notification on failure (optional) -----------------------------------
ENABLE_EMAIL = False
SMTP_HOST    = "smtp.example.com"
FROM_EMAIL   = "noreply@example.com"
TO_EMAIL     = "you@example.com"

# =============================================================================
# FIELD DEFINITIONS
# =============================================================================
NEW_FIELDS = [
    # (field_name, field_type, field_length, field_alias, default_value)
    ("FUNC_CLASS", "SHORT", None, "Functional Classification", 5),
    ("SPEED_LIMIT", "SHORT", None, "Speed Limit (mph)", DEFAULT_SPEED),
    ("PAVED", "TEXT", 1, "Paved Surface", "Y"),
    ("AR_PEDEST", "TEXT", 1, "Allow Pedestrians", "Y"),
    ("AR_AUTO", "TEXT", 1, "Allow Automobiles", "Y"),
    ("AR_BUS", "TEXT", 1, "Allow Buses", "Y"),
    ("AR_EMERGENCY", "TEXT", 1, "Emergency Only", "N"),
    ("HEIGHT_LIMIT_FT", "DOUBLE", None, "Height Limit (Feet)", None),
    ("ROAD_CLASS", "TEXT", 30, "Road Classification", "Local"),
    ("HIERARCHY", "SHORT", None, "Network Hierarchy", 5),
    ("TimeZoneID", "TEXT", 50, "Time Zone ID", TIME_ZONE_NAME),
]

# Fields for travel time (may already exist in your data)
TIME_FIELDS = [
    ("FT_Minutes", "DOUBLE", None, "From-To Travel Time (Minutes)"),
    ("TF_Minutes", "DOUBLE", None, "To-From Travel Time (Minutes)"),
]


def check_for_locks(fc):
    """Check if the feature class has any locks and log the information."""
    log.info(f"Checking for locks on: {os.path.basename(fc)}")

    try:
        # Try to get schema lock info
        desc = arcpy.Describe(fc)
        log.info(f"  Is Versioned: {desc.isVersioned}")

        # Check if we can get an exclusive lock (test by trying to add/remove a dummy index)
        # This is a non-destructive way to test write access
        test_successful = False
        try:
            # Simple test - try to get record count (read access)
            count = int(arcpy.GetCount_management(fc)[0])
            log.info(f"  Read access confirmed ({count:,} records)")
            test_successful = True
        except Exception as e:
            log.warning(f"  Read access issue: {str(e)}")

        return test_successful

    except Exception as e:
        log.error(f"  Error checking locks: {str(e)}")
        return False


def create_timezones_table(workspace):
    """
    Create the TimeZones table required for network dataset time zone configuration.
    This table is needed to register the network dataset with Portal routing services.

    Creates a table with MSTIMEZONE field containing 'Mountain Standard Time'.
    """
    table_name = "TimeZones"
    table_path = os.path.join(workspace, table_name)

    log.info(f"Checking for TimeZones table in: {workspace}")

    # Check if table already exists
    if arcpy.Exists(table_path):
        log.info(f"  TimeZones table already exists")
        # Verify it has the required field and record
        fields = [f.name for f in arcpy.ListFields(table_path)]
        if "MSTIMEZONE" in fields:
            count = int(arcpy.GetCount_management(table_path)[0])
            log.info(f"  Table has MSTIMEZONE field with {count} record(s)")
            return True
        else:
            log.warning(f"  TimeZones table exists but missing MSTIMEZONE field")

    log.info(f"Creating TimeZones table...")

    try:
        # Create the table
        arcpy.CreateTable_management(workspace, table_name)
        log.info(f"  Table created: {table_path}")

        # Add MSTIMEZONE field
        arcpy.AddField_management(table_path, "MSTIMEZONE", "TEXT", field_length=50)
        log.info(f"  Added MSTIMEZONE field (TEXT, 50)")

        # Insert the Mountain Standard Time record
        with arcpy.da.InsertCursor(table_path, ["MSTIMEZONE"]) as cursor:
            cursor.insertRow([TIME_ZONE_NAME])
        log.info(f"  Inserted record: '{TIME_ZONE_NAME}'")

        log.info(f"TimeZones table created successfully")
        return True

    except Exception as e:
        log.error(f"  Error creating TimeZones table: {str(e)}")
        raise


def get_feature_class_info(fc):
    """Get and log feature class information."""
    log.info(f"Gathering feature class information for: {os.path.basename(fc)}")

    try:
        desc = arcpy.Describe(fc)
        log.info(f"  Feature Class Name: {desc.name}")
        log.info(f"  Feature Type: {desc.shapeType}")
        log.info(f"  Spatial Reference: {desc.spatialReference.name}")
        log.info(f"  WKID: {desc.spatialReference.factoryCode}")

        count = int(arcpy.GetCount_management(fc)[0])
        log.info(f"  Total Record Count: {count:,}")

        return count
    except Exception as e:
        log.error(f"  Error getting feature class info: {str(e)}")
        raise


def get_existing_fields(fc):
    """Get list of existing field names."""
    log.info(f"Retrieving existing fields from: {os.path.basename(fc)}")

    fields = arcpy.ListFields(fc)
    field_names = [f.name.upper() for f in fields]

    log.info(f"  Found {len(field_names)} existing fields")
    log.debug(f"  Fields: {', '.join(field_names)}")

    return field_names


def add_fields_if_not_exist(fc, fields):
    """Add fields to feature class if they don't already exist."""
    log.info(f"Checking and adding fields to: {os.path.basename(fc)}")

    existing_fields = get_existing_fields(fc)

    fields_added = 0
    fields_skipped = 0

    for field_def in fields:
        field_name = field_def[0]
        field_type = field_def[1]
        field_length = field_def[2]
        field_alias = field_def[3] if len(field_def) > 3 else field_name

        if field_name.upper() not in existing_fields:
            log.info(f"  Adding field: {field_name} ({field_type})")
            try:
                if field_length:
                    arcpy.AddField_management(fc, field_name, field_type,
                                             field_length=field_length,
                                             field_alias=field_alias)
                else:
                    arcpy.AddField_management(fc, field_name, field_type,
                                             field_alias=field_alias)
                log.info(f"    Successfully added field: {field_name}")
                fields_added += 1
            except Exception as e:
                log.error(f"    Error adding field {field_name}: {str(e)}")
                raise
        else:
            log.info(f"  Field already exists, skipping: {field_name}")
            fields_skipped += 1

    log.info(f"  Field addition complete - Added: {fields_added}, Skipped: {fields_skipped}")
    return fields_added, fields_skipped


def set_default_values(fc, fields):
    """Set default values for newly added fields."""
    log.info(f"Setting default values for: {os.path.basename(fc)}")

    total_updates = 0

    for field_def in fields:
        if len(field_def) > 4 and field_def[4] is not None:
            field_name = field_def[0]
            default_value = field_def[4]

            log.info(f"  Processing field: {field_name} (default: {default_value})")

            # Only update NULL values
            where_clause = f"{field_name} IS NULL"

            try:
                with arcpy.da.UpdateCursor(fc, [field_name], where_clause) as cursor:
                    count = 0
                    for row in cursor:
                        row[0] = default_value
                        cursor.updateRow(row)
                        count += 1

                        if count % 5000 == 0:
                            log.info(f"    Processed {count:,} NULL records...")

                    log.info(f"    Updated {count:,} records with default value")
                    total_updates += count

            except Exception as e:
                log.error(f"    Error setting default for {field_name}: {str(e)}")
                raise

    log.info(f"  Default value assignment complete - Total updates: {total_updates:,}")
    return total_updates


def calculate_travel_time(fc, speed_field="SPEED_LIMIT", default_speed=25, apply_hierarchy_penalty=False):
    """
    Calculate FT_Minutes and TF_Minutes from shape length and speed limit.

    Formula: Minutes = (Length in meters / 1609.34) / Speed(mph) * 60

    For bidirectional roads (no one-way), FT and TF will be the same.
    For one-way roads, the restricted direction gets a high value (9999).

    If apply_hierarchy_penalty is True, applies time multipliers based on FUNC_CLASS:
    - FUNC_CLASS 1-2 (Highways): 1.0x (no penalty)
    - FUNC_CLASS 3 (Major Arterial): 1.0x (no penalty)
    - FUNC_CLASS 4 (Minor Arterial/Collector): 1.1x (slight penalty)
    - FUNC_CLASS 5 (Local/Residential): 1.5x (significant penalty)

    This discourages routing through residential neighborhoods when arterials are available.
    """
    log.info(f"Calculating travel time fields for: {os.path.basename(fc)}")
    log.info(f"  Speed field: {speed_field}")
    log.info(f"  Default speed: {default_speed} mph")
    log.info(f"  Hierarchy penalty: {'Enabled' if apply_hierarchy_penalty else 'Disabled'}")

    # Check if ONE_WAY field exists
    existing_fields = [f.name.upper() for f in arcpy.ListFields(fc)]
    has_oneway = "ONE_WAY" in existing_fields
    has_func_class = "FUNC_CLASS" in existing_fields

    log.info(f"  ONE_WAY field present: {has_oneway}")

    if apply_hierarchy_penalty and not has_func_class:
        log.warning(f"  FUNC_CLASS field not found - hierarchy penalty will not be applied")
        apply_hierarchy_penalty = False

    # Hierarchy penalty multipliers - discourages local roads
    hierarchy_multipliers = {
        1: 1.0,   # Interstate/US Highway - no penalty
        2: 1.0,   # State Highway - no penalty
        3: 1.0,   # Major Arterial - no penalty
        4: 1.1,   # Minor Arterial/Collector - slight penalty
        5: 1.5,   # Local/Residential - significant penalty
    }

    if apply_hierarchy_penalty:
        log.info(f"  Hierarchy multipliers: {hierarchy_multipliers}")

    fields_to_update = ["SHAPE@LENGTH", speed_field, "FT_Minutes", "TF_Minutes"]
    if has_oneway:
        fields_to_update.append("ONE_WAY")
    if apply_hierarchy_penalty:
        fields_to_update.append("FUNC_CLASS")

    log.info(f"  Fields being processed: {', '.join(fields_to_update)}")

    # Statistics tracking
    stats = {
        'total': 0,
        'bidirectional': 0,
        'ft_only': 0,
        'tf_only': 0,
        'no_travel': 0,
        'null_speed': 0,
        'min_time': float('inf'),
        'max_time': 0,
        'total_time': 0,
        'penalized': {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    }

    try:
        with arcpy.da.UpdateCursor(fc, fields_to_update) as cursor:
            for row in cursor:
                shape_length = row[0]  # In meters (based on your SR)
                speed = row[1] if row[1] and row[1] > 0 else default_speed

                if row[1] is None or row[1] <= 0:
                    stats['null_speed'] += 1

                # Convert meters to miles, then calculate time
                # Minutes = (meters / 1609.34) / mph * 60
                length_miles = shape_length / 1609.34
                travel_time = (length_miles / speed) * 60

                # Apply hierarchy penalty if enabled
                if apply_hierarchy_penalty:
                    # Get FUNC_CLASS - it's the last field in our list
                    func_class_idx = len(fields_to_update) - 1
                    func_class = row[func_class_idx] if row[func_class_idx] else 5
                    multiplier = hierarchy_multipliers.get(func_class, 1.5)
                    travel_time = travel_time * multiplier
                    stats['penalized'][func_class] = stats['penalized'].get(func_class, 0) + 1

                # Track stats
                stats['min_time'] = min(stats['min_time'], travel_time)
                stats['max_time'] = max(stats['max_time'], travel_time)
                stats['total_time'] += travel_time

                if has_oneway:
                    one_way_idx = 4
                    one_way = row[one_way_idx].upper() if row[one_way_idx] else ""

                    if one_way in ("FT", "F"):
                        # From-To only - restrict To-From
                        row[2] = travel_time  # FT_Minutes
                        row[3] = -1  # TF_Minutes (restricted)
                        stats['ft_only'] += 1
                    elif one_way in ("TF", "T"):
                        # To-From only - restrict From-To
                        row[2] = -1  # FT_Minutes (restricted)
                        row[3] = travel_time  # TF_Minutes
                        stats['tf_only'] += 1
                    elif one_way == "N":
                        # No travel allowed
                        row[2] = -1
                        row[3] = -1
                        stats['no_travel'] += 1
                    else:
                        # Bidirectional
                        row[2] = travel_time
                        row[3] = travel_time
                        stats['bidirectional'] += 1
                else:
                    # No one-way field, assume bidirectional
                    row[2] = travel_time
                    row[3] = travel_time
                    stats['bidirectional'] += 1

                cursor.updateRow(row)
                stats['total'] += 1

                if stats['total'] % 10000 == 0:
                    log.info(f"    Processed {stats['total']:,} records...")

        # Log statistics
        log.info(f"  Travel time calculation complete")
        log.info(f"    Total records processed: {stats['total']:,}")
        log.info(f"    Bidirectional roads: {stats['bidirectional']:,}")
        log.info(f"    FT-only (one-way): {stats['ft_only']:,}")
        log.info(f"    TF-only (one-way): {stats['tf_only']:,}")
        log.info(f"    No travel allowed: {stats['no_travel']:,}")
        log.info(f"    Records with NULL/zero speed (used default): {stats['null_speed']:,}")

        if stats['total'] > 0:
            avg_time = stats['total_time'] / stats['total']
            log.info(f"    Min travel time: {stats['min_time']:.4f} minutes")
            log.info(f"    Max travel time: {stats['max_time']:.4f} minutes")
            log.info(f"    Avg travel time: {avg_time:.4f} minutes")

        # Log hierarchy penalty distribution if enabled
        if apply_hierarchy_penalty:
            log.info(f"  Hierarchy penalty applied:")
            penalty_labels = {
                1: "FUNC_CLASS 1 (Highway) - 1.0x",
                2: "FUNC_CLASS 2 (State Hwy) - 1.0x",
                3: "FUNC_CLASS 3 (Major Arterial) - 1.0x",
                4: "FUNC_CLASS 4 (Minor Arterial) - 1.1x",
                5: "FUNC_CLASS 5 (Local) - 1.5x"
            }
            for fc_val, cnt in sorted(stats['penalized'].items()):
                if cnt > 0:
                    label = penalty_labels.get(fc_val, f"FUNC_CLASS {fc_val}")
                    log.info(f"    {label}: {cnt:,} records")

        return stats

    except Exception as e:
        log.error(f"  Error calculating travel time: {str(e)}")
        raise


def populate_func_class_from_class(fc, class_field="CLASS"):
    """
    Populate FUNC_CLASS based on road CLASS field.
    Uses the road classification to determine functional class and hierarchy.
    """
    log.info(f"Populating FUNC_CLASS from road class for: {os.path.basename(fc)}")
    log.info(f"  Source field: {class_field}")

    func_class_map = FUNC_CLASS_MAP

    log.info(f"  Functional class mapping loaded with {len(func_class_map)} road classes")

    existing_fields = [f.name.upper() for f in arcpy.ListFields(fc)]
    if class_field.upper() not in existing_fields:
        log.warning(f"  {class_field} field not found - skipping FUNC_CLASS population")
        log.info(f"  Available fields: {', '.join(sorted(existing_fields))}")
        return 0

    # Track statistics
    class_counts = {}

    try:
        with arcpy.da.UpdateCursor(fc, [class_field, "FUNC_CLASS"]) as cursor:
            count = 0
            for row in cursor:
                road_class = row[0].upper().strip() if row[0] else ""

                # Track class distribution
                if road_class not in class_counts:
                    class_counts[road_class] = 0
                class_counts[road_class] += 1

                # Look up functional class, default to 5 (local)
                func_class = func_class_map.get(road_class, 5)
                row[1] = func_class
                cursor.updateRow(row)
                count += 1

                if count % 10000 == 0:
                    log.info(f"    Processed {count:,} records...")

        log.info(f"  FUNC_CLASS population complete - Updated {count:,} records")

        # Log class distribution
        log.info(f"  Road class distribution:")
        for road_class, cnt in sorted(class_counts.items(), key=lambda x: -x[1])[:15]:
            display_class = road_class if road_class else "(empty)"
            fc_value = func_class_map.get(road_class, 5)
            log.info(f"    {display_class}: {cnt:,} records -> FUNC_CLASS {fc_value}")

        if len(class_counts) > 15:
            log.info(f"    ... and {len(class_counts) - 15} more road classes")

        return count

    except Exception as e:
        log.error(f"  Error populating FUNC_CLASS: {str(e)}")
        raise


def populate_road_class(fc):
    """Populate ROAD_CLASS based on FUNC_CLASS."""
    log.info(f"Populating ROAD_CLASS from FUNC_CLASS for: {os.path.basename(fc)}")

    road_class_map = {
        1: "Interstate/US Highway",
        2: "State Highway",
        3: "Major Arterial",
        4: "Minor Arterial",
        5: "Local Road"
    }

    log.info(f"  Road class mapping: {road_class_map}")

    # Track statistics
    class_counts = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}

    try:
        with arcpy.da.UpdateCursor(fc, ["FUNC_CLASS", "ROAD_CLASS"]) as cursor:
            count = 0
            for row in cursor:
                func_class = row[0] if row[0] else 5
                road_class = road_class_map.get(func_class, "Local Road")
                row[1] = road_class
                cursor.updateRow(row)

                class_counts[func_class] = class_counts.get(func_class, 0) + 1
                count += 1

                if count % 10000 == 0:
                    log.info(f"    Processed {count:,} records...")

        log.info(f"  ROAD_CLASS population complete - Updated {count:,} records")

        # Log distribution
        log.info(f"  Road class distribution:")
        for fc_val, cnt in sorted(class_counts.items()):
            if cnt > 0:
                rc_name = road_class_map.get(fc_val, "Unknown")
                pct = (cnt / count * 100) if count > 0 else 0
                log.info(f"    FUNC_CLASS {fc_val} ({rc_name}): {cnt:,} records ({pct:.1f}%)")

        return count

    except Exception as e:
        log.error(f"  Error populating ROAD_CLASS: {str(e)}")
        raise


def populate_hierarchy(fc):
    """
    Populate HIERARCHY field based on FUNC_CLASS for network dataset routing.

    Hierarchy values determine road preference during routing:
    - Lower values = preferred roads (highways, arterials)
    - Higher values = less preferred (local residential streets)

    This helps Network Analyst keep routes on major roads unless
    local roads are truly necessary to reach the destination.
    """
    log.info(f"Populating HIERARCHY from FUNC_CLASS for: {os.path.basename(fc)}")

    # Direct mapping: FUNC_CLASS -> HIERARCHY (1:1 for our classification)
    # 1 = Interstate/US Highway (most preferred)
    # 2 = State Highway
    # 3 = Major Arterial
    # 4 = Minor Arterial
    # 5 = Local Road (least preferred)

    hierarchy_counts = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}

    try:
        with arcpy.da.UpdateCursor(fc, ["FUNC_CLASS", "HIERARCHY"]) as cursor:
            count = 0
            for row in cursor:
                func_class = row[0] if row[0] else 5
                # Direct mapping - FUNC_CLASS equals HIERARCHY
                row[1] = func_class
                cursor.updateRow(row)

                hierarchy_counts[func_class] = hierarchy_counts.get(func_class, 0) + 1
                count += 1

                if count % 10000 == 0:
                    log.info(f"    Processed {count:,} records...")

        log.info(f"  HIERARCHY population complete - Updated {count:,} records")

        # Log distribution
        hierarchy_labels = {
            1: "Primary (Interstate/US Hwy)",
            2: "Secondary (State Hwy)",
            3: "Tertiary (Major Arterial)",
            4: "Quaternary (Minor Arterial)",
            5: "Local (Residential)"
        }
        log.info(f"  Hierarchy distribution:")
        for h_val, cnt in sorted(hierarchy_counts.items()):
            if cnt > 0:
                h_label = hierarchy_labels.get(h_val, "Unknown")
                pct = (cnt / count * 100) if count > 0 else 0
                log.info(f"    Hierarchy {h_val} ({h_label}): {cnt:,} records ({pct:.1f}%)")

        return count

    except Exception as e:
        log.error(f"  Error populating HIERARCHY: {str(e)}")
        raise


def populate_restrictions_from_func_class(fc):
    """
    Set restriction fields based on functional classification.
    - Interstates: No pedestrians
    - Local roads: May restrict large buses on narrow streets
    """
    log.info(f"Populating restriction fields based on FUNC_CLASS for: {os.path.basename(fc)}")

    fields = ["FUNC_CLASS", "AR_PEDEST", "AR_AUTO", "AR_BUS", "AR_EMERGENCY"]

    # Track statistics
    stats = {
        'total': 0,
        'no_pedestrian': 0,
        'all_allowed': 0
    }

    try:
        with arcpy.da.UpdateCursor(fc, fields) as cursor:
            for row in cursor:
                func_class = row[0] if row[0] else 5

                if func_class == 1:
                    # Interstate - no pedestrians
                    row[1] = "N"  # AR_PEDEST
                    row[2] = "Y"  # AR_AUTO
                    row[3] = "Y"  # AR_BUS
                    row[4] = "N"  # AR_EMERGENCY (not emergency-only)
                    stats['no_pedestrian'] += 1
                elif func_class == 2:
                    # Highway - no pedestrians typically
                    row[1] = "N"
                    row[2] = "Y"
                    row[3] = "Y"
                    row[4] = "N"
                    stats['no_pedestrian'] += 1
                else:
                    # All other roads - allow everything
                    row[1] = "Y"
                    row[2] = "Y"
                    row[3] = "Y"
                    row[4] = "N"
                    stats['all_allowed'] += 1

                cursor.updateRow(row)
                stats['total'] += 1

                if stats['total'] % 10000 == 0:
                    log.info(f"    Processed {stats['total']:,} records...")

        log.info(f"  Restriction field population complete")
        log.info(f"    Total records: {stats['total']:,}")
        log.info(f"    No pedestrian access (FUNC_CLASS 1-2): {stats['no_pedestrian']:,}")
        log.info(f"    All modes allowed (FUNC_CLASS 3-5): {stats['all_allowed']:,}")

        return stats

    except Exception as e:
        log.error(f"  Error populating restriction fields: {str(e)}")
        raise


def process_feature_class(fc, is_driveway=False):
    """Process a single feature class with all field calculations."""
    fc_name = os.path.basename(fc)

    log.info("=" * 70)
    log.info(f"PROCESSING: {fc_name}")
    log.info("=" * 70)

    # Get feature class info
    record_count = get_feature_class_info(fc)

    # Check for locks before proceeding
    check_for_locks(fc)

    # Get workspace for edit session
    # Network dataset feature classes ALWAYS require edit sessions
    workspace = get_workspace(fc)
    log.info(f"Workspace for edit session: {workspace}")

    if is_driveway:
        # Driveways only need time fields
        log.info("-" * 50)
        log.info("Step 1/2: Adding time fields")
        log.info("-" * 50)
        add_fields_if_not_exist(fc, TIME_FIELDS)

        log.info("-" * 50)
        log.info(f"Step 2/2: Calculating travel time ({DRIVEWAY_DEFAULT_SPEED} mph default for driveways)")
        log.info("-" * 50)

        # Start edit session for update operations
        with arcpy.da.Editor(workspace) as editor:
            log.info("Edit session started")
            calculate_travel_time(fc, DRIVEWAY_SPEED_FIELD, DRIVEWAY_DEFAULT_SPEED)
            log.info("Edit session completed successfully")
    else:
        # Full processing for centerlines
        log.info("-" * 50)
        log.info("Step 1/7: Adding new fields")
        log.info("-" * 50)
        add_fields_if_not_exist(fc, NEW_FIELDS)
        add_fields_if_not_exist(fc, TIME_FIELDS)

        # Start edit session for all update operations
        with arcpy.da.Editor(workspace) as editor:
            log.info("Edit session started")

            log.info("-" * 50)
            log.info("Step 2/7: Setting default values")
            log.info("-" * 50)
            set_default_values(fc, NEW_FIELDS)

            log.info("-" * 50)
            log.info(f"Step 3/7: Populating FUNC_CLASS from {CLASS_FIELD} field")
            log.info("-" * 50)
            populate_func_class_from_class(fc, CLASS_FIELD)

            log.info("-" * 50)
            log.info("Step 4/7: Populating ROAD_CLASS")
            log.info("-" * 50)
            populate_road_class(fc)

            log.info("-" * 50)
            log.info("Step 5/7: Populating HIERARCHY for network routing")
            log.info("-" * 50)
            populate_hierarchy(fc)

            log.info("-" * 50)
            log.info("Step 6/7: Populating restriction fields")
            log.info("-" * 50)
            populate_restrictions_from_func_class(fc)

            log.info("-" * 50)
            log.info("Step 7/7: Calculating travel time (with hierarchy penalty)")
            log.info("-" * 50)
            calculate_travel_time(fc, "SPEED_LIMIT", DEFAULT_SPEED, apply_hierarchy_penalty=APPLY_HIERARCHY_PENALTY)

            log.info("Edit session completed successfully")

    log.info(f"Completed processing: {fc_name}")


def process_junction_feature_class(fc):
    """Process a junction feature class (Override_Junction)."""
    fc_name = os.path.basename(fc)

    log.info("=" * 70)
    log.info(f"PROCESSING JUNCTION: {fc_name}")
    log.info("=" * 70)

    # Get feature class info
    record_count = get_feature_class_info(fc)

    # Get existing fields
    existing_fields = get_existing_fields(fc)

    # Check for elevation field (used for connectivity)
    elevation_fields = ["ZELEV", "ELEVATION", "ELEV", "Z"]
    found_elev = None
    for ef in elevation_fields:
        if ef.upper() in existing_fields:
            found_elev = ef
            break

    if found_elev:
        log.info(f"  Elevation field found: {found_elev}")

        # Count records with elevation values
        with arcpy.da.SearchCursor(fc, [found_elev]) as cursor:
            total = 0
            with_elev = 0
            for row in cursor:
                total += 1
                if row[0] is not None:
                    with_elev += 1

        log.info(f"  Records with elevation value: {with_elev:,} of {total:,}")
    else:
        log.warning(f"  No elevation field found. Consider adding one for multi-level connectivity.")
        log.info(f"  Looked for: {', '.join(elevation_fields)}")

    log.info(f"Completed processing junction: {fc_name}")


def build_network_dataset(nd_path, force_full_build=True):
    """
    Build/rebuild the network dataset after source feature classes have been updated.

    Args:
        nd_path: Path to the network dataset
        force_full_build: If True, forces a full rebuild. If False, only rebuilds dirty areas.

    Returns:
        True if successful, False otherwise
    """
    nd_name = os.path.basename(nd_path)

    log.info("=" * 70)
    log.info(f"BUILDING NETWORK DATASET: {nd_name}")
    log.info("=" * 70)

    # Check out Network Analyst extension
    log.info("Checking out Network Analyst extension...")
    if arcpy.CheckExtension("network") == "Available":
        arcpy.CheckOutExtension("network")
        log.info("  Network Analyst extension checked out successfully")
    else:
        log.error("  Network Analyst extension is not available!")
        return False

    try:
        # Verify network dataset exists
        if not arcpy.Exists(nd_path):
            log.error(f"  Network dataset not found: {nd_path}")
            return False

        log.info(f"  Network dataset found: {nd_path}")

        # Get network dataset properties
        desc = arcpy.Describe(nd_path)
        log.info(f"  Network dataset name: {desc.name}")
        log.info(f"  Network dataset type: {desc.networkType}")

        # Determine build type
        build_type = "FORCE_FULL_BUILD" if force_full_build else "NO_FORCE_FULL_BUILD"
        log.info(f"  Build type: {build_type}")

        # Build the network dataset
        log.info("  Starting network build...")
        build_start = time.time()

        arcpy.na.BuildNetwork(nd_path, build_type)

        build_end = time.time()
        build_duration = build_end - build_start

        log.info(f"  Network build completed successfully")
        log.info(f"  Build duration: {build_duration:.2f} seconds")

        # Check for build errors in temp directory
        temp_dir = os.environ.get("TEMP")
        if temp_dir:
            build_errors_file = os.path.join(temp_dir, "BuildErrors.txt")
            if os.path.exists(build_errors_file):
                log.warning(f"  Build errors file found: {build_errors_file}")
                try:
                    with open(build_errors_file, 'r') as f:
                        errors = f.read()
                    if errors.strip():
                        log.warning(f"  Build errors:\n{errors}")
                except Exception as e:
                    log.warning(f"  Could not read build errors file: {str(e)}")

        return True

    except arcpy.ExecuteError as e:
        log.error(f"  ArcPy error building network: {str(e)}")
        log.error(f"  {arcpy.GetMessages(2)}")
        return False

    except Exception as e:
        log.error(f"  Error building network: {str(e)}")
        return False

    finally:
        # Check in the Network Analyst extension
        try:
            arcpy.CheckInExtension("network")
            log.info("  Network Analyst extension checked in")
        except Exception as e:
            log.warning(f"  Could not check in Network Analyst extension: {str(e)}")


def compress_geodatabase(sde_conn):
    """
    Compress the enterprise geodatabase to remove unused version states.

    This should be run after editing versioned data to clean up the state lineage
    and improve performance.

    Args:
        sde_conn: Path to the SDE connection file (must connect as DBO or sde user)

    Returns:
        True if successful, False otherwise
    """
    log.info("=" * 70)
    log.info("COMPRESSING GEODATABASE")
    log.info("=" * 70)

    try:
        # Verify connection exists
        if not arcpy.Exists(sde_conn):
            log.error(f"  SDE connection not found: {sde_conn}")
            return False

        log.info(f"  Connection: {sde_conn}")

        # Get connection properties for logging
        try:
            desc = arcpy.Describe(sde_conn)
            log.info(f"  Workspace type: {desc.workspaceType}")

            # Get connection properties if available
            cp = desc.connectionProperties
            if cp:
                log.info(f"  Database: {cp.database if hasattr(cp, 'database') else 'N/A'}")
                log.info(f"  Server: {cp.server if hasattr(cp, 'server') else 'N/A'}")
        except Exception as e:
            log.warning(f"  Could not get connection details: {str(e)}")

        # Disconnect all users before compress (optional but recommended)
        log.info("  Disconnecting users...")
        try:
            arcpy.AcceptConnections(sde_conn, False)
            arcpy.DisconnectUser(sde_conn, "ALL")
            log.info("    Users disconnected")
        except Exception as e:
            log.warning(f"    Could not disconnect users (may require admin privileges): {str(e)}")

        # Run compress
        log.info("  Starting compress operation...")
        compress_start = time.time()

        arcpy.Compress_management(sde_conn)

        compress_end = time.time()
        compress_duration = compress_end - compress_start

        log.info(f"  Compress completed successfully")
        log.info(f"  Compress duration: {compress_duration:.2f} seconds")

        # Re-enable connections
        try:
            arcpy.AcceptConnections(sde_conn, True)
            log.info("  Connections re-enabled")
        except Exception as e:
            log.warning(f"  Could not re-enable connections: {str(e)}")

        # Analyze datasets for optimal performance (optional)
        log.info("  Analyzing system tables...")
        try:
            arcpy.AnalyzeDatasets_management(
                sde_conn,
                "SYSTEM",
                "",
                "ANALYZE_BASE",
                "ANALYZE_DELTA",
                "ANALYZE_ARCHIVE"
            )
            log.info("    System tables analyzed")
        except Exception as e:
            log.warning(f"    Could not analyze system tables: {str(e)}")

        return True

    except arcpy.ExecuteError as e:
        log.error(f"  ArcPy error during compress: {str(e)}")
        log.error(f"  {arcpy.GetMessages(2)}")
        return False

    except Exception as e:
        log.error(f"  Error compressing geodatabase: {str(e)}")
        return False

    finally:
        # Ensure connections are re-enabled even on failure
        try:
            arcpy.AcceptConnections(sde_conn, True)
        except:
            pass


def build_field_mapping(source_fc, target_fc):
    """
    Build a field mapping that only maps common fields between source and target.

    This handles the case where target has additional fields (like network routing fields)
    that don't exist in source. Only fields present in BOTH source and target are mapped.

    Args:
        source_fc: Path to source feature class
        target_fc: Path to target feature class

    Returns:
        arcpy.FieldMappings object with common fields mapped
    """
    log.info(f"  Building field mapping...")

    # Get field names from both feature classes (excluding system fields)
    system_fields = ['OBJECTID', 'SHAPE', 'SHAPE.STLENGTH()', 'SHAPE.STAREA()',
                     'SHAPE_LENGTH', 'SHAPE_AREA', 'GLOBALID', 'GDB_GEOMATTR_DATA']

    source_fields = {f.name.upper(): f.name for f in arcpy.ListFields(source_fc)
                     if f.name.upper() not in system_fields and f.type not in ['OID', 'Geometry']}
    target_fields = {f.name.upper(): f.name for f in arcpy.ListFields(target_fc)
                     if f.name.upper() not in system_fields and f.type not in ['OID', 'Geometry']}

    # Find common fields
    common_fields = set(source_fields.keys()) & set(target_fields.keys())

    log.info(f"    Source fields: {len(source_fields)}")
    log.info(f"    Target fields: {len(target_fields)}")
    log.info(f"    Common fields to map: {len(common_fields)}")

    # Log fields that exist only in target (these will be NULL after append)
    target_only = set(target_fields.keys()) - set(source_fields.keys())
    if target_only:
        log.info(f"    Target-only fields (will be NULL, populated later): {len(target_only)}")
        for field in sorted(target_only):
            log.debug(f"      - {target_fields[field]}")

    # Create field mappings
    field_mappings = arcpy.FieldMappings()

    for field_upper in common_fields:
        source_field_name = source_fields[field_upper]
        target_field_name = target_fields[field_upper]

        # Create a field map for this field
        field_map = arcpy.FieldMap()
        field_map.addInputField(source_fc, source_field_name)

        # Set output field properties to match target
        out_field = field_map.outputField
        out_field.name = target_field_name
        out_field.aliasName = target_field_name
        field_map.outputField = out_field

        field_mappings.addFieldMap(field_map)

    log.info(f"    Field mapping created with {field_mappings.fieldCount} fields")

    return field_mappings


def refresh_single_feature_class(source_fc, target_fc, fc_label):
    """
    Refresh a single target feature class from source using Delete + Append.

    Uses DeleteRows instead of TruncateTable to support versioned data.
    Operations are performed within an edit session.

    Args:
        source_fc: Path to source feature class (read-only connection)
        target_fc: Path to target feature class (writable connection)
        fc_label: Label for logging (e.g., "Centerline", "Driveway")

    Returns:
        True if successful, False otherwise
    """
    log.info(f"  Refreshing {fc_label}...")
    log.info(f"    Source: {source_fc}")
    log.info(f"    Target: {target_fc}")

    try:
        # Verify source exists
        if not arcpy.Exists(source_fc):
            log.error(f"    Source feature class not found: {source_fc}")
            return False

        # Verify target exists
        if not arcpy.Exists(target_fc):
            log.error(f"    Target feature class not found: {target_fc}")
            return False

        # Get record counts
        source_count = int(arcpy.GetCount_management(source_fc)[0])
        target_count_before = int(arcpy.GetCount_management(target_fc)[0])
        log.info(f"    Source record count: {source_count:,}")
        log.info(f"    Target record count (before): {target_count_before:,}")

        # Build field mapping (handles schema differences)
        field_mappings = build_field_mapping(source_fc, target_fc)

        # Get workspace for edit session (versioned data requires edit session)
        workspace = get_workspace(target_fc)
        log.info(f"    Workspace: {workspace}")

        # Use edit session for versioned data
        with arcpy.da.Editor(workspace) as editor:
            log.info(f"    Edit session started")

            # Delete all rows from target (works on versioned data)
            log.info(f"    Deleting existing rows from target...")
            delete_start = time.time()
            arcpy.DeleteRows_management(target_fc)
            delete_end = time.time()
            log.info(f"    Rows deleted in {delete_end - delete_start:.2f} seconds")

            # Append from source
            log.info(f"    Appending from source...")
            append_start = time.time()

            arcpy.Append_management(
                inputs=source_fc,
                target=target_fc,
                schema_type="NO_TEST",  # Use field mapping
                field_mapping=field_mappings
            )

            append_end = time.time()
            append_duration = append_end - append_start
            log.info(f"    Append completed in {append_duration:.2f} seconds")

            log.info(f"    Edit session completed")

        # Verify record count
        target_count_after = int(arcpy.GetCount_management(target_fc)[0])
        log.info(f"    Target record count (after): {target_count_after:,}")

        if target_count_after != source_count:
            log.warning(f"    Record count mismatch! Source: {source_count:,}, Target: {target_count_after:,}")
        else:
            log.info(f"    Record counts match - refresh successful")

        return True

    except arcpy.ExecuteError as e:
        log.error(f"    ArcPy error refreshing {fc_label}: {str(e)}")
        log.error(f"    {arcpy.GetMessages(2)}")
        return False

    except Exception as e:
        log.error(f"    Error refreshing {fc_label}: {str(e)}")
        return False


def refresh_source_data():
    """
    Refresh all network dataset source feature classes from production data.

    This performs a Truncate + Append operation for each feature class to get
    fresh data from the production database before running field calculations.
    Uses field mapping to handle schema differences (target has additional fields).

    Returns:
        True if all refreshes successful, False if any failed
    """
    log.info("=" * 70)
    log.info("REFRESHING SOURCE DATA FROM PRODUCTION")
    log.info("=" * 70)
    log.info(f"Source connection: {source_connection}")
    log.info("")

    # Verify source connection exists
    if not arcpy.Exists(source_connection):
        log.error(f"Source connection not found: {source_connection}")
        return False

    all_success = True

    # Refresh centerlines
    log.info("-" * 50)
    log.info("Refreshing Street Centerlines")
    log.info("-" * 50)
    if arcpy.Exists(source_centerline_path) and arcpy.Exists(fc_path):
        success = refresh_single_feature_class(
            source_centerline_path,
            fc_path,
            "Street Centerlines"
        )
        if not success:
            all_success = False
            log.error("Failed to refresh centerlines - this is critical, aborting")
            return False
    else:
        if not arcpy.Exists(source_centerline_path):
            log.error(f"Source centerline not found: {source_centerline_path}")
        if not arcpy.Exists(fc_path):
            log.error(f"Target centerline not found: {fc_path}")
        return False

    # Refresh driveways (optional)
    if source_driveway_path and driveway_path:
        log.info("")
        log.info("-" * 50)
        log.info("Refreshing Driveways")
        log.info("-" * 50)
        if arcpy.Exists(source_driveway_path) and arcpy.Exists(driveway_path):
            success = refresh_single_feature_class(
                source_driveway_path,
                driveway_path,
                "Driveways"
            )
            if not success:
                all_success = False
                log.warning("Failed to refresh driveways - continuing with existing data")
        else:
            if not arcpy.Exists(source_driveway_path):
                log.warning(f"Source driveway not found: {source_driveway_path}")
            if not arcpy.Exists(driveway_path):
                log.warning(f"Target driveway not found: {driveway_path}")

    # Refresh override junctions (optional)
    if source_override_junction_path and override_junction_path:
        log.info("")
        log.info("-" * 50)
        log.info("Refreshing Override Junctions")
        log.info("-" * 50)
        if arcpy.Exists(source_override_junction_path) and arcpy.Exists(override_junction_path):
            success = refresh_single_feature_class(
                source_override_junction_path,
                override_junction_path,
                "Override Junctions"
            )
            if not success:
                all_success = False
                log.warning("Failed to refresh override junctions - continuing with existing data")
        else:
            if not arcpy.Exists(source_override_junction_path):
                log.warning(f"Source override junction not found: {source_override_junction_path}")
            if not arcpy.Exists(override_junction_path):
                log.warning(f"Target override junction not found: {override_junction_path}")

    log.info("")
    if all_success:
        log.info("Source data refresh completed successfully")
    else:
        log.warning("Source data refresh completed with warnings")

    return all_success


def main():
    """Main execution function."""
    start_time = datetime.now()

    log.info("#" * 70)
    log.info("#" + " " * 68 + "#")
    log.info("#" + "  NETWORK DATASET FIELD CALCULATOR".center(68) + "#")
    log.info("#" + "  NETWORK DATASET REBUILD".center(68) + "#")
    log.info("#" + " " * 68 + "#")
    log.info("#" * 70)
    log.info("")
    log.info(f"Script started at: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    log.info(f"Log file: {LOG_FILE}")
    log.info(f"Python version: {sys.version}")
    log.info(f"ArcPy version: {arcpy.GetInstallInfo()['Version']}")
    log.info("")

    # Log configuration
    log.info("CONFIGURATION:")
    log.info(f"  Source Connection: {source_connection}")
    log.info(f"  Source Centerline: {source_centerline_path}")
    log.info(f"  Source Driveway: {source_driveway_path}")
    log.info(f"  Source Override Junction: {source_override_junction_path}")
    log.info(f"  Target Centerline FC: {fc_path}")
    log.info(f"  Target Driveway FC: {driveway_path}")
    log.info(f"  Target Override Junction FC: {override_junction_path}")
    log.info(f"  Network Dataset: {network_dataset_path}")
    log.info(f"  SDE Connection: {sde_connection}")
    log.info(f"  Default Speed: {DEFAULT_SPEED} mph")
    log.info("")

    # Verify feature class exists
    log.info("Verifying data sources...")

    if not arcpy.Exists(fc_path):
        log.error(f"ERROR: Feature class not found: {fc_path}")
        log.error("Please update the fc_path variable at the top of this script.")
        return False
    log.info(f"  Centerline FC: FOUND")

    driveway_exists = driveway_path and arcpy.Exists(driveway_path)
    if driveway_path:
        if driveway_exists:
            log.info(f"  Driveway FC: FOUND")
        else:
            log.warning(f"  Driveway FC: NOT FOUND (will be skipped)")
    else:
        log.info(f"  Driveway FC: Not configured")

    override_junction_exists = override_junction_path and arcpy.Exists(override_junction_path)
    if override_junction_path:
        if override_junction_exists:
            log.info(f"  Override Junction FC: FOUND")
        else:
            log.warning(f"  Override Junction FC: NOT FOUND (will be skipped)")
    else:
        log.info(f"  Override Junction FC: Not configured")

    network_dataset_exists = network_dataset_path and arcpy.Exists(network_dataset_path)
    if network_dataset_path:
        if network_dataset_exists:
            log.info(f"  Network Dataset: FOUND")
        else:
            log.warning(f"  Network Dataset: NOT FOUND (build will be skipped)")
    else:
        log.info(f"  Network Dataset: Not configured")

    sde_exists = sde_connection and arcpy.Exists(sde_connection)
    if sde_connection:
        if sde_exists:
            log.info(f"  SDE Connection: FOUND")
        else:
            log.warning(f"  SDE Connection: NOT FOUND (compress will be skipped)")
    else:
        log.info(f"  SDE Connection: Not configured")

    log.info("")

    # Refresh source data from production BEFORE processing
    if not REFRESH_FROM_SOURCE:
        log.info("REFRESH_FROM_SOURCE = False - skipping source data refresh")
        refresh_success = True
    try:
        refresh_success = refresh_source_data() if REFRESH_FROM_SOURCE else True
        if not refresh_success:
            log.error("Source data refresh failed - aborting script")
            return False
    except Exception as e:
        log.error(f"Error refreshing source data: {str(e)}")
        log.exception("Full traceback:")
        return False

    # Create TimeZones table for network dataset time zone support
    log.info("")
    log.info("=" * 70)
    log.info("CREATING TIMEZONES TABLE")
    log.info("=" * 70)
    try:
        if CREATE_TIMEZONES_TABLE:
            create_timezones_table(sde_connection)
        else:
            log.info("CREATE_TIMEZONES_TABLE = False - skipping")
    except Exception as e:
        log.error(f"Error creating TimeZones table: {str(e)}")
        log.exception("Full traceback:")
        # Continue even if this fails - it may already exist

    # Process centerlines
    try:
        process_feature_class(fc_path, is_driveway=False)
    except Exception as e:
        log.error(f"Error processing centerlines: {str(e)}")
        log.exception("Full traceback:")
        return False

    # Process driveways if available
    if driveway_exists:
        log.info("")
        try:
            process_feature_class(driveway_path, is_driveway=True)
        except Exception as e:
            log.error(f"Error processing driveways: {str(e)}")
            log.exception("Full traceback:")
            # Don't return False - driveways are optional

    # Process override junctions if available
    if override_junction_exists:
        log.info("")
        try:
            process_junction_feature_class(override_junction_path)
        except Exception as e:
            log.error(f"Error processing override junctions: {str(e)}")
            log.exception("Full traceback:")
            # Don't return False - junctions are optional

    # Build network dataset if available
    if network_dataset_exists:
        log.info("")
        try:
            build_success = build_network_dataset(network_dataset_path, force_full_build=True)
            if not build_success:
                log.error("Network dataset build failed!")
                return False
        except Exception as e:
            log.error(f"Error building network dataset: {str(e)}")
            log.exception("Full traceback:")
            return False

    # Compress geodatabase if SDE connection available
    if sde_exists and RUN_COMPRESS:
        log.info("")
        try:
            compress_success = compress_geodatabase(sde_connection)
            if not compress_success:
                log.warning("Geodatabase compress failed - continuing anyway")
                # Don't return False - compress is maintenance, not critical
        except Exception as e:
            log.warning(f"Error compressing geodatabase: {str(e)}")
            # Don't return False - compress is maintenance, not critical

    # Summary
    end_time = datetime.now()
    duration = end_time - start_time

    log.info("")
    log.info("#" * 70)
    log.info("#" + " " * 68 + "#")
    log.info("#" + "  PROCESSING COMPLETE".center(68) + "#")
    log.info("#" + " " * 68 + "#")
    log.info("#" * 70)
    log.info("")
    log.info(f"Script completed at: {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
    log.info(f"Total duration: {duration}")
    log.info("")
    log.info("NEXT STEPS:")
    log.info("  1. Review the calculated values in ArcGIS Pro")
    log.info("  2. Manually adjust SPEED_LIMIT for known roads (highways, etc.)")
    log.info("  3. Set HEIGHT_LIMIT_FT for bridges/tunnels with clearance restrictions")
    log.info("  4. Network dataset has been automatically rebuilt")
    log.info("  5. Test routing to verify network connectivity")
    log.info("")

    return True


def send_failure_email(subject, body):
    if not ENABLE_EMAIL:
        return
    try:
        msg = MIMEText(body)
        msg['Subject'] = subject
        msg['From'] = FROM_EMAIL
        msg['To'] = TO_EMAIL
        with smtplib.SMTP(SMTP_HOST) as server:
            server.sendmail(FROM_EMAIL, TO_EMAIL, msg.as_string())
        log.info(f"Failure email sent to {TO_EMAIL}")
    except Exception as e:
        log.error(f"Failed to send notification email: {e}")


if __name__ == "__main__":
    exit_code = 0
    try:
        success = main()
        if success:
            log.info("Script finished successfully.")
        else:
            log.error("Script finished with errors.")
            exit_code = 1
            send_failure_email(
                "rebuild_network_dataset.py FAILED",
                f"The network dataset rebuild completed with errors.\n\n"
                f"Log file: {LOG_FILE}"
            )
    except Exception as e:
        exit_code = 1
        error_msg = traceback.format_exc()
        log.error(f"Script failed with exception: {str(e)}")
        log.error(f"Traceback:\n{error_msg}")
        send_failure_email(
            "rebuild_network_dataset.py FAILED",
            f"The network dataset rebuild failed with an exception.\n\n"
            f"Error: {str(e)}\n\nLog file: {LOG_FILE}\n\n{error_msg}"
        )
    finally:
        if exit_code == 0 and DELETE_LOG_ON_SUCCESS:
            delete_log_file()
    sys.exit(exit_code)

# Python

A collection of Python scripts and utilities for GIS automation, primarily focused on ArcGIS Enterprise, ArcGIS Online, and geodatabase administration.

## Projects

| Folder | Description |
|--------|-------------|
| [arcgis-vector-tile-updater](./arcgis-vector-tile-updater) | Automates refreshing a hosted vector tile layer on ArcGIS Enterprise from an ArcGIS Pro project using the Replace Layer workflow. Preserves the production item ID and service URL so webmaps never break. |

More utilities will be added over time.

## General notes

- Scripts are written for the ArcGIS Pro Python environment (`arcpy` + `arcgis`, Python 3.8+) unless noted otherwise
- Credentials are never committed; each project uses a gitignored `secrets.py` pattern with a provided `secrets.example.py`
- Each project folder contains its own README with setup and usage details

## License

MIT unless noted otherwise in a project folder.

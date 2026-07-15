# Python

A collection of Python scripts and utilities for GIS automation, primarily focused on ArcGIS Enterprise, ArcGIS Online, and geodatabase administration.

## Projects

| Folder | Description |
|--------|-------------|
| [arcgis-vector-tile-updater](./arcgis-vector-tile-updater) | Automates refreshing a hosted vector tile layer on ArcGIS Enterprise from an ArcGIS Pro project using the Replace Layer workflow. Preserves the production item ID and service URL so webmaps never break. |
| [arcgis-locator-rebuilder](./arcgis-locator-rebuilder) | Scheduled rebuilds of composite geocoding locators via the ArcGIS Server Admin REST API, with safe service stop/rebuild/restart orchestration and guaranteed service restart on failure. |
| [arcgis-network-dataset-rebuilder](./arcgis-network-dataset-rebuilder) | End-to-end network dataset maintenance for routing: refresh sources from production, populate network attributes and hierarchy-penalized travel times, rebuild the network, and compress the geodatabase. |
| [arcgis-replica-auditor](./arcgis-replica-auditor) | Replica health system: audits feature service sync replicas across multiple enterprise geodatabases, unregisters replicas older than a rolling age threshold, and ships with a single-file HTML dashboard visualizing owners, sync status, and source databases. |
| [arcgis-webmap-popups](./arcgis-webmap-popups) | Applies one consistent, accessible Arcade popup to every feature layer in a MapServer group layer, driven from a single reusable expression file. Auto-formats dates, domains, currency, phones, emails, and links with WCAG-conscious HTML. |

More utilities will be added over time.

## General notes

- Scripts are written for the ArcGIS Pro Python environment (`arcpy` + `arcgis`, Python 3.8+) unless noted otherwise
- Credentials are never committed; projects that need them use a gitignored `secrets.py` pattern with a provided `secrets.example.py`
- Each project folder contains its own README with setup and usage details

## License

Apache License 2.0 unless noted otherwise in a project folder.

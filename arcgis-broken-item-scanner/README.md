# ArcGIS Broken Item Scanner

Finds ArcGIS Enterprise Portal and ArcGIS Online items that point at services which no
longer respond, then emails each item owner a single summary of everything they own that
needs attention.

Most organizations accumulate web maps and apps whose layers have been renamed, moved, or
deleted. Nobody notices until someone opens the map and half of it is missing. This scans
the whole organization on a schedule and puts the list in front of the person who can
actually fix it.

## What it checks

- Every item's own service URL.
- For every Web Map, each operational layer URL and each basemap layer URL.

A reference counts as broken when the request fails outright (host unreachable, timeout,
SSL failure), returns HTTP 400 or higher, or returns an ArcGIS REST error block.

Token and permission problems are reported separately from missing services. A 498 or 499
reads as an authentication failure and a 403 reads as access denied, so a token problem
never lands in someone's inbox looking like a deleted layer.

## Why two scripts

`scan_portal.py` signs in once. `scan_agol.py` can sign in twice, because ArcGIS Online
organizations usually hold items that reference federated services on a Portal. Each URL
is probed with the token that matches its host. Without that, every secured reference on
your own servers would come back as an authentication failure.

## Requirements

- Python 3.8 or later
- `arcgis` (ArcGIS API for Python)
- `requests`

The ArcGIS Pro conda environment already has both. Otherwise:

```
pip install -r requirements.txt
```

Accounts used for the scan need to see everything you want checked. An administrator
account or a role with view access across the organization gives the most complete
picture. A viewer account will silently skip content it cannot see.

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

2. Fill in `config.py`. At minimum: the URLs, the account names and passwords, the SMTP
   settings, `FROM_EMAIL`, `ADMIN_EMAIL`, and `ORG_USERNAME_SUFFIXES`.

3. Leave `DRY_RUN = True` for the first run. The scan runs, the log and CSV are written,
   and nothing is emailed.

4. Run it:

   ```
   python scan_portal.py
   python scan_agol.py
   ```

5. Read the owner-to-address table in the log. Anyone falling back to `ADMIN_EMAIL` has a
   username that could not be resolved and no email on their profile. Add them to
   `OWNER_EMAIL_OVERRIDES` or fix the profile.

6. Optional middle step: set `TEST_REDIRECT` to your own address and `DRY_RUN = False`.
   Real messages send, but all of them go to you, with `[TEST, intended for ...]` in the
   subject.

7. When the mapping looks right, clear `TEST_REDIRECT` and leave `DRY_RUN = False`.

## Owner resolution

Owner usernames are not always email addresses, so each one is resolved in this order:

1. Strip any suffix listed in `ORG_USERNAME_SUFFIXES`. ArcGIS Online appends the
   organization short name, turning `jane.doe@example.org` into
   `jane.doe@example.org_YourOrgName`.
2. Check `OWNER_EMAIL_OVERRIDES`, which is where service accounts belong.
3. Use the stripped username if it is already a valid address.
4. Read the email from the user profile.
5. Fall back to `ADMIN_EMAIL` and log a warning.

Every result is format-checked, so a malformed address never reaches the send call.

Items owned by accounts in `SKIP_OWNERS` are ignored entirely. Esri publishes a large
amount of content under its own accounts and none of it is yours to repair.

## Output

- **Owner email.** One message per owner listing every broken item they own, with an item
  page link, the failing reference, and the reason. The wording asks them to repair,
  delete, or ask for help.
- **Administrator summary.** Counts per owner, the username-to-address table, and the CSV
  path. Sent to `ADMIN_EMAIL`.
- **CSV report.** One row per item, written to `REPORT_DIR`, retained for
  `CSV_RETENTION_DAYS`.
- **Log.** Written to `LOG_DIR`. Deleted on a clean run when `DELETE_LOG_ON_SUCCESS` is
  True, so a log file on disk always means something went wrong.

## Scheduling

Weekly is a sensible cadence. More often than that and the same message starts to feel
like noise before anyone has had time to act on it.

Windows Task Scheduler, running as a service account with read access to the script folder:

```
Program:   C:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe
Arguments: scan_portal.py
Start in:  C:\path\to\arcgis-broken-item-scanner
```

cron, weekly on Monday at 6am:

```
0 6 * * 1 cd /opt/arcgis-broken-item-scanner && /usr/bin/python3 scan_portal.py
```

Exit code 0 means the run completed, whether or not broken items were found. Exit code 2
means the run failed and a notice was emailed. Findings are deliberately not an error
exit, so a scheduler configured to retry on failure does not send everyone a second copy.

## Security

`config.py` holds plaintext passwords and is excluded by `.gitignore`. Keep it that way.
Restrict filesystem permissions on the script folder to the accounts that need it. For a
tighter setup, replace the password values with lookups against a secret store such as
the `keyring` package or your platform's credential manager.

## Limitations

- Web Map layer inspection covers operational layers and basemap layers. Web app and
  dashboard configurations are not parsed, so an app pointing at a dead service is only
  caught through the item URL of the service itself.
- Items the scanning account cannot see are not scanned.
- A service that is merely slow can look broken. Raise `REQUEST_TIMEOUT` if you see
  timeouts on healthy services.

## License

Apache License 2.0. Provided as-is with no warranty.

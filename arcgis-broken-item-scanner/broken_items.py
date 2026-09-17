"""
Script Name: broken_items.py
Project: arcgis-broken-item-scanner
Version: 1.0.0

Description:
    Shared code for the ArcGIS broken item scanner. Handles configuration loading,
    logging, the item scan, owner resolution, CSV output, and email. The entry
    scripts scan_portal.py and scan_agol.py build a profile and call run().

    Nothing in this module is specific to one organization. Every value it needs
    comes from config.py.

Internal Use Notice:
    Provided as-is with no warranty. Test with DRY_RUN = True before letting it
    email anyone.

Dependencies:
    - Python 3.8+
    - arcgis (ArcGIS API for Python)
    - requests

Exit Codes:
    - 0: Completed, whether or not broken items were found.
    - 2: Fatal error. Failure email sent if email is configured, log preserved.
"""

import os
import re
import sys
import csv
import ssl
import time
import smtplib
import logging
import traceback
import datetime as dt
from urllib.parse import urlparse
from email.message import EmailMessage

import requests
from arcgis.gis import GIS

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")

log = logging.getLogger("broken_items")


# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------
def load_config():
    """Import config.py from the script folder and fail with a clear message if missing."""
    if SCRIPT_DIR not in sys.path:
        sys.path.insert(0, SCRIPT_DIR)
    try:
        import config
    except ImportError:
        print("config.py not found. Copy config.example.py to config.py and fill it in.")
        sys.exit(2)
    return config


def resolve_dir(cfg_value, default_name):
    """Allow relative output folders in config, anchored to the script folder."""
    path = cfg_value or default_name
    if not os.path.isabs(path):
        path = os.path.join(SCRIPT_DIR, path)
    os.makedirs(path, exist_ok=True)
    return path


# ----------------------------------------------------------------------
# Logging
# ----------------------------------------------------------------------
def setup_logging(cfg, script_name):
    log_dir = resolve_dir(getattr(cfg, "LOG_DIR", None), "logs")
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = os.path.join(log_dir, f"{script_name}_{stamp}.log")

    log.setLevel(logging.INFO)
    log.propagate = False
    for handler in log.handlers[:]:
        handler.close()
        log.removeHandler(handler)

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    log.addHandler(file_handler)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s"))
    log.addHandler(console)
    return log_file


def delete_log_file(log_file_path):
    """Remove the log after a clean run so only real problems leave a file behind."""
    try:
        for handler in log.handlers[:]:
            handler.close()
            log.removeHandler(handler)
        time.sleep(0.5)
        if os.path.exists(log_file_path):
            os.remove(log_file_path)
    except Exception as e:
        print(f"Error deleting log file: {e}")


def elapsed_report(start, end):
    """Format an elapsed time span for the log."""
    total = int(round(end - start))
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d} (h:m:s)"
    if minutes:
        return f"{minutes:02d}:{seconds:02d} (m:s)"
    return f"{seconds} seconds"


# ----------------------------------------------------------------------
# Email
# ----------------------------------------------------------------------
def send_email(cfg, recipient, subject, body):
    """Send one plain text message with an HTML alternative. Honors DRY_RUN."""
    if getattr(cfg, "DRY_RUN", True):
        log.info("DRY RUN, not sending to %s | subject: %s", recipient, subject)
        return

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.FROM_EMAIL
    msg["To"] = recipient
    msg.set_content(body)
    html_body = body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
    msg.add_alternative(f"<html><body><p>{html_body}</p></body></html>", subtype="html")

    with smtplib.SMTP(cfg.SMTP_HOST, getattr(cfg, "SMTP_PORT", 25), timeout=30) as smtp:
        smtp.ehlo()
        if getattr(cfg, "SMTP_USE_TLS", False):
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
        if getattr(cfg, "SMTP_USER", ""):
            smtp.login(cfg.SMTP_USER, cfg.SMTP_PASSWORD)
        smtp.send_message(msg)


def route(cfg, recipient):
    """Final delivery address. TEST_REDIRECT overrides everything when set."""
    return getattr(cfg, "TEST_REDIRECT", "") or recipient


def tag_subject(cfg, subject, recipient):
    """Mark redirected mail so a test run is obvious in the inbox."""
    if getattr(cfg, "TEST_REDIRECT", ""):
        return f"[TEST, intended for {recipient}] {subject}"
    return subject


# ----------------------------------------------------------------------
# Sign in
# ----------------------------------------------------------------------
def connect(url, user, password, verify_ssl, label, required=True):
    """Sign in. When required is False a failure is logged and None is returned."""
    try:
        gis = GIS(url, user, password, verify_cert=verify_ssl)
        log.info("%s sign-in OK as %s", label, gis.properties.user.username)
        return gis
    except Exception as e:
        if required:
            raise
        log.error("%s sign-in failed: %s", label, e)
        log.error("Continuing without a %s token. Secured references on that host will "
                  "report as authentication failures rather than missing services.", label)
        return None


def get_token(gis):
    if gis is None:
        return None
    try:
        return gis._con.token
    except Exception:
        return None


def token_for_url(cfg, url, primary_token, federated_token):
    """Pick the token that matches the host in the URL."""
    host = (urlparse(url).hostname or "").lower()
    federated_suffix = (getattr(cfg, "FEDERATED_HOST_SUFFIX", "") or "").lower()
    agol_suffix = (getattr(cfg, "AGOL_HOST_SUFFIX", ".arcgis.com") or "").lower()
    if federated_suffix and host.endswith(federated_suffix):
        return federated_token or primary_token
    if agol_suffix and host.endswith(agol_suffix):
        return primary_token
    return None


# ----------------------------------------------------------------------
# Scan
# ----------------------------------------------------------------------
def get_all_items(gis, page_size):
    """Page through every org item, sidestepping the 10,000-item search cap."""
    query = f"orgid:{gis.properties.id}"
    log.info("Search query: %s", query)

    all_items = []
    start = 1
    while True:
        resp = gis.content.advanced_search(
            query=query,
            max_items=page_size,
            start=start,
            sort_field="created",
            sort_order="asc",
            as_dict=False,
        )
        results = resp.get("results", [])
        if not results:
            break
        all_items.extend(results)
        log.info("...pulled %d / %d items", len(all_items), resp.get("total", 0))
        next_start = resp.get("nextStart", -1)
        if next_start is None or next_start <= 0:
            break
        start = next_start
    return all_items


def check_url(cfg, session, url, token):
    """Return (ok, detail). ok=False means the URL looks broken."""
    if not url:
        return True, "no url (nothing to check)"
    if not (urlparse(url).scheme or "").startswith("http"):
        return True, f"non-http url skipped ({url})"

    params = {"f": "json"}
    if token:
        params["token"] = token
    try:
        r = session.get(url, params=params,
                        timeout=getattr(cfg, "REQUEST_TIMEOUT", 20),
                        verify=getattr(cfg, "VERIFY_SSL", True))
    except requests.exceptions.SSLError as e:
        return False, f"SSL error: {e}"
    except requests.exceptions.ConnectionError:
        return False, "connection failed / host unreachable"
    except requests.exceptions.Timeout:
        return False, f"timed out after {getattr(cfg, 'REQUEST_TIMEOUT', 20)}s"
    except Exception as e:
        return False, f"request error: {e}"

    if r.status_code >= 400:
        return False, f"HTTP {r.status_code}"

    try:
        data = r.json()
    except ValueError:
        return True, f"HTTP {r.status_code} ok"

    if isinstance(data, dict) and "error" in data:
        err = data["error"] or {}
        code = err.get("code")
        message = err.get("message", "")
        if code in (498, 499):
            which = "no token sent" if not token else "token rejected"
            return False, f"authentication failed ({which}), not confirmed missing: {message}"
        if code == 403:
            return False, f"access denied (403), the account may lack permission: {message}"
        return False, f"REST error {code}: {message}"

    return True, f"HTTP {r.status_code} ok"


def collect_layer_urls(item):
    """Pull operational layer and basemap layer URLs out of a Web Map's JSON."""
    urls = []
    try:
        data = item.get_data()
    except Exception:
        return urls
    if not isinstance(data, dict):
        return urls
    for lyr in data.get("operationalLayers", []) or []:
        if lyr.get("url"):
            urls.append(("operationalLayer", lyr.get("title", "?"), lyr["url"]))
    for lyr in (data.get("baseMap", {}) or {}).get("baseMapLayers", []) or []:
        if lyr.get("url"):
            urls.append(("basemapLayer", lyr.get("title", "?"), lyr["url"]))
    return urls


def scan(cfg, gis, federated_gis=None):
    """Scan every item in the org. Returns a list of broken reference records."""
    session = requests.Session()
    session.headers.update({"User-Agent": "arcgis-broken-item-scanner"})

    primary_token = get_token(gis)
    federated_token = get_token(federated_gis)
    log.info("Tokens available -> primary: %s | federated: %s",
             "yes" if primary_token else "no", "yes" if federated_token else "no")

    skip_owners = {o.lower() for o in getattr(cfg, "SKIP_OWNERS", [])}

    log.info("Enumerating items, this can take a few minutes on a large org...")
    items = get_all_items(gis, getattr(cfg, "PAGE_SIZE", 100))
    log.info("Found %d items to scan.", len(items))

    broken = []
    scanned = 0

    for item in items:
        scanned += 1
        owner = getattr(item, "owner", "") or ""
        if owner.lower() in skip_owners:
            continue

        label = f"[{item.type}] '{item.title}' ({item.id})"

        if getattr(item, "url", None):
            token = token_for_url(cfg, item.url, primary_token, federated_token)
            ok, detail = check_url(cfg, session, item.url, token)
            if ok:
                log.info("OK    %s -> %s", label, detail)
            else:
                log.warning("BROKEN %s -> %s | %s", label, detail, item.url)
                broken.append({
                    "item_id": item.id,
                    "name": item.title,
                    "item_type": item.type,
                    "owner": owner,
                    "broken_reference": "item url",
                    "reason": detail,
                    "url": item.url,
                })

        if item.type == "Web Map":
            for kind, title, layer_url in collect_layer_urls(item):
                token = token_for_url(cfg, layer_url, primary_token, federated_token)
                ok, detail = check_url(cfg, session, layer_url, token)
                if ok:
                    log.info("  OK   layer %s '%s' -> %s", kind, title, detail)
                else:
                    log.warning("  BROKEN layer %s '%s' -> %s | %s", kind, title, detail, layer_url)
                    broken.append({
                        "item_id": item.id,
                        "name": item.title,
                        "item_type": item.type,
                        "owner": owner,
                        "broken_reference": f"{kind}: {title}",
                        "reason": detail,
                        "url": layer_url,
                    })

    log.info("Scan complete. %d items scanned, %d broken reference(s) found.", scanned, len(broken))
    return broken


# ----------------------------------------------------------------------
# Owner resolution
# ----------------------------------------------------------------------
_email_cache = {}


def normalize_owner(cfg, owner):
    """
    Strip the org suffix that ArcGIS Online appends to usernames.
    "jane.doe@example.org_YourOrgName" -> "jane.doe@example.org"
    A username with no known suffix comes back unchanged.
    """
    key = (owner or "").strip()
    for suffix in getattr(cfg, "ORG_USERNAME_SUFFIXES", []):
        if suffix and key.lower().endswith(suffix.lower()):
            return key[: -len(suffix)]
    return key


def looks_like_email(value):
    return bool(EMAIL_PATTERN.match((value or "").strip()))


def resolve_recipient(cfg, owner, gis=None):
    """
    Map an owner username to an email address. None means do not email.

    Order of resolution:
      1. Strip the org suffix off the username.
      2. Explicit override table.
      3. The stripped username itself, when it is already a valid address.
      4. The email on the user profile.
      5. ADMIN_EMAIL, with a warning in the log.
    """
    raw = (owner or "").strip()
    skip_owners = {o.lower() for o in getattr(cfg, "SKIP_OWNERS", [])}
    if not raw or raw.lower() in skip_owners:
        return None

    key = normalize_owner(cfg, raw)
    if not key or key.lower() in skip_owners:
        return None

    overrides = {k.lower(): v for k, v in getattr(cfg, "OWNER_EMAIL_OVERRIDES", {}).items()}
    if key.lower() in overrides:
        return overrides[key.lower()]
    if raw in _email_cache:
        return _email_cache[raw]
    if looks_like_email(key):
        _email_cache[raw] = key
        return key

    address = cfg.ADMIN_EMAIL
    if gis is not None:
        try:
            user = gis.users.get(raw)
            profile_email = getattr(user, "email", None) if user is not None else None
            if looks_like_email(profile_email):
                address = profile_email
            else:
                log.warning("No usable email on the profile for '%s', routing to %s", raw, address)
        except Exception as e:
            log.warning("Could not read the profile for '%s' (%s), routing to %s", raw, e, address)
    _email_cache[raw] = address
    return address


def build_owner_map(cfg, broken, gis):
    """Log every distinct owner and the address it resolved to. Returns that mapping."""
    mapping = {}
    for record in broken:
        raw = record["owner"]
        if raw not in mapping:
            mapping[raw] = resolve_recipient(cfg, raw, gis)
    log.info("Owner to address mapping:")
    for raw in sorted(mapping):
        log.info("  %-45s -> %s", raw, mapping[raw] or "(skipped)")
    return mapping


def group_by_recipient(cfg, broken, gis):
    """Collapse broken references into {recipient: {item_id: item record}}."""
    grouped = {}
    for record in broken:
        recipient = resolve_recipient(cfg, record["owner"], gis)
        if not recipient:
            continue
        items = grouped.setdefault(recipient, {})
        entry = items.setdefault(record["item_id"], {
            "item_id": record["item_id"],
            "name": record["name"],
            "item_type": record["item_type"],
            "owner": record["owner"],
            "refs": [],
        })
        entry["refs"].append({
            "broken_reference": record["broken_reference"],
            "reason": record["reason"],
            "url": record["url"],
        })
    return grouped


# ----------------------------------------------------------------------
# Message bodies
# ----------------------------------------------------------------------
def item_page_url(profile, item_id):
    return f"{profile['home_url']}/home/item.html?id={item_id}"


def build_owner_body(cfg, profile, items):
    """Plain text body for one owner."""
    item_count = len(items)
    ref_count = sum(len(i["refs"]) for i in items.values())
    max_refs = getattr(cfg, "MAX_REFS_PER_ITEM", 10)
    support = getattr(cfg, "SUPPORT_EMAIL", cfg.ADMIN_EMAIL)
    lines = [
        "Hi,",
        "",
        f"The {profile['label']} broken item scan turned up {item_count} item(s) listed under your "
        f"account with {ref_count} reference(s) that did not respond. A broken reference means the item "
        f"points at a service URL that returned an error or could not be reached, so the item will not "
        f"draw correctly for anyone who opens it.",
        "",
        f"When you have a chance, ideally within about {getattr(cfg, 'REVIEW_DAYS', 10)} business days, "
        f"could you look through the list below and let each item land in one of these three spots:",
        "",
        "  Repair  - the service moved or was renamed. Update the layer URL in the item or web map, "
        "then reopen the item to confirm it draws.",
        "  Delete  - the item is not used anymore. Removing it keeps it out of future scans and out of "
        "search results for everyone else.",
        f"  Ask us  - the service should be running and is not, or you are not sure which of the above "
        f"applies. Reply to this message or email {support} and we will take a look with you.",
        "",
        "The scan runs on a schedule, so anything still broken will show up again. That is a reminder, "
        "not a nag, and there is no problem with telling us an item needs more time.",
        "",
        "YOUR ITEMS",
        "",
    ]
    for rec in sorted(items.values(), key=lambda r: r["name"].lower()):
        lines.append(f"{rec['name']}  ({rec['item_type']})")
        lines.append(f"  Item page: {item_page_url(profile, rec['item_id'])}")
        lines.append(f"  Owner: {rec['owner']}")
        lines.append(f"  Broken references: {len(rec['refs'])}")
        for ref in rec["refs"][:max_refs]:
            lines.append(f"  - {ref['broken_reference']}: {ref['reason']}")
            lines.append(f"      {ref['url']}")
        extra = len(rec["refs"]) - max_refs
        if extra > 0:
            lines.append(f"  - ...and {extra} more broken reference(s) on this item.")
        lines.append("")
    lines.extend([
        f"Thanks for keeping these current. Questions are welcome at {support}.",
        "",
        f"This message was generated automatically by {profile['script_name']}.",
    ])
    return "\n".join(lines)


def build_admin_body(cfg, profile, grouped, total_refs, csv_path, owner_map):
    lines = [
        f"{profile['label']} broken item scan of {profile['home_url']} is complete.",
        "",
        f"Owners with findings: {len(grouped)}",
        f"Items with broken references: {sum(len(v) for v in grouped.values())}",
        f"Broken references total: {total_refs}",
        "",
    ]
    if getattr(cfg, "DRY_RUN", True):
        lines.extend(["DRY_RUN is on, so no owner mail was sent. Set DRY_RUN = False in config.py "
                      "when you are ready.", ""])
    elif getattr(cfg, "TEST_REDIRECT", ""):
        lines.extend([f"TEST_REDIRECT is set to {cfg.TEST_REDIRECT}, so every message went there "
                      f"instead of to the owners.", ""])
    lines.append("Per owner:")
    for recipient in sorted(grouped):
        items = grouped[recipient]
        refs = sum(len(i["refs"]) for i in items.values())
        lines.append(f"  {recipient}: {len(items)} item(s), {refs} broken reference(s)")
    if owner_map:
        lines.extend(["", "Username to address resolution:"])
        for raw in sorted(owner_map):
            lines.append(f"  {raw} -> {owner_map[raw] or '(skipped)'}")
    lines.extend([
        "",
        f"CSV report: {csv_path}",
        "",
        f"This message was generated automatically by {profile['script_name']}.",
    ])
    return "\n".join(lines)


# ----------------------------------------------------------------------
# CSV report
# ----------------------------------------------------------------------
def write_csv(cfg, profile, broken, gis):
    """Write one row per item that has any broken reference."""
    items = {}
    for record in broken:
        item_id = record["item_id"]
        if item_id not in items:
            items[item_id] = {
                "item_id": item_id,
                "name": record["name"],
                "item_type": record["item_type"],
                "owner_username": record["owner"],
                "owner_email": resolve_recipient(cfg, record["owner"], gis) or "",
                "broken_count": 0,
            }
        items[item_id]["broken_count"] += 1

    report_dir = resolve_dir(getattr(cfg, "REPORT_DIR", None), "reports")
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(report_dir, f"{profile['slug']}_broken_items_{stamp}.csv")
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["item_id", "name", "item_type", "owner_username", "owner_email", "broken_count"])
        for item in items.values():
            writer.writerow([item["item_id"], item["name"], item["item_type"],
                             item["owner_username"], item["owner_email"], item["broken_count"]])
    return len(items), path


def purge_old_reports(cfg, profile):
    """Delete CSV reports older than CSV_RETENTION_DAYS."""
    try:
        report_dir = resolve_dir(getattr(cfg, "REPORT_DIR", None), "reports")
        cutoff = time.time() - (getattr(cfg, "CSV_RETENTION_DAYS", 90) * 86400)
        prefix = f"{profile['slug']}_broken_items_"
        for name in os.listdir(report_dir):
            if not name.startswith(prefix) or not name.endswith(".csv"):
                continue
            full = os.path.join(report_dir, name)
            if os.path.getmtime(full) < cutoff:
                os.remove(full)
                log.info("Removed expired report: %s", name)
    except Exception as e:
        log.warning("Report cleanup skipped: %s", e)


# ----------------------------------------------------------------------
# Runner
# ----------------------------------------------------------------------
def run(profile):
    """
    Full run for one profile. A profile is a dict with these keys:
        slug        short name used in file names, for example "portal"
        label       human readable name used in email text
        home_url    base URL used to build item page links
        url         sign-in URL
        user        sign-in username
        password    sign-in password
        script_name name shown at the bottom of emails
        federated   optional dict with url, user, password for a second sign-in
    """
    cfg = load_config()
    log_file = setup_logging(cfg, profile["slug"] + "_broken_items")
    start = time.time()

    try:
        log.info("Script execution started")
        log.info("Target: %s (%s) | DRY_RUN: %s | Redirect: %s",
                 profile["url"], profile["user"],
                 getattr(cfg, "DRY_RUN", True),
                 getattr(cfg, "TEST_REDIRECT", "") or "none")

        verify_ssl = getattr(cfg, "VERIFY_SSL", True)
        gis = connect(profile["url"], profile["user"], profile["password"],
                      verify_ssl, profile["label"], required=True)

        federated_gis = None
        fed = profile.get("federated")
        if fed:
            federated_gis = connect(fed["url"], fed["user"], fed["password"],
                                    verify_ssl, "Federated Portal", required=False)

        broken = scan(cfg, gis, federated_gis)

        if not broken:
            log.info("No broken items found.")
            log.info("Completed in %s", elapsed_report(start, time.time()))
            if getattr(cfg, "DELETE_LOG_ON_SUCCESS", True):
                delete_log_file(log_file)
            return 0

        owner_map = build_owner_map(cfg, broken, gis)
        unique_items, csv_path = write_csv(cfg, profile, broken, gis)
        log.info("CSV written: %s (%d item(s) with broken references)", csv_path, unique_items)
        purge_old_reports(cfg, profile)

        grouped = group_by_recipient(cfg, broken, gis)
        sent, failed = 0, 0
        for recipient in sorted(grouped):
            items = grouped[recipient]
            subject = f"Please review: {len(items)} {profile['label']} item(s) with broken references"
            body = build_owner_body(cfg, profile, items)
            try:
                send_email(cfg, route(cfg, recipient), tag_subject(cfg, subject, recipient), body)
                log.info("Notified %s about %d item(s)", recipient, len(items))
                sent += 1
            except Exception as e:
                log.error("Failed to notify %s: %s", recipient, e)
                failed += 1

        admin_subject = (f"{profile['label']} broken item scan: {len(broken)} broken reference(s), "
                         f"{len(grouped)} owner(s)")
        admin_body = build_admin_body(cfg, profile, grouped, len(broken), csv_path, owner_map)
        try:
            send_email(cfg, cfg.ADMIN_EMAIL, admin_subject, admin_body)
        except Exception as e:
            log.error("Failed to send the administrator summary: %s", e)

        log.info("Owner emails sent: %d | failed: %d", sent, failed)
        log.info("Completed in %s", elapsed_report(start, time.time()))
        if getattr(cfg, "DELETE_LOG_ON_SUCCESS", True):
            delete_log_file(log_file)
        return 0

    except Exception as e:
        log.error("Error occurred: %s", e, exc_info=True)
        try:
            send_email(
                cfg,
                cfg.ADMIN_EMAIL,
                f"ALERT: {profile['script_name']} failed",
                (f"{profile['script_name']} failed.\n\n"
                 f"Time of failure: {dt.datetime.now().strftime('%B %d, %Y %I:%M %p')}\n\n"
                 f"Log file: {log_file}\n\n"
                 f"Error details:\n{traceback.format_exc()}"),
            )
        except Exception as mail_error:
            log.error("Failed to send the failure email: %s", mail_error)
        return 2

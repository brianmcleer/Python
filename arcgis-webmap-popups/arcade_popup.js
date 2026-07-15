// =============================================================================
// Accessible field-listing popup Arcade expression
// =============================================================================
// Renders every non-excluded field of a feature as "Label: value", one per
// line, auto-formatting dates, booleans, currency, phone numbers, emails,
// coded domain values, and hyperlinks. Produces WCAG-conscious HTML:
// semantic <strong> labels, descriptive link accessible-names, tel:/mailto:
// links, "(opens in new window)" announcements.
//
// This expression is generic and needs no editing to work. Two things you may
// want to customize are clearly marked below:
//   1. fieldsToExclude - system/internal field names to hide
//   2. The DOCUMENT LINK SETTINGS block - turn relative field values into
//      links against your own document server (disabled by default)
// =============================================================================

// ---- 1. Fields to hide (system + internal housekeeping) ---------------------
var fieldsToExclude = ["OBJECTID", "OBJECTID_1", "FID", "SHAPE", "GLOBALID",
    "SHAPE.STAREA()", "SHAPE.STLENGTH()", "SHAPE__AREA", "SHAPE__LENGTH",
    "CREATED_USER", "CREATED_DATE", "LAST_EDITED_USER", "LAST_EDITED_DATE"];

// ---- 2. DOCUMENT LINK SETTINGS ----------------------------------------------
// If your data stores relative document paths (e.g. "docs/permit123.pdf") that
// should become clickable links against a base URL, set ENABLE_DOC_LINKS to
// true and DOC_BASE_URL to your server. Absolute http(s):// values are always
// linked regardless of this setting.
var ENABLE_DOC_LINKS = false;
var DOC_BASE_URL = "https://your-doc-server.example.com/";

var content = "";
var seenUrls = {};

// Bold label using semantic <strong> tag (bold + announced as emphasis).
function labelHtml(label) {
    return "<strong>" + label + ":</strong> ";
}

// Build a link, keeping visible text short but giving screen readers a
// descriptive accessible name via the title attribute.
function buildLink(href, accessibleName, opensNewWindow) {
    var fullName = accessibleName;
    if (opensNewWindow) { fullName += " (opens in new window)"; }
    var attrs = "href='" + href + "' title='" + fullName + "'";
    if (opensNewWindow) { attrs += " target='_blank' rel='noopener noreferrer'"; }
    return "<a " + attrs + ">Click here for more info.</a>";
}

// Decide whether a value is a hyperlink, and to what URL.
function resolveHref(valText) {
    var lowerValue = Lower(valText);
    if (Left(lowerValue, 8) == "https://" || Left(lowerValue, 7) == "http://") {
        return valText;
    }
    if (ENABLE_DOC_LINKS && (Right(lowerValue, 4) == ".pdf"
        || Right(lowerValue, 5) == ".xlsx" || Right(lowerValue, 4) == ".doc"
        || Right(lowerValue, 5) == ".docx")) {
        return DOC_BASE_URL + valText;
    }
    return "";
}

function isURLValue(val) {
    return resolveHref(Text(val)) != "";
}

function formatURL(urlLabel, urlValue) {
    var valText = Text(urlValue);
    var href = resolveHref(valText);
    var verb = "View ";
    if (Right(Lower(href), 4) == ".pdf") { verb = "Open PDF: "; }
    else if (Right(Lower(href), 5) == ".xlsx") { verb = "Open spreadsheet: "; }
    var accessibleName = verb + urlLabel;
    return labelHtml(urlLabel) + buildLink(href, accessibleName, true) + "<br/>";
}

function formatDate(dateVal) {
    if (IsEmpty(dateVal)) { return ""; }
    var d = Date(dateVal);
    return Text(d, "MM/DD/YYYY");
}

// Resolve coded-domain values (e.g. status 1 -> "Active") before any
// downstream type detection runs.
function resolveDomainValue(fldInfo, fldValue) {
    if (fldInfo == null) { return fldValue; }
    if (!HasKey(fldInfo, "domain")) { return fldValue; }
    var dom = fldInfo["domain"];
    if (dom == null) { return fldValue; }
    if (!HasKey(dom, "codedValues")) { return fldValue; }
    var codedVals = dom["codedValues"];
    if (codedVals == null) { return fldValue; }
    for (var k = 0; k < Count(codedVals); k++) {
        var cv = codedVals[k];
        if (cv != null && HasKey(cv, "code") && cv["code"] == fldValue) {
            if (HasKey(cv, "name")) { return cv["name"]; }
        }
    }
    return fldValue;
}

function stripToDigits(val) {
    var s = Text(val);
    var result = "";
    var digits = "0123456789";
    for (var i = 0; i < Count(s); i++) {
        var ch = Mid(s, i, 1);
        if (Find(ch, digits) != -1) { result += ch; }
    }
    return result;
}

// Phone detection: 10 or 11 digits, phone-like punctuation only, with alias
// hints to confirm and anti-hints to reject IDs/coordinates/measurements that
// merely happen to have 10 digits.
function isPhoneValue(val, fldAlias) {
    var s = Text(val);
    if (Find(".", s) != -1) { return false; }
    if (Left(s, 1) == "-") { return false; }

    var digits = stripToDigits(s);
    var len = Count(digits);
    if (len != 10 && len != 11) { return false; }
    if (len == 11 && Left(digits, 1) != "1") { return false; }

    var validChars = "0123456789 ()-+extEXT";
    for (var i = 0; i < Count(s); i++) {
        var ch = Mid(s, i, 1);
        if (Find(ch, validChars) == -1) { return false; }
    }

    var aliasLower = Lower(fldAlias);
    var antiHints = ["parcel", "account", "permit", "apn", "pin", "case",
                     "tract", "lot", "block", "zone", "section",
                     "acre", "area", "length", "width", "height", "size",
                     "coord", "latitude", "longitude", "lat", "long", "lon",
                     "easting", "northing", "elevation",
                     "year", "date", "time", "stamp",
                     "price", "cost", "value", "amount", "fee", "tax",
                     "id", "number", "no", "num", "code", "key"];
    for (var j = 0; j < Count(antiHints); j++) {
        if (Find(antiHints[j], aliasLower) != -1) { return false; }
    }

    var phoneHints = ["phone", "fax", "tel", "cell", "mobile", "contact"];
    for (var k = 0; k < Count(phoneHints); k++) {
        if (Find(phoneHints[k], aliasLower) != -1) { return true; }
    }

    var hasParens = Find("(", s) != -1 && Find(")", s) != -1;
    var hasLeadingPlus = Left(s, 1) == "+";
    return hasParens || hasLeadingPlus;
}

function formatPhone(label, val) {
    var digits = stripToDigits(val);
    var ten = digits;
    if (Count(digits) == 11) { ten = Right(digits, 10); }
    var telNum = "1" + ten;
    var display = "(" + Left(ten, 3) + ") " + Mid(ten, 3, 3) + "-" + Right(ten, 4);
    return labelHtml(label)
         + "<a href='tel:+" + telNum + "' title='Call " + display + "'>"
         + display + "</a><br/>";
}

function isEmailValue(val) {
    var s = Text(val);
    if (Find(" ", s) != -1) { return false; }
    var at = Find("@", s);
    if (at <= 0) { return false; }
    if (Find("@", s, at + 1) != -1) { return false; }
    var domain = Mid(s, at + 1, Count(s) - at - 1);
    if (Find(".", domain) == -1) { return false; }
    if (Count(domain) < 3) { return false; }
    return true;
}

function formatEmail(label, val) {
    var addr = Text(val);
    return labelHtml(label)
         + "<a href='mailto:" + addr + "' title='Send email to " + addr + "'>"
         + addr + "</a><br/>";
}

function isBooleanValue(val, fldAlias) {
    var s = Upper(Trim(Text(val)));
    if (s == "TRUE" || s == "FALSE") { return true; }
    if (s == "YES" || s == "NO") { return true; }
    if (s == "Y" || s == "N") { return true; }
    if (s == "1" || s == "0") {
        var a = Lower(fldAlias);
        if (Right(a, 1) == "?" || Left(a, 3) == "is " || Left(a, 4) == "has "
            || Left(a, 3) == "in " || Find("flag", a) != -1) {
            return true;
        }
    }
    return false;
}

function formatBoolean(label, val) {
    var s = Upper(Trim(Text(val)));
    var isTrue = (s == "TRUE" || s == "YES" || s == "Y" || s == "1");
    var display = "";
    if (isTrue) {
        if (s == "TRUE") { display = "True"; } else { display = "Yes"; }
    } else {
        if (s == "FALSE") { display = "False"; } else { display = "No"; }
    }
    return labelHtml(label) + display + "<br/>";
}

// Currency detection with anti-hints so measurement aliases ("Square Feet")
// are never treated as money just because "fee" is a substring of "feet".
function isCurrencyValue(val, fldAlias) {
    var s = Text(val);
    if (Left(s, 1) == "$") { return true; }
    var a = Lower(fldAlias);

    var antiHints = ["feet", "ft", "square", "sq ", "area",
                     "length", "width", "height", "depth", "size",
                     "acre", "meter", "mile", "yard", "inch",
                     "elevation", "weight", "volume",
                     "count", "qty", "quantity", "number", "num", "id", "code"];
    for (var j = 0; j < Count(antiHints); j++) {
        if (Find(antiHints[j], a) != -1) { return false; }
    }

    if (Find("price", a) != -1 || Find("cost", a) != -1
        || Find("amount", a) != -1 || Find("fee", a) != -1
        || Find("salary", a) != -1 || Find("revenue", a) != -1
        || Find("budget", a) != -1) {
        var trimmed = s;
        if (Left(trimmed, 1) == "$") { trimmed = Mid(trimmed, 1, Count(trimmed) - 1); }
        var cleaned = "";
        for (var i = 0; i < Count(trimmed); i++) {
            var ch = Mid(trimmed, i, 1);
            if (ch != ",") { cleaned += ch; }
        }
        var n = Number(cleaned);
        if (!IsNan(n)) { return true; }
    }
    return false;
}

function formatCurrency(label, val) {
    var s = Text(val);
    var display = s;
    if (Left(s, 1) != "$") {
        var n = Number(s);
        if (!IsNan(n)) { display = Text(n, "$#,###.##"); }
    }
    return labelHtml(label) + display + "<br/>";
}

// ---- Main loop --------------------------------------------------------------
Expects($feature, "*");

var schemaDict = Schema($feature);
var fieldsArray = schemaDict["fields"];

for (var i = 0; i < Count(fieldsArray); i++) {
    var fld = fieldsArray[i];
    if (fld == null) { continue; }

    var fldName = "";
    var fldAlias = "";
    var fldType = "";
    if (HasKey(fld, "name")) { fldName = fld["name"]; }
    if (HasKey(fld, "alias")) { fldAlias = fld["alias"]; }
    if (HasKey(fld, "type")) { fldType = fld["type"]; }

    if (IsEmpty(fldName)) { continue; }
    if (IsEmpty(fldAlias)) { fldAlias = fldName; }

    var fldValue = $feature[fldName];
    fldValue = resolveDomainValue(fld, fldValue);

    if (fldType == "esriFieldTypeString" && !IsEmpty(fldValue)) {
        fldValue = Trim(Text(fldValue));
    }

    if (IndexOf(fieldsToExclude, Upper(fldName)) == -1 && !IsEmpty(fldValue) && Upper(Text(fldValue)) != "NULL") {
        if (isURLValue(fldValue)) {
            var valKey = Text(fldValue);
            if (!HasKey(seenUrls, valKey)) {
                content += formatURL(fldAlias, fldValue);
                seenUrls[valKey] = true;
            }
        } else if (isPhoneValue(fldValue, fldAlias)) {
            content += formatPhone(fldAlias, fldValue);
        } else if (isEmailValue(fldValue)) {
            content += formatEmail(fldAlias, fldValue);
        } else if (isBooleanValue(fldValue, fldAlias)) {
            content += formatBoolean(fldAlias, fldValue);
        } else if (isCurrencyValue(fldValue, fldAlias)) {
            content += formatCurrency(fldAlias, fldValue);
        } else if (fldType == "esriFieldTypeDate") {
            content += labelHtml(fldAlias) + formatDate(fldValue) + "<br/>";
        } else {
            content += labelHtml(fldAlias) + Text(fldValue) + "<br/>";
        }
    }
}

return {
    type: 'text',
    text: content
};

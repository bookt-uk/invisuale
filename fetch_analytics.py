#!/usr/bin/env python3
"""Pull live traffic numbers from GA4 and write admin-data.json for /admin.

Run daily by the Auto Deals workflow. Needs a Google service account with
Viewer access on the GA4 property, supplied as the GA4_SA_KEY secret (the
full JSON key). Without it the script leaves existing data alone but stamps
it stale, so the dashboard shows "last updated X" instead of pretending
there were no visitors.

GA4 property: Invisuale (540668206)
"""
import json, os, sys, datetime, urllib.request, urllib.parse

PROPERTY_ID = os.environ.get("GA4_PROPERTY_ID", "540668206")
OUT = "admin-data.json"
API = f"https://analyticsdata.googleapis.com/v1beta/properties/{PROPERTY_ID}:runReport"
TZ = datetime.timezone(datetime.timedelta(hours=1))  # Europe/London (BST); date comes from GA4 anyway


def uk_today():
    """Today's date in Europe/London, matching what the dashboard checks."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.datetime.now(ZoneInfo("Europe/London")).date().isoformat()
    except Exception:
        return datetime.date.today().isoformat()


def mark_stale(reason):
    """Keep whatever data exists but flag it, so the page can be honest."""
    try:
        with open(OUT) as f:
            d = json.load(f)
    except Exception:
        return
    d["stale"] = True
    d["stale_reason"] = reason
    with open(OUT, "w") as f:
        json.dump(d, f, indent=2)
    print(f"analytics: {reason} — existing data marked stale")


def get_token(sa):
    """Service-account JWT -> OAuth2 access token."""
    from google.oauth2 import service_account  # provided by google-auth
    import google.auth.transport.requests
    creds = service_account.Credentials.from_service_account_info(
        sa, scopes=["https://www.googleapis.com/auth/analytics.readonly"])
    creds.refresh(google.auth.transport.requests.Request())
    return creds.token


def run_report(token, body):
    req = urllib.request.Request(
        API, data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.loads(r.read())


def rows(res, n=1):
    """[(dimension, metric_int), ...] from a runReport response."""
    out = []
    for row in res.get("rows", []):
        dims = [d.get("value", "") for d in row.get("dimensionValues", [])]
        val = int(row.get("metricValues", [{}])[0].get("value", 0) or 0)
        out.append((dims[0] if dims else "", val) if n == 1 else (tuple(dims), val))
    return out


def total(res, idx=0):
    t = res.get("totals", [])
    if not t:
        return 0
    return int(t[0].get("metricValues", [])[idx].get("value", 0) or 0)


def pretty_source(s):
    s = (s or "").strip()
    return {"(direct)": "Direct", "(none)": "Direct", "": "Direct",
            "google": "Google", "bing": "Bing", "chatgpt.com": "ChatGPT",
            "sonysphoneshop": "Sony's Phone Shop", "ui.awin.com": "Awin",
            "uk.search.yahoo.com": "Yahoo"}.get(s.lower(), s)


def main():
    raw = os.environ.get("GA4_SA_KEY", "").strip()
    if not raw:
        mark_stale("GA4_SA_KEY not set")
        return 0
    try:
        sa = json.loads(raw)
        token = get_token(sa)
    except Exception as e:
        mark_stale(f"auth failed: {e}")
        return 0

    try:
        d28 = {"dateRanges": [{"startDate": "28daysAgo", "endDate": "today"}]}
        d1 = {"dateRanges": [{"startDate": "today", "endDate": "today"}]}
        M = lambda *m: [{"name": x} for x in m]
        D = lambda *d: [{"name": x} for x in d]
        top = lambda k: {"limit": k, "orderBys": [{"desc": True, "metric": {"metricName": "screenPageViews"}}]}

        tot28 = run_report(token, {**d28, "metrics": M("totalUsers", "screenPageViews", "sessions")})
        tot1 = run_report(token, {**d1, "metrics": M("totalUsers", "screenPageViews", "sessions")})
        v24 = run_report(token, {"dateRanges": [{"startDate": "yesterday", "endDate": "today"}],
                                 "metrics": M("screenPageViews")})

        pages28 = run_report(token, {**d28, "dimensions": D("pagePath"), "metrics": M("screenPageViews"), **top(8)})
        pages1 = run_report(token, {**d1, "dimensions": D("pagePath"), "metrics": M("screenPageViews"), **top(5)})
        src28 = run_report(token, {**d28, "dimensions": D("sessionSource"), "metrics": M("totalUsers"),
                                   "limit": 8, "orderBys": [{"desc": True, "metric": {"metricName": "totalUsers"}}]})
        src1 = run_report(token, {**d1, "dimensions": D("sessionSource"), "metrics": M("totalUsers"),
                                  "limit": 5, "orderBys": [{"desc": True, "metric": {"metricName": "totalUsers"}}]})
        cty28 = run_report(token, {**d28, "dimensions": D("country", "countryId"), "metrics": M("totalUsers"),
                                   "limit": 7, "orderBys": [{"desc": True, "metric": {"metricName": "totalUsers"}}]})
        cty1 = run_report(token, {**d1, "dimensions": D("country", "countryId"), "metrics": M("totalUsers"),
                                  "limit": 5, "orderBys": [{"desc": True, "metric": {"metricName": "totalUsers"}}]})
        daily = run_report(token, {**d28, "dimensions": D("date"), "metrics": M("screenPageViews"),
                                   "orderBys": [{"dimension": {"dimensionName": "date"}}], "limit": 40})
    except Exception as e:
        mark_stale(f"GA4 query failed: {e}")
        return 0

    def country_rows(res):
        """[name, users, ISO code] so the dashboard can show a flag."""
        out = []
        for row in res.get("rows", []):
            dv = [d.get("value", "") for d in row.get("dimensionValues", [])]
            v = int(row.get("metricValues", [{}])[0].get("value", 0) or 0)
            out.append([dv[0], v, dv[1] if len(dv) > 1 else ""])
        return out

    data = {
        "range": "Last 28 days",
        "updated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "stale": False,
        "totals": {"visitors": total(tot28, 0), "pageviews": total(tot28, 1), "sessions": total(tot28, 2)},
        "today": {
            "date": uk_today(),
            "visitors": total(tot1, 0), "views": total(tot1, 1), "sessions": total(tot1, 2),
            "pages": [list(x) for x in rows(pages1)],
            "sources": [[pretty_source(k), v] for k, v in rows(src1)],
            "countries": country_rows(cty1),
        },
        "sources": [[pretty_source(k), v] for k, v in rows(src28)],
        "countries": country_rows(cty28),
        "pages": [list(x) for x in rows(pages28)],
        "daily": [v for _, v in rows(daily)],
    }
    data["totals"]["views24h"] = total(v24, 0)

    with open(OUT, "w") as f:
        json.dump(data, f, indent=2)
    print(f"analytics: wrote {OUT} — {data['totals']['visitors']} visitors / "
          f"{data['totals']['pageviews']} views (28d), today {data['today']['views']} views")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Records Archytan Lite's public adoption numbers, one CSV row per day (UTC):
image pulls, npm and PyPI downloads, and this repository's stars, watchers,
forks, issues and traffic. Standard library only.

    python adoption_metrics.py adoption.csv                # the last 7 days
    python adoption_metrics.py adoption.csv --backfill 30  # a longer window

Sources with daily history (npm, PyPI, repository traffic) refresh every
day in the window, which fills in figures a source publishes late. Point in
time figures (total image pulls, stars, watchers, forks, open issues) are
written to yesterday's row, the day that just ended. Daily image pulls are
the difference between consecutive days' totals.

Each source is read on its own: one that fails leaves its cells as they
were and the rest still update. Rerunning is safe.
"""
import csv
import datetime as dt
import json
import os
import re
import sys
import urllib.request

NPM_PACKAGE = "@high-archytech-solutions/archytan-lite"
PYPI_PACKAGE = "archytan-lite"
GHCR_PAGE = "https://github.com/orgs/high-archytech-solutions/packages/container/package/archytan-lite"
REPO = os.environ.get("REPO", "High-ArchyTech-Solutions/archytan-lite-community")
TOKEN = os.environ.get("GH_TOKEN", "")

FIELDS = [
    "date",
    "image_pulls_total",
    "npm_downloads",
    "pypi_downloads",
    "pypi_downloads_reporting_os",
    "github_stars",
    "github_watchers",
    "github_forks",
    "github_open_issues",
    "github_views",
    "github_unique_visitors",
    "github_clones",
]


def fetch(url, headers=None):
    request = urllib.request.Request(url, headers={"User-Agent": "archytan-adoption-metrics", **(headers or {})})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", "replace")


def github(path):
    headers = {"Accept": "application/vnd.github+json"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    return json.loads(fetch(f"https://api.github.com{path}", headers))


def attempt(name, read):
    try:
        return read()
    except Exception as err:  # one source failing must not stop the others
        print(f"{name}: unavailable ({type(err).__name__}: {err})", file=sys.stderr)
        return None


def image_pulls_total():
    page = fetch(GHCR_PAGE)
    return int(re.search(r'Total downloads</span>\s*<h3 title="([\d,]+)"', page).group(1).replace(",", ""))


def npm_daily(start, end):
    data = json.loads(fetch(f"https://api.npmjs.org/downloads/range/{start}:{end}/{NPM_PACKAGE}"))
    return {day["day"]: day["downloads"] for day in data["downloads"]}


def pypi_daily():
    overall = json.loads(fetch(f"https://pypistats.org/api/packages/{PYPI_PACKAGE}/overall?mirrors=false"))
    total = {r["date"]: r["downloads"] for r in overall["data"] if r["category"] == "without_mirrors"}
    # Installers that report an operating system are mostly people; the rest
    # are mostly mirrors, scanners and bots.
    system = json.loads(fetch(f"https://pypistats.org/api/packages/{PYPI_PACKAGE}/system"))
    reporting = {}
    for r in system["data"]:
        if r["category"] not in (None, "null", "other"):
            reporting[r["date"]] = reporting.get(r["date"], 0) + r["downloads"]
    return total, reporting


def main():
    path = sys.argv[1]
    days = int(sys.argv[sys.argv.index("--backfill") + 1]) if "--backfill" in sys.argv else 7
    yesterday = dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)
    window = [str(yesterday - dt.timedelta(days=i)) for i in reversed(range(days))]

    rows = {}
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as f:
            rows = {r["date"]: r for r in csv.DictReader(f)}
    for day in window:
        rows.setdefault(day, {"date": day})

    def put(day, field, value):
        if day in rows and value is not None:
            rows[day][field] = str(value)

    total = attempt("image pulls", image_pulls_total)
    put(str(yesterday), "image_pulls_total", total)

    npm = attempt("npm", lambda: npm_daily(window[0], window[-1]))
    if npm is not None:
        for day in window:
            put(day, "npm_downloads", npm.get(day))

    pypi = attempt("PyPI", pypi_daily)
    if pypi is not None:
        downloads, reporting = pypi
        for day in window:
            if day in downloads:
                put(day, "pypi_downloads", downloads[day])
                put(day, "pypi_downloads_reporting_os", reporting.get(day, 0))

    repo = attempt("GitHub repository", lambda: github(f"/repos/{REPO}"))
    if repo is not None:
        put(str(yesterday), "github_stars", repo["stargazers_count"])
        put(str(yesterday), "github_watchers", repo["subscribers_count"])
        put(str(yesterday), "github_forks", repo["forks_count"])
        put(str(yesterday), "github_open_issues", repo["open_issues_count"])

    views = attempt("GitHub views", lambda: github(f"/repos/{REPO}/traffic/views"))
    if views is not None:
        for v in views["views"]:
            put(v["timestamp"][:10], "github_views", v["count"])
            put(v["timestamp"][:10], "github_unique_visitors", v["uniques"])
    clones = attempt("GitHub clones", lambda: github(f"/repos/{REPO}/traffic/clones"))
    if clones is not None:
        for c in clones["clones"]:
            put(c["timestamp"][:10], "github_clones", c["count"])

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, restval="", lineterminator="\n")
        writer.writeheader()
        for day in sorted(rows):
            writer.writerow({k: rows[day].get(k, "") for k in FIELDS})
    print(f"{path}: {len(rows)} day(s), window {window[0]} to {window[-1]}")


if __name__ == "__main__":
    main()

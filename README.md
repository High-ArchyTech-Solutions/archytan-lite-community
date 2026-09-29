# Archytan Lite adoption numbers

[adoption.csv](adoption.csv) holds one row per day (UTC), written by the
[Adoption metrics](https://github.com/High-ArchyTech-Solutions/archytan-lite-community/actions/workflows/adoption-metrics.yml)
workflow on `main`. The gate itself sends nothing anywhere, so these public
counters are the only numbers there are.

| Column | Meaning |
|---|---|
| `image_pulls_total` | Total pulls of `ghcr.io/high-archytech-solutions/archytan-lite` as GitHub's package page reports it, taken at the end of the day. Pulls on a day are the difference from the previous row. |
| `npm_downloads` | Downloads of `@high-archytech-solutions/archytan-lite` that day. |
| `pypi_downloads` | Downloads of `archytan-lite` that day, known mirrors excluded. |
| `pypi_downloads_reporting_os` | Those whose installer reported an operating system, which are mostly people rather than scanners. |
| `github_stars`, `github_watchers`, `github_forks`, `github_open_issues` | This repository at the end of the day. Watchers include people following releases and security advisories. |
| `github_views`, `github_unique_visitors`, `github_clones` | This repository's traffic that day, when GitHub makes it available to the workflow. |

Numbers we generate ourselves, to subtract when reading them:

- **Release days.** The release pipeline pulls and verifies the new image,
  and registries, mirrors and security scanners download every new client
  version within hours.
- **This repository's quickstart check.** It pulls the image when the
  README or the quickstart scripts change, and every Monday around 06:17
  UTC.
- **Before 2026-09-29.** The first rows were backfilled from each source's
  history. Point-in-time columns start on 2026-09-28, and every pull up to
  then came from our own releases and testing.

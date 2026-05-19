# sldt-semantic-analyzer

Semantic analyzer for **SLDT** models: it fetches the `.ttl` (SAMM / BAMM,
Catena-X) models from the upstream
[`eclipse-tractusx/sldt-semantic-models`](https://github.com/eclipse-tractusx/sldt-semantic-models)
repository, parses them, and turns them into an interactive, **fully static**
web site (catalog, model graphs, dependency graphs, generated docs, release
notes and quality issues).

> **Pipeline:** `fetch` → `parse` → `graph` → static JSON → D3 web page.
> The deployed site runs no server and no Python — everything is pre-computed
> offline and committed as JSON that GitHub Pages just serves.

**Live site:**
<https://anisbs.github.io/sldt-semantic-analyzer/web/>

---

## Table of contents

- [Overview](#overview)
- [Features](#features)
- [Tech stack](#tech-stack)
- [Project structure](#project-structure)
- [Setup](#setup)
- [Commands](#commands)
- [The web UI](#the-web-ui)
- [Runtime data flow](#runtime-data-flow)
- [Quality issues](#quality-issues)
- [Deployment & automation](#deployment--automation)
- [Conventions](#conventions)
- [Data source & license](#data-source--license)

---

## Overview

The upstream repository ships ~279 `.ttl` model files using **four
meta-models** — SAMM `2.0.0`/`2.1.0` (recent,
`urn:samm:org.eclipse.esmf…`) and BAMM `1.0.0`/`2.0.0` (older,
`urn:bamm:io.openmanufacturing…`). They describe the same concepts under
different namespaces.

This project:

1. **Fetches** a shallow clone of the upstream models (idempotent resync).
2. **Parses** every `.ttl` with `rdflib`, detecting the meta-model from the
   `@prefix` declarations and working on full URIs, so it is vocabulary
   independent. A malformed file is **never** allowed to crash the parser —
   it is logged and skipped.
3. **Generates** D3-ready JSON: one graph per model file, a global catalog,
   an inter-model dependency map, and a pre-computed quality-issues report.
4. **Serves** a single static HTML page (D3, no framework) that reads those
   JSON files. The same relative paths work locally and on GitHub Pages.

Models are organized upstream as
`io.catenax.<model_name>/<version>/<ModelName>.ttl`, with a sibling
`metadata.json` (status), an optional `gen/` folder (generated artifacts)
and a model-level `RELEASE_NOTES.md` (Keep-a-Changelog).

### Model identity

Extracted from the unnamed default prefix
`@prefix : <urn:FAMILY:NS:VERSION#>`:

- **`model_family`** — `SAMM` or `BAMM` (distinct from `meta_model`, which is
  the *spec* version).
- **`model_name`** — `NS` without the org prefix `io.catenax.`
  (e.g. `batch`, `battery.battery_pass`).
- **`model_version`** — the version **of the model** (e.g. `2.0.0`; a model
  usually has several versions).
- **`status`** — `release` / `deprecated` / `draft`, read from the
  `metadata.json` of the version folder. Missing or broken file →
  `undefined` (logged); an upstream typo `deprecate` is normalized to
  `deprecated`. One `metadata.json` covers every `.ttl` of its version
  folder.

A graph id is **unique per file** (`io.catenax.x__<version>__<TtlName>`)
because a single version folder can contain several `.ttl` files (e.g.
`io.catenax.material_accounting/1.0.0/` has 6).

---

## Features

- **Catalog & KPI (Home)** — models grouped by name, filterable; KPI cards
  per status; a version selector per model. `family` and `status` are
  per version.
- **Model Viewer** — type-ahead model search + version selector + a shared
  layout selector, with sub-tabs:
  - **Graph** — D3 graph of the model's elements (Aspect, Property,
    Characteristic, Entity, …), colored by kind, drag/zoom/tooltips.
  - **Dependency graph** — recursive, cycle-safe BFS over inter-model
    dependencies; nodes re-root the graph on click; Outgoing/Incoming
    toggle; node fill = role, outer ring = status.
  - **Generated docs** — the upstream self-contained HTML doc (inline SVG)
    fetched from jsDelivr and rendered in a sandboxed `iframe srcdoc`
    (no self-hosting), plus links to JSON Schema / sample payload / OpenAPI.
  - **Release notes** — the model-level changelog (same for every version),
    cached once per model.
  - **Issues** — quality issues of the selected model+version.
- **Issues (dedicated tab)** — an aggregated quality view: one KPI card per
  issue type, scoped by status (checkboxes, `release` checked by default),
  with a clickable list of the affected model+version.
- **Layouts** — a shared layout engine: *Static (auto-layout)* (default,
  force computed once then frozen), *Force (animated)*, *Circle*, *Grid*.

---

## Tech stack

- **Backend / parsing:** Python ≥ 3.11, [`rdflib`](https://rdflib.dev/)
  ≥ 7.0 for Turtle parsing. The fetch step uses only `git` + the stdlib.
- **Front-end:** a single static `web/index.html` with
  [D3 v7](https://d3js.org/) loaded from a CDN — no framework, no build step.
- The backend produces **JSON** consumed by the D3 page.

---

## Project structure

```
src/sldt_analyzer/
  fetch.py        # Fetch / idempotent resync of the upstream models
  parser.py       # Parse .ttl (SAMM & BAMM) -> Element / Edge / Dependency
                  #   + model_name / model_version / model_family / status
                  #   + release notes (model-level) + inter-model deps
  graph.py        # ParsedModel -> JSON (D3-ready); writes index.json,
                  #   deps.json, issues.json and one <id>.json per model
  issues.py       # Offline quality-issue computation -> issues.json
web/
  index.html      # The static page: Home / Model Viewer / Issues
  data/graph/     # Generated JSON (committed, served by GitHub Pages):
                  #   index.json + deps.json + issues.json + one per model
.github/workflows/
  update-models.yml  # Weekly cron: resync upstream -> regenerate -> push
scripts/
  test_parser.py  # Quick parser sanity check on a varied sample
data/
  sldt-semantic-models/  # Large local clone (git-ignored, reproducible)
pyproject.toml    # Package metadata; declares rdflib
```

The generated JSON lives under `web/data/graph/` (not `data/`) **on
purpose**: it must be committed so GitHub Pages can serve it. Only the large
upstream clone (`data/sldt-semantic-models/`) is git-ignored and
reproducible via the fetch step.

---

## Setup

```bash
# 1. Create a virtualenv and install rdflib (needed from the parsing step on)
python3 -m venv .venv
.venv/bin/pip install "rdflib>=7.0"

# 2. Fetch the upstream models into data/ (git-ignored)
PYTHONPATH=src python3 -m sldt_analyzer.fetch
```

The fetch step needs `git` on the `PATH`; everything else is stdlib. The
parsing and graph steps require the virtualenv (for `rdflib`).

---

## Commands

```bash
# Fetch / resync the upstream models (no external dependency)
PYTHONPATH=src python3 -m sldt_analyzer.fetch          # clone, or resync if present
PYTHONPATH=src python3 -m sldt_analyzer.fetch --force  # clean re-clone
PYTHONPATH=src python3 -m sldt_analyzer.fetch -v       # verbose (git commands)

# Parse the .ttl files (needs rdflib -> use the venv)
.venv/bin/python scripts/test_parser.py                       # sanity check on a sample
PYTHONPATH=src .venv/bin/python -m sldt_analyzer.parser        # parse everything + summary

# Generate all JSON into web/data/graph/
PYTHONPATH=src .venv/bin/python -m sldt_analyzer.graph         # index + deps + issues + one per model

# Serve the site locally (from the PROJECT ROOT, not from web/)
python3 -m http.server 8000
#   then open  http://localhost:8000/web/
```

Updating the site locally = run `fetch` then `graph` (no commit needed to
test locally).

---

## The web UI

Three top-level tabs:

- **Home** — KPI cards per status (computed front-side from `index.json`) and
  a catalog grouped by `model_name`, filterable, with a per-model version
  selector. Clicking a KPI card filters the models that have ≥1 version of
  that status (without removing versions from the dropdowns).
- **Model Viewer** — pick a model (type-ahead) and a version, then explore
  the sub-tabs (Graph, Dependency graph, Generated docs, Release notes,
  Issues). The **Layout** selector is shared by the element graph and the
  dependency graph.
- **Issues** — aggregated quality view (see below).

---

## Runtime data flow

The deployed site is **100 % static**: no application server, no database,
no online Python. GitHub Pages only **serves files**. Everything is
pre-computed offline (locally or by the cron workflow) and frozen in the
repository at commit time. In the visitor's browser:

1. Load `web/index.html` (tabs Home / Model Viewer / Issues).
2. Load D3 from the CDN.
3. `fetch("data/graph/index.json")` → the model catalog;
   `fetch("data/graph/deps.json")` → the inter-model dependency map;
   `fetch("data/graph/issues.json")` → the pre-computed quality issues.
4. Render the active tab. Per-model element graphs are fetched on demand
   (`data/graph/<id>.json`). Generated docs are fetched from jsDelivr on
   demand and rendered in a sandboxed iframe.

`fetch` paths are **relative to the page**, so they are identical locally
(`http.server`) and online (Pages), with no configuration. The site
**never** parses `.ttl` at runtime — it reads JSON already produced by the
`fetch → parse → graph` chain.

---

## Quality issues

`issues.py` computes quality issues **offline** (because the site is static)
at the **model+version** granularity (key `name@version`). It is generated
by the `graph` command, so the weekly cron refreshes it automatically.

Six issue types are tracked:

| Type | Severity | Meaning |
|---|---|---|
| `deprecated_dep` | error | A **non-deprecated** model that **transitively** reaches a `deprecated` model. If `A` uses `B` uses `C` and `C` is deprecated, **both `A` and `B`** are flagged (BFS over the dependency graph; a shortest path is included). |
| `circular_dep` | error | The model is part of a dependency cycle (strongly connected component of size > 1; iterative Tarjan). |
| `unresolved_dep` | error | The model depends on a `name@version` that is absent from the catalog. |
| `missing_files` | error | A version folder missing its `.ttl` and/or `metadata.json`; or a model with no version folder at all (`version`). Detected by scanning the local clone. |
| `orphan_isolated` | warning | An element with no edge at all (no incoming nor outgoing). |
| `orphan_unreachable` | warning | An element not reachable from the Aspect by following edges. **Skipped** for Aspect-less models (`shared.*` libraries), where it would be a pure false positive. |

In the **Issues** tab, the **Status** checkboxes (the same four statuses as
Home; `release` checked by default) define the scope of the KPI counts.
Each KPI card is highlighted as soon as its count is > 0 (red for errors,
amber for warnings, dimmed when zero). Clicking a card lists the affected
model+version; a list row that resolves to a catalog entry opens it in the
Model Viewer directly on its **Issues** sub-tab.

`issues.json` is self-contained (it carries `name`/`version`/`family`/
`status` per entry), so **no quality computation happens in the browser**.

---

## Deployment & automation

- The site is **static**: the generated JSON is committed and served by
  **GitHub Pages → Deploy from branch** (`main`, folder `/ (root)`). Enable
  it once in *Settings → Pages*. URL:
  `https://anisbs.github.io/sldt-semantic-analyzer/web/`.
- `.github/workflows/update-models.yml` runs on a **weekly cron** (Monday
  06:00 UTC) and on **manual dispatch** (Actions tab). It resyncs the
  upstream models, regenerates `web/data/graph/` (including `issues.json`,
  which is wired into the `graph` step) and **pushes by itself** if anything
  changed → Pages rebuilds. **No manual commit** is needed to keep the site
  up to date.
- The workflow has **no `push` trigger**. Pushing to `main` therefore does
  *not* recompute anything — Pages simply redeploys the already-committed
  static files (live within ~1–2 min). Recomputation against fresh upstream
  data happens only on the cron, on a manual workflow run, or when you run
  `fetch` + `graph` locally.

---

## Conventions

- **Robust parsing:** a malformed `.ttl` must **never** crash the parser. It
  is silently ignored and logged at `WARNING` level (path + reason); parsing
  continues. Some old models are Latin-1 (an encoding fallback is handled).
- **Site language:** the entire UI of `web/index.html` (visible text, `lang`,
  title, placeholders, messages, labels) is in **English**. The displayed
  data (model names, descriptions) comes from the upstream `.ttl` files and
  is **not** translated.
- **D3 graph:** start simple (force-directed). Visual polish comes later.
- **Incremental delivery:** one small feature at a time; each feature is
  validated by actually running it before moving on. Documentation is kept
  in sync within the same change.

---

## Data source & license

The semantic models analyzed here belong to the upstream Eclipse Tractus-X
project
[`eclipse-tractusx/sldt-semantic-models`](https://github.com/eclipse-tractusx/sldt-semantic-models)
and are distributed under that project's own license. This analyzer only
fetches, parses and visualizes them; it does not redistribute the models
(the upstream clone is git-ignored). No separate license file is currently
set for this repository.

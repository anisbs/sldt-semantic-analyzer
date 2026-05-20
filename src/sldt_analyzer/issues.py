"""Feature 8 — Quality issues (pré-calculées hors-ligne).

Le site est 100 % statique : on ne calcule rien dans le navigateur. Ce module
parcourt les modèles parsés + le clone amont + la carte de dépendances déjà
agrégée par `graph.py`, et produit **`issues.json`** (consommé par l'onglet
Issues et le sous-onglet Issues du Model Viewer).

Granularité : **modèle+version** (clé `name@version`, même clé que
`deps.json`). Un dossier de version peut contenir plusieurs `.ttl` ; les issues
au niveau élément (orphelins, doc, structurelles) sont **agrégées** (union)
sur la clé.

Types d'issues (chaque entrée porte aussi une `description` détaillée qui sert
à l'aide en ligne de l'onglet Issues — Feature 8b).
"""

from __future__ import annotations

import logging
import re
from collections import deque
from pathlib import Path

from sldt_analyzer.parser import ParsedModel

logger = logging.getLogger("sldt.issues")


# Métadonnées des types d'issue (ordre = ordre d'affichage des KPI).
# `severity` : error -> KPI surligné rouge dès count>0 ; warning -> ambre.
# `description` : aide en ligne (panneau "About these checks" du front).
ISSUE_TYPES = [
    # ---- Existantes (dépendances inter-modèles) ----------------------------
    {"id": "deprecated_dep", "severity": "error",
     "label": "Uses a deprecated model (transitive)",
     "description": (
         "A non-deprecated model+version transitively depends on a deprecated "
         "one. If A uses B and B uses C, and C is deprecated, then both A and "
         "B are flagged. The shortest path to the deprecated target is shown.")},
    {"id": "circular_dep", "severity": "error",
     "label": "Circular dependency",
     "description": (
         "The model+version is part of a dependency cycle (strongly connected "
         "component of size > 1, or a self-loop). Cycles between Catena-X "
         "models break code-generation and resolution order.")},
    {"id": "unresolved_dep", "severity": "error",
     "label": "Unresolved dependency (not in catalog)",
     "description": (
         "The model references another `name@version` via an `ext-*:` prefix, "
         "but that target is absent from the catalog (the model+version is "
         "not present in the upstream repository).")},
    {"id": "missing_files", "severity": "error",
     "label": "Missing files (metadata.json / ttl / version)",
     "description": (
         "Filesystem scan: a version folder is missing a `.ttl` file, a "
         "`metadata.json`, or both; or the model folder has no version "
         "subfolder at all (`version`).")},
    {"id": "orphan_isolated", "severity": "warning",
     "label": "Orphan element (isolated, no edge)",
     "description": (
         "An element has no edge at all — neither incoming nor outgoing. "
         "It's defined in the .ttl but not connected to anything in the "
         "model graph.")},
    {"id": "orphan_unreachable", "severity": "warning",
     "label": "Orphan element (unreachable from Aspect)",
     "description": (
         "The element is connected somewhere but can't be reached by "
         "following edges starting from the model's `Aspect`. Ignored for "
         "`shared.*` models, which have no Aspect by design.")},

    # ---- Hygiène de catalogue (NEW) ----------------------------------------
    {"id": "outdated_dependency", "severity": "warning",
     "label": "Depends on an older version (newer release exists)",
     "description": (
         "The model+version depends on `dep@vX` while a newer **release** "
         "`dep@vY` (vY > vX) exists in the catalog. Consider migrating to "
         "the latest released version of the dependency.")},
    {"id": "older_release_exists", "severity": "warning",
     "label": "Older release while a newer release exists",
     "description": (
         "This model+version is marked `release` but a newer `release` of "
         "the same model exists. Older releases should typically be moved "
         "to `deprecated` once a newer one is published.")},
    {"id": "dep_on_draft", "severity": "warning",
     "label": "Depends on a draft model (unstable contract)",
     "description": (
         "The model+version depends on another model whose status is "
         "`draft`. Draft models have unstable contracts — consumers should "
         "wait for a `release` before taking a hard dependency.")},
    {"id": "dep_on_undefined", "severity": "warning",
     "label": "Depends on a model with undefined status",
     "description": (
         "The model+version depends on another model whose `metadata.json` "
         "is absent or malformed (status `undefined`). The lifecycle of the "
         "dependency is unknown.")},

    # ---- Structure du modèle (NEW) -----------------------------------------
    {"id": "aspect_without_properties", "severity": "error",
     "label": "Aspect with no properties",
     "description": (
         "An `Aspect` declares an empty `samm:properties` list — it carries "
         "no payload at all. Either it's a stub, or the properties list was "
         "accidentally emptied.")},
    {"id": "property_without_characteristic", "severity": "error",
     "label": "Property without characteristic",
     "description": (
         "A `Property` has no `samm:characteristic`. Properties without a "
         "characteristic can't be code-generated (no data type).")},
    {"id": "characteristic_without_datatype", "severity": "error",
     "label": "Characteristic without dataType",
     "description": (
         "A `Characteristic` (not a `Trait`, `Constraint` or other "
         "specialized subtype) is missing `samm:dataType`. Pure "
         "`Characteristic` elements must declare a datatype to be usable.")},
    {"id": "trait_without_constraint", "severity": "error",
     "label": "Trait without constraint",
     "description": (
         "A `samm-c:Trait` defines no `samm-c:constraint`. A Trait without "
         "constraints is pointless — it's equivalent to its underlying "
         "`baseCharacteristic`.")},
    {"id": "unused_property", "severity": "warning",
     "label": "Property defined but never used",
     "description": (
         "A `Property` is declared in the file but never referenced by any "
         "`samm:properties` collection (directly or via a bnode wrapper). "
         "Likely dead code.")},
    {"id": "bad_naming_property", "severity": "warning",
     "label": "Property/Event/Operation name not camelCase",
     "description": (
         "SAMM convention: Property/Event/Operation local names should be "
         "`camelCase` (start lowercase). Names violating the convention "
         "break tooling that relies on it.")},
    {"id": "bad_naming_type", "severity": "warning",
     "label": "Aspect/Entity/Characteristic name not PascalCase",
     "description": (
         "SAMM convention: Aspect/Entity/Characteristic/Constraint local "
         "names should be `PascalCase` (start uppercase).")},

    # ---- Documentation des éléments (NEW) ----------------------------------
    {"id": "missing_description", "severity": "warning",
     "label": "Element with no description",
     "description": (
         "The element has no `samm:description` literal at all. Generated "
         "documentation (HTML, JSON Schema description, OpenAPI) will be "
         "blank for this element.")},
    {"id": "missing_preferred_name", "severity": "warning",
     "label": "Element with no preferredName",
     "description": (
         "The element has no `samm:preferredName`. Tooling falls back to "
         "the raw local name, which is less readable for end users.")},
    {"id": "empty_description", "severity": "warning",
     "label": "Empty/whitespace description",
     "description": (
         "The element has a `samm:description` but it's empty or contains "
         "only whitespace. Same end-user impact as no description.")},
    {"id": "description_not_english", "severity": "warning",
     "label": "Description has no English (@en) version",
     "description": (
         "The element has at least one description, but none with a `@en` "
         "language tag. Catena-X conventions expect English as the primary "
         "language.")},

    # ---- Cohérence métadonnée / système de fichiers (NEW) ------------------
    {"id": "namespace_mismatch", "severity": "error",
     "label": "Model URN doesn't match file path",
     "description": (
         "The `@prefix : <urn:samm:io.catenax.X:V#>` in the .ttl doesn't "
         "match the on-disk path `io.catenax.X/V/`. Tooling that resolves "
         "by URN or by path will see different identities for the same "
         "file.")},
    {"id": "stem_name_mismatch", "severity": "warning",
     "label": "File name doesn't match Aspect name",
     "description": (
         "Convention: the `.ttl` file stem matches the local name of its "
         "`Aspect` (e.g. `BatteryPass.ttl` ↔ `:BatteryPass a samm:Aspect`). "
         "Files with no Aspect (`shared.*`) are not flagged.")},
    {"id": "non_semver_version", "severity": "warning",
     "label": "Version folder isn't a valid semver",
     "description": (
         "The version folder name doesn't match `MAJOR.MINOR.PATCH`. "
         "Sorting and 'newer release exists' checks fall back to string "
         "ordering for such versions.")},
    {"id": "meta_model_drift", "severity": "warning",
     "label": "Uses an older SAMM/BAMM meta-model version",
     "description": (
         "The model uses an older SAMM/BAMM meta-model version than the "
         "newest one observed in the SAME source (Catena-X and IDTA are "
         "compared independently — they have their own migration cadence). "
         "Consider migrating to the current meta-model (e.g. SAMM 2.0.0 → "
         "2.1.0 inside Catena-X).")},

    # ---- Gouvernance documentaire (NEW) ------------------------------------
    {"id": "missing_release_notes", "severity": "warning",
     "label": "Model has no RELEASE_NOTES.md",
     "description": (
         "The model folder has no `RELEASE_NOTES.md`. Consumers can't tell "
         "what changed between versions.")},
    {"id": "missing_release_date", "severity": "warning",
     "label": "Version not documented in RELEASE_NOTES.md",
     "description": (
         "The model has a `RELEASE_NOTES.md` but no `## [<version>]` "
         "section for this specific version. No release date is recorded.")},
    {"id": "missing_gen_docs", "severity": "warning",
     "label": "No generated HTML documentation (gen/)",
     "description": (
         "No `gen/<Stem>.html` artifact is present upstream for this "
         "model+version. The Generated docs sub-tab will be empty.")},
]

# Préfixes d'org reconnus pour les modèles de catalogue (Catena-X + IDTA).
# Utilisés pour : (i) filtrer les dossiers à scanner en FS et (ii) strip le
# préfixe pour obtenir le `model_name` court. Le segment URN attendu côté
# `namespace_mismatch` dépend de la source (voir `_EXPECTED_ORG_BY_SOURCE`).
_ORG_PREFIXES = ("io.catenax.", "io.openmanufacturing.", "io.admin-shell.idta.")
# Segment d'org à insérer dans l'URN attendu, par source.
_EXPECTED_ORG_BY_SOURCE = {
    "catenax": "io.catenax.",
    "idta":    "io.admin-shell.idta.",
}
# Sous-dossiers d'un dossier modèle qui ne sont PAS des dossiers de version.
_NOT_VERSION_DIRS = {"gen"}
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
_NAME_PROP = re.compile(r"^[a-z][a-zA-Z0-9]*$")    # camelCase
_NAME_TYPE = re.compile(r"^[A-Z][a-zA-Z0-9]*$")    # PascalCase

# Sous-types de `Characteristic` qui n'exigent PAS `samm:dataType` (Trait
# utilise baseCharacteristic+constraint, les *Constraint sont des contraintes
# pas des caractéristiques porteuses de données). Tout sous-type ABSENT de
# cette liste qui n'est pas non plus `Characteristic` "pur" est traité comme
# un sous-type spécialisé (Enumeration, Quantifiable, …) -> dataType requis.
_CHAR_NO_DATATYPE_REQUIRED = {
    "Trait",
    "Constraint", "RegularExpressionConstraint", "LengthConstraint",
    "RangeConstraint", "EncodingConstraint", "LocaleConstraint",
    "LanguageConstraint", "FixedPointConstraint",
}


def _key(name: str, version: str) -> str:
    return f"{name}@{version}"


def _strip_org(dirname: str) -> str:
    for org in _ORG_PREFIXES:
        if dirname.startswith(org):
            return dirname[len(org):]
    return dirname


def _vkey(v: str) -> tuple:
    """Tuple comparable d'une version. Repli (-1, version) pour non-semver
    afin que les non-semver soient TOUJOURS ordonnées avant un vrai semver
    (donc jamais 'plus récent' qu'un X.Y.Z), évitant des faux positifs."""
    if not _SEMVER_RE.match(v or ""):
        return (-1, v)
    return tuple(int(x) for x in v.split("."))


# ---- Issues au niveau dépendances (deprecated transitif / cycle / non résolu)

def _tarjan_scc(adj: dict[str, list[str]]) -> list[list[str]]:
    """SCC (Tarjan, **itératif** — pas de récursion : graphes profonds sûrs)."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    sccs: list[list[str]] = []
    counter = 0

    for start in list(adj.keys()):
        if start in index:
            continue
        work: list[tuple[str, int]] = [(start, 0)]
        while work:
            node, pi = work[-1]
            if pi == 0:
                index[node] = low[node] = counter
                counter += 1
                stack.append(node)
                on_stack.add(node)
            neighbours = adj.get(node, ())
            if pi < len(neighbours):
                work[-1] = (node, pi + 1)
                nb = neighbours[pi]
                if nb not in index:
                    work.append((nb, 0))
                elif nb in on_stack:
                    low[node] = min(low[node], index[nb])
            else:
                if low[node] == index[node]:
                    comp: list[str] = []
                    while True:
                        w = stack.pop()
                        on_stack.discard(w)
                        comp.append(w)
                        if w == node:
                            break
                    sccs.append(comp)
                work.pop()
                if work:
                    parent = work[-1][0]
                    low[parent] = min(low[parent], low[node])
    return sccs


def _shortest_path(adj: dict[str, list[str]], src: str, dst: str) -> list[str]:
    """BFS : un chemin (liste de clés) src->dst, ou [] si aucun."""
    if src == dst:
        return [src]
    prev: dict[str, str] = {src: src}
    q = deque([src])
    while q:
        u = q.popleft()
        for v in adj.get(u, ()):
            if v in prev:
                continue
            prev[v] = u
            if v == dst:
                path = [v]
                while path[-1] != src:
                    path.append(prev[path[-1]])
                return list(reversed(path))
            q.append(v)
    return []


def _dep_issues(
    deps_out: dict, status_by_key: dict[str, str]
) -> dict[str, dict]:
    """deprecated_dep / circular_dep / unresolved_dep / dep_on_draft /
    dep_on_undefined / outdated_dependency par clé `name@version`."""
    present = set(deps_out)
    adj: dict[str, list[str]] = {
        k: [d for d in v["deps"] if d in present] for k, v in deps_out.items()
    }
    out: dict[str, dict] = {}

    # unresolved : cibles hors catalogue.
    for k, v in deps_out.items():
        missing = sorted(d for d in v["deps"] if d not in present)
        if missing:
            out.setdefault(k, {})["unresolved_dep"] = {
                "count": len(missing), "detail": missing,
            }

    # dep_on_draft / dep_on_undefined : cibles présentes au statut concerné.
    for k, v in deps_out.items():
        drafts, unds = [], []
        for d in v["deps"]:
            st = status_by_key.get(d)
            if st == "draft":
                drafts.append(d)
            elif st == "undefined":
                unds.append(d)
        if drafts:
            out.setdefault(k, {})["dep_on_draft"] = {
                "count": len(drafts), "detail": sorted(drafts),
            }
        if unds:
            out.setdefault(k, {})["dep_on_undefined"] = {
                "count": len(unds), "detail": sorted(unds),
            }

    # outdated_dependency : dépend de `dep@vX` alors qu'une release `dep@vY`
    # plus récente existe au catalogue. Reposes sur status_by_key + _vkey.
    latest_release: dict[str, str] = {}
    for k, st in status_by_key.items():
        if st != "release":
            continue
        if "@" not in k:
            continue
        name, version = k.rsplit("@", 1)
        cur = latest_release.get(name)
        if cur is None or _vkey(version) > _vkey(cur):
            latest_release[name] = version
    for k, v in deps_out.items():
        outdated = []
        for d in v["deps"]:
            if "@" not in d:
                continue
            dn, dv = d.rsplit("@", 1)
            lr = latest_release.get(dn)
            if lr and _vkey(dv) < _vkey(lr):
                outdated.append({"target": d, "latest_release": f"{dn}@{lr}"})
        if outdated:
            outdated.sort(key=lambda x: x["target"])
            out.setdefault(k, {})["outdated_dependency"] = {
                "count": len(outdated), "detail": outdated,
            }

    # circular : membre d'une SCC de taille > 1 (ou auto-boucle).
    for comp in _tarjan_scc(adj):
        in_cycle = len(comp) > 1 or (
            len(comp) == 1 and comp[0] in adj.get(comp[0], ())
        )
        if not in_cycle:
            continue
        members = sorted(comp)
        for k in comp:
            others = [m for m in members if m != k] or [k]  # auto-boucle
            out.setdefault(k, {})["circular_dep"] = {
                "count": len(others), "detail": others,
            }

    # deprecated_dep : modèle NON déprécié atteignant (transitif) un déprécié.
    deprecated = {
        k for k in present if status_by_key.get(k) == "deprecated"
    }
    for k in present:
        if status_by_key.get(k) == "deprecated":
            continue
        seen = {k}
        q = deque([k])
        while q:
            u = q.popleft()
            for v in adj.get(u, ()):
                if v not in seen:
                    seen.add(v)
                    q.append(v)
        hit = sorted((seen & deprecated) - {k})
        if hit:
            out.setdefault(k, {})["deprecated_dep"] = {
                "count": len(hit),
                "detail": [
                    {"target": t, "path": _shortest_path(adj, k, t)}
                    for t in hit
                ],
            }
    return out


# ---- Issues au niveau catalogue (older release exists) -------------------

def _catalog_issues(
    models: list[ParsedModel], status_by_key: dict[str, str]
) -> dict[str, dict]:
    """older_release_exists : ce modèle+version est `release` mais une release
    plus récente du même `model_name` existe au catalogue."""
    # name -> liste des versions release
    by_name: dict[str, list[str]] = {}
    for k, st in status_by_key.items():
        if st != "release" or "@" not in k:
            continue
        name, version = k.rsplit("@", 1)
        by_name.setdefault(name, []).append(version)

    out: dict[str, dict] = {}
    for name, versions in by_name.items():
        if len(versions) < 2:
            continue
        latest = max(versions, key=_vkey)
        lk = _vkey(latest)
        for v in versions:
            if _vkey(v) < lk:
                out[_key(name, v)] = {
                    "older_release_exists": {
                        "count": 1,
                        "detail": [{"latest_release": f"{name}@{latest}"}],
                    }
                }
    return out


# ---- Issues au niveau éléments (orphelins + doc + structure) -------------

def _element_issues(model: ParsedModel) -> dict[str, list[dict]]:
    """Issues élément par élément pour UN .ttl. La clé du dict est l'id
    d'issue ; la valeur est une liste de `{name, kind, file, ...}`."""
    file = Path(model.path).stem
    deg: dict[str, int] = {e.urn: 0 for e in model.elements}
    adj: dict[str, list[str]] = {e.urn: [] for e in model.elements}
    out_edges: dict[str, dict[str, int]] = {
        e.urn: {} for e in model.elements
    }
    for edge in model.edges:
        if edge.source in deg:
            deg[edge.source] += 1
        if edge.target in deg:
            deg[edge.target] += 1
        if edge.source in adj:
            adj[edge.source].append(edge.target)
            out_edges[edge.source][edge.label] = (
                out_edges[edge.source].get(edge.label, 0) + 1
            )

    res: dict[str, list[dict]] = {iid: [] for iid in (
        "orphan_isolated", "orphan_unreachable",
        "missing_description", "missing_preferred_name", "empty_description",
        "description_not_english",
        "aspect_without_properties", "property_without_characteristic",
        "characteristic_without_datatype", "trait_without_constraint",
        "unused_property", "bad_naming_property", "bad_naming_type",
    )}

    def stub(e):
        return {"name": e.name, "kind": e.kind, "file": file}

    # -- orphelins (logique d'origine)
    for e in model.elements:
        if deg.get(e.urn, 0) == 0:
            res["orphan_isolated"].append(stub(e))

    aspects = [e.urn for e in model.elements if e.kind == "Aspect"]
    if aspects:  # sans Aspect (shared.*) : check non pertinent -> ignoré
        seen = set(aspects)
        q = deque(aspects)
        while q:
            u = q.popleft()
            for v in adj.get(u, ()):
                if v not in seen:
                    seen.add(v)
                    q.append(v)
        for e in model.elements:
            if e.urn not in seen:
                res["orphan_unreachable"].append(stub(e))

    # -- doc & structure & naming
    empty_aspects = set(model.aspects_empty_properties)
    unused = set(model.unused_property_urns)
    for e in model.elements:
        # Documentation
        if e.description is None:
            res["missing_description"].append(stub(e))
        elif not str(e.description).strip():
            res["empty_description"].append(stub(e))
        if e.preferred_name is None:
            res["missing_preferred_name"].append(stub(e))
        if e.description_langs and "en" not in e.description_langs:
            res["description_not_english"].append(stub(e))

        # Naming convention
        if e.kind in {"Property", "AbstractProperty", "Event", "Operation"}:
            if not _NAME_PROP.match(e.name or ""):
                res["bad_naming_property"].append(stub(e))
        elif e.kind in {"Aspect", "Entity", "AbstractEntity",
                        "Characteristic", "Constraint"}:
            if not _NAME_TYPE.match(e.name or ""):
                res["bad_naming_type"].append(stub(e))

        # Structurelles
        if e.kind == "Aspect" and e.urn in empty_aspects:
            res["aspect_without_properties"].append(stub(e))
        if e.kind == "Property":
            if "characteristic" not in out_edges.get(e.urn, {}):
                res["property_without_characteristic"].append(stub(e))
            if e.urn in unused:
                res["unused_property"].append(stub(e))
        if e.kind == "Characteristic":
            # Trait n'exige pas dataType, mais exige constraint(s).
            if "Trait" in e.types:
                if "constraint" not in out_edges.get(e.urn, {}):
                    res["trait_without_constraint"].append(stub(e))
            else:
                # Sous-type exigeant dataType (pur Characteristic, ou subtype
                # Enumeration/Quantifiable/…). On exempte uniquement les types
                # listés dans _CHAR_NO_DATATYPE_REQUIRED (Trait + Constraint*).
                requires_dt = not any(
                    t in _CHAR_NO_DATATYPE_REQUIRED for t in e.types
                )
                if requires_dt and "dataType" not in out_edges.get(e.urn, {}):
                    res["characteristic_without_datatype"].append(stub(e))

    return res


# ---- Issues fichiers (scan du clone amont) -------------------------------

def _missing_files(models_dir: Path, source: str = "catenax") -> dict[str, list[str]]:
    """Scan disque : par clé `name@version`, fichiers manquants parmi
    `metadata.json` / `ttl`. Modèle sans aucun dossier de version -> clé
    `name@` avec `version`. `metadata.json` est attendu **uniquement** pour
    `catenax` (IDTA n'a pas ce fichier par convention amont — ne pas le
    flagger pour éviter ~80 faux positifs). Jamais d'exception."""
    out: dict[str, list[str]] = {}
    try:
        model_dirs = sorted(
            p for p in Path(models_dir).iterdir()
            if p.is_dir() and p.name.startswith(_ORG_PREFIXES)
        )
    except Exception as exc:  # noqa: BLE001 — dossier illisible : on logge
        logger.warning("Scan missing_files impossible (%s) : %s",
                        models_dir, exc)
        return out

    expect_metadata = (source == "catenax")

    for mdir in model_dirs:
        name = _strip_org(mdir.name)
        version_dirs = []
        try:
            subs = sorted(d for d in mdir.iterdir() if d.is_dir())
        except Exception:  # noqa: BLE001
            continue
        for d in subs:
            if d.name in _NOT_VERSION_DIRS:
                continue
            try:
                names = {f.name for f in d.iterdir() if f.is_file()}
            except Exception:  # noqa: BLE001
                names = set()
            has_ttl = any(n.endswith(".ttl") for n in names)
            has_meta = "metadata.json" in names
            if not (has_ttl or (expect_metadata and has_meta)):
                continue
            version_dirs.append(d.name)
            miss = []
            if not has_ttl:
                miss.append("ttl")
            if expect_metadata and not has_meta:
                miss.append("metadata.json")
            if miss:
                out[_key(name, d.name)] = miss
        if not version_dirs:
            out[_key(name, "")] = ["version"]
    return out


def _non_semver_versions(models_dir: Path) -> list[tuple[str, str]]:
    """Dossiers de version dont le nom n'est pas un semver `X.Y.Z`.
    Retourne `[(name, version_dir), ...]`. Jamais d'exception."""
    found: list[tuple[str, str]] = []
    try:
        model_dirs = sorted(
            p for p in Path(models_dir).iterdir()
            if p.is_dir() and p.name.startswith(_ORG_PREFIXES)
        )
    except Exception:  # noqa: BLE001
        return found
    for mdir in model_dirs:
        name = _strip_org(mdir.name)
        try:
            subs = sorted(d for d in mdir.iterdir() if d.is_dir())
        except Exception:  # noqa: BLE001
            continue
        for d in subs:
            if d.name in _NOT_VERSION_DIRS:
                continue
            if not _SEMVER_RE.match(d.name):
                found.append((name, d.name))
    return found


# ---- Issues métadonnée (URN ↔ fichier, stem, méta-modèle, doc) ----------

def _expected_urn_segment(name: str, version: str, source: str = "catenax") -> str:
    """Segment urn attendu pour `name@version` (sans famille ni `#`),
    dépendant de la source (catenax -> `io.catenax.X:V`, idta ->
    `io.admin-shell.idta.X:V`). Source inconnue -> on retombe sur catenax."""
    org = _EXPECTED_ORG_BY_SOURCE.get(source, "io.catenax.")
    return f"{org}{name}:{version}"


def _model_level_issues(
    models: list[ParsedModel],
) -> dict[str, dict[str, dict]]:
    """Issues calculées par .ttl mais au niveau clé `name@version` :
    namespace_mismatch / stem_name_mismatch / meta_model_drift /
    missing_release_notes / missing_release_date / missing_gen_docs."""
    out: dict[str, dict[str, dict]] = {}

    # Latest meta-model par (source, famille) — `meta_model_drift`. Calculé
    # par source pour ne pas pénaliser Catena-X (SAMM 2.1.0) sous prétexte
    # qu'IDTA est passé à SAMM 2.2.0 : les deux référentiels ont leur propre
    # cadence de migration et le drift n'a de sens qu'à l'intérieur d'un
    # même référentiel.
    latest_meta: dict[tuple[str, str], str] = {}
    for m in models:
        if not m.meta_model:
            continue
        # ex. "SAMM 2.1.0"
        try:
            fam, ver = m.meta_model.split(" ", 1)
        except ValueError:
            continue
        key = (m.source, fam)
        cur = latest_meta.get(key)
        if cur is None or _vkey(ver) > _vkey(cur):
            latest_meta[key] = ver

    for m in models:
        k = _key(m.model_name, m.model_version)
        bucket = out.setdefault(k, {})

        # namespace_mismatch : segment urn vs chemin disque.
        # m.namespace ex. "urn:samm:io.catenax.batch:2.0.0#" (catenax)
        #                "urn:samm:io.admin-shell.idta.shared:3.1.0#" (idta)
        expected = _expected_urn_segment(m.model_name, m.model_version, m.source)
        if expected not in (m.namespace or ""):
            bucket.setdefault("namespace_mismatch", {"count": 0, "detail": []})
            bucket["namespace_mismatch"]["detail"].append({
                "file": Path(m.path).stem,
                "namespace": m.namespace or "",
                "expected_segment": expected,
            })
            bucket["namespace_mismatch"]["count"] += 1

        # stem_name_mismatch : nom de fichier vs nom de l'Aspect.
        # Modèles sans Aspect (shared.*) -> non applicable.
        aspect = next((e for e in m.elements if e.kind == "Aspect"), None)
        if aspect is not None:
            stem = Path(m.path).stem
            if stem != aspect.name:
                bucket.setdefault("stem_name_mismatch", {"count": 0, "detail": []})
                bucket["stem_name_mismatch"]["detail"].append({
                    "file": stem, "aspect": aspect.name,
                })
                bucket["stem_name_mismatch"]["count"] += 1

        # meta_model_drift : version SAMM/BAMM utilisée < dernière de la
        # MÊME source (et famille). Catena-X et IDTA ont chacun leur "latest".
        if m.meta_model:
            try:
                fam, ver = m.meta_model.split(" ", 1)
                latest = latest_meta.get((m.source, fam))
                if latest and _vkey(ver) < _vkey(latest):
                    bucket["meta_model_drift"] = {
                        "count": 1,
                        "detail": [{"using": m.meta_model,
                                    "latest": f"{fam} {latest}"}],
                    }
            except ValueError:
                pass

        # missing_release_notes : pas de RELEASE_NOTES.md du tout.
        rn = Path(m.path).parent.parent / "RELEASE_NOTES.md"
        if not rn.is_file():
            bucket["missing_release_notes"] = {
                "count": 1, "detail": [str(rn)],
            }
        else:
            # missing_release_date : RELEASE_NOTES.md présent mais pas de
            # section `## [<version>]` (release_date est None).
            if m.release_date is None:
                bucket.setdefault("missing_release_date", {
                    "count": 1, "detail": [m.model_version or "?"],
                })

        # missing_gen_docs : pas de gen/<Stem>.html voisin.
        gen_html = Path(m.path).parent / "gen" / (Path(m.path).stem + ".html")
        if not gen_html.is_file():
            bucket.setdefault("missing_gen_docs", {
                "count": 1, "detail": [Path(m.path).stem],
            })

    # Nettoyage : on retire les entrées vides (clé sans aucune issue).
    return {k: v for k, v in out.items() if v}


# ---- Assemblage ----------------------------------------------------------

def build_issues(
    models: list[ParsedModel],
    raw_models: list[ParsedModel],
    dirs_by_source: list[tuple[Path, str]],
    deps_out: dict,
) -> dict:
    """Construit le dict `issues.json` complet.

    Deux listes en entrée :
      - `models`     : ParsedModel FUSIONNÉS (1 par modèle+version, cf.
        `graph.merge_models`). Utilisés pour les checks-éléments (orphelins,
        doc, naming, structure, `unused_property` — qui ne peut être correct
        qu'à l'échelle du modèle complet) et pour le `_catalog_issues`.
      - `raw_models` : ParsedModel BRUTS (1 par .ttl). Utilisés pour les
        checks au niveau fichier : `namespace_mismatch`, `stem_name_mismatch`,
        `meta_model_drift`, `missing_release_*`, `missing_gen_docs` — où
        chaque `.ttl` est évalué individuellement (un dossier multi-`.ttl`
        peut avoir des stems qui matchent l'Aspect porteur mais pas les
        helpers `*_shared.ttl`, ce qui est correct).

    `dirs_by_source` : liste de `(models_dir, source_key)` à scanner pour
    les checks FS (missing_files, non_semver_version). Le tag de source
    propagé aux issues conditionne aussi le préfixe URN attendu pour
    `namespace_mismatch` (déduit par `m.source` sur chaque ParsedModel)."""
    # Statut, famille, source par clé.
    status_by_key: dict[str, str] = {}
    family_by_key: dict[str, str] = {}
    source_by_key: dict[str, str] = {}
    for m in models:
        k = _key(m.model_name, m.model_version)
        if k not in status_by_key or status_by_key[k] == "undefined":
            status_by_key[k] = m.status
        if not family_by_key.get(k):
            family_by_key[k] = m.model_family
        if not source_by_key.get(k):
            source_by_key[k] = m.source

    # On enrichit status_by_key avec les clés présentes dans deps_out qui ne
    # sont pas dans models (cas limite : un modèle parsé mais sans path normal)
    # pour les checks de dépendance ; sans effet sur les autres checks.
    for k in deps_out:
        status_by_key.setdefault(k, "undefined")

    # Accumulateur : clé -> { issue_id -> {count, detail} }
    acc: dict[str, dict] = {}

    def add(key: str, issue_id: str, count: int, detail) -> None:
        if count <= 0:
            return
        acc.setdefault(key, {})[issue_id] = {"count": count, "detail": detail}

    # 1) Dépendances inter-modèles (6 types).
    for k, issues in _dep_issues(deps_out, status_by_key).items():
        for iid, payload in issues.items():
            add(k, iid, payload["count"], payload["detail"])

    # 2) Older release (catalogue).
    for k, issues in _catalog_issues(models, status_by_key).items():
        for iid, payload in issues.items():
            add(k, iid, payload["count"], payload["detail"])

    # 3) Issues éléments (orphelins + doc + struct), calculées sur le
    #    ParsedModel FUSIONNÉ -> 1 entrée par modèle+version, pas de
    #    faux positifs IDTA sur les Property du `_shared.ttl`.
    for m in models:
        k = _key(m.model_name, m.model_version)
        for iid, items in _element_issues(m).items():
            add(k, iid, len(items), items)

    # 4) Fichiers manquants (scan disque, par source). On propage la source
    #    du scan vers `source_by_key` pour les clés `name@` (modèles sans
    #    aucun dossier de version, donc sans ParsedModel associé).
    for d, src in dirs_by_source:
        for k, miss in _missing_files(d, src).items():
            source_by_key.setdefault(k, src)
            add(k, "missing_files", len(miss), miss)

    # 5) Versions non semver (par source).
    for d, src in dirs_by_source:
        for name, vdir in _non_semver_versions(d):
            k = _key(name, vdir)
            source_by_key.setdefault(k, src)
            add(k, "non_semver_version", 1, [vdir])

    # 6) Issues niveau fichier (URN / stem / meta-model drift / docs).
    #    Calculées sur la liste BRUTE : un fichier mal nommé du groupe est
    #    encore flaggé individuellement (utile pour identifier le .ttl
    #    fautif dans un dossier multi-fichiers).
    for k, issues in _model_level_issues(raw_models).items():
        for iid, payload in issues.items():
            add(k, iid, payload["count"], payload["detail"])

    # Sérialisation : on enrichit chaque clé avec name/version/family/source/
    # status pour rendre issues.json autonome.
    out_models: dict[str, dict] = {}
    for k in sorted(acc):
        at = k.rfind("@")
        name = k[:at] if at >= 0 else k
        version = k[at + 1:] if at >= 0 else ""
        issues = acc[k]
        out_models[k] = {
            "name": name,
            "version": version,
            "family": family_by_key.get(k, ""),
            "source": source_by_key.get(k, "unknown"),
            "status": status_by_key.get(k, "undefined"),
            "total": sum(v["count"] for v in issues.values()),
            "issues": issues,
        }
    return {"issue_types": ISSUE_TYPES, "models": out_models}

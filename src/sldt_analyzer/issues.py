"""Feature 8 — Quality issues (pré-calculées hors-ligne).

Le site est 100 % statique : on ne calcule rien dans le navigateur. Ce module
parcourt les modèles parsés + le clone amont + la carte de dépendances déjà
agrégée par `graph.py`, et produit **`issues.json`** (consommé par l'onglet
Issues et le sous-onglet Issues du Model Viewer).

Granularité : **modèle+version** (clé `name@version`, même clé que
`deps.json`). Un dossier de version peut contenir plusieurs `.ttl` ; les issues
au niveau élément (orphelins) sont **agrégées** (union) sur la clé.

6 types d'issues :
  - `deprecated_dep`     : modèle NON déprécié qui (transitivement) utilise un
                           modèle déprécié. A uses B uses C ; si C est
                           `deprecated`, A **et** B sont signalés.
  - `circular_dep`       : modèle membre d'un cycle de dépendances (SCC>1).
  - `unresolved_dep`     : dépend d'un `name@version` absent du catalogue.
  - `missing_files`      : dossier de version sans `.ttl` et/ou sans
                           `metadata.json` ; modèle sans aucun dossier de
                           version (`version`).
  - `orphan_isolated`    : élément sans aucune arête (entrante ni sortante).
  - `orphan_unreachable` : élément non atteignable depuis l'Aspect racine
                           (en suivant les arêtes). **Ignoré** pour les
                           modèles sans Aspect (`shared.*`) — sinon tous
                           leurs éléments seraient signalés (faux positifs).

Convention du projet : aucune exception ne doit faire crasher la chaîne. Les
cas inattendus sont ignorés silencieusement (au pire un type d'issue vide).
"""

from __future__ import annotations

import logging
from collections import deque
from pathlib import Path

from sldt_analyzer.parser import ParsedModel

logger = logging.getLogger("sldt.issues")

# Métadonnées des types (ordre = ordre d'affichage des KPI). `severity` :
# error -> KPI surligné rouge dès count>0 ; warning -> ambre.
ISSUE_TYPES = [
    {"id": "deprecated_dep", "severity": "error",
     "label": "Uses a deprecated model (transitive)"},
    {"id": "circular_dep", "severity": "error",
     "label": "Circular dependency"},
    {"id": "unresolved_dep", "severity": "error",
     "label": "Unresolved dependency (not in catalog)"},
    {"id": "missing_files", "severity": "error",
     "label": "Missing files (metadata.json / ttl / version)"},
    {"id": "orphan_isolated", "severity": "warning",
     "label": "Orphan element (isolated, no edge)"},
    {"id": "orphan_unreachable", "severity": "warning",
     "label": "Orphan element (unreachable from Aspect)"},
]

_ORG_PREFIXES = ("io.catenax.", "io.openmanufacturing.")
# Sous-dossiers d'un dossier modèle qui ne sont PAS des dossiers de version.
_NOT_VERSION_DIRS = {"gen"}


def _key(name: str, version: str) -> str:
    return f"{name}@{version}"


def _strip_org(dirname: str) -> str:
    for org in _ORG_PREFIXES:
        if dirname.startswith(org):
            return dirname[len(org):]
    return dirname


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
        # work stack : (node, iterator-position dans ses voisins)
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
    """deprecated_dep / circular_dep / unresolved_dep par clé `name@version`.

    `deps_out` = sortie de graph.py : { "name@version": {..., "deps": [...]} }.
    Une cible de `deps` absente de `deps_out` = hors catalogue (non résolue).
    """
    present = set(deps_out)
    # Arêtes vers cibles présentes (pour cycles & atteignabilité deprecated).
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
            continue  # un modèle déprécié n'est pas signalé pour ça
        # BFS : toutes les clés atteignables depuis k.
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


# ---- Issues au niveau éléments (orphelins) -------------------------------

def _orphan_issues(model: ParsedModel) -> dict[str, list[dict]]:
    """Pour un .ttl : éléments isolés et éléments non atteignables depuis
    l'Aspect. `orphan_unreachable` ignoré si le modèle n'a pas d'Aspect."""
    file = Path(model.path).stem
    deg: dict[str, int] = {e.urn: 0 for e in model.elements}
    adj: dict[str, list[str]] = {e.urn: [] for e in model.elements}
    for edge in model.edges:
        if edge.source in deg:
            deg[edge.source] += 1
        if edge.target in deg:
            deg[edge.target] += 1
        if edge.source in adj:
            adj[edge.source].append(edge.target)

    isolated = [
        {"name": e.name, "kind": e.kind, "file": file}
        for e in model.elements if deg.get(e.urn, 0) == 0
    ]

    unreachable: list[dict] = []
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
        unreachable = [
            {"name": e.name, "kind": e.kind, "file": file}
            for e in model.elements if e.urn not in seen
        ]
    return {"orphan_isolated": isolated, "orphan_unreachable": unreachable}


# ---- Issues fichiers (scan du clone amont) -------------------------------

def _missing_files(models_dir: Path) -> dict[str, list[str]]:
    """Scan disque : par clé `name@version`, fichiers manquants parmi
    `metadata.json` / `ttl`. Modèle sans aucun dossier de version -> clé
    `name@` avec `version`. Jamais d'exception."""
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

    for mdir in model_dirs:
        name = _strip_org(mdir.name)
        version_dirs = []
        try:
            subs = sorted(d for d in mdir.iterdir() if d.is_dir())
        except Exception:  # noqa: BLE001 — modèle illisible : on ignore
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
            if not (has_ttl or has_meta):
                continue  # ni .ttl ni metadata.json : pas un dossier version
            version_dirs.append(d.name)
            miss = []
            if not has_ttl:
                miss.append("ttl")
            if not has_meta:
                miss.append("metadata.json")
            if miss:
                out[_key(name, d.name)] = miss
        if not version_dirs:
            out[_key(name, "")] = ["version"]
    return out


# ---- Assemblage ----------------------------------------------------------

def build_issues(
    models: list[ParsedModel], models_dir: Path, deps_out: dict
) -> dict:
    """Construit le dict `issues.json` complet."""
    # Statut & famille par clé (cohérents dans un dossier de version ; en cas
    # d'écart on préfère une valeur définie).
    status_by_key: dict[str, str] = {}
    family_by_key: dict[str, str] = {}
    for m in models:
        k = _key(m.model_name, m.model_version)
        if k not in status_by_key or status_by_key[k] == "undefined":
            status_by_key[k] = m.status
        if not family_by_key.get(k):
            family_by_key[k] = m.model_family

    # Accumulateur : clé -> { issue_id -> {count, detail} }
    acc: dict[str, dict] = {}

    def add(key: str, issue_id: str, count: int, detail) -> None:
        if count <= 0:
            return
        acc.setdefault(key, {})[issue_id] = {"count": count, "detail": detail}

    # Dépendances (deprecated transitif / cycle / non résolu).
    for k, issues in _dep_issues(deps_out, status_by_key).items():
        for iid, payload in issues.items():
            add(k, iid, payload["count"], payload["detail"])

    # Orphelins (agrégés par clé, union des .ttl du dossier de version).
    orphan_acc: dict[str, dict[str, list]] = {}
    for m in models:
        k = _key(m.model_name, m.model_version)
        o = _orphan_issues(m)
        bucket = orphan_acc.setdefault(
            k, {"orphan_isolated": [], "orphan_unreachable": []})
        bucket["orphan_isolated"].extend(o["orphan_isolated"])
        bucket["orphan_unreachable"].extend(o["orphan_unreachable"])
    for k, b in orphan_acc.items():
        for iid in ("orphan_isolated", "orphan_unreachable"):
            add(k, iid, len(b[iid]), b[iid])

    # Fichiers manquants (scan disque).
    for k, miss in _missing_files(models_dir).items():
        add(k, "missing_files", len(miss), miss)

    # Sérialisation : on enrichit chaque clé avec name/version/family/status
    # pour rendre issues.json autonome (l'onglet Issues n'a pas à recouper).
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
            "status": status_by_key.get(k, "undefined"),
            "total": sum(v["count"] for v in issues.values()),
            "issues": issues,
        }
    return {"issue_types": ISSUE_TYPES, "models": out_models}

"""Feature 3 — Modèle de graphe (JSON nœuds/arêtes pour D3).

Approche « simple » (option 1) : un graphe **par modèle**, sans résoudre les
références inter-fichiers (`ext-*:`). Le format JSON produit est directement
consommable par un force-directed D3 (feature 4).

Multi-sources (Feature 3b) : on agrège dans la même sortie les modèles des
référentiels Catena-X et IDTA. Chaque entrée porte un champ `source` pour
permettre au front (Home/Search/Issues) de distinguer les deux. Aucune
collision de clé `name@version` entre les sources (vérifié sur les données).

Sortie : un dossier `web/data/graph/` contenant
  - `index.json`      : liste des modèles { id, source, name, status, ... }
  - `<id>.json`       : un graphe { meta, nodes[], links[] } par modèle
  - `deps.json`       : carte name@version -> deps (avec source des deps)
  - `search.json`     : index plat des éléments cherchables (Feature 6C)
  - `issues.json`     : qualité pré-calculée (Feature 8)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from sldt_analyzer.issues import build_issues
from sldt_analyzer.parser import (
    Dependency, Edge, Element, ParsedModel, parse_directory,
)

logger = logging.getLogger("sldt.graph")


@dataclass(frozen=True)
class Source:
    key: str             # catenax | idta
    models_dir: Path     # où vivent les .ttl clonés en local
    repo: str            # owner/repo GitHub (pour les URLs jsDelivr côté front)


# Registre des sources connues, miroir de `fetch.SOURCES`. Ordre = ordre de
# parcours (Catena-X en premier => ses IDs de fichier de graphe restent en
# tête comme avant, le diff git reste minimal).
SOURCES: tuple[Source, ...] = (
    Source("catenax", Path("data/sldt-semantic-models"),
           "eclipse-tractusx/sldt-semantic-models"),
    Source("idta",    Path("data/smt-semantic-models"),
           "admin-shell-io/smt-semantic-models"),
)

# Sortie sous web/ pour être commitée et servie par GitHub Pages (le gros
# clone des .ttl reste, lui, sous data/ qui est git-ignored).
DEFAULT_OUT_DIR = Path("web/data/graph")


def _model_id(model: ParsedModel, models_dir: Path) -> str:
    """Identifiant unique par MODÈLE+VERSION (les .ttl frères d'un même dossier
    de version sont fusionnés, cf. `merge_models`) : 'io.catenax.batch__4.0.0'
    ou 'io.admin-shell.idta.batterypass.circularity__1.0.0'.

    Calculé depuis le chemin du .ttl (porteur d'Aspect en priorité) : on prend
    le dossier de version (= parent direct) et son parent (= dossier de
    modèle), pour rester en phase avec la convention amont
    `<io.X.modèle>/<version>/`."""
    try:
        rel = Path(model.path).resolve().relative_to(models_dir.resolve())
    except ValueError:  # chemin hors du dossier modèles : repli sur le nom
        return Path(model.path).stem
    # rel = io.<...>/<version>/<File>.ttl  -> id = io.<...>__<version>
    parts = rel.parts
    if len(parts) >= 3:
        return f"{parts[0]}__{parts[1]}"
    return str(rel.with_suffix("")).replace("/", "__")


# Artefacts générés voisins du .ttl : <dir>/gen/<Stem><suffixe>. Servis tels
# quels par l'amont (jsDelivr), jamais hébergés ici.
_GEN_SUFFIXES = {
    "html": ".html",          # doc HTML autonome (SVG inline)
    "schema": "-schema.json", # JSON Schema
    "payload": ".json",       # payload exemple
    "openapi": ".yml",        # OpenAPI
}


def _gen_artifacts(model: ParsedModel, models_dir: Path) -> dict:
    """Quels artefacts `gen/` existent en amont pour ce .ttl.

    Retourne `{ "base": "<chemin-repo-relatif>/gen/<Stem>", "html": bool,
    "schema": bool, "payload": bool, "openapi": bool }` ; `base` est en
    séparateurs POSIX (URL jsDelivr). Repli sûr si chemin inattendu.

    Fallback (cas IDTA `batterypass.*`) : si aucun `gen/<Stem>.html` ne
    matche le nom du .ttl porteur (ex. `.ttl=Circularity` mais doc générée
    en `CircularityBattery.html`), on scanne le dossier `gen/` et on prend
    le premier `<X>.html` trouvé comme nouveau Stem — sinon ~10 modèles
    IDTA seraient à tort `gen.html=False`."""
    try:
        rel = Path(model.path).resolve().relative_to(
            models_dir.resolve()
        ).with_suffix("")
    except ValueError:
        return {"base": None, **{k: False for k in _GEN_SUFFIXES}}
    gen_dir = models_dir / rel.parent / "gen"
    gen_rel = rel.parent / "gen" / rel.name

    # Path-based first (Catena-X convention : <Stem>.ttl ↔ gen/<Stem>.*).
    if (models_dir / (str(gen_rel) + ".html")).is_file():
        stem_dir = gen_rel
    else:
        # Fallback : pick the first .html present in gen/ as the new stem.
        # Convention IDTA met parfois un suffixe métier dans le nom de la doc
        # (`Circularity.ttl` → `gen/CircularityBattery.html`).
        try:
            htmls = sorted(gen_dir.glob("*.html"))
        except OSError:
            htmls = []
        if htmls:
            stem_dir = rel.parent / "gen" / htmls[0].stem
        else:
            stem_dir = gen_rel  # nothing found — keep the original base
    out = {"base": stem_dir.as_posix()}
    for key, suf in _GEN_SUFFIXES.items():
        out[key] = (models_dir / (str(stem_dir) + suf)).is_file()
    return out


def merge_models(models: list[ParsedModel]) -> list[ParsedModel]:
    """Fusionne les `ParsedModel` partageant `(source, model_name, model_version)`
    en un seul ParsedModel agrégé. Un dossier de version contient typiquement
    un Aspect + N helpers dans le MÊME namespace (`Circularity.ttl` +
    `Circularity_shared.ttl` + `Namespace.ttl` côté IDTA, ou 6 Aspects qui
    cohabitent dans `material_accounting/` côté Catena-X) ; ces fichiers
    décrivent UN seul modèle morcelé. La fusion produit le graphe complet.

    Choix de design :
      - `path` du fusionné = path du **porteur d'Aspect** (1er si plusieurs ;
        1er du groupe si aucun Aspect). Sert ensuite à `_gen_artifacts` et aux
        chemins relatifs.
      - éléments dédupliqués par `urn` (on garde le premier vu).
      - arêtes dédupliquées par `(source, target, label)`.
      - dépendances dédupliquées par `(name, version)`.
      - `used_property_urns` = union (utilisé pour recalculer
        `unused_property_urns` à l'échelle du groupe).
      - `aspects_empty_properties` = union (un Aspect n'est défini que dans
        un seul .ttl ; l'union ne dédoublonne pas en pratique).
      - statut/release_*/meta_model/dependencies viennent du leader (porteur
        d'Aspect)."""
    groups: dict[tuple[str, str, str], list[ParsedModel]] = {}
    for m in models:
        key = (m.source, m.model_name, m.model_version)
        groups.setdefault(key, []).append(m)

    out: list[ParsedModel] = []
    for key, grp in groups.items():
        if len(grp) == 1:
            out.append(grp[0])
            continue

        with_aspect = [m for m in grp if any(e.kind == "Aspect" for e in m.elements)]
        leader = with_aspect[0] if with_aspect else grp[0]

        # Union éléments par urn (premier vu gagne).
        elems_by_urn: dict[str, Element] = {}
        for m in grp:
            for e in m.elements:
                elems_by_urn.setdefault(e.urn, e)

        # Union arêtes par (source, target, label).
        seen_edges: set[tuple[str, str, str]] = set()
        edges: list[Edge] = []
        for m in grp:
            for ed in m.edges:
                k = (ed.source, ed.target, ed.label)
                if k in seen_edges:
                    continue
                seen_edges.add(k)
                edges.append(ed)

        # Union dépendances par (name, version).
        seen_deps: set[tuple[str, str]] = set()
        deps: list[Dependency] = []
        for m in grp:
            for d in m.dependencies:
                dk = (d.name, d.version)
                if dk in seen_deps:
                    continue
                seen_deps.add(dk)
                deps.append(d)

        # Property URN référencées par toutes les `samm:properties` du groupe.
        merged_used: set[str] = set()
        for m in grp:
            merged_used.update(m.used_property_urns)
        # Recalcul `unused_property_urns` à l'échelle du groupe : une Property
        # n'est "unused" QUE si aucun .ttl du groupe ne la référence. Ça
        # élimine les faux positifs IDTA (Property dans `_shared.ttl`,
        # référencée par l'Aspect dans le .ttl frère).
        merged_unused = sorted(
            e.urn for e in elems_by_urn.values()
            if e.kind == "Property" and e.urn not in merged_used
        )

        # Empty-aspects : union (un Aspect ne vit que dans un .ttl, pas de
        # collision possible).
        merged_empty: list[str] = sorted({
            urn for m in grp for urn in m.aspects_empty_properties
        })

        out.append(ParsedModel(
            path=leader.path,
            namespace=leader.namespace,
            meta_model=leader.meta_model,
            model_family=leader.model_family,
            model_name=leader.model_name,
            model_version=leader.model_version,
            source=leader.source,
            status=leader.status,
            release_date=leader.release_date,
            release_notes=leader.release_notes,
            elements=list(elems_by_urn.values()),
            edges=edges,
            dependencies=deps,
            aspects_empty_properties=merged_empty,
            unused_property_urns=merged_unused,
            used_property_urns=sorted(merged_used),
        ))
    return out


# Feature 6C — éléments exposés par l'onglet Recherche (cross-modèles).
# AbstractProperty/AbstractEntity inclus : ce sont des variantes de
# Property/Entity, on ne veut pas perdre ces correspondances.
_SEARCH_KINDS = {"Aspect", "Property", "AbstractProperty",
                 "Entity", "AbstractEntity"}


def _short_desc(text: str | None, limit: int = 240) -> str | None:
    """Description compacte pour la liste de résultats (le texte plein
    reste disponible dans le Model Viewer). Espaces normalisés, tronquée."""
    if not text:
        return None
    t = " ".join(text.split())
    return t if len(t) <= limit else t[: limit - 1].rstrip() + "…"


def model_to_graph(model: ParsedModel) -> dict:
    """Convertit un ParsedModel en {meta, nodes, links} pour D3."""
    nodes = [
        {
            "id": e.urn,
            "label": e.name,
            "kind": e.kind,
            "preferredName": e.preferred_name,
            "description": e.description,
        }
        for e in model.elements
    ]
    links = [
        {
            "source": edge.source,
            "target": edge.target,
            "label": edge.label,
            "optional": edge.optional,
        }
        for edge in model.edges
    ]
    return {
        "meta": {
            "namespace": model.namespace,
            "meta_model": model.meta_model,
            "model_family": model.model_family,
            "model_name": model.model_name,
            "model_version": model.model_version,
            "source": model.source,
            "status": model.status,
            "release_date": model.release_date,
            "release_notes": model.release_notes,
            "dependencies": [
                {"name": d.name, "version": d.version,
                 "family": d.family, "source": d.source}
                for d in model.dependencies
            ],
            "path": model.path,
        },
        "nodes": nodes,
        "links": links,
    }


def build_graphs(
    sources: tuple[Source, ...] = SOURCES,
    out_dir: Path = DEFAULT_OUT_DIR,
) -> Path:
    """Parse tous les modèles (toutes sources), **fusionne** les .ttl frères
    d'un même dossier de version (cf. `merge_models`) et écrit les JSON
    consommés par le front : 1 graphe par modèle+version, `index.json`,
    `deps.json`, `search.json`, `issues.json`. Les sources absentes du disque
    sont skip avec un WARNING."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    index = []
    search: list[dict] = []  # Feature 6C — éléments cherchables (cross-modèles)
    deps_map: dict[str, dict] = {}

    # On garde 2 listes :
    #   - `raw_models`  : un ParsedModel par .ttl (utilisé par `issues.py`
    #                     pour les checks **par fichier** : URN ↔ chemin,
    #                     stem ↔ Aspect, missing_gen_docs, meta_model_drift…).
    #   - `all_models`  : la version FUSIONNÉE (1 par modèle+version),
    #                     utilisée pour la sortie graphe ET pour les checks
    #                     **au niveau modèle** (orphelins, doc, naming,
    #                     `unused_property` recalculé à l'échelle du groupe).
    raw_models: list[ParsedModel] = []
    dirs_for_issues: list[tuple[Path, str]] = []
    # Couple (model fusionné, source) pour retrouver le models_dir → `_model_id`
    # et `_gen_artifacts` ont besoin du dossier de leur source.
    fused_with_dir: list[tuple[ParsedModel, Path]] = []

    for src in sources:
        if not src.models_dir.is_dir():
            logger.warning("Source %s : %s absent, ignoré", src.key, src.models_dir)
            continue
        models = parse_directory(src.models_dir)
        raw_models.extend(models)
        dirs_for_issues.append((src.models_dir, src.key))
        for fm in merge_models(models):
            fused_with_dir.append((fm, src.models_dir))

    all_models = [fm for fm, _ in fused_with_dir]

    for model, models_dir in fused_with_dir:
        gid = _model_id(model, models_dir)
        graph = model_to_graph(model)
        key = f"{model.model_name}@{model.model_version}"
        deps_map[key] = {
            "name": model.model_name,
            "version": model.model_version,
            "family": model.model_family,
            "source": model.source,
            "deps": sorted({
                f"{d.name}@{d.version}" for d in model.dependencies
            }),
        }
        (out_dir / f"{gid}.json").write_text(
            json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        for e in model.elements:
            if e.kind not in _SEARCH_KINDS:
                continue
            search.append({
                "name": e.name,
                "preferredName": e.preferred_name,
                "description": _short_desc(e.description),
                "kind": e.kind,
                "model_name": model.model_name,
                "version": model.model_version,
                "family": model.model_family,
                "source": model.source,
                "status": model.status,
                "id": gid,
            })
        aspects = [n for n in graph["nodes"] if n["kind"] == "Aspect"]
        # Nom affiché : 1 Aspect → son nom, plusieurs → liste compacte, aucun
        # (cas `shared.*`) → l'id technique.
        if len(aspects) == 1:
            display = aspects[0]["label"]
        elif aspects:
            display = " · ".join(a["label"] for a in aspects)
        else:
            display = gid
        # Chemin du dossier de version, relatif au repo amont (ex.
        # `io.catenax.batch/4.0.0`) — sert au front à construire l'URL
        # "View on GitHub". Le leader's path est `data/<repo>/<rel>/<File>.ttl`,
        # le dossier de version est son parent.
        try:
            rel_ver = (Path(model.path).resolve().relative_to(models_dir.resolve())
                       .parent.as_posix())
        except ValueError:
            rel_ver = None
        index.append(
            {
                "id": gid,
                "name": display,
                "model_name": model.model_name,
                "version": model.model_version,
                "family": model.model_family,
                "source": model.source,
                "status": model.status,
                "meta_model": model.meta_model,
                "n_nodes": len(graph["nodes"]),
                "n_links": len(graph["links"]),
                "n_aspects": len(aspects),
                "has_aspect": bool(aspects),
                "file": f"{gid}.json",
                "repo_path": rel_ver,
                "gen": _gen_artifacts(model, models_dir),
            }
        )

    index.sort(key=lambda m: m["id"])
    (out_dir / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Nettoyage : supprimer les fichiers de graphe obsolètes (ex. après un
    # renommage de schéma d'ID — fusion par namespace). On garde tout sauf
    # les fichiers de graphe absents de l'index courant. Les 4 fichiers
    # d'agrégation (index/deps/issues/search) ne sont jamais purgés.
    aggregate = {"index.json", "deps.json", "issues.json", "search.json"}
    valid_files = {e["file"] for e in index} | aggregate
    removed = 0
    for p in out_dir.glob("*.json"):
        if p.name not in valid_files:
            p.unlink()
            removed += 1
    if removed:
        logger.info("Nettoyage : %d fichiers de graphe obsolètes supprimés", removed)

    # deps.json : carte modèle+version -> dépendances (clé "name@version").
    # Pas de collision de clé constatée entre Catena-X et IDTA, on garde la
    # clé telle quelle (le champ `source` lève l'ambiguïté de provenance).
    # Une clé absente = ce modèle+version n'est pas dans le catalogue (cible
    # de dépendance non résolue) ; le front la dessine en "manquant".
    deps_out = dict(sorted(deps_map.items()))
    (out_dir / "deps.json").write_text(
        json.dumps(deps_out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    n_with_deps = sum(1 for v in deps_out.values() if v["deps"])
    by_source = {
        src.key: sum(1 for m in index if m["source"] == src.key) for src in sources
    }
    logger.info(
        "Graphe écrit : %d modèles fusionnés (par source : %s) -> %s "
        "[%d .ttl bruts en entrée]",
        len(index), by_source, out_dir, len(raw_models),
    )
    logger.info(
        "deps.json : %d modèles+version, %d avec dépendances",
        len(deps_out), n_with_deps,
    )
    n_doc = sum(1 for m in index if m["gen"].get("html"))
    logger.info("gen/ : %d/%d modèles ont une doc HTML amont (porteur d'Aspect)",
                n_doc, len(index))

    # issues.json : qualité des modèles, pré-calculée (site statique).
    # On passe les 2 listes : fusionnée pour les checks-éléments, brute pour
    # les checks-fichier (URN/stem/gen-html-par-fichier).
    issues = build_issues(all_models, raw_models, dirs_for_issues, deps_out)
    (out_dir / "issues.json").write_text(
        json.dumps(issues, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    n_flagged = len(issues["models"])
    per_type = {
        t["id"]: sum(1 for v in issues["models"].values() if t["id"] in v["issues"])
        for t in issues["issue_types"]
    }
    logger.info(
        "issues.json : %d modèles+version avec ≥1 issue %s",
        n_flagged, per_type,
    )

    # search.json : index plat des éléments cherchables (Feature 6C).
    # Auto-contenu (comme issues.json) : name/desc/modèle/version/status y
    # figurent -> filtrage par statut côté front sans relire index.json.
    # Trié pour des diffs git stables (sortie commitée).
    search.sort(key=lambda s: (s["model_name"], s["version"],
                               s["kind"], s["name"]))
    (out_dir / "search.json").write_text(
        json.dumps(search, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info(
        "search.json : %d éléments cherchables (Aspect/Property/Entity)",
        len(search),
    )
    return out_dir


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Génère les graphes JSON (nœuds/arêtes) des modèles."
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR,
                        help=f"Répertoire de sortie (défaut : {DEFAULT_OUT_DIR})")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    build_graphs(SOURCES, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

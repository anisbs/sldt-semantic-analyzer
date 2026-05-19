"""Feature 3 — Modèle de graphe (JSON nœuds/arêtes pour D3).

Approche « simple » (option 1) : un graphe **par modèle**, sans résoudre les
références inter-fichiers (`ext-*:`). Le format JSON produit est directement
consommable par un force-directed D3 (feature 4).

Sortie : un dossier `data/graph/` contenant
  - `index.json`      : liste des modèles { id, name, meta_model, n_nodes, ... }
  - `<id>.json`       : un graphe { meta, nodes[], links[] } par modèle
  - `search.json`     : index plat des éléments cherchables (Feature 6C)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from sldt_analyzer.issues import build_issues
from sldt_analyzer.parser import ParsedModel, parse_directory

logger = logging.getLogger("sldt.graph")

DEFAULT_MODELS_DIR = Path("data/sldt-semantic-models")
# Sortie sous web/ pour être commitée et servie par GitHub Pages (le gros
# clone des .ttl reste, lui, sous data/ qui est git-ignored).
DEFAULT_OUT_DIR = Path("web/data/graph")


def _model_id(model: ParsedModel, models_dir: Path) -> str:
    """Identifiant unique par FICHIER (un dossier de version peut contenir
    plusieurs .ttl) : 'io.catenax.batch__4.0.0__Batch'."""
    try:
        rel = Path(model.path).resolve().relative_to(
            models_dir.resolve()
        ).with_suffix("")
        return str(rel).replace("/", "__")
    except ValueError:  # chemin hors du dossier modèles : repli sur le nom
        return Path(model.path).stem


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
    séparateurs POSIX (URL jsDelivr). Repli sûr si chemin inattendu."""
    try:
        rel = Path(model.path).resolve().relative_to(
            models_dir.resolve()
        ).with_suffix("")
    except ValueError:
        return {"base": None, **{k: False for k in _GEN_SUFFIXES}}
    gen_rel = rel.parent / "gen" / rel.name
    out = {"base": gen_rel.as_posix()}
    for key, suf in _GEN_SUFFIXES.items():
        out[key] = (models_dir / (str(gen_rel) + suf)).is_file()
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
            "status": model.status,
            "release_date": model.release_date,
            "release_notes": model.release_notes,
            "dependencies": [
                {"name": d.name, "version": d.version, "family": d.family}
                for d in model.dependencies
            ],
            "path": model.path,
        },
        "nodes": nodes,
        "links": links,
    }


def build_graphs(
    models_dir: Path = DEFAULT_MODELS_DIR,
    out_dir: Path = DEFAULT_OUT_DIR,
) -> Path:
    """Parse tous les modèles et écrit un graphe JSON par modèle + un index."""
    models = parse_directory(models_dir)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    index = []
    search: list[dict] = []  # Feature 6C — éléments cherchables (cross-modèles)
    # Dépendances agrégées AU NIVEAU (model_name, version) : un dossier de
    # version peut contenir plusieurs .ttl, chacun avec ses `ext-*:`. Le front
    # raisonne par modèle+version -> on fait l'UNION de leurs dépendances.
    deps_map: dict[str, dict] = {}
    for model in models:
        gid = _model_id(model, Path(models_dir))
        graph = model_to_graph(model)
        key = f"{model.model_name}@{model.model_version}"
        entry = deps_map.setdefault(
            key,
            {
                "name": model.model_name,
                "version": model.model_version,
                "family": model.model_family,
                "deps": set(),
            },
        )
        entry["deps"].update(
            f"{d.name}@{d.version}" for d in model.dependencies
        )
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
                "status": model.status,
                "id": gid,
            })
        aspect = next((n for n in graph["nodes"] if n["kind"] == "Aspect"), None)
        index.append(
            {
                "id": gid,
                "name": aspect["label"] if aspect else gid,
                "model_name": model.model_name,
                "version": model.model_version,
                "family": model.model_family,
                "status": model.status,
                "meta_model": model.meta_model,
                "n_nodes": len(graph["nodes"]),
                "n_links": len(graph["links"]),
                "has_aspect": aspect is not None,
                "file": f"{gid}.json",
                "gen": _gen_artifacts(model, Path(models_dir)),
            }
        )

    index.sort(key=lambda m: m["id"])
    (out_dir / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # deps.json : carte modèle+version -> dépendances (clé "name@version").
    # Une clé absente = ce modèle+version n'est pas dans le catalogue (cible
    # de dépendance non résolue) ; le front la dessine en "manquant".
    deps_out = {
        key: {
            "name": v["name"],
            "version": v["version"],
            "family": v["family"],
            "deps": sorted(v["deps"]),
        }
        for key, v in sorted(deps_map.items())
    }
    (out_dir / "deps.json").write_text(
        json.dumps(deps_out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    n_with_deps = sum(1 for v in deps_out.values() if v["deps"])
    logger.info(
        "Graphe écrit : %d modèles -> %s (index.json + deps.json + 1 fichier/modèle)",
        len(index), out_dir,
    )
    logger.info(
        "deps.json : %d modèles+version, %d avec dépendances",
        len(deps_out), n_with_deps,
    )
    n_doc = sum(1 for m in index if m["gen"].get("html"))
    logger.info("gen/ : %d/%d fichiers ont une doc HTML amont", n_doc, len(index))

    # issues.json : qualité des modèles, pré-calculée (site statique).
    issues = build_issues(models, Path(models_dir), deps_out)
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
        description="Génère les graphes JSON (nœuds/arêtes) des modèles SLDT."
    )
    parser.add_argument("--dir", type=Path, default=DEFAULT_MODELS_DIR,
                        help=f"Répertoire des modèles (défaut : {DEFAULT_MODELS_DIR})")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR,
                        help=f"Répertoire de sortie (défaut : {DEFAULT_OUT_DIR})")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    build_graphs(args.dir, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

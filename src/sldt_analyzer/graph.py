"""Feature 3 — Modèle de graphe (JSON nœuds/arêtes pour D3).

Approche « simple » (option 1) : un graphe **par modèle**, sans résoudre les
références inter-fichiers (`ext-*:`). Le format JSON produit est directement
consommable par un force-directed D3 (feature 4).

Sortie : un dossier `data/graph/` contenant
  - `index.json`      : liste des modèles { id, name, meta_model, n_nodes, ... }
  - `<id>.json`       : un graphe { meta, nodes[], links[] } par modèle
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

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
    for model in models:
        gid = _model_id(model, Path(models_dir))
        graph = model_to_graph(model)
        (out_dir / f"{gid}.json").write_text(
            json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8"
        )
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
            }
        )

    index.sort(key=lambda m: m["id"])
    (out_dir / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info(
        "Graphe écrit : %d modèles -> %s (index.json + 1 fichier/modèle)",
        len(index), out_dir,
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

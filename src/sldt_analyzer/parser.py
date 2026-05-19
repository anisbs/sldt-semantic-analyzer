"""Feature 2 — Parsing des modèles .ttl (SAMM & BAMM).

Le dépôt mélange deux vocabulaires (mêmes concepts, namespaces différents) :
  - SAMM : urn:samm:org.eclipse.esmf.samm:meta-model:<v>#   (récent)
  - BAMM : urn:bamm:io.openmanufacturing:meta-model:<v>#     (ancien)

Le parseur détecte le méta-modèle via les @prefix puis travaille sur les URI
complètes, donc il est indépendant du vocabulaire.

Convention imposée : un fichier malformé ne doit JAMAIS faire crasher le
parseur. `parse_file` retourne `None` et logge un WARNING (chemin + raison).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from rdflib import RDF, Graph, URIRef
from rdflib.collection import Collection

logger = logging.getLogger("sldt.parser")

# Types du méta-modèle qu'on extrait (noms locaux, après le '#').
_ELEMENT_TYPES = {
    "Aspect", "Property", "AbstractProperty",
    "Characteristic", "Entity", "AbstractEntity",
    "Operation", "Event", "Constraint",
}
# Prédicats reliant les éléments entre eux (nom local -> libellé d'arête).
_EDGE_PREDICATES = {
    "properties": "properties",
    "characteristic": "characteristic",
    "dataType": "dataType",
    "baseCharacteristic": "baseCharacteristic",
    "elementCharacteristic": "elementCharacteristic",
}


@dataclass
class Element:
    urn: str
    kind: str               # Aspect | Property | Characteristic | Entity | ...
    name: str               # nom local (après le '#')
    preferred_name: str | None = None
    description: str | None = None


@dataclass
class Edge:
    source: str             # urn
    target: str             # urn
    label: str              # properties | characteristic | dataType | ...
    optional: bool = False


@dataclass
class ParsedModel:
    path: str
    namespace: str          # urn propre du modèle (@prefix : ...)
    meta_model: str         # ex. "SAMM 2.1.0" / "BAMM 1.0.0"
    elements: list[Element] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)


def _local_name(uri: str) -> str:
    """Partie après le dernier '#' (ou '/' ou ':')."""
    for sep in ("#", "/", ":"):
        if sep in uri:
            uri = uri.rsplit(sep, 1)[-1]
    return uri


def _detect_meta_model(graph: Graph) -> tuple[str, str] | None:
    """Retourne (namespace_meta_modele, libellé) ou None si introuvable."""
    for prefix, ns in graph.namespaces():
        ns = str(ns)
        if prefix in ("samm", "bamm") and "meta-model" in ns:
            family = "SAMM" if "esmf" in ns else "BAMM"
            version = ns.rstrip("#").rsplit(":", 1)[-1]
            return ns, f"{family} {version}"
    return None


def _model_namespace(graph: Graph) -> str:
    """urn propre du modèle = namespace lié au préfixe par défaut ('')."""
    for prefix, ns in graph.namespaces():
        if prefix == "":
            return str(ns)
    return ""


def _list_items(graph: Graph, head) -> list:
    """Déroule une collection RDF ( :a :b ... ) ; [] si ce n'en est pas une."""
    try:
        return list(Collection(graph, head))
    except Exception:  # noqa: BLE001 — collection malformée : on ignore
        return []


def parse_file(path: Path) -> ParsedModel | None:
    """Parse un .ttl. Retourne None (+ WARNING) si malformé/non exploitable."""
    path = Path(path)
    graph = Graph()
    try:
        try:
            graph.parse(path, format="turtle")
        except UnicodeDecodeError:
            # Quelques vieux modèles sont en Latin-1, pas en UTF-8 : on retente
            # plutôt que d'ignorer un fichier par ailleurs valide.
            graph.parse(
                data=path.read_text(encoding="latin-1"), format="turtle"
            )
    except Exception as exc:  # noqa: BLE001 — Turtle invalide : on logge et skip
        logger.warning("Fichier ignoré (Turtle invalide) : %s — %s", path, exc)
        return None

    detected = _detect_meta_model(graph)
    if detected is None:
        logger.warning("Fichier ignoré (méta-modèle SAMM/BAMM absent) : %s", path)
        return None
    meta_ns, meta_label = detected

    model = ParsedModel(
        path=str(path),
        namespace=_model_namespace(graph),
        meta_model=meta_label,
    )

    M = lambda local: URIRef(meta_ns + local)  # noqa: E731 — fabrique d'URI méta

    # 1) Éléments : sujets typés avec un type du méta-modèle (ou un sous-type
    #    de Characteristic dans le namespace 'characteristic:').
    char_ns = meta_ns.replace("meta-model", "characteristic")
    seen: dict[str, Element] = {}
    for subj, _, typ in graph.triples((None, RDF.type, None)):
        if not isinstance(subj, URIRef):
            continue
        typ_str = str(typ)
        if typ_str.startswith(meta_ns):
            kind = _local_name(typ_str)
            if kind not in _ELEMENT_TYPES:
                continue
        elif typ_str.startswith(char_ns):
            kind = "Characteristic"
        else:
            continue
        urn = str(subj)
        if urn in seen:
            continue
        seen[urn] = Element(
            urn=urn,
            kind=kind,
            name=_local_name(urn),
            preferred_name=_first_literal(graph, subj, M("preferredName")),
            description=_first_literal(graph, subj, M("description")),
        )
    model.elements = list(seen.values())

    # 2) Arêtes entre éléments connus.
    prop_pred = M("property")      # dans le wrapper bnode [ samm:property X ; ...]
    optional_pred = M("optional")
    for local, label in _EDGE_PREDICATES.items():
        pred = M(local)
        for subj, _, obj in graph.triples((None, pred, None)):
            src = str(subj)
            if src not in seen:  # on ne garde que les arêtes entre éléments connus
                continue
            # L'objet peut être : un élément direct, une collection RDF, ou
            # une collection de wrappers [ samm:property X ; samm:optional b ].
            targets = _list_items(graph, obj) or [obj]
            for t in targets:
                opt = False
                if not isinstance(t, URIRef):  # bnode wrapper { property, optional }
                    inner = graph.value(t, prop_pred)
                    if inner is None:
                        continue
                    opt = bool(graph.value(t, optional_pred))
                    t = inner
                tgt = str(t)
                if tgt in seen:
                    model.edges.append(Edge(src, tgt, label, opt))

    return model


def _first_literal(graph: Graph, subj, pred) -> str | None:
    for obj in graph.objects(subj, pred):
        return str(obj)
    return None


def parse_directory(root: Path) -> list[ParsedModel]:
    """Parse tous les .ttl sous `root`. Les fichiers ignorés (malformés ou
    sans méta-modèle) ne sont pas dans la liste — ils sont déjà loggés."""
    root = Path(root)
    files = sorted(root.rglob("*.ttl"))
    models: list[ParsedModel] = []
    for f in files:
        model = parse_file(f)
        if model is not None:
            models.append(model)
    skipped = len(files) - len(models)
    logger.info(
        "Parsing terminé : %d/%d fichiers exploités, %d ignorés.",
        len(models), len(files), skipped,
    )
    return models


def main(argv: list[str] | None = None) -> int:
    import argparse
    from collections import Counter

    parser = argparse.ArgumentParser(
        description="Parse tous les modèles SLDT (.ttl) et résume."
    )
    parser.add_argument(
        "--dir", type=Path,
        default=Path("data/sldt-semantic-models"),
        help="Répertoire des modèles (défaut : data/sldt-semantic-models)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(levelname)-7s %(message)s"
    )
    models = parse_directory(args.dir)

    meta = Counter(m.meta_model for m in models)
    kinds: Counter = Counter()
    n_edges = 0
    for m in models:
        kinds.update(e.kind for e in m.elements)
        n_edges += len(m.edges)
    logger.info("Vocabulaires : %s", dict(meta))
    logger.info("Éléments     : %d %s", sum(kinds.values()), dict(kinds))
    logger.info("Arêtes       : %d", n_edges)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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

import json
import logging
import re
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
class Dependency:
    """Un autre modèle (name@version) référencé via un @prefix `ext-*:`."""
    family: str             # SAMM | BAMM
    name: str               # ex. "generic.digital_product_passport"
    version: str            # ex. "5.0.0"


@dataclass
class ParsedModel:
    path: str
    namespace: str          # urn propre du modèle (@prefix : ...)
    meta_model: str         # ex. "SAMM 2.1.0" / "BAMM 1.0.0" (version de la SPEC)
    model_family: str = ""  # SAMM | BAMM (issu de l'urn du modèle)
    model_name: str = ""    # ex. "batch" (namespace sans "io.catenax.")
    model_version: str = "" # ex. "2.0.0" (version DU MODÈLE, ≠ meta_model)
    status: str = "undefined"  # release | deprecated | draft | undefined
    release_date: str | None = None   # date de la section RELEASE_NOTES.md
    release_notes: str | None = None  # corps markdown de la section version
    elements: list[Element] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    dependencies: list[Dependency] = field(default_factory=list)


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


# urn:<famille>:<namespace>:<version>#  (le @prefix : ... sans nom)
_URN_RE = re.compile(r"^urn:(?P<family>[^:]+):(?P<ns>.+):(?P<version>[^:#]+)#?$")
_ORG_PREFIXES = ("io.catenax.", "io.openmanufacturing.")


def _parse_model_urn(namespace: str) -> tuple[str, str, str]:
    """'urn:samm:io.catenax.batch:2.0.0#' -> ('SAMM', 'batch', '2.0.0').
    Repli sûr (jamais d'exception) si la forme est inattendue."""
    m = _URN_RE.match((namespace or "").strip())
    if not m:
        return ("", (namespace or "").rstrip("#"), "")
    family = m.group("family").upper()        # samm/bamm -> SAMM/BAMM
    name = m.group("ns")
    for org in _ORG_PREFIXES:
        if name.startswith(org):
            name = name[len(org):]
            break
    return (family, name, m.group("version"))


def _dependencies(
    graph: Graph, own_ns: str, own_name: str, own_version: str
) -> list[Dependency]:
    """Modèles dont CE .ttl dépend, déduits des `@prefix` pointant vers un
    autre namespace de modèle Catena-X (`urn:samm|bamm:io.catenax.…:<v>#`).

    On exclut le préfixe par défaut (le modèle lui-même), le namespace propre
    et les namespaces du méta-modèle (`org.eclipse.esmf.samm:…`,
    `io.openmanufacturing:meta-model…` : le segment ns ne commence pas par
    `io.catenax.`/`io.openmanufacturing.`). Dédupliqué par (name, version)."""
    deps: dict[tuple[str, str], Dependency] = {}
    for prefix, ns in graph.namespaces():
        ns = str(ns)
        if prefix == "" or ns == own_ns:
            continue
        m = _URN_RE.match(ns.strip())
        if not m or not m.group("ns").startswith(_ORG_PREFIXES):
            continue  # méta-modèle ou URN non-modèle : pas une dépendance
        family, name, version = _parse_model_urn(ns)
        if (name, version) == (own_name, own_version):
            continue  # alias vers soi-même
        deps.setdefault((name, version), Dependency(family, name, version))
    return sorted(deps.values(), key=lambda d: (d.name, d.version))


# Quelques metadata.json amont ont la coquille "deprecate".
_STATUS_FIX = {"deprecate": "deprecated"}


def _read_status(ttl_path: Path) -> str:
    """`status` du metadata.json voisin (même dossier de version).
    Absent -> 'undefined' ; illisible/malformé -> WARNING + 'undefined'."""
    meta = Path(ttl_path).parent / "metadata.json"
    if not meta.is_file():
        return "undefined"
    try:
        raw = meta.read_bytes()
        try:
            txt = raw.decode("utf-8")
        except UnicodeDecodeError:
            txt = raw.decode("latin-1")
        status = json.loads(txt).get("status")
    except Exception as exc:  # noqa: BLE001 — JSON cassé : on logge et on continue
        logger.warning("metadata.json illisible : %s — %s", meta, exc)
        return "undefined"
    if not isinstance(status, str) or not status.strip():
        return "undefined"
    status = status.strip().lower()
    return _STATUS_FIX.get(status, status)


# Titre de section "Keep a Changelog" : "## [6.1.0] - 2025-10-30"
_RN_HEAD = re.compile(r"^##\s*\[([^\]]+)\]\s*(?:-\s*(.+?))?\s*$")


def _read_release_notes(ttl_path: Path, version: str) -> tuple[str | None, str | None]:
    """RELEASE_NOTES.md = changelog **du modèle** (dossier parent du dossier
    de version), **indépendant de la version**. Retourne
    `(date_de_cette_version, changelog_complet)` : le changelog est identique
    pour toutes les versions du modèle ; la date est juste la métadonnée de
    la section `## [<version>]` (utile pour étiqueter la version, ce ne sont
    pas « les notes »). Absent -> (None, None) ; jamais d'exception."""
    rn = Path(ttl_path).parent.parent / "RELEASE_NOTES.md"
    if not rn.is_file():
        return (None, None)
    try:
        raw = rn.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")
    except Exception as exc:  # noqa: BLE001 — fichier illisible : on logge
        logger.warning("RELEASE_NOTES.md illisible : %s — %s", rn, exc)
        return (None, None)

    full = text.strip() or None
    date: str | None = None
    if version:
        for line in text.splitlines():
            m = _RN_HEAD.match(line)
            if m and m.group(1).strip() == version:
                date = (m.group(2) or "").strip() or None
                break
    return (date, full)


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

    ns = _model_namespace(graph)
    family, name, version = _parse_model_urn(ns)
    rdate, rnotes = _read_release_notes(path, version)
    model = ParsedModel(
        path=str(path),
        namespace=ns,
        meta_model=meta_label,
        model_family=family,
        model_name=name,
        model_version=version,
        status=_read_status(path),
        release_date=rdate,
        release_notes=rnotes,
        dependencies=_dependencies(graph, ns, name, version),
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
    families = Counter(m.model_family for m in models)
    statuses = Counter(m.status for m in models)
    kinds: Counter = Counter()
    n_edges = 0
    for m in models:
        kinds.update(e.kind for e in m.elements)
        n_edges += len(m.edges)
    n_deps = sum(1 for m in models if m.dependencies)
    n_dep_edges = sum(len(m.dependencies) for m in models)
    n_notes = sum(1 for m in models if m.release_notes)  # versions dont le modèle a un RELEASE_NOTES.md
    logger.info("Vocabulaires : %s", dict(meta))
    logger.info("Familles     : %s", dict(families))
    logger.info("Statuts      : %s", dict(statuses))
    logger.info("Release notes: %d/%d versions dont le modèle a un RELEASE_NOTES.md", n_notes, len(models))
    logger.info("Éléments     : %d %s", sum(kinds.values()), dict(kinds))
    logger.info("Arêtes       : %d", n_edges)
    logger.info("Dépendances  : %d fichiers en ont (%d arêtes inter-modèles)", n_deps, n_dep_edges)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

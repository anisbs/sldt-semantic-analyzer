"""Feature 2 — Parsing des modèles .ttl (SAMM & BAMM, Catena-X & IDTA).

Vocabulaires gérés (mêmes concepts, namespaces différents) :
  - SAMM : urn:samm:org.eclipse.esmf.samm:meta-model:<v>#   (récent)
  - BAMM : urn:bamm:io.openmanufacturing:meta-model:<v>#     (ancien)

Référentiels (sources) gérés — distingués au niveau du modèle ET de chaque
dépendance via le champ `source` :
  - `catenax`     : modèles dont l'URN commence par `io.catenax.`
  - `idta`        : modèles dont l'URN commence par `io.admin-shell.idta.`
  - `external`    : dépendance vers un référentiel tiers connu mais non
                    présent dans notre catalogue (ex. `io.BatteryPass.*`)

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

from rdflib import RDF, Graph, Literal, URIRef
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
# Prédicats vivant dans le namespace `samm-c:` (caractéristiques), pas dans
# le méta-modèle. `samm-c:constraint` relie un Trait à ses Constraint(s) :
# nécessaire pour détecter `trait_without_constraint`.
_CHAR_EDGE_PREDICATES = {
    "constraint": "constraint",
}


@dataclass
class ExternalRef:
    """Une arête sortante d'un Element vers un URN qui n'est pas défini
    DANS le même fichier `.ttl` que cet élément. Deux cas distincts :
      - **Same-namespace, sibling file** (cas IDTA dominant) : Aspect dans
        `Foo.ttl` référence une Property définie dans `Foo_shared.ttl` du
        même dossier de version. Après fusion (`graph.merge_models`), la
        cible devient un nœud du modèle agrégé et la ref est **promue en
        Edge** (= arête visible).
      - **Cross-namespace, true external** (bridge cross-modèle) : ex.
        `CarbonFootprintBattery` (idta) qui référence
        `pcf:ProductCarbonFootprint` (catenax). La cible reste hors du
        modèle ; la ref est conservée et affichée dans le tooltip du
        nœud côté front (Feature 23)."""
    predicate: str             # ex. "characteristic", "dataType", "properties"
    target_urn: str            # URN complète (urn:samm:...#localName)
    target_local: str          # localName (après '#'), ex. "ProductCarbonFootprint"
    target_model: str          # "name@version" si l'URN est parseable, "" sinon
    target_source: str         # catenax | idta | external | unknown
    optional: bool = False


@dataclass
class Element:
    urn: str
    kind: str               # Aspect | Property | Characteristic | Entity | ...
    name: str               # nom local (après le '#')
    preferred_name: str | None = None
    description: str | None = None
    # Langues des `samm:description` rencontrées (tags BCP-47, ex. "en", "de").
    # Vide si aucune description (différent d'une description sans @lang).
    description_langs: list[str] = field(default_factory=list)
    # Tous les `rdf:type` (noms locaux). Permet de distinguer un `Trait`, un
    # `Enumeration`, etc. d'un `Characteristic` "pur" (qui DOIT avoir dataType).
    types: list[str] = field(default_factory=list)
    # Refs sortantes vers des URN absents de `seen` lors du parse de CE .ttl
    # (cf. ExternalRef). Tient compte de la cible et permet la promotion en
    # Edge lors de la fusion (`graph.merge_models`).
    external_refs: list[ExternalRef] = field(default_factory=list)


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
    source: str = "unknown" # catenax | idta | external (référentiel tiers connu)


@dataclass
class ParsedModel:
    path: str
    namespace: str          # urn propre du modèle (@prefix : ...)
    meta_model: str         # ex. "SAMM 2.1.0" / "BAMM 1.0.0" (version de la SPEC)
    model_family: str = ""  # SAMM | BAMM (issu de l'urn du modèle)
    model_name: str = ""    # ex. "batch" (namespace sans préfixe org)
    model_version: str = "" # ex. "2.0.0" (version DU MODÈLE, ≠ meta_model)
    source: str = "unknown" # catenax | idta (référentiel d'origine du modèle)
    status: str = "undefined"  # release | deprecated | draft | undefined
    release_date: str | None = None   # date de la section RELEASE_NOTES.md
    release_notes: str | None = None  # corps markdown de la section version
    elements: list[Element] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    dependencies: list[Dependency] = field(default_factory=list)
    # URNs des Aspects de ce fichier ayant une liste `samm:properties` vide
    # (différent de "Aspect sans propriétés DANS ce graph" : on tient compte
    # ici de la collection RDF d'origine, y compris items externes ext-*:).
    aspects_empty_properties: list[str] = field(default_factory=list)
    # URNs des Properties définies dans ce fichier mais jamais référencées par
    # une `samm:properties` (collection ou wrapper bnode). Calculé pendant le
    # parse car nécessite la collection RDF complète.
    unused_property_urns: list[str] = field(default_factory=list)
    # URNs **référencées** par une `samm:properties` dans ce fichier (peuvent
    # vivre dans un autre fichier du même namespace — cas IDTA : Aspect ici,
    # Property dans le _shared.ttl voisin). Exposé pour permettre de fusionner
    # plusieurs ParsedModel d'un même namespace et de recalculer
    # `unused_property` à l'échelle du modèle complet (sinon faux positifs
    # massifs côté IDTA).
    used_property_urns: list[str] = field(default_factory=list)


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


# Fallback texte si rdflib ne nous rend pas le préfixe par défaut. Cas
# observé côté IDTA : quand `@prefix : <X>` ET `@prefix bp: <X>` pointent
# vers la même URN, rdflib peut ne garder que `bp:` et perdre le préfixe vide.
_DEFAULT_NS_RE = re.compile(r"^\s*@prefix\s*:\s*<([^>]+)>\s*\.", re.MULTILINE)


def _model_namespace(graph: Graph, path: Path | None = None) -> str:
    """urn propre du modèle = namespace lié au préfixe par défaut ('').
    Si rdflib l'a perdu (préfixe collision : un autre prefix pointant vers la
    même URN), fallback en lisant la ligne `@prefix : <...>` du fichier."""
    for prefix, ns in graph.namespaces():
        if prefix == "":
            return str(ns)
    if path is None:
        return ""
    try:
        raw = Path(path).read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")
    except OSError:
        return ""
    m = _DEFAULT_NS_RE.search(text)
    return m.group(1) if m else ""


# urn:<famille>:<namespace>:<version>#  (le @prefix : ... sans nom)
_URN_RE = re.compile(r"^urn:(?P<family>[^:]+):(?P<ns>.+):(?P<version>[^:#]+)#?$")

# Préfixes de namespace qu'on reconnaît comme « URN de modèle » (par opposition
# au méta-modèle, dont le segment ns contient `:meta-model` / `:characteristic`
# / etc., avec un `:` au lieu d'un `.`). On strip le préfixe pour obtenir le
# `model_name` court, et on associe une `source`. Ordre = priorité de match.
_ORG_SOURCES: tuple[tuple[str, str], ...] = (
    ("io.catenax.",         "catenax"),
    ("io.openmanufacturing.", "catenax"),   # legacy BAMM (jamais utilisé en pratique mais défensif)
    ("io.admin-shell.idta.", "idta"),
)
# Référentiels tiers que nos modèles peuvent référencer mais qu'on n'ingère
# pas (= absents du catalogue). On les surface en `Dependency(source="external")`
# pour que le front les affiche en "absent du catalogue" plutôt que de les
# perdre silencieusement (utile pour les ponts IDTA -> BatteryPass).
_EXTERNAL_SOURCES: tuple[tuple[str, str], ...] = (
    ("io.BatteryPass.",     "external"),
)
_ALL_ORG_SOURCES = _ORG_SOURCES + _EXTERNAL_SOURCES
_ALL_ORG_PREFIXES = tuple(p for p, _ in _ALL_ORG_SOURCES)


def _source_for_ns_segment(ns_segment: str) -> tuple[str, str]:
    """('io.catenax.batch') -> ('catenax', 'batch'). Inconnu -> ('unknown', ns_segment)."""
    for prefix, source in _ALL_ORG_SOURCES:
        if ns_segment.startswith(prefix):
            return source, ns_segment[len(prefix):]
    return ("unknown", ns_segment)


def _parse_model_urn(namespace: str) -> tuple[str, str, str, str]:
    """'urn:samm:io.catenax.batch:2.0.0#' -> ('SAMM', 'batch', '2.0.0', 'catenax').
    Repli sûr (jamais d'exception) si la forme est inattendue."""
    m = _URN_RE.match((namespace or "").strip())
    if not m:
        return ("", (namespace or "").rstrip("#"), "", "unknown")
    family = m.group("family").upper()        # samm/bamm -> SAMM/BAMM
    source, name = _source_for_ns_segment(m.group("ns"))
    return (family, name, m.group("version"), source)


def _dependencies(
    graph: Graph, own_ns: str, own_name: str, own_version: str
) -> list[Dependency]:
    """Modèles dont CE .ttl dépend, déduits des `@prefix` pointant vers un
    namespace de modèle reconnu (Catena-X, IDTA, ou référentiel tiers connu).

    On exclut le préfixe par défaut (le modèle lui-même), le namespace propre
    et les namespaces du méta-modèle (segment ns en `:meta-model` /
    `:characteristic` / etc., dont aucun ne commence par un des préfixes d'org
    avec un `.` à la fin). Dédupliqué par (name, version)."""
    deps: dict[tuple[str, str], Dependency] = {}
    for prefix, ns in graph.namespaces():
        ns = str(ns)
        if prefix == "" or ns == own_ns:
            continue
        m = _URN_RE.match(ns.strip())
        if not m or not m.group("ns").startswith(_ALL_ORG_PREFIXES):
            continue  # méta-modèle ou URN non-modèle : pas une dépendance
        family, name, version, source = _parse_model_urn(ns)
        if (name, version) == (own_name, own_version):
            continue  # alias vers soi-même
        deps.setdefault((name, version), Dependency(family, name, version, source))
    return sorted(deps.values(), key=lambda d: (d.name, d.version))


# Quelques metadata.json amont ont la coquille "deprecate".
_STATUS_FIX = {"deprecate": "deprecated"}

# Statut par défaut quand le metadata.json est absent / illisible / sans statut.
# IDTA (smt-semantic-models) ne publie QUE des modèles released et ne fournit
# pas de metadata.json -> on assume `release` au lieu d'`undefined`. Les autres
# sources restent `undefined` (réellement inconnu). Un statut explicite et
# valide dans le metadata.json prime toujours sur ce défaut.
_DEFAULT_STATUS_BY_SOURCE = {"idta": "release"}


def _read_status(ttl_path: Path, source: str = "unknown") -> str:
    """`status` du metadata.json voisin (même dossier de version).
    Absent/sans statut -> défaut par source (`release` pour IDTA, sinon
    'undefined') ; illisible/malformé -> WARNING + défaut par source."""
    default = _DEFAULT_STATUS_BY_SOURCE.get(source, "undefined")
    meta = Path(ttl_path).parent / "metadata.json"
    if not meta.is_file():
        return default
    try:
        raw = meta.read_bytes()
        try:
            txt = raw.decode("utf-8")
        except UnicodeDecodeError:
            txt = raw.decode("latin-1")
        status = json.loads(txt).get("status")
    except Exception as exc:  # noqa: BLE001 — JSON cassé : on logge et on continue
        logger.warning("metadata.json illisible : %s — %s", meta, exc)
        return default
    if not isinstance(status, str) or not status.strip():
        return default
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

    ns = _model_namespace(graph, path)
    family, name, version, source = _parse_model_urn(ns)
    rdate, rnotes = _read_release_notes(path, version)
    model = ParsedModel(
        path=str(path),
        namespace=ns,
        meta_model=meta_label,
        model_family=family,
        model_name=name,
        model_version=version,
        source=source,
        status=_read_status(path, source),
        release_date=rdate,
        release_notes=rnotes,
        dependencies=_dependencies(graph, ns, name, version),
    )

    M = lambda local: URIRef(meta_ns + local)  # noqa: E731 — fabrique d'URI méta

    # 1) Éléments : sujets typés avec un type du méta-modèle (ou un sous-type
    #    de Characteristic dans le namespace 'characteristic:').
    char_ns = meta_ns.replace("meta-model", "characteristic")
    seen: dict[str, Element] = {}
    desc_pred = M("description")
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
        # On capture TOUS les rdf:type (locaux) pour détecter Trait/Enumeration/
        # Constraint en aval : un Characteristic "pur" exige `dataType`, un
        # Trait non (il a `baseCharacteristic` + `constraint`).
        types: list[str] = []
        for _, _, t in graph.triples((subj, RDF.type, None)):
            t_str = str(t)
            if t_str.startswith(meta_ns) or t_str.startswith(char_ns):
                types.append(_local_name(t_str))
        # Langues des descriptions (vide si pas de samm:description du tout).
        langs: list[str] = []
        for obj in graph.objects(subj, desc_pred):
            if isinstance(obj, Literal):
                langs.append(obj.language or "")
        seen[urn] = Element(
            urn=urn,
            kind=kind,
            name=_local_name(urn),
            preferred_name=_first_literal(graph, subj, M("preferredName")),
            description=_first_literal(graph, subj, desc_pred),
            description_langs=langs,
            types=types,
        )
    model.elements = list(seen.values())

    # 2) Arêtes entre éléments connus.
    prop_pred = M("property")      # dans le wrapper bnode [ samm:property X ; ...]
    optional_pred = M("optional")
    properties_pred = M("properties")
    # On note tous les URIs cités via `samm:properties` (collection ou
    # wrappers) — y compris URIs externes (ext-*:) — pour 2 usages :
    #  - détecter `unused_property` (Property locale jamais citée)
    #  - détecter `aspect_without_properties` (collection RDF effectivement vide)
    used_property_uris: set[str] = set()
    aspects_with_props: set[str] = set()
    for subj, _, head in graph.triples((None, properties_pred, None)):
        # `head` peut être une rdf:Collection ou rdf:nil ; _list_items gère
        # les 2 (et retourne [] pour autre chose, ce qu'on traite comme vide).
        items = _list_items(graph, head)
        src = str(subj)
        if src in seen and seen[src].kind == "Aspect" and items:
            aspects_with_props.add(src)
        for it in items:
            if isinstance(it, URIRef):
                used_property_uris.add(str(it))
            else:  # bnode wrapper [ samm:property X ; ... ]
                inner = graph.value(it, prop_pred)
                if isinstance(inner, URIRef):
                    used_property_uris.add(str(inner))
    model.aspects_empty_properties = sorted(
        e.urn for e in model.elements
        if e.kind == "Aspect" and e.urn not in aspects_with_props
    )
    model.unused_property_urns = sorted(
        e.urn for e in model.elements
        if e.kind == "Property" and e.urn not in used_property_uris
    )
    model.used_property_urns = sorted(used_property_uris)

    # Prédicats à scanner : couple (URIRef du prédicat, libellé d'arête).
    edge_preds: list[tuple[URIRef, str]] = [
        (M(local), label) for local, label in _EDGE_PREDICATES.items()
    ] + [
        (URIRef(char_ns + local), label)
        for local, label in _CHAR_EDGE_PREDICATES.items()
    ]
    for pred, label in edge_preds:
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
                else:
                    # Cible hors de CE fichier — stockée en `external_refs`
                    # sur l'Element source. Sera (a) promue en Edge si la
                    # cible apparaît dans un .ttl frère après `merge_models`,
                    # ou (b) conservée comme info cross-modèle dans le
                    # tooltip côté front.
                    target_local = _local_name(tgt)
                    target_model = ""
                    target_source = "unknown"
                    if "#" in tgt:
                        base_ns = tgt.rsplit("#", 1)[0] + "#"
                        _f, t_name, t_ver, t_src = _parse_model_urn(base_ns)
                        if t_name and t_ver:
                            target_model = f"{t_name}@{t_ver}"
                        target_source = t_src
                    seen[src].external_refs.append(ExternalRef(
                        predicate=label, target_urn=tgt,
                        target_local=target_local,
                        target_model=target_model,
                        target_source=target_source,
                        optional=opt,
                    ))

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


_DEFAULT_DIRS = (
    Path("data/sldt-semantic-models"),   # Catena-X
    Path("data/smt-semantic-models"),    # IDTA
)


def main(argv: list[str] | None = None) -> int:
    import argparse
    from collections import Counter

    parser = argparse.ArgumentParser(
        description="Parse tous les modèles (.ttl) et résume."
    )
    parser.add_argument(
        "--dir", type=Path, action="append", default=None,
        help="Répertoire des modèles (peut être répété ; "
             "défaut : data/sldt-semantic-models + data/smt-semantic-models)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(levelname)-7s %(message)s"
    )
    dirs = args.dir or [d for d in _DEFAULT_DIRS if d.exists()]
    models: list[ParsedModel] = []
    for d in dirs:
        models.extend(parse_directory(d))

    meta = Counter(m.meta_model for m in models)
    families = Counter(m.model_family for m in models)
    sources = Counter(m.source for m in models)
    statuses = Counter(m.status for m in models)
    kinds: Counter = Counter()
    n_edges = 0
    for m in models:
        kinds.update(e.kind for e in m.elements)
        n_edges += len(m.edges)
    n_deps = sum(1 for m in models if m.dependencies)
    n_dep_edges = sum(len(m.dependencies) for m in models)
    dep_sources = Counter(d.source for m in models for d in m.dependencies)
    n_notes = sum(1 for m in models if m.release_notes)  # versions dont le modèle a un RELEASE_NOTES.md
    logger.info("Vocabulaires : %s", dict(meta))
    logger.info("Familles     : %s", dict(families))
    logger.info("Sources      : %s", dict(sources))
    logger.info("Statuts      : %s", dict(statuses))
    logger.info("Release notes: %d/%d versions dont le modèle a un RELEASE_NOTES.md", n_notes, len(models))
    logger.info("Éléments     : %d %s", sum(kinds.values()), dict(kinds))
    logger.info("Arêtes       : %d", n_edges)
    logger.info("Dépendances  : %d fichiers en ont (%d arêtes inter-modèles) — par source : %s",
                n_deps, n_dep_edges, dict(dep_sources))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

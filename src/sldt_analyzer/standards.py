"""Feature — Lien standards Catena-X ↔ modèles sémantiques.

Reconstruit, à partir de la **Catena-X Standard Library**
(`catenax-eV/catenax-ev.github.io`, repo Docusaurus), le lien entre chaque
standard `CX-XXXX` et les modèles qu'il utilise — *au lieu de* consommer le
repo tiers `jSchuetz88/cx-dependencies` (archivé/gelé déc. 2025).

On ne traite que les standards **released** de la **dernière release stable**
(ex. `Saturn`), lus sous `versioned_docs/version-<release>/standards/`. Le
`next` (`docs/standards/`) est ignoré.

Pour chaque standard on extrait :
  - `link`                 : URL publique du standard (release courante)
  - `normative`            : refs `CX-XXXX` de la section "Normative References"
                             (best-effort : la section n'existe pas partout)
  - `non_normative`        : TOUT le reste des `CX-XXXX` cités (ancienne section
                             "Non-normative References" + refs hors-section type
                             "standalone standards", ex. CX-0143), fusionné —
                             c.-à-d. tous les CX cités SAUF les `normative`
  - `deprecated_refs`      : sous-ensemble (normative ∪ non_normative) qui sont
                             des standards dépréciés
  - `semantic_models`      : modèles cités (`urn:samm:…` ET `urn:bamm:…` ->
                             clé `name@version`)
  - `bamm_models`          : sous-ensemble cité via `urn:bamm:` (méta-modèle
                             legacy — autoritatif via le schéma d'URN)

Les **standards dépréciés** ne sont plus dans le dossier de la release : on les
récupère via l'union des tables "Deprecated Standards" des `changelog.md` de
TOUTES les releases (un standard déprécié dans Jupiter peut encore être cité
par un standard actif de Saturn).

Convention projet : un fichier malformé/illisible ne fait JAMAIS crasher — on
logge un WARNING et on continue.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

logger = logging.getLogger("sldt.standards")

# URL publique d'un standard de la release courante (Docusaurus sert la
# dernière release sous `/docs/standards/`, sans préfixe de version).
SITE = "https://catenax-ev.github.io/docs/standards/"

# Tirets non-ASCII tolérés entre "CX" et le numéro : l'amont écrit parfois
# `CX–0002` (en-dash), `CX—0002` (em-dash), figure dash, minus… (coquilles).
# On capture les 4 chiffres et on normalise toujours vers `CX-XXXX` (ASCII).
CX_RE = re.compile(r"CX[-‐-―−](\d{4})")


def _cx_ids(text: str) -> set[str]:
    """Tous les identifiants CX cités, normalisés au tiret ASCII (`CX-XXXX`)."""
    return {f"CX-{d}" for d in CX_RE.findall(text)}
# Capture les deux méta-modèles : `urn:samm:` (récent) ET `urn:bamm:` (legacy).
# Le schéma EST le méta-modèle (groupe 1) — un modèle cité en `urn:bamm:` est
# BAMM par construction (cf. `bamm_models`, autoritatif, indépendant du
# catalogue). `\s*` après le schéma : coquille amont avec un espace
# (ex. CX-0154 : `urn:samm: io.catenax.digital_engineering_master_data:1.0.0`).
URN_RE = re.compile(r"urn:(samm|bamm):\s*([\w.\-]+):(\d+\.\d+\.\d+)")
H_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
DEP_ROW = re.compile(r"^\|\s*(CX-\d{4})\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|")

# Préfixes d'org à retirer pour obtenir le `model_name` court (aligné sur
# `parser._parse_model_urn` : clé catalogue = `name@version`).
_ORG_PREFIXES = ("io.catenax.", "io.openmanufacturing.", "io.admin-shell.idta.")

# Dossier du clone local de la library (git-ignored, peuplé par `fetch.py`).
DEFAULT_LIBRARY_DIR = Path("data/cx-standards-library")

# Issues qualité AU NIVEAU STANDARD (clé CX-XXXX), distinctes des issues
# modèle d'`issues.py` (clé name@version). Chaque type pointe le champ de
# `standards[CX]` qui porte la liste fautive ; le front compte/affiche depuis
# ces champs. `deprecated_refs` existe déjà ; `bamm_models` vient du schéma
# d'URN (`urn:bamm:`, autoritatif) ; `deprecated_models` croise `semantic_
# models` avec le statut du catalogue.
STANDARD_ISSUE_TYPES = [
    {"id": "deprecated-standard-ref", "severity": "error",
     "label": "References a deprecated standard", "field": "deprecated_refs",
     "description": (
         "The standard cites another Catena-X standard that has been "
         "deprecated (listed in a release changelog's Deprecated Standards "
         "table). The reference should be updated or removed.")},
    {"id": "deprecated-model-ref", "severity": "error",
     "label": "References a deprecated semantic model",
     "field": "deprecated_models",
     "description": (
         "The standard references a semantic model whose version is marked "
         "deprecated in the catalog. Consumers should migrate to a "
         "non-deprecated version of that model.")},
    {"id": "bamm-model-ref", "severity": "warning",
     "label": "References a BAMM model", "field": "bamm_models",
     "description": (
         "The standard references a model still using the legacy BAMM "
         "meta-model (urn:bamm:…) instead of SAMM. Such models are outdated "
         "and should be migrated to SAMM.")},
]


def _read(path: Path) -> str:
    """Lecture tolérante (UTF-8 puis Latin-1). '' si illisible (+ WARNING)."""
    try:
        raw = path.read_bytes()
    except OSError as exc:  # noqa: BLE001
        logger.warning("Fichier illisible : %s — %s", path, exc)
        return ""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1", errors="replace")


def _model_key(ns_segment: str, version: str) -> str:
    for org in _ORG_PREFIXES:
        if ns_segment.startswith(org):
            ns_segment = ns_segment[len(org):]
            break
    return f"{ns_segment}@{version}"


def _sections(text: str) -> list[tuple[int, str, str]]:
    """Découpe un markdown en (niveau, titre, corps). Corps borné par le
    prochain titre du MÊME fichier (appeler par fichier, pas sur une union)."""
    out: list[tuple[int, str, str]] = []
    cur: list | None = None
    for line in text.splitlines():
        m = H_RE.match(line)
        if m:
            if cur:
                out.append((cur[0], cur[1], "\n".join(cur[2])))
            cur = [len(m.group(1)), m.group(2), []]
        elif cur:
            cur[2].append(line)
    if cur:
        out.append((cur[0], cur[1], "\n".join(cur[2])))
    return out


def _section_body(secs: list[tuple[int, str, str]], predicate) -> str:
    for _lvl, title, body in secs:
        if predicate(title.upper()):
            return body
    return ""


def _derive_title(folder_name: str, cxid: str,
                  secs: list[tuple[int, str, str]]) -> str:
    """Titre du standard. 61/62 standards ont un titre H1 contenant le CX-id
    (`# CX-0126 Industry Core: Part Type 2.1.1`) — on le prend. Sinon (cas
    multi-fichiers sans H1 titre, ex. CX-0143), on dérive du nom de dossier
    `CX-0143-UseCaseCircular…` -> `CX-0143 Use Case Circular …`."""
    for lvl, t, _ in secs:
        if lvl == 1 and cxid in t:
            return t.strip()
    m = re.match(r"(CX-\d{4})-(.*)", folder_name)
    if m:
        rest = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", m.group(2))
        rest = rest.replace("-", " ").strip()
        return f"{m.group(1)} {rest}".strip()
    return folder_name


def _latest_release(library_dir: Path) -> str | None:
    """Nom de la dernière release stable. `versions.json` liste les versions,
    la plus récente en tête. Repli : tri des dossiers `versioned_docs/version-*`."""
    vj = library_dir / "versions.json"
    if vj.is_file():
        try:
            versions = json.loads(_read(vj))
            if isinstance(versions, list) and versions:
                return str(versions[0])
        except (ValueError, TypeError) as exc:
            logger.warning("versions.json illisible : %s — %s", vj, exc)
    vd = library_dir / "versioned_docs"
    try:
        rels = sorted(p.name.replace("version-", "")
                      for p in vd.glob("version-*") if p.is_dir())
    except OSError:
        rels = []
    return rels[-1] if rels else None


def _deprecated_standards(library_dir: Path) -> dict[str, dict]:
    """Union des tables "Deprecated Standards" des `changelog.md` de toutes les
    releases. CX-id -> {name, reason, deprecated_in}. 1ʳᵉ release rencontrée
    (parcours du + récent au + ancien) gagne."""
    vd = library_dir / "versioned_docs"
    # Releases triées du plus récent au plus ancien : on s'appuie sur
    # versions.json en tête, puis les autres dossiers.
    latest = _latest_release(library_dir)
    rel_dirs = sorted((p for p in vd.glob("version-*") if p.is_dir()),
                      key=lambda p: p.name)
    # `latest` en premier pour la priorité, puis les autres (ordre indifférent
    # entre eux : on dédoublonne par CX-id).
    ordered = ([vd / f"version-{latest}"] if latest else []) + [
        p for p in rel_dirs if p.name != f"version-{latest}"
    ]

    out: dict[str, dict] = {}
    for rel_dir in ordered:
        cl = rel_dir / "standards" / "changelog.md"
        if not cl.is_file():
            continue
        rel_name = rel_dir.name.replace("version-", "")
        in_dep = False
        for line in _read(cl).splitlines():
            m = H_RE.match(line)
            if m:
                in_dep = "DEPRECATED STANDARDS" in m.group(2).upper()
                continue
            if in_dep:
                r = DEP_ROW.match(line)
                if r and r.group(1) not in out:
                    out[r.group(1)] = {
                        "name": r.group(2).strip(),
                        "reason": r.group(3).strip(),
                        "deprecated_in": rel_name,
                    }
    return out


def _standard_md_files(folder: Path) -> list[Path]:
    """Tous les `.md` d'un dossier standard sauf le Changelog (un standard peut
    être multi-fichiers : CX-0143 = SEM/API/UC/references)."""
    return [p for p in sorted(folder.glob("*.md"))
            if p.name.lower() != "changelog.md"]


def _release_order(library_dir: Path) -> list[str]:
    """Releases du + récent au + ancien. `versions.json` donne l'ordre canonique
    (plus récent en tête) ; les dossiers `version-*` non listés (ex. 24.03) sont
    ajoutés après (ordre alpha), faute de mieux."""
    listed: list[str] = []
    vj = library_dir / "versions.json"
    if vj.is_file():
        try:
            v = json.loads(_read(vj))
            if isinstance(v, list):
                listed = [str(x) for x in v]
        except (ValueError, TypeError) as exc:  # noqa: BLE001
            logger.warning("versions.json illisible : %s — %s", vj, exc)
    vd = library_dir / "versioned_docs"
    try:
        dirs = sorted(p.name.replace("version-", "")
                      for p in vd.glob("version-*") if p.is_dir())
    except OSError:
        dirs = []
    return listed + [d for d in dirs if d not in listed]


def _withdrawn_standards(library_dir: Path, active_ids: set[str],
                         deprecated: dict[str, dict]) -> dict[str, dict]:
    """CX retirés silencieusement : présents dans une release ANTÉRIEURE mais
    plus dans la release courante, et non marqués dépréciés. CX-id ->
    {name, last_seen_in}. `last_seen_in` = release la + récente (hors courante)
    où le dossier existe encore."""
    vd = library_dir / "versioned_docs"
    order = _release_order(library_dir)
    latest = order[0] if order else None
    out: dict[str, dict] = {}
    for rel in order:
        if rel == latest:
            continue
        std_dir = vd / f"version-{rel}" / "standards"
        if not std_dir.is_dir():
            continue
        for folder in sorted(std_dir.glob("CX-*")):
            if not folder.is_dir():
                continue
            cxm = CX_RE.search(folder.name)
            if not cxm:
                continue
            cxid = f"CX-{cxm.group(1)}"
            if cxid in active_ids or cxid in deprecated or cxid in out:
                continue
            md = _standard_md_files(folder)
            secs = _sections(_read(md[0])) if md else []
            out[cxid] = {
                "name": _derive_title(folder.name, cxid, secs),
                "last_seen_in": rel,
            }
    return out


def build_standards(library_dir: Path = DEFAULT_LIBRARY_DIR,
                    catalog: dict[str, dict] | None = None) -> dict | None:
    """Construit le dict `standards.json`. Retourne None si la library est
    absente (clone non effectué) — l'appelant skippe alors proprement.

    `catalog` (optionnel) : map `name@version -> {"status", "family"}` issue du
    catalogue (cf. `graph.py`). Sert à calculer l'issue standard `deprecated_
    models` (modèle cité au statut `deprecated`). Absent -> cette liste reste
    vide. (`bamm_models` ne dépend PAS du catalogue : il vient du schéma
    `urn:bamm:` de l'URL citée, autoritatif.)

    Forme :
        {
          "release": "Saturn",
          "standards": { "CX-0126": { title, link, normative[], non_normative[],
                         deprecated_refs[], deprecated_models[], bamm_models[],
                         semantic_models[] }, ... },
          "deprecated_standards": { "CX-0013": {name, reason, deprecated_in} },
          "withdrawn_standards": { "CX-0011": {name, last_seen_in} },
          "standard_issue_types": [ {id, label, field}, ... ],
          "models": { "name@version": ["CX-0126", ...] }   # inverse
        }

    `withdrawn_standards` : CX présents dans une release ANTÉRIEURE mais absents
    de la release courante ET non déclarés dépréciés (retirés silencieusement
    en amont, ex. CX-0011). Permet de les distinguer d'un id réellement inconnu.
    """
    catalog = catalog or {}
    library_dir = Path(library_dir)
    latest = _latest_release(library_dir)
    if latest is None:
        logger.warning("Standard Library absente ou vide (%s) — standards.json "
                       "non généré", library_dir)
        return None
    std_dir = library_dir / "versioned_docs" / f"version-{latest}" / "standards"
    if not std_dir.is_dir():
        logger.warning("Dossier standards introuvable : %s — standards.json "
                       "non généré", std_dir)
        return None

    deprecated = _deprecated_standards(library_dir)

    standards: dict[str, dict] = {}
    for folder in sorted(std_dir.glob("CX-*")):
        if not folder.is_dir():
            continue
        cxm = CX_RE.search(folder.name)
        if not cxm:
            continue
        cxid = cxm.group(0)
        md_files = _standard_md_files(folder)
        if not md_files:
            continue
        texts = [_read(p) for p in md_files]
        text = "\n\n".join(texts)
        secs = [s for t in texts for s in _sections(t)]

        title = _derive_title(folder.name, cxid, secs)
        norm_body = _section_body(
            secs, lambda u: "NORMATIVE REFERENCES" in u and "NON-NORMATIVE" not in u)

        normative = sorted(_cx_ids(norm_body) - {cxid})
        # Deux catégories seulement : `normative` (section dédiée) et
        # `non_normative` = TOUT le reste des CX cités (ancienne section
        # "Non-normative References" + refs hors-section, ex-"other"), fusionné.
        referenced = _cx_ids(text) - {cxid}
        non_normative = sorted(referenced - set(normative))
        deprecated_refs = sorted(x for x in referenced if x in deprecated)
        # `findall` -> (scheme, ns, version). `semantic_models` fusionne les
        # deux méta-modèles ; le schéma sert à isoler les refs BAMM.
        cited = URN_RE.findall(text)
        models = sorted({_model_key(ns, v) for _scheme, ns, v in cited})
        # `bamm_models` : autoritatif via le schéma `urn:bamm:` (le méta-modèle
        # est dans l'URN, pas besoin du catalogue — attrape même les modèles
        # non ingérés).
        bamm_models = sorted({_model_key(ns, v)
                              for scheme, ns, v in cited if scheme == "bamm"})
        # `deprecated_models` : catalogue-driven (on ne juge le statut que des
        # modèles connus du catalogue ; statut inconnu pour les non ingérés).
        deprecated_models = sorted(
            m for m in models
            if catalog.get(m, {}).get("status") == "deprecated")

        standards[cxid] = {
            "title": title,
            "link": SITE + folder.name,
            "normative": normative,
            "non_normative": non_normative,
            "deprecated_refs": deprecated_refs,
            "deprecated_models": deprecated_models,
            "bamm_models": bamm_models,
            "semantic_models": models,
        }

    # Carte inverse modèle -> standards qui le citent (indépendante du
    # catalogue ; le front intersecte avec ses propres clés).
    inverse: dict[str, list[str]] = {}
    for cxid, s in standards.items():
        for mk in s["semantic_models"]:
            inverse.setdefault(mk, []).append(cxid)
    models_map = {k: sorted(v) for k, v in sorted(inverse.items())}

    # Standards retirés silencieusement (présents avant, absents de la release
    # courante, non dépréciés) — pour distinguer un ref retiré d'un id inconnu.
    withdrawn = _withdrawn_standards(library_dir, set(standards), deprecated)

    return {
        "release": latest,
        "standards": dict(sorted(standards.items())),
        "deprecated_standards": dict(sorted(deprecated.items())),
        "withdrawn_standards": dict(sorted(withdrawn.items())),
        "standard_issue_types": STANDARD_ISSUE_TYPES,
        "models": models_map,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse
    from collections import Counter

    parser = argparse.ArgumentParser(
        description="Extrait le lien standards↔modèles depuis la Standard Library."
    )
    parser.add_argument("--library", type=Path, default=DEFAULT_LIBRARY_DIR,
                        help=f"Clone local de la library (défaut : {DEFAULT_LIBRARY_DIR})")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    data = build_standards(args.library)
    if data is None:
        return 1

    st = data["standards"]
    with_models = sum(1 for s in st.values() if s["semantic_models"])
    with_dep = sum(1 for s in st.values() if s["deprecated_refs"])
    all_models = {m for s in st.values() for m in s["semantic_models"]}
    logger.info("Release            : %s", data["release"])
    logger.info("Standards          : %d (avec ≥1 modèle : %d)", len(st), with_models)
    logger.info("Modèles distincts  : %d", len(all_models))
    logger.info("Standards dépréciés: %d (référencés par %d standards actifs)",
                len(data["deprecated_standards"]), with_dep)
    logger.info("Carte inverse      : %d modèles -> standards", len(data["models"]))
    sample = next((c for c, s in st.items() if s["deprecated_refs"]), None)
    if sample:
        logger.info("Exemple %s deprecated_refs : %s",
                    sample, st[sample]["deprecated_refs"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

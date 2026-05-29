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
  - `normative` /          : refs `CX-XXXX` des sections "Normative" /
    `non_normative`          "Non-normative References" (best-effort : ces
                             sections n'existent que ~39/62 standards)
  - `referenced_standards` : TOUS les `CX-XXXX` cités (robuste, ~60/62) —
                             couvre les standards qui listent leurs refs hors
                             section dédiée (ex. CX-0143 : "standalone standards")
  - `deprecated_refs`      : sous-ensemble de `referenced_standards` qui sont
                             des standards dépréciés
  - `semantic_models`      : modèles cités (`urn:samm:…` -> clé `name@version`)

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

CX_RE = re.compile(r"\bCX-\d{4}\b")
# `\s*` après `urn:samm:` : coquille amont avec un espace
# (ex. CX-0154 : `urn:samm: io.catenax.digital_engineering_master_data:1.0.0`).
URN_RE = re.compile(r"urn:samm:\s*([\w.\-]+):(\d+\.\d+\.\d+)")
H_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
DEP_ROW = re.compile(r"^\|\s*(CX-\d{4})\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|")

# Préfixes d'org à retirer pour obtenir le `model_name` court (aligné sur
# `parser._parse_model_urn` : clé catalogue = `name@version`).
_ORG_PREFIXES = ("io.catenax.", "io.openmanufacturing.", "io.admin-shell.idta.")

# Dossier du clone local de la library (git-ignored, peuplé par `fetch.py`).
DEFAULT_LIBRARY_DIR = Path("data/cx-standards-library")


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


def build_standards(library_dir: Path = DEFAULT_LIBRARY_DIR) -> dict | None:
    """Construit le dict `standards.json`. Retourne None si la library est
    absente (clone non effectué) — l'appelant skippe alors proprement.

    Forme :
        {
          "release": "Saturn",
          "standards": { "CX-0126": { title, link, normative[], non_normative[],
                         referenced_standards[], deprecated_refs[],
                         semantic_models[] }, ... },
          "deprecated_standards": { "CX-0013": {name, reason, deprecated_in} },
          "models": { "name@version": ["CX-0126", ...] }   # inverse
        }
    """
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
        nonnorm_body = _section_body(
            secs, lambda u: "NON-NORMATIVE REFERENCES" in u)

        normative = sorted(set(CX_RE.findall(norm_body)) - {cxid})
        non_normative = sorted(set(CX_RE.findall(nonnorm_body)) - {cxid})
        referenced = sorted(set(CX_RE.findall(text)) - {cxid})
        deprecated_refs = sorted(x for x in referenced if x in deprecated)
        models = sorted({_model_key(ns, v) for ns, v in URN_RE.findall(text)})

        standards[cxid] = {
            "title": title,
            "link": SITE + folder.name,
            "normative": normative,
            "non_normative": non_normative,
            "referenced_standards": referenced,
            "deprecated_refs": deprecated_refs,
            "semantic_models": models,
        }

    # Carte inverse modèle -> standards qui le citent (indépendante du
    # catalogue ; le front intersecte avec ses propres clés).
    inverse: dict[str, list[str]] = {}
    for cxid, s in standards.items():
        for mk in s["semantic_models"]:
            inverse.setdefault(mk, []).append(cxid)
    models_map = {k: sorted(v) for k, v in sorted(inverse.items())}

    return {
        "release": latest,
        "standards": dict(sorted(standards.items())),
        "deprecated_standards": dict(sorted(deprecated.items())),
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

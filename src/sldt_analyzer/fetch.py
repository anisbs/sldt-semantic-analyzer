"""Feature 1 — Rapatriement des modèles sémantiques.

Clone (ou resynchronise) en local les dépôts amont qui hébergent les .ttl
qu'on visualise. Deux sources gérées :

  - catenax : eclipse-tractusx/sldt-semantic-models      (modèles Catena-X)
  - idta    : admin-shell-io/smt-semantic-models         (modèles IDTA)

Clone superficiel (`--depth 1`), résumé du nombre de .ttl trouvés.

Usage :
    python -m sldt_analyzer.fetch                       # rapatrie TOUTES les sources
    python -m sldt_analyzer.fetch --source catenax      # Catena-X seul
    python -m sldt_analyzer.fetch --source idta         # IDTA seul
    python -m sldt_analyzer.fetch --force               # supprime et re-clone proprement
    python -m sldt_analyzer.fetch --source idta --dest /chemin --branch main -v
"""

from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("sldt.fetch")


@dataclass(frozen=True)
class Source:
    key: str
    url: str
    default_dest: Path
    label: str
    # Patterns sparse-checkout (non-cone, format gitignore). Vide = clone
    # complet. Utilisé pour les gros dépôts dont on ne veut qu'une fraction
    # (ex. la Standard Library : seulement les .md, pas les assets/images).
    sparse: tuple[str, ...] = ()
    # Glob compté dans le résumé final (les .ttl pour les modèles, .md pour
    # la library de standards).
    count_glob: str = "*.ttl"


# Registre des sources connues. Pour en ajouter une (ex. BatteryPass), il
# suffit d'ajouter une entrée ici — `fetch_models` et la CLI suivent.
SOURCES: dict[str, Source] = {
    "catenax": Source(
        key="catenax",
        url="https://github.com/eclipse-tractusx/sldt-semantic-models.git",
        default_dest=Path("data/sldt-semantic-models"),
        label="Catena-X",
    ),
    "idta": Source(
        key="idta",
        url="https://github.com/admin-shell-io/smt-semantic-models.git",
        default_dest=Path("data/smt-semantic-models"),
        label="IDTA",
    ),
    # Catena-X Standard Library (Docusaurus) : on n'en veut que les .md des
    # standards released (sous versioned_docs) pour reconstruire le lien
    # standards↔modèles. Clone sparse md-only -> ~quelques Mo au lieu de >100.
    "standards": Source(
        key="standards",
        url="https://github.com/catenax-eV/catenax-ev.github.io.git",
        default_dest=Path("data/cx-standards-library"),
        label="Catena-X Standard Library",
        sparse=(
            "/versions.json",
            "/versioned_docs/*/standards/*.md",
            "/versioned_docs/*/standards/*/*.md",
        ),
        count_glob="*.md",
    ),
}


def _run(cmd: list[str]) -> str:
    logger.debug("$ %s", " ".join(cmd))
    result = subprocess.run(cmd, check=True, text=True, capture_output=True)
    return result.stdout.strip()


def _is_git_repo(path: Path) -> bool:
    return (path / ".git").is_dir()


def clone(url: str, dest: Path, branch: str | None,
          sparse: tuple[str, ...] = ()) -> None:
    logger.info("Clone superficiel %s -> %s", url, dest)
    if sparse:
        # Clone partiel + sparse-checkout : aucun blob non listé n'est
        # rapatrié (`--filter=blob:none`), et seul un sous-ensemble de
        # chemins est matérialisé. Idéal pour un gros dépôt (Docusaurus)
        # dont on ne veut que des .md.
        cmd = ["git", "clone", "--depth", "1", "--filter=blob:none",
               "--no-checkout"]
        if branch:
            cmd += ["--branch", branch]
        cmd += [url, str(dest)]
        _run(cmd)
        _run(["git", "-C", str(dest), "sparse-checkout", "set",
              "--no-cone", *sparse])
        _run(["git", "-C", str(dest), "checkout"])
        return
    cmd = ["git", "clone", "--depth", "1"]
    if branch:
        cmd += ["--branch", branch]
    cmd += [url, str(dest)]
    _run(cmd)


def resync(dest: Path) -> None:
    branch = _run(["git", "-C", str(dest), "rev-parse", "--abbrev-ref", "HEAD"])
    logger.info("Resynchronisation (branche %s) de %s", branch, dest)
    _run(["git", "-C", str(dest), "fetch", "--depth", "1", "origin", branch])
    _run(["git", "-C", str(dest), "reset", "--hard", f"origin/{branch}"])


def count_files(dest: Path, glob: str = "*.ttl") -> int:
    return sum(1 for _ in dest.rglob(glob))


def fetch_models(
    source: str = "catenax",
    dest: Path | None = None,
    branch: str | None = None,
    force: bool = False,
) -> Path:
    """Garantit une copie locale à jour de la source. Retourne le chemin du dépôt."""
    if source not in SOURCES:
        raise ValueError(
            f"Source inconnue: {source!r} (connues: {sorted(SOURCES)})"
        )
    src = SOURCES[source]
    dest = (dest or src.default_dest).resolve()

    logger.info("Source %s (%s) -> %s", src.label, src.key, dest)

    if force and dest.exists():
        logger.info("--force : suppression de %s", dest)
        shutil.rmtree(dest)

    if _is_git_repo(dest):
        try:
            resync(dest)
        except subprocess.CalledProcessError as exc:
            detail = exc.stderr.strip() if exc.stderr else exc
            logger.warning("Resync échouée (%s) — on conserve la copie locale", detail)
    else:
        if dest.exists():
            logger.warning("%s existe mais n'est pas un dépôt git, remplacement", dest)
            shutil.rmtree(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        clone(src.url, dest, branch, src.sparse)

    n = count_files(dest, src.count_glob)
    logger.info("OK — %d fichiers %s disponibles sous %s", n, src.count_glob, dest)
    return dest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Rapatrie les modèles sémantiques (.ttl) en local."
    )
    parser.add_argument(
        "--source", choices=[*sorted(SOURCES), "all"], default="all",
        help="Source à rapatrier (défaut : all)",
    )
    parser.add_argument(
        "--dest", type=Path, default=None,
        help="Répertoire cible (défaut : data/<repo> selon la source ; "
             "incompatible avec --source all)",
    )
    parser.add_argument(
        "--branch", default=None,
        help="Branche à cloner (défaut : branche par défaut du dépôt)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Supprime la copie locale et re-clone proprement",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Logs détaillés (commandes git)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.source == "all" and args.dest is not None:
        parser.error("--dest ne peut pas être utilisé avec --source all")

    sources = sorted(SOURCES) if args.source == "all" else [args.source]
    rc = 0
    for key in sources:
        try:
            fetch_models(
                source=key, dest=args.dest, branch=args.branch, force=args.force,
            )
        except subprocess.CalledProcessError as exc:
            detail = exc.stderr.strip() if exc.stderr else exc
            logger.error("[%s] commande git échouée : %s", key, detail)
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())

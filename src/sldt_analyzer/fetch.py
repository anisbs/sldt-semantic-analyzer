"""Feature 1 — Rapatriement des modèles SLDT.

Clone (ou resynchronise) le dépôt eclipse-tractusx/sldt-semantic-models en
local, en clone superficiel (--depth 1), puis résume les .ttl trouvés.

Usage :
    python -m sldt_analyzer.fetch                # clone ou resync data/sldt-semantic-models
    python -m sldt_analyzer.fetch --force        # supprime et re-clone proprement
    python -m sldt_analyzer.fetch --dest /chemin --branch main -v
"""

from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
from pathlib import Path

REPO_URL = "https://github.com/eclipse-tractusx/sldt-semantic-models.git"
DEFAULT_DEST = Path("data/sldt-semantic-models")

logger = logging.getLogger("sldt.fetch")


def _run(cmd: list[str]) -> str:
    logger.debug("$ %s", " ".join(cmd))
    result = subprocess.run(cmd, check=True, text=True, capture_output=True)
    return result.stdout.strip()


def _is_git_repo(path: Path) -> bool:
    return (path / ".git").is_dir()


def clone(dest: Path, branch: str | None) -> None:
    logger.info("Clone superficiel %s -> %s", REPO_URL, dest)
    cmd = ["git", "clone", "--depth", "1"]
    if branch:
        cmd += ["--branch", branch]
    cmd += [REPO_URL, str(dest)]
    _run(cmd)


def resync(dest: Path) -> None:
    branch = _run(["git", "-C", str(dest), "rev-parse", "--abbrev-ref", "HEAD"])
    logger.info("Resynchronisation (branche %s) de %s", branch, dest)
    _run(["git", "-C", str(dest), "fetch", "--depth", "1", "origin", branch])
    _run(["git", "-C", str(dest), "reset", "--hard", f"origin/{branch}"])


def count_ttl(dest: Path) -> int:
    return sum(1 for _ in dest.rglob("*.ttl"))


def fetch_models(
    dest: Path = DEFAULT_DEST,
    branch: str | None = None,
    force: bool = False,
) -> Path:
    """Garantit une copie locale à jour du dépôt. Retourne le chemin du dépôt."""
    dest = dest.resolve()

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
        clone(dest, branch)

    n = count_ttl(dest)
    logger.info("OK — %d fichiers .ttl disponibles sous %s", n, dest)
    return dest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Rapatrie les modèles SLDT (.ttl) en local."
    )
    parser.add_argument(
        "--dest", type=Path, default=DEFAULT_DEST,
        help=f"Répertoire cible (défaut : {DEFAULT_DEST})",
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

    try:
        fetch_models(dest=args.dest, branch=args.branch, force=args.force)
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() if exc.stderr else exc
        logger.error("Commande git échouée : %s", detail)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

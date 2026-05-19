"""Script de test du parseur sur un petit échantillon varié.

Objectif (convention projet) : valider l'approche d'extraction AVANT de tout
parser. On couvre : SAMM récent, BAMM ancien, modèle complexe, modèle partagé,
et un fichier volontairement malformé (doit être ignoré sans crash).

Usage : .venv/bin/python scripts/test_parser.py
"""

from __future__ import annotations

import logging
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from sldt_analyzer.parser import parse_file  # noqa: E402

MODELS_DIR = ROOT / "data" / "sldt-semantic-models"

SAMPLE = [
    ("SAMM simple", "io.catenax.batch/4.0.0/Batch.ttl"),
    ("BAMM ancien", "io.catenax.batch/1.0.2/Batch.ttl"),
    ("SAMM complexe", "io.catenax.single_level_bom_as_built/4.0.0/SingleLevelBomAsBuilt.ttl"),
    ("Modèle partagé", "io.catenax.shared.industry_core.common/1.0.0/Common.ttl"),
]


def _show(model) -> None:
    kinds = Counter(e.kind for e in model.elements)
    edges = Counter(e.label for e in model.edges)
    print(f"    méta-modèle : {model.meta_model}")
    print(f"    namespace   : {model.namespace}")
    print(f"    éléments    : {len(model.elements)}  {dict(kinds)}")
    print(f"    arêtes      : {len(model.edges)}  {dict(edges)}")
    aspect = next((e for e in model.elements if e.kind == "Aspect"), None)
    if aspect:
        print(f"    aspect      : {aspect.name} — {aspect.preferred_name!r}")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    ok = 0

    for label, rel in SAMPLE:
        path = MODELS_DIR / rel
        print(f"\n[{label}] {rel}")
        if not path.exists():
            print("    ABSENT — ignoré")
            continue
        model = parse_file(path)
        if model is None:
            print("    ÉCHEC parsing (cf. WARNING ci-dessus)")
            continue
        _show(model)
        ok += 1

    # Cas malformé : doit retourner None + WARNING, sans exception.
    print("\n[Cas malformé] turtle invalide")
    with tempfile.NamedTemporaryFile("w", suffix=".ttl", delete=False) as fh:
        fh.write("@prefix : <urn:bad#> .\n:Broken a samm:Aspect  # parenthèse jamais fermée\n:x samm:properties ( :a :b \n")
        bad = Path(fh.name)
    result = parse_file(bad)
    bad.unlink(missing_ok=True)
    print("    -> None (OK, ignoré sans crash)" if result is None
          else "    -> NON ATTENDU : aurait dû être ignoré")

    print(f"\n=== {ok}/{len(SAMPLE)} modèles parsés avec succès ===")
    return 0 if ok == len(SAMPLE) and result is None else 1


if __name__ == "__main__":
    raise SystemExit(main())

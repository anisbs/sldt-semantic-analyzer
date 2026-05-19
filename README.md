# sldt-semantic-analyzer

Analyseur sémantique des modèles **SLDT** (`.ttl` SAMM / Catena-X, dépôt
[`eclipse-tractusx/sldt-semantic-models`](https://github.com/eclipse-tractusx/sldt-semantic-models)) :
rapatriement → parsing → visualisation en graphe.

Construit progressivement, feature par feature. Voir [`CLAUDE.md`](CLAUDE.md)
pour l'état d'avancement, la stack et les conventions.

## Démarrage

```bash
# Rapatrier les modèles en local (data/, git-ignored)
PYTHONPATH=src python3 -m sldt_analyzer.fetch
```

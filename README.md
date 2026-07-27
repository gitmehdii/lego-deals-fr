# bricks

Détecte les promos sur les sets LEGO en France et les publie dans Discord, avec
le prix de référence officiel et l'historique de prix.

La valeur du projet tient en deux points : associer un deal brut à un set LEGO
identifié, et savoir si le prix du jour est réellement bon comparé à tout ce
qu'on a déjà observé.

- `CLAUDE.md` — décisions techniques et règles du projet
- `SPEC.md` — ce que le système fait
- `TICKETS.md` — les six lots, dans l'ordre
- `schema.sql` — le schéma de référence de la base

## Prérequis

[`uv`](https://docs.astral.sh/uv/) et Python 3.12.

## Installation

```bash
uv sync
cp .env.example .env   # puis remplir
```

## Base de données

```bash
uv run alembic upgrade head
```

Crée les cinq tables dans le SQLite pointé par `DATABASE_URL`.

## Commandes

```bash
uv run python -m bricks.ingest --source dealabs
uv run python -m bricks.catalog sync
uv run python -m bricks.health
```

En l'état (lot 1), `ingest` et `catalog` ne font rien d'autre que signaler
qu'ils ne sont pas implémentés, et `health` affiche une page vide.

## Développement

```bash
uv run ruff check .
uv run ruff format .
uv run pytest
```

`tests/test_schema_fidelity.py` compare le DDL produit par les modèles
SQLAlchemy et par la migration Alembic à `schema.sql`. Toute divergence entre
les trois fait échouer la suite.

## État d'avancement

| Lot | Sujet | État |
|---|---|---|
| 1 | Socle | fait |
| 2 | Catalogue | à faire |
| 3 | Ingestion Dealabs | à faire |
| 4 | Résolution | à faire |
| 5 | Détection et alertes | à faire |
| 6 | Observabilité et déploiement | à faire |

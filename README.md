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

En l'état, `ingest` signale qu'il n'est pas implémenté et `health` affiche une
page vide.

### Catalogue

```bash
uv run python -m bricks.catalog sync [--since-year 2016] [--skip-rrp]
```

Remplit la table `sets` en deux temps :

1. **Identité** — les dumps CSV de Rebrickable donnent numéro, nom, thème,
   année, nombre de pièces et image. `name_normalized` est calculé à l'import.
2. **Prix conseillé** — l'API Brickset donne le RRP en euros, année par année.
   Sans `BRICKSET_API_KEY`, cette phase est sautée avec un avertissement et
   l'import de l'identité reste valable.

La commande est idempotente : `updated_at` ne bouge que si quelque chose a
réellement changé, donc un second sync ne touche pas une ligne. `--since-year`
limite la phase des prix aux sets récents, `--skip-rrp` la désactive.

Le RRP couvre 94 % des sets de 500 pièces et plus, et se raréfie en dessous.
Ce n'est pas un trou : ce qui manque, ce sont les BrickLink Designer Program,
le matériel éducatif Dacta et les sachets promotionnels, que LEGO n'a jamais
vendus sur LEGO.com — il n'existe donc aucun prix conseillé à récupérer. Ces
sets ne déclencheront jamais d'alerte par seuil de remise, seulement par plus
bas prix historique.

Les deux phases sont commitées séparément : si l'API Brickset tombe en cours de
route, l'import de l'identité est déjà durable et le sync suivant reprend.

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
| 2 | Catalogue | fait |
| 3 | Ingestion Dealabs | à faire |
| 4 | Résolution | à faire |
| 5 | Détection et alertes | à faire |
| 6 | Observabilité et déploiement | à faire |

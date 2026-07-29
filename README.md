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

Crée les six tables dans la base pointée par `DATABASE_URL`.

## Commandes

```bash
uv run python -m bricks.ingest --source dealabs
uv run python -m bricks.catalog sync
uv run python -m bricks.health
```

### Ingestion

```bash
uv run python -m bricks.ingest --source dealabs
```

Lit le flux RSS que Dealabs publie sur son groupe LEGO, déduplique sur
`(source, external_id)` et empile un `price_point` **à chaque observation**,
même quand le prix n'a pas bougé — « on a regardé et c'était toujours 79,99 »
est une information en soi.

Le prix et le marchand viennent de l'attribut `pepper:merchant` du flux ; le
titre n'est lu qu'en secours.

Chaque exécution écrit une ligne dans `runs`, y compris quand elle échoue :
statut `error`, message nettoyé par `redact_secrets`, et code de sortie 1.

`DEALABS_RSS_URL` pointe par défaut sur le flux public du groupe. Un flux
d'alerte personnel s'y substitue sans toucher au code — mais cette URL-là est
personnelle, traite-la comme un secret.

### Résolution

Chaque offre ingérée est associée à un `set_num`, en deux stratégies :

1. **Numéro de set** — on lit un nombre de 4 à 7 chiffres dans le titre et on
   le croise avec le catalogue. Score 1.0. C'est elle qui fait le travail :
   **97 % des titres réels observés portent leur numéro**.
2. **Correspondance floue** — `rapidfuzz` en `token_sort_ratio` sur
   `name_normalized`, seulement si aucun numéro n'a été trouvé.

En dessous de `MIN_RESOLUTION_SCORE` (0.85), `set_num` reste NULL et l'offre ne
déclenchera jamais d'alerte. Le score et la méthode sont stockés dans tous les
cas, y compris quand le verdict est rejeté.

Les pièges traités, chacun couvert par un test : une année (`LEGO Star Wars
2024`) n'est pas un numéro, un décompte de pièces non plus, un prix non plus,
et deux vrais sets dans un même titre donnent NULL plutôt qu'un pari.

`tests/fixtures/titles.yaml` est le filet de sécurité : de vrais titres
Dealabs avec le `set_num` attendu. **À enrichir chaque fois qu'une résolution
rate.**

### Détection et alertes

```bash
uv run python -m bricks.ingest --source dealabs --dry-run
```

Une offre résolue, active et avec un prix est évaluée sur deux critères
indépendants — il suffit qu'un seul soit vrai :

- **A, seuil de remise** : `discount_pct >= MIN_DISCOUNT_PCT`, calculé sur le
  RRP et **jamais** sur le prix barré du marchand, qui est du marketing.
- **B, plus bas prix historique** : strictement sous tout ce qui a été observé
  pour ce set, tous marchands confondus, et seulement à partir de 3
  observations antérieures.

Trois garde-fous : pas deux alertes pour la même offre à moins de 24 h, pas de
nouvelle alerte si le prix n'a pas baissé d'au moins 5 % depuis la dernière, et
10 alertes maximum par run. Atteindre le plafond est journalisé bruyamment,
parce que c'est plus souvent un bug qu'un vendredi noir.

`--dry-run` affiche les embeds en console sans toucher ni à Discord ni à la
table `alerts`. Sans `DISCORD_WEBHOOK_URL`, le run prend le même chemin avec un
avertissement : les offres et les price points valent déjà le coup.

Seules les offres **vues pendant le run** sont évaluées. Alerter sur un prix
que personne n'a confirmé aujourd'hui enverrait le lecteur sur une page morte.

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

### Santé

```bash
uv run python -m bricks.health
```

Dernier run par source, offres actives, taux de résolution sur les 100
dernières offres, alertes sur 7 jours. **Sort en code 1** quand une source a
l'air morte, pour qu'un cron le remarque sans que personne lise la page.

Une source est déclarée morte après 3 runs vides ou 3 échecs consécutifs. Un
avertissement part alors dans Discord, en rouge et sans vignette ni prix —
impossible à confondre avec un deal — puis plus rien pendant 24 h, quelle que
soit la durée de la panne. Répéter toutes les 15 minutes apprendrait à
l'ignorer, ce qui est pire que le silence.

## Architecture

```
                 Rebrickable ──┐
                 Brickset ─────┤
                 Dealabs ──────┤
                               ▼
                         sources/          récupère, ne persiste rien
                               │
                               ▼
                         services/         orchestre + persiste
                          │        │        (n'a jamais entendu parler de Discord)
                          ▼        ▼
                       core/      db/      logique pure   SQLAlchemy
                          │
                          ▼
                       adapters/
                       ├── cli/            ingest · catalog · health
                       └── webhook/        embeds Discord, français
```

La règle qui tient l'ensemble : **`services/` n'importe jamais rien de
`adapters/`**. C'est ce qui permettra d'ajouter un serveur MCP ou une API sans
rien refactorer, et c'est aussi pourquoi le français ne vit que dans
`adapters/webhook/`.

## Déploiement

Deux workflows planifiés, qui appellent le CLI et rien d'autre :

| Workflow | Cadence | Commande |
|---|---|---|
| `ingest.yml` | toutes les 15 min | `ingest --source dealabs` |
| `catalogue.yml` | lundi | `catalog sync --since-year 2015` |

Secrets GitHub à renseigner : `DATABASE_URL`, `DISCORD_WEBHOOK_URL`,
`DEALABS_RSS_URL`, `BRICKSET_API_KEY`.

`DATABASE_URL` **doit** pointer sur Turso : le disque d'un runner est effacé
entre deux exécutions, donc un SQLite sur fichier repartirait vide à chaque
fois et `price_points` — la seule table que personne ne pourrait reconstruire
— n'accumulerait jamais rien.

```
sqlite+libsql://<base>.turso.io/?authToken=<token>&secure=true
```

Cette URL s'écrit sous cette forme exacte, pour deux raisons qui ne se voient
pas :

- **`sqlite+`** — `turso db show --url` donne `libsql://…`. Aucun dialecte
  SQLAlchemy ne répond à ce nom : collée telle quelle, l'URL échoue en
  `NoSuchModuleError`, loin de l'endroit où on l'a saisie.
- **`secure=true`** — le pilote `sqlalchemy-libsql` choisit `http` ou `https`
  d'après ce drapeau, **et le met à `false` par défaut**. Sans lui, la
  connexion part en clair avec le token dans la query string, et rien ne le
  signale.

Les deux formes sont refusées au démarrage, avec la forme attendue dans le
message. `tests/test_config.py` épingle la règle au pilote lui-même plutôt
qu'à ce paragraphe : chaque URL acceptée est passée au vrai dialecte, et le
test échoue si l'une d'elles construit un `http://`.

Mise en place :

```bash
turso db create bricks
turso db show bricks --url          # libsql://bricks-<org>.turso.io
turso db tokens create bricks
```

Recomposer l'URL à la main, puis la vérifier depuis la machine avant de la
coller dans un secret GitHub — un runner est un mauvais endroit pour découvrir
qu'une URL est fausse :

```bash
export DATABASE_URL='sqlite+libsql://…/?authToken=…&secure=true'
uv run alembic upgrade head
uv run python -m bricks.health      # « Database driver  sqlite+libsql »
```

`health` sur une base vide lit les six tables et sort en 0 : c'est le test de
fumée du branchement, il n'en existe pas de plus court.

Ensuite renseigner les quatre secrets, et lancer `catalogue.yml` à la main une
première fois : `ingest.yml` applique bien les migrations, mais sans catalogue
aucune offre ne se résout.

Ce qui reste non vérifié à ce jour : tout ce qui précède a été exercé de bout
en bout **à travers le dialecte libSQL sur un fichier local** — migrations,
lectures, écritures, et l'insertion en masse des 27 843 sets. Le trajet
distant, lui, n'a jamais été essayé faute d'identifiants.

Aucune ligne de `src/` ne sait qu'elle tourne dans GitHub Actions. Passer sur
un VPS, c'est changer le déclencheur.

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
| 3 | Ingestion Dealabs | fait |
| 4 | Résolution | fait (jeu de test à 39/50) |
| 5 | Détection et alertes | fait (rendu visuel des embeds jamais regardé) |
| 6 | Observabilité et déploiement | code fait et vérifié ; reste le branchement Turso, qui demande des identifiants |

Le lot 6 est terminé côté code. Ce qui a été vérifié à la main : `health` sur
les données réelles, et l'avertissement de santé qui part **exactement à la
3ᵉ exécution**, aussi bien quand le flux est injoignable que quand il répond
un RSS valide et vide.

Ce qui ne peut pas l'être sans identifiants Turso ni secrets GitHub : la
connexion distante elle-même, et les 48 h de fonctionnement autonome que
TICKETS.md demande.

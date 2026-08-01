# TICKETS.md

Six lots, dans l'ordre. Chacun est taillé pour une session Claude Code et se
termine par quelque chose de vérifiable à la main.

**Règle** : on ne commence pas un lot tant que le précédent ne remplit pas son
critère de fin. Pas de branche parallèle, pas d'anticipation sur le lot suivant.

---

## Lot 1 : socle

Poser la structure et l'outillage. Aucune logique métier.

**Contenu**

- Arborescence `sources/ core/ services/ adapters/ db/` avec des `__init__.py`
- `pyproject.toml` géré par `uv`, dépendances de CLAUDE.md
- Configuration via `pydantic-settings`, plus un `.env.example` complet
- Modèles SQLAlchemy correspondant à `schema.sql`, migration Alembic initiale
- Logging `structlog` en JSON, avec un `run_id` injectable en contexte
- Trois entrées CLI qui existent et ne font rien : `ingest`, `catalog`, `health`
- `ruff` configuré, workflow GitHub Actions qui lance lint et tests
- README court : à quoi sert le projet, comment lancer en local

**Terminé quand**

`uv run alembic upgrade head` crée les cinq tables dans un SQLite local, et
`python -m bricks.health` affiche une page vide sans planter.

---

## Lot 2 : catalogue

Remplir la table `sets`. Sans elle, aucune résolution n'est possible.

**Contenu**

- Téléchargement et import du dump CSV Rebrickable (sets, thèmes)
- Calcul de `name_normalized` à l'import
- Enrichissement RRP via l'API Brickset
- Commande idempotente : deux exécutions ne créent pas de doublons
- Gestion propre du cas « pas de prix en euros » : `rrp_eur` reste NULL

**Terminé quand**

`python -m bricks.catalog sync` remplit la table avec plusieurs milliers de
sets, une requête SQL sur `10497-1` renvoie le bon nom et un RRP en euros, et
relancer la commande ne change pas le nombre de lignes.

---

## Lot 3 : ingestion Dealabs

Faire entrer des offres dans la base. Pas encore de résolution, pas d'alerte.

**Contenu**

- Protocole `Source` et modèle `RawOffer`
- Implémentation `DealabsSource` : lecture RSS, prix et marchand lus dans
  `pepper:merchant`, extraction depuis le titre en secours
- Déduplication sur `(source, external_id)`
- Création d'un `PricePoint` à chaque observation, y compris quand le prix
  n'a pas bougé
- Traçage complet dans `runs`, y compris en cas d'exception
- Respect des règles de scraping de CLAUDE.md

**Terminé quand**

`python -m bricks.ingest --source dealabs` remplit `offers` et
`price_points`, une seconde exécution immédiate ne crée aucune offre en double
mais bien de nouveaux price points, et la table `runs` contient deux lignes en
statut `ok`.

---

## Lot 4 : résolution

Le cœur du projet. C'est le lot où il faut prendre son temps.

**Contenu**

- Stratégie 1 : extraction du numéro de set, croisée avec le catalogue
- Stratégie 2 : correspondance floue `rapidfuzz` sur `name_normalized`
- Normalisation du titre : accents, ponctuation, mots vides du domaine, prix,
  nom du marchand
- Score et méthode systématiquement stockés
- Seuil `MIN_RESOLUTION_SCORE`, en dessous duquel `set_num` reste NULL
- `tests/fixtures/titles.yaml` avec au moins 50 titres réels de Dealabs et le
  résultat attendu pour chacun

**Terminé quand**

La suite de tests passe sur le jeu de 50 titres avec au moins 90 % de
résolutions correctes et **zéro faux positif** au-dessus du seuil. Un faux
positif compte plus lourd qu'un échec de résolution : mieux vaut rater un deal
que d'annoncer le mauvais set.

---

## Lot 5 : détection et alertes

Le premier lot qui produit quelque chose de visible.

**Contenu**

- Calcul du discount à partir du RRP, jamais du prix barré
- Critère A : seuil de remise
- Critère B : plus bas prix historique, avec le minimum de 3 observations
- Les trois règles anti-spam de la spec
- Construction de l'embed Discord au format décrit en SPEC.md section 6
- Envoi via webhook, dans `adapters/webhook/`
- Écriture d'une ligne dans `alerts` pour chaque message envoyé
- Option `--dry-run` qui affiche l'embed en console sans rien envoyer

**Terminé quand**

Un run complet sur des données réelles envoie une alerte correctement formatée
dans un salon de test, la relancer aussitôt n'envoie rien grâce à l'anti-spam,
et `--dry-run` ne touche ni Discord ni la table `alerts`.

---

## Lot 6 : observabilité et déploiement

Le lot qui fait passer le projet de « ça marche chez moi » à quelque chose de
sérieux.

**Contenu**

- `python -m bricks.health` complet : dernier run par source, offres actives,
  taux de résolution sur les 100 dernières offres, alertes sur 7 jours
- Alerte de santé Discord après 3 runs à zéro entrée ou 3 échecs consécutifs,
  visuellement distincte, pas plus d'une fois par 24 h
- Migration vers Turso, `DATABASE_URL` qui bascule entre SQLite local et libSQL
- Workflow GitHub Actions planifié toutes les 15 minutes, appelant le CLI et
  rien d'autre
- Secrets en secrets GitHub
- README complété avec un schéma de l'architecture

**Terminé quand**

Le pipeline tourne seul pendant 48 h sans intervention, `health` remonte des
chiffres cohérents, et couper volontairement l'URL du flux RSS déclenche
l'alerte de santé au bon moment.

---

## Après la v1

Dans cet ordre de priorité, et seulement une fois les six lots terminés.

- **v1.1** Slash commands via Discord HTTP Interactions sur une fonction Vercel.
  `/set 10497` pour l'historique de prix, `/stats` pour la santé du pipeline.
- **v1.2** Une deuxième source, pour valider que l'abstraction tient vraiment.
- **v1.3** Configuration multi-serveur, en activant le `guild_id` déjà présent.
- **Plus tard** MCP, site web, affiliation. Rien de tout ça n'est décidé.

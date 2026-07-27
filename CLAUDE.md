# CLAUDE.md

Brief permanent du projet. À lire avant toute session de dev.

## Ce qu'on construit

Un pipeline qui détecte les promos sur les sets LEGO en France et les publie
dans Discord, avec le prix de référence officiel et l'historique de prix.

**Ce n'est pas un bot qui repost du RSS.** La valeur est dans deux choses :
associer un deal brut à un set LEGO identifié, et savoir si le prix du jour est
réellement bon comparé à tout ce qu'on a déjà observé.

Utilisateur unique en v1 : le développeur. Pas de multi-serveur, pas de site,
pas d'affiliation. Ces choses arriveront peut-être plus tard, elles ne doivent
pas influencer une seule ligne de code aujourd'hui.

## Vocabulaire

Ces huit mots ont une définition unique. Ne jamais inventer de synonyme, ni en
français, ni en anglais, ni dans un nom de variable.

| Terme | Définition |
|---|---|
| **Set** | Un produit LEGO identifié par son numéro officiel (`10497-1`). Référence catalogue. Ne porte jamais de prix courant. |
| **Offer** | Un set proposé à un prix donné, chez un marchand donné, à une URL donnée. C'est ce qu'une Source nous remonte. |
| **PricePoint** | Un relevé de prix daté rattaché à une Offer. On empile, on n'écrase jamais. |
| **RRP** | Prix public conseillé par LEGO, en euros. La seule référence honnête pour calculer une remise. |
| **Discount** | `(rrp - price) / rrp * 100`, **toujours en pourcentage entre 0 et 100**, jamais en ratio. Même unité en configuration (`MIN_DISCOUNT_PCT`) et en base (`alerts.discount_pct`), donc aucune conversion nulle part dans le code. Jamais calculé sur le prix barré du marchand, qui est du marketing. |
| **Resolution** | Associer le titre d'une Offer à un `set_num`. Produit toujours un score de confiance. |
| **Alert** | Un message Discord effectivement envoyé. Une Offer peut exister sans Alert. |
| **Run** | Une exécution du pipeline d'ingestion, tracée en base. |

## Langue

La règle est le **destinataire**, pas l'endroit où vit le texte.

- Destinataire final, c'est-à-dire ce qui part dans Discord : **français**
- Destinataire développeur, c'est-à-dire code, noms de tables, noms de
  colonnes, commentaires, messages de commit, sortie du CLI, aide `--help`,
  logs et messages d'exception : **anglais**
- Documentation du repo (ce fichier, README.md, SPEC.md, TICKETS.md, commentaires
  de `.env.example`) : **français**

Pas de mélange à l'intérieur d'une de ces trois catégories.

> Note : si le repo passe public un jour, README et SPEC devront être traduits
> en anglais. Décision consciente à prendre à ce moment-là, pas avant.

## Stack

| Rôle | Choix |
|---|---|
| Langage | Python 3.12 |
| Gestion des deps | `uv` |
| HTTP | `httpx` |
| Parsing RSS | `feedparser` |
| Validation / modèles | `pydantic` v2 |
| Config | `pydantic-settings` |
| ORM + migrations | `SQLAlchemy` 2.x + `Alembic` |
| Base de données | SQLite en local, Turso (libSQL) en prod |
| Fuzzy matching | `rapidfuzz` |
| Logs | `structlog` (sortie JSON) |
| Tests | `pytest` |
| Slash commands (v1.1) | `discord.py` |

Ne pas ajouter de dépendance sans raison écrite dans la PR. Pas de framework
web en v1, il n'y a pas de serveur.

## Architecture

```
src/bricks/
  sources/      Récupère des offres brutes depuis le monde extérieur.
                Une source = un fichier. Interface commune `Source`.
                Ne sait rien de la base ni de Discord.

  core/         Logique métier pure. Résolution, calcul de remise,
                détection d'un deal qui vaut une alerte.
                Fonctions sans effet de bord, faciles à tester.

  services/     API interne. Orchestre core + persistance.
                Pydantic en entrée, Pydantic en sortie.
                ZÉRO import de discord, zéro connaissance de l'affichage.

  adapters/     Le monde extérieur consomme services/.
                webhook/    envoi des alertes Discord
                cli/        points d'entrée en ligne de commande
                (plus tard : mcp/, api/, bot/)

  db/           Modèles SQLAlchemy, migrations Alembic.
```

**La règle qui compte** : `services/` ne doit jamais importer quoi que ce soit
de `adapters/`, et ne doit jamais savoir qu'un Discord existe. C'est ce qui rend
le projet lisible pour quelqu'un qui reprend le code, et c'est ce qui permettra
d'ajouter un serveur MCP ou une API plus tard sans rien refactorer.

Si tu te retrouves à importer `httpx` dans `core/`, tu t'es trompé de couche.

## Point d'entrée

Le pipeline est un CLI pur :

```bash
python -m bricks.ingest --source dealabs
python -m bricks.catalog sync
python -m bricks.health
```

**Aucune ligne de code ne doit savoir qu'elle tourne dans GitHub Actions.** Pas
de lecture de `GITHUB_*`, pas de `::notice::`, rien. Le workflow appelle le CLI,
c'est tout. Le jour où on bascule sur un VPS, on change le déclencheur et rien
d'autre.

## Configuration

Tout passe par variables d'environnement, chargées via `pydantic-settings`.
Un `.env.example` à jour est maintenu à la racine.

```
DATABASE_URL           sqlite:///local.db  ou  sqlite+libsql://...
BRICKSET_API_KEY
DISCORD_WEBHOOK_URL
DEALABS_RSS_URL
MIN_DISCOUNT_PCT       défaut 25      pourcentage, 0-100
MIN_RESOLUTION_SCORE   défaut 0.85    ratio, 0-1
LOG_LEVEL              défaut INFO
```

**Jamais de secret en dur, jamais de secret dans un commit, jamais de secret
dans un log.** Si un log doit mentionner l'URL du webhook, il affiche
`DISCORD_WEBHOOK_URL set: true`, pas la valeur.

## Règles de scraping

On est un petit projet qui consomme le travail des autres. On se tient bien.

- User-Agent explicite et honnête, avec une URL de contact
- Un seul appel par run et par source, jamais de boucle serrée
- Respect de `robots.txt`
- Timeout et retry avec backoff exponentiel, maximum 3 tentatives
- Sur une erreur 429 ou 5xx : on abandonne le run et on réessaie au suivant,
  on ne martèle pas

En v1 on ne scrape aucun site marchand directement. On lit un flux RSS que
Dealabs publie volontairement. Cette contrainte est délibérée.

## Ce qui est hors périmètre v1

À rappeler à voix haute si une session part dans cette direction :

- Scrapers directs sur les sites marchands
- Support multi-serveur actif (le `guild_id` existe en base, il n'est pas exploité)
- Site web, SEO, pages produit
- Liens d'affiliation
- Alertes personnalisées par utilisateur
- Serveur MCP
- Slash commands (repoussées en v1.1)

## Définition de "terminé"

Une tâche n'est finie que si :

1. Le comportement décrit dans TICKETS.md est vérifiable à la main
2. La logique de `core/` est couverte par des tests
3. `ruff check` et `ruff format` passent
4. Le `.env.example` est à jour si une variable a été ajoutée
5. Aucun secret n'est apparu dans le diff

## Style de code

- Type hints partout, sans exception
- Docstrings uniquement quand le nom de la fonction ne suffit pas
- Pas de commentaire qui paraphrase le code
- Une fonction qui dépasse 40 lignes est un signal, pas une fatalité
- Les datetime sont en UTC, stockées en ISO 8601, converties en Europe/Paris
  seulement au moment de l'affichage

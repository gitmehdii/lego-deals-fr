# SPEC.md

Spécification fonctionnelle de la v1.

Ce document décrit **ce que le système fait**. Le comment est dans le code,
les décisions techniques sont dans CLAUDE.md.

---

## 1. La vie d'une offre, racontée

Le plus simple pour comprendre le projet est de suivre un deal du début à la fin.

**07h00.** Le cron se déclenche. Un `Run` est créé en base avec le statut
`running`. À partir de maintenant, quoi qu'il arrive, on saura que ce run a eu
lieu et comment il s'est terminé.

**07h00.** Le pipeline demande à la source Dealabs ce qu'elle a de neuf. Elle
répond avec une liste d'entrées RSS brutes. Une entrée ressemble à ça :

> « LEGO Icons 10497 Galaxy Explorer à 79,99€ (au lieu de 99,99€) - Amazon »

**07h00.** Pour chaque entrée, on regarde si on la connaît déjà, grâce au couple
`(source, external_id)`. Trois cas :

- Jamais vue : on crée une `Offer`
- Déjà vue, même prix : on met à jour `last_seen_at` et on s'arrête là
- Déjà vue, prix différent : on met à jour l'offre et on continue

**07h00.** Nouvelle offre, donc il faut savoir de quel set on parle. C'est la
**Resolution**, détaillée en section 3. Ici le titre contient `10497`, ce numéro
existe dans notre catalogue, la résolution renvoie `10497-1` avec un score de
1.0. Si aucun set n'avait été trouvé avec assez de confiance, l'offre serait
quand même enregistrée mais avec `set_num` à NULL, et aucune alerte ne partirait.

**07h00.** On enregistre un `PricePoint` : 79,99 € observés maintenant. C'est
une ligne de plus, on n'écrase jamais la précédente. Dans un an cette table sera
la seule chose que personne d'autre ne pourra reconstruire.

**07h00.** On regarde si ça vaut une alerte. Le catalogue nous dit que le RRP du
10497 est 99,99 €. La remise réelle est donc de 20 %. Le seuil est à 25 %. On
regarde alors le second critère : est-ce le prix le plus bas jamais observé pour
ce set, toutes offres confondues ? Non, on l'avait vu à 74,90 € en mars. Donc
**pas d'alerte**. L'offre est en base, l'historique est enrichi, mais on ne
dérange personne.

**07h00.** Le `Run` passe à `ok` avec ses compteurs : 34 entrées trouvées,
3 nouvelles, 3 résolues, 0 alerte.

**Le lendemain, 12h15.** Même offre, le prix est passé à 69,99 €. Nouvelle
`PricePoint`. La remise est maintenant de 30 %, au-dessus du seuil, et c'est en
plus le prix le plus bas jamais vu. Une `Alert` est créée et le message part
dans Discord avec la mention « plus bas prix observé ».

**Et si le parser casse ?** Le run se termine en `error`, ou en `ok` avec zéro
entrée trouvée. Au bout de trois runs consécutifs à zéro entrée, un message
d'avertissement part dans Discord. C'est la seule façon de ne pas découvrir dans
trois semaines que le projet est mort.

---

## 2. Sources

Une source répond à une seule question : « qu'est-ce qui est en promo en ce
moment ? ». Elle ne persiste rien, elle ne sait pas ce qu'est un set LEGO.

Interface commune :

```python
class Source(Protocol):
    name: str

    def fetch(self) -> list[RawOffer]: ...
```

`RawOffer` est un modèle Pydantic volontairement pauvre :

| Champ | Type | Note |
|---|---|---|
| `external_id` | str | Identifiant du deal chez la source |
| `title` | str | Titre brut, non nettoyé |
| `url` | str | Lien vers le deal |
| `price_eur` | float \| None | None si la source ne le donne pas |
| `merchant` | str \| None | Amazon, Cdiscount, Fnac... |
| `published_at` | datetime \| None | |

### Source v1 : Dealabs

Lecture du flux RSS que Dealabs publie sur son groupe LEGO. L'URL est en
configuration, elle n'est jamais en dur, et un flux d'alerte personnel
configuré sur le mot-clé « lego » s'y substitue sans toucher au code.

Le flux porte une extension propre à la plateforme Pepper :

```xml
<pepper:merchant name="Alternate" price="158,90€"/>
```

**C'est la source primaire du prix et du marchand.** Observée présente sur
l'intégralité des entrées d'un flux réel, elle est structurée, donc plus fiable
que n'importe quelle lecture du titre. Une version antérieure de ce document
prescrivait d'extraire le prix du titre par expression régulière ; c'était
écrit avant d'avoir regardé le flux.

L'extraction depuis le titre reste implémentée, en **secours** : elle sert
quand l'attribut est absent ou illisible, et elle gère les formats `79,99€`,
`79.99 €` et `79€99`. Si aucun prix n'est obtenu par l'une ou l'autre voie,
`price_eur` reste à None et l'offre est ignorée pour la détection, mais
conservée en base.

### Sources futures

Le point de cette abstraction est qu'ajouter une source revienne à écrire un
fichier dans `sources/` et à l'enregistrer. Rien d'autre dans le code ne doit
changer.

---

## 3. Resolution

C'est le morceau le plus intéressant du projet et celui qui mérite le plus de
tests.

**Problème** : passer d'un titre écrit par un humain pressé à un `set_num`
officiel, sans se tromper. Une mauvaise résolution est pire qu'une absence de
résolution, parce qu'elle produit une alerte fausse.

### Stratégie 1 : numéro de set explicite

On cherche dans le titre toute suite de 4 à 7 chiffres. Pour chaque candidat, on
vérifie qu'il existe dans la table `sets`. Si un seul candidat matche, score
**1.0**, méthode `set_number`.

Pièges à gérer, chacun mérite un test :

- « LEGO Star Wars 2024 » : 2024 est une année, elle ne doit pas matcher un set
- « Lot de 1000 pièces » : 1000 pourrait exister comme set, d'où la nécessité de
  croiser avec le reste du titre
- « 75192 UCS Millennium Falcon 7500 pièces » : deux candidats, on prend celui
  qui existe au catalogue et on baisse le score si les deux existent
- Numéros avec suffixe : `10497-1` et `10497` doivent mener au même set

### Stratégie 2 : correspondance floue sur le nom

Si aucun numéro n'a été trouvé. On normalise le titre (minuscules, accents
retirés, ponctuation retirée, mots vides du domaine retirés : « lego », « set »,
« jeu de construction », « à », « au lieu de », le prix, le nom du marchand).

On compare ensuite au champ `name_normalized` de la table `sets` avec
`rapidfuzz`, en **`token_sort_ratio`**. Le meilleur score, ramené entre 0 et 1,
devient le score de résolution, méthode `fuzzy_name`.

> **Correction.** Une version antérieure prescrivait `token_set_ratio`. C'est
> faux, et d'une façon qui produit exactement les faux positifs que le lot 4
> interdit : ce scorer renvoie **100 dès que les mots d'un nom du catalogue
> sont inclus dans le titre**. Mesuré sur le catalogue réel de 27 810 sets,
> après normalisation du titre :
>
> | Titre | `token_set_ratio` | `token_sort_ratio` |
> |---|---|---|
> | `Lego Harry Potter … sur PS5` (un jeu vidéo) | **100** → `71022-1` | 67.6 |
> | `Sélection de trois Botanicals…` (un lot) | **100** → « rose » | 58.8 |
> | `LEGO Star Wars 75447 - Le Razor Crest` | **100** → `75292-1`, le Razor Crest **de 2019** | 66.7 |
>
> Quatre faux positifs sur cinq sondes, tous au score maximum. Ce n'est pas un
> problème de réglage : `token_set_ratio` ignore par construction ce que le
> titre contient en plus. `token_sort_ratio` tient compte de la longueur et
> reste sous le seuil dans tous ces cas.

**Conséquence assumée : la stratégie 2 se déclenche rarement.** Les titres
Dealabs sont en français, les noms Rebrickable en anglais, et aucune mesure de
similarité ne franchit honnêtement cet écart. C'est la stratégie 1 qui fait le
travail — 97 % des titres réels observés portent leur numéro de set. La
stratégie 2 n'existe que pour les cas où le titre partage des noms propres avec
le catalogue, et son silence est un comportement correct, pas une panne.

### Seuil

Une offre n'est associée à un set que si le score dépasse `MIN_RESOLUTION_SCORE`
(0.85 par défaut). En dessous, `set_num` reste NULL. L'offre est conservée, elle
est simplement invisible pour la détection.

Le score et la méthode sont **toujours** stockés. C'est ce qui permettra un jour
de mesurer la qualité de la résolution plutôt que de la deviner.

### Jeu de test

Un fichier `tests/fixtures/titles.yaml` contient au minimum 50 titres réels
copiés depuis Dealabs, avec le `set_num` attendu (ou `null`). C'est le filet de
sécurité du projet, à enrichir chaque fois qu'une résolution rate.

---

## 4. Catalogue

La table `sets` est alimentée par une commande dédiée, lancée manuellement ou
une fois par semaine.

Deux sources complémentaires :

- **Rebrickable** publie des dumps CSV complets du catalogue (numéro, nom,
  année, thème, nombre de pièces). C'est la base, et c'est bien plus efficace
  que d'interroger une API set par set.
- **Brickset** fournit le prix de détail conseillé via son API. C'est ce qui
  nous donne le RRP, sans lequel aucune remise n'est calculable honnêtement.

Le RRP doit être en euros. Si l'API ne renvoie qu'un prix dans une autre devise
pour un set donné, le champ `rrp_eur` reste NULL et ce set ne déclenchera jamais
d'alerte par seuil de remise. Il pourra toujours en déclencher une par plus bas
prix historique.

La commande est idempotente : la relancer deux fois ne crée pas de doublons et
met simplement à jour ce qui a changé.

---

## 5. Détection

Une offre résolue, avec un prix, est évaluée selon deux critères indépendants.

**Critère A, seuil de remise.** `discount_pct >= MIN_DISCOUNT_PCT` (25 % par
défaut). Nécessite un `rrp_eur` connu.

**Critère B, plus bas prix historique.** Le prix observé est strictement
inférieur à tous les `price_points` déjà enregistrés pour ce set, toutes offres
et tous marchands confondus. Ne nécessite pas de RRP. Ne se déclenche que si on
a au moins 3 observations antérieures, sinon « plus bas prix jamais vu » ne veut
rien dire.

Une alerte part si A **ou** B est vrai.

### Anti-spam

Trois garde-fous, chacun testable :

1. Pas deux alertes pour la même `offer` à moins de 24 h d'intervalle
2. Pas de nouvelle alerte pour la même offre si le prix n'a pas baissé d'au
   moins 5 % depuis la dernière alerte
3. Maximum 10 alertes **par salon** et par run, les meilleures d'abord. Si un
   plafond est atteint, le run le journalise clairement, parce que c'est
   souvent le signe d'un bug plutôt que d'un vendredi noir

> Le plafond était global jusqu'au 2 août 2026. Mesuré sur des données
> réelles, il coupait à chaque run : 19 offres qualifiantes sur 30 actives, et
> une avalanche de promos fidélité sur un seul thème suffisait à faire taire
> tous les autres. Par salon, chacun a son quota et une catégorie ne peut plus
> étouffer les autres.

Un plus bas prix historique passe devant une alerte de seuil quel que soit son
pourcentage. La plupart des sets n'ont pas de RRP, donc pas de remise
calculable : classés sur la remise seule, leurs records se retrouvaient
derrière tout le reste et étaient les premiers écartés par le plafond — soit
l'inverse exact de ce que la section 5 appelle « le fait le plus rare ».

---

## 5 bis. Où part l'alerte

Le serveur porte plusieurs salons, et une alerte va dans celui qui correspond
au thème du set. Cinq salons, volontairement larges :

| Salon | Thèmes |
|---|---|
| `star_wars` | Star Wars |
| `collection` | Icons, Botanicals, Architecture, Ideas, Brickheadz |
| `vehicules` | Technic, Speed Champions, Racers, Train |
| `univers` | Harry Potter, Marvel, DC, Minecraft, Mario, Ninjago, Disney… |
| `divers` | tout le reste, **y compris tout thème inconnu** |

Le catalogue compte 150 thèmes et les promos réelles n'en touchent qu'une
vingtaine, dont la moitié une seule fois. Un salon par thème serait deux
douzaines de pièces vides. Le fourre-tout garantit qu'un thème que LEGO
inventera l'an prochain est discret, jamais perdu.

Le routage est une décision métier, pas un détail d'affichage : `core/` choisit
le nom du salon, `alerts.channel_id` l'enregistre, et `adapters/` se contente
d'y associer une URL de webhook. Un webhook Discord étant lié à un seul salon,
il en faut un par salon ; celui qui manque retombe sur le fourre-tout.

## 6. Format de l'alerte

Un embed Discord, en français.

```
🧱  LEGO Icons 10497 Galaxy Explorer

     69,99 €          au lieu de 99,99 €          -30 %

     🔻 Plus bas prix jamais observé
     Précédent record : 74,90 € le 12 mars 2026

     Marchand : Amazon
     1 254 pièces · Icons · 2022

     [ Voir le deal ]
```

Règles d'affichage :

- La couleur de l'embed suit la remise : vert au-delà de 40 %, orange entre
  25 et 40 %, gris en dessous
- L'image du set en thumbnail quand elle est disponible
- La ligne « plus bas prix » n'apparaît que si le critère B s'est déclenché
- Les dates sont affichées en Europe/Paris, jamais en UTC
- Le lien pointe vers le deal, jamais vers une redirection maison en v1

---

## 7. Observabilité

Sans cette section, le projet est un jouet.

**Chaque run est tracé** dans la table `runs`, avec ses compteurs et son statut.
Un run qui plante enregistre quand même sa ligne, avec le message d'erreur.

**Les logs sont structurés** en JSON via `structlog`, avec systématiquement le
`run_id` en contexte. Un log doit répondre à « qu'est-ce qui s'est passé et pour
quel run », pas raconter une histoire.

**Alerte de santé.** Trois raisons, dans cet ordre de priorité :

| Raison | Déclencheur |
|---|---|
| `failing` | les 3 derniers runs ont échoué |
| `no_items` | les 3 derniers runs ont trouvé zéro entrée |
| `stale` | aucun run réussi depuis 18 heures |

Le message est distinct visuellement des alertes de deals et n'est pas répété
plus d'une fois par 24 h.

Les deux premières comptent des runs **qui ont eu lieu**. La troisième existe
parce qu'un run qui ne démarre jamais n'écrit aucune ligne dans `runs` : les
compteurs restent à zéro et rien ne le remarque. Observé en production —
4 exécutions planifiées sur une semaine ont été annulées par GitHub avant la
moindre étape, et la base n'en garde aucune trace. **La seule chose que la
surveillance ne voit pas, c'est sa propre absence.**

Le seuil de 18 h est mesuré, pas deviné : sur une semaine, le plus long écart
réel entre deux runs réussis a été de 9,5 heures, un creux de nuit. Le reste
est de la marge, parce qu'un avertissement qu'on apprend à ignorer est pire
que le silence.

> **Limite assumée.** Cette règle est évaluée pendant un run et par
> `bricks.health`. Si plus aucun run ne démarre du tout, rien ne l'évalue —
> `health` le dira à qui le lance, mais aucun message ne partira tout seul.
> Fermer complètement ce trou demanderait un veilleur externe et indépendant,
> hors périmètre v1.

**Commande de diagnostic.** `python -m bricks.health` affiche en une page :
date du dernier run réussi par source, nombre d'offres actives, taux de
résolution des 100 dernières offres, nombre d'alertes envoyées sur 7 jours.

Le taux de résolution est la métrique à surveiller. S'il s'effondre, c'est que
Dealabs a changé son format de titre ou que le catalogue est périmé.

---

## 8. Ce que la v1 ne fait pas

Écrit ici pour qu'on puisse dire non sans rediscuter :

- Aucun scraping de site marchand
- Aucune commande Discord (repoussé en v1.1)
- Aucune personnalisation par utilisateur
- Aucun lien d'affiliation
- Aucune interface web
- Aucun serveur MCP
- Une seule source, une seule destination

# lego-deals-fr

Finds LEGO discounts in France and posts them to Discord, with the official recommended
price and the price history behind them.

[![ci](https://github.com/gitmehdii/lego-deals-fr/actions/workflows/ci.yml/badge.svg)](https://github.com/gitmehdii/lego-deals-fr/actions/workflows/ci.yml)

The value is in two things: matching a raw deal to an identified LEGO set, and knowing
whether today's price is actually good compared to everything observed so far. The Python
package is called `bricks`.

## Quick start

Requirements: [`uv`](https://docs.astral.sh/uv/) and Python 3.12.

```bash
uv sync
cp .env.example .env          # then fill it in
uv run alembic upgrade head   # creates the six tables in DATABASE_URL
```

Three commands, one per job:

```bash
uv run python -m bricks.ingest --source dealabs   # fetch deals, resolve, alert
uv run python -m bricks.catalog sync              # fill the set catalogue
uv run python -m bricks.health                    # is anything still alive
```

## Ingestion

Reads the RSS feed Dealabs publishes for its LEGO group, deduplicates on
`(source, external_id)`, and appends a `price_point` on **every observation**, including
when the price has not moved. "We looked and it was still 79.99" is information in itself.

Price and merchant come from the feed's `pepper:merchant` attribute. The title is only read
as a fallback.

Every execution writes a row to `runs`, including when it fails: status `error`, a message
cleaned by `redact_secrets`, and exit code 1.

`DEALABS_RSS_URL` defaults to the public group feed. A personal alert feed can be
substituted without touching the code, but that URL is personal, so treat it as a secret.

## Resolution

Each ingested offer is matched to a `set_num` by two strategies:

1. **Set number.** A 4 to 7 digit number is read from the title and cross-checked against
   the catalogue. Score 1.0. This is what does the work: 97% of real observed titles carry
   their number.
2. **Fuzzy match.** `rapidfuzz` with `token_sort_ratio` on `name_normalized`, used only when
   no number was found.

Below `MIN_RESOLUTION_SCORE` (0.85), `set_num` stays NULL and the offer will never trigger
an alert. The score and the method are stored either way, including when the verdict is a
rejection.

The traps are handled and each one has a test: a year (`LEGO Star Wars 2024`) is not a set
number, neither is a piece count, neither is a price, and two real sets in one title yield
NULL rather than a guess.

`tests/fixtures/titles.yaml` is the safety net: 52 real Dealabs titles with their expected
`set_num`, plus 6 hand-built traps counted separately. It should grow every time a
resolution misses.

Labels are proposed automatically and cross-checked against the catalogue, requiring the
official English name to match the French title. None has been reviewed by a human yet, and
the file says so in its header.

## Detection and alerts

```bash
uv run python -m bricks.ingest --source dealabs --dry-run
```

An offer that is resolved, active and priced is evaluated on two independent criteria. One
is enough to fire:

- **A, discount threshold.** `discount_pct >= MIN_DISCOUNT_PCT`, computed on the
  recommended retail price and never on the merchant's struck-through price, which is
  marketing. The RRP is the only honest reference for a discount.
- **B, historical low.** Strictly below everything ever observed for that set, across all
  merchants, and only once there are at least 3 prior observations.

Three guard rails: no two alerts for the same offer within 24 hours, no new alert unless the
price dropped at least 5% since the last one, and at most 10 alerts per channel per run.
Hitting a cap is logged loudly, because it is more often a bug than a black friday.

`--dry-run` prints the embeds to the console without touching Discord or the `alerts` table.
Without `DISCORD_WEBHOOK_URL` the run takes the same path with a warning, since the offers
and the price points are worth collecting on their own.

Only offers seen during the run are evaluated. Alerting on a price nobody confirmed today
would send the reader to a dead page.

### Channels by theme

An alert goes to the channel matching the set's theme:

| Channel | Themes | Variable |
|---|---|---|
| `star_wars` | Star Wars | `DISCORD_WEBHOOK_STAR_WARS` |
| `collection` | Icons, Botanicals, Architecture, Ideas | `DISCORD_WEBHOOK_COLLECTION` |
| `vehicules` | Technic, Speed Champions, Racers, Train | `DISCORD_WEBHOOK_VEHICULES` |
| `univers` | Harry Potter, Marvel, DC, Minecraft, Mario | `DISCORD_WEBHOOK_UNIVERS` |
| `divers` | everything else, and any unknown theme | `DISCORD_WEBHOOK_DIVERS` |

Five deliberately broad channels: the catalogue has 150 themes, real discounts only touch
about twenty of them, and half of those appear exactly once. Across the first 39 real
alerts the split was univers 14, divers 8, collection 7, star wars 5, vehicules 5, with no
channel dominating.

A Discord webhook is bound to one channel, so each channel needs its own. Each is optional:
without its own webhook a channel falls back to `DISCORD_WEBHOOK_URL`. Setting only that one
reproduces the single-channel behaviour exactly, which makes the migration gradual.

Routing lives in `core/channels.py` as a pure function, because it is a business decision.
`alerts.channel_id` records it and `adapters/` only maps it to a URL. A test pins the
channel list to the configuration fields, so a channel added without its variable fails
instead of silently landing in the catch-all.

If Discord refuses a message the run does not fall over: the offers and price points are
already collected. Sending stops there. The cap is 10 and Discord accepts 5 messages per
2 seconds, so a refusal is most likely a rate limit, and insisting is exactly what a rate
limit asks you not to do. Nothing is lost, because an alert with no row in `alerts` is an
alert the next run proposes again.

### Open question: prices net of loyalty credit

On a real run, 9 alerts out of 10 are E.Leclerc offers phrased as "via X euros de
fidelite", and the cap of 10 is reached. This is not a detection bug, it is the semantics of
the price in the feed.

E.Leclerc runs "25% cagnottes" promotions, and the price Dealabs publishes is net of that
credit, not what you pay at the till. Adding the credit back gives ordinary shelf prices:

| Feed price | Credit | At the till | RRP | Advertised | Actual |
|---|---|---|---|---|---|
| 9.86 | 3.29 | 13.15 | 19.99 | -51% | **-34%** |
| 5.99 | 2.00 | 7.99 | 9.99 | -40% | **-20%** |
| 32.92 | 10.98 | 43.90 | 59.99 | -45% | **-27%** |

The gap is about 19 points. The credit is real, but it can only be spent at the same
merchant later, so it is not the price paid. The project rules out the merchant's
struck-through price because it is marketing, and a price net of loyalty credit raises the
same question. It is not settled: the options are to keep the feed price, use the till
price, or show both.

## Catalogue

```bash
uv run python -m bricks.catalog sync [--since-year 2016] [--skip-rrp]
```

Fills the `sets` table in two stages:

1. **Identity.** Rebrickable CSV dumps give number, name, theme, year, piece count and
   image. `name_normalized` is computed at import.
2. **Recommended price.** The Brickset API gives the RRP in euros, year by year. Without
   `BRICKSET_API_KEY` this stage is skipped with a warning and the identity import stays
   valid.

The command is idempotent: `updated_at` only moves when something actually changed, so a
second sync touches no rows. `--since-year` limits the price stage to recent sets,
`--skip-rrp` disables it.

The two stages commit separately, so if the Brickset API goes down mid-run the identity
import is already durable and the next sync resumes.

RRP covers 94% of sets of 500 pieces and up, and thins out below that. This is not a gap in
the data: what is missing are BrickLink Designer Program sets, Dacta educational material
and promotional polybags, which LEGO never sold on LEGO.com, so no recommended price exists
to fetch. Those sets can only ever alert on a historical low, never on a discount threshold.

## Health

```bash
uv run python -m bricks.health
```

Reports the last run per source, active offers, the resolution rate over the last 100
offers, and alerts over 7 days. It **exits 1** when a source looks dead, so a cron notices
without anyone reading the output.

A source is declared dead after 3 empty runs, 3 consecutive failures, or 18 hours without a
single successful run. A warning then goes to Discord, in red and with no thumbnail or
price so it cannot be mistaken for a deal, and then nothing for 24 hours regardless of how
long the outage lasts. Repeating it every run would teach the reader to ignore it, which is
worse than silence.

The third rule exists because the first two only count runs that happened. A run cancelled
before it starts writes nothing to `runs`, so the counters stay at zero and nothing notices.
That happened 4 times in one week of production without leaving a trace in the database. The
18 hour threshold is measured: the longest real gap between two successful runs was 9.5
hours.

That rule only half closes the hole, and that is deliberate. It is evaluated during a run
and by `health`. If nothing starts at all, `health` will say so to whoever runs it, but no
message goes out on its own. Closing it fully needs an external watchdog.

## Architecture

```
                 Rebrickable ──┐
                 Brickset ─────┤
                 Dealabs ──────┤
                               ▼
                         sources/          fetches, persists nothing
                               │
                               ▼
                         services/         orchestrates and persists
                          │        │        (has never heard of Discord)
                          ▼        ▼
                       core/      db/      pure logic     SQLAlchemy
                          │
                          ▼
                       adapters/
                       ├── cli/            ingest, catalog, health
                       └── webhook/        Discord embeds, in French
```

The rule that holds it together: `services/` never imports anything from `adapters/`. That
is what will allow an MCP server or an HTTP API to be added without refactoring, and it is
also why French only exists inside `adapters/webhook/`.

## Deployment

Two scheduled workflows, which call the CLI and nothing else:

| Workflow | Schedule | Command |
|---|---|---|
| `ingest.yml` | `*/15`, about every 1h45 in practice | `ingest --source dealabs` |
| `catalogue.yml` | Mondays | `catalog sync --since-year 2015` |

### The real cadence is not the cron

Measured over 6 days of production and 85 scheduled triggers:

| | |
|---|---|
| Requested cadence | 1 run per 15 min |
| **Actual cadence** | **1 run per 104 min** |
| `*/15` slots honoured | **14%** |
| Median, 90th percentile, max gap | 92 min, 181 min, 6.1 h |
| Day (8am to midnight Paris) vs night | 93 min vs 131 min |

GitHub queues scheduled workflows, it does not guarantee them, and it drops them more
readily on the free tier. Nights are noticeably worse than days.

The cron stays at `*/15` on purpose: asking more often yields more runs than asking less
often. Lowering it to `*/30` would not bring the configuration closer to reality, it would
just halve the runs obtained. The documentation was what needed to align, not the cron.

What this changes in practice: a short-lived deal can slip between two runs, and the
staleness rule in `health` is calibrated on this cadence, at 18 hours, when the longest gap
between two successful runs was 9.5 hours.

### Secrets

| Secret | | Without it |
|---|---|---|
| `DATABASE_URL` | **required** | nothing persists |
| `DISCORD_WEBHOOK_URL` | recommended | the run works, logs a warning, alerts nobody |
| `BRICKSET_API_KEY` | recommended | `catalog sync` imports identity and skips prices |
| `DEALABS_RSS_URL` | optional | defaults to the public LEGO group feed |

A missing secret arrives in the runner as an **empty string**, not as an absent variable.
The configuration treats both the same way, so leaving an optional secret unset falls back
to its default instead of overwriting it.

### Database URL

`DATABASE_URL` must point at Turso. A runner's disk is wiped between executions, so a
file-backed SQLite would start empty every time and `price_points`, the one table nobody
could rebuild, would never accumulate anything.

```
sqlite+libsql://<database>.turso.io/?authToken=<token>&secure=true
```

This URL is written in exactly that form for two reasons that are not visible:

- **`sqlite+`**: `turso db show --url` gives `libsql://...`. No SQLAlchemy dialect answers
  to that name, so pasted as is the URL fails with `NoSuchModuleError`, far from where it
  was typed.
- **`secure=true`**: the `sqlalchemy-libsql` driver picks `http` or `https` from this flag,
  and defaults it to `false`. Without it the connection goes out in clear text with the
  token in the query string, and nothing warns you.

Both wrong forms are rejected at startup, with the expected form in the error message.
`tests/test_config.py` pins the rule to the driver itself rather than to this paragraph:
every accepted URL is handed to the real dialect, and the test fails if any of them builds
an `http://`.

Setting it up:

```bash
turso db create bricks
turso db show bricks --url          # libsql://bricks-<org>.turso.io
turso db tokens create bricks
```

Recompose the URL by hand, then check it from your machine before pasting it into a GitHub
secret, because a runner is a bad place to discover a URL is wrong:

```bash
export DATABASE_URL='sqlite+libsql://.../?authToken=...&secure=true'
uv run alembic upgrade head
uv run python -m bricks.health      # prints "Database driver  sqlite+libsql"
```

`health` on an empty database reads the six tables and exits 0. That is the smallest
possible smoke test of the connection.

Then set the four secrets and run `catalogue.yml` once by hand: `ingest.yml` does apply the
migrations, but without a catalogue no offer resolves.

What is not verified yet: everything above has been exercised end to end through the libSQL
dialect against a local file, including migrations, reads, writes and the bulk insert of
27,843 sets. The remote path has never been tried, for lack of credentials.

No line of `src/` knows it runs inside GitHub Actions. Moving to a VPS means changing the
trigger.

## Development

```bash
uv run ruff check .
uv run ruff format .
uv run pytest
```

`tests/test_schema_fidelity.py` compares the DDL produced by the SQLAlchemy models and by
the Alembic migration against `schema.sql`. Any divergence between the three fails the
suite.

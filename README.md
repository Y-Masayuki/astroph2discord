# astroph2discord

Deliver new **arXiv astro-ph** papers that match your keywords to a **Discord**
channel, automatically, every day — for free via GitHub Actions.

This is a Discord-focused successor to
[arXiv-owl](https://github.com/Y-Masayuki/arXiv-owl). It differs in two ways:

- **Official arXiv API** (`export.arxiv.org/api`) instead of scraping the
  advanced-search HTML, so it does not break when arXiv changes its page layout.
- **Discord webhook** delivery (rich embeds, message chunking) instead of Slack.

## How it works

1. Query the arXiv API for papers in your chosen `astro-ph` subcategories
   whose date (`updated` by default) falls in the last *N* days, newest first.
2. Score each paper: sum the weights of the keywords found in its title +
   abstract (case-insensitive substring match).
3. Drop papers already notified in a previous run (a committed
   `seen_ids.json` cache), then post the rest to Discord as embeds, packed so
   each message stays within Discord's limits (≤10 embeds and ≤6000 chars).

### Why a look-back window + a cache?

arXiv's API can only sort by submission or last-updated date, not by the date a
paper is *announced* in the daily listing. To avoid missing papers when a
scheduled run fails, is skipped, or runs over a weekend, the window
(`days`) overlaps by a few days. The `seen_ids.json` cache — committed back by
the Action — guarantees an overlapping window never produces duplicate
notifications, and lets a missed run be recovered on the next one. The cache
key includes the version (e.g. `2605.11486v2`), so a replacement (v2) is
treated as new and re-notified.

> Limitation: a paper whose *announcement* is delayed weeks after its original
> submission (e.g. a long moderation hold) can still fall outside any
> reasonable window. For a brand-new deployment, run a one-time catch-up (see
> below) to backfill recent matches.

## Setup

### 1. Create a Discord webhook

In Discord: **Server Settings → Integrations → Webhooks → New Webhook**, pick
the target channel, then **Copy Webhook URL**. The URL looks like
`https://discord.com/api/webhooks/<id>/<token>`.

### 2. Configure keywords

Edit [`config.yaml`](config.yaml). Add/remove keywords and adjust weights, the
`categories` you watch, the look-back window `days`, and `score_threshold`.

### 3. Run it automatically (GitHub Actions — recommended)

Fork/push this repo to your account, then add the webhook URL as a secret:

**Repo → Settings → Secrets and variables → Actions → New repository secret**
- Name: `DISCORD_WEBHOOK_URL`
- Value: your webhook URL

The workflow in [`.github/workflows/arxiv2discord.yml`](.github/workflows/arxiv2discord.yml)
runs daily (23:00 UTC = 08:00 JST). You can also trigger it manually from the
**Actions** tab ("Run workflow"), optionally overriding the look-back days.

> Note: arXiv does not announce on weekends. A daily run with `days: 1` is
> simplest and never duplicates papers (each run covers a disjoint 24 h window).
> If you prefer weekday-only runs, change the cron and bump `days` on Mondays.

### 4. Run it locally (optional)

```bash
pip install -r requirements.txt
export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/…"

python arxiv2discord.py                 # use config.yaml defaults
python arxiv2discord.py --days 3        # look back 3 days
python arxiv2discord.py --days 3 --dry-run   # print payload, send nothing
```

`--dry-run` prints the exact JSON that would be sent to Discord — handy for
tuning keywords without spamming your channel.

### One-time catch-up (recover older matches)

When you first deploy — or if a paper's announcement was delayed — post all
matches from a wider window, ignoring the cache:

```bash
python arxiv2discord.py --days 60 --no-state
```

`--no-state` neither reads nor writes `seen_ids.json`, so it will (re)post every
match in the window. Use it deliberately; the daily Action keeps `--state` on.

## Configuration reference

| Key               | Meaning                                                                   |
| ----------------- | ------------------------------------------------------------------------- |
| `categories`      | arXiv categories to query (default: all six `astro-ph.*`).                |
| `days`            | Look-back window in days. Overlap is safe thanks to the seen-id cache.    |
| `date_field`      | `updated` (catches v2 replacements & updates) or `submitted` (v1 only).   |
| `max_results`     | Safety cap on papers pulled from the API per run.                         |
| `score_threshold` | Minimum summed score required to notify.                                  |
| `keywords`        | `keyword: weight` map; scored against title + abstract.                   |

CLI flags: `--days`, `--config`, `--dry-run`, `--state-file PATH`, `--no-state`.

## De-duplication & not missing papers

- `seen_ids.json` records the versioned arXiv ids already notified; the daily
  Action commits it back. This makes the overlapping look-back window safe (no
  duplicates) and recovers any run that failed or was skipped.
- `date_field: updated` means a **replacement (v2, v3, …)** is treated as a new
  item (its id carries the version) and re-notified, flagged with 🔄 and a
  "revised" date. This is the case the original submitted-date filter missed.

## Notes & limitations

- The arXiv API requires a non-default `User-Agent`; this tool sets one. If you
  reuse the code elsewhere, keep a descriptive UA or arXiv returns `403`.
- Matching is plain substring matching (so `disk` also matches `disks`). For
  whole-word matching you'd need to switch `score_article` to a regex.
- The arXiv API cannot sort by *announcement* date. A paper announced long after
  its submission/update can still fall outside the window; use the one-time
  catch-up to backfill.

## Credits

- [Y-Masayuki/arXiv-owl](https://github.com/Y-Masayuki/arXiv-owl)
- [jinshisai/arXiv-owl](https://github.com/jinshisai/arXiv-owl)
- [fkubota/Carrier-Owl](https://github.com/fkubota/Carrier-Owl) (original idea)

## License

MIT — see [LICENSE](LICENSE).

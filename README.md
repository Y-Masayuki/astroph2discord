<p align="center">
  <img src="docs/logo-banner.svg" alt="astroph2discord" width="560">
</p>

# astroph2discord

Deliver new **arXiv astro-ph** papers that match your keywords to a **Discord**
channel, automatically, every day — for free via GitHub Actions.

## Why astroph2discord?

I built the idea from scratch around the **official
arXiv API** (rather than scraping arXiv's HTML, which breaks whenever the page
layout changes) and Discord webhooks, and added a few things I wanted —
Japanese translation, an author watch-list, de-duplication, and detection of
revised papers. I'm sharing it publicly so anyone can set up the same flow for
their own keywords in a few minutes.

Key features:

- **Official arXiv API** source — robust against arXiv HTML changes.
- **Discord webhook** delivery as full-width Markdown messages.
- Keyword scoring, author watch-list, optional **Japanese (DeepL)** translation.
- Catches **revised papers (v2, v3, …)** and never sends duplicates.

## Example

A matched paper as it appears in Discord — title, score & hit keywords, authors,
categories, the arXiv Comments field, a PDF link, and the abstract in English
with an optional Japanese translation:

![Example notification in Discord](docs/example.png)

## How it works

1. Query the arXiv API for papers in your chosen `astro-ph` subcategories
   whose date (`updated` by default) falls in the last *N* days, newest first.
2. Score each paper: sum the weights of the keywords found in its title +
   abstract (case-insensitive substring match).
3. Drop papers already notified in a previous run (a committed
   `seen_ids.json` cache), then post the rest to Discord as full-width Markdown
   messages, split automatically to stay under Discord's 2000-char message limit.

## Installation (no coding required)

You don't need to install anything on your computer. Everything runs for free on
GitHub Actions, and every step below is done in your web browser. It takes about
10 minutes.

**What you need:** a [GitHub](https://github.com) account, and a Discord server
where you are allowed to create a webhook (your own server works — you can make
one for free). Japanese translation is optional and needs a free DeepL key.

### Step 1 — Copy this repository into your account

At the top of this repository page, click **Use this template → Create a new
repository** (or **Fork** if you prefer). Give it a name like `my-arxiv2discord`
and create it. Everything from here on happens in *your* copy.

### Step 2 — Create a Discord webhook

A webhook is the address your papers get posted to.

1. In Discord, open the channel you want, click the ⚙️ (**Edit Channel**).
2. Go to **Integrations → Webhooks → New Webhook**.
3. Give it a name (e.g. `arXiv`), then click **Copy Webhook URL**.

The URL looks like `https://discord.com/api/webhooks/<id>/<token>`. Keep it
secret — anyone with it can post to your channel.

### Step 3 — Add the webhook as a secret

In your repository: **Settings → Secrets and variables → Actions → New
repository secret**.

- **Name:** `DISCORD_WEBHOOK_URL`
- **Secret:** paste the webhook URL from Step 2 → **Add secret**

*(Optional, for Japanese translation)* Get a free key at
[DeepL API Free](https://www.deepl.com/pro-api) (free keys end with `:fx`) and
add a second secret named `DEEPL_API_KEY`. If you skip this, set
`translate: false` in `config.yaml` (Step 4) to send English only.

### Step 4 — Choose your keywords

Open **`config.yaml`** in your repository and click the ✏️ (**Edit this file**).
Replace the example keywords with your own (`keyword: weight`), adjust the
`categories` you want to watch, and set `score_threshold`. Optionally turn
`translate` on/off and list `highlight_authors` (e.g. your own name). Scroll
down and click **Commit changes**. No tools needed — GitHub edits it in place.

### Step 5 — Turn on Actions and do a first test run

1. Open the **Actions** tab. If prompted, click **I understand my workflows,
   enable them**.
2. Select **arxiv2discord** on the left, then **Run workflow** (top right).
3. Tick **Disable de-dup cache** so you get notifications even on the first run,
   set days to e.g. `7`, and click **Run workflow**.
4. After a minute the run turns green and the papers appear in your Discord
   channel. 🎉

### Step 6 — That's it: it now runs every day

The workflow runs automatically every day (02:00 UTC = 11:00 JST) and posts only
**new or revised** papers — duplicates are filtered out by the `seen_ids.json`
cache, so you never get the same paper twice, and a skipped/failed run is
recovered next time. To run by hand later, use **Run workflow** with **Disable
de-dup cache** left unticked.

---

### Advanced: run from the command line (optional)

For development or a one-off catch-up you can run it locally:

```bash
pip install -r requirements.txt
export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/…"
export DEEPL_API_KEY="…:fx"              # only if translate: true

python arxiv2discord.py                       # use config.yaml defaults
python arxiv2discord.py --days 3 --dry-run    # print payload, send nothing
python arxiv2discord.py --days 60 --no-state  # one-time catch-up (re-posts all)
```

`--dry-run` prints the exact messages without sending them; `--no-state`
ignores the de-dup cache (handy to backfill older matches after first setup).

## Configuration reference

| Key               | Meaning                                                                   |
| ----------------- | ------------------------------------------------------------------------- |
| `categories`         | arXiv categories to query (default: all six `astro-ph.*`).             |
| `days`               | Look-back window in days. Overlap is safe thanks to the seen-id cache. |
| `date_field`         | `updated` (catches v2 replacements & updates) or `submitted` (v1 only).|
| `max_results`        | Safety cap on papers pulled from the API per run.                      |
| `score_threshold`    | Minimum summed score required to notify.                               |
| `heartbeat`          | `true` posts a one-line "no new papers today" on quiet days.           |
| `keywords`           | `keyword: weight` map; scored against title + abstract.               |
| `translate`          | `true` to append a Japanese translation (needs `DEEPL_API_KEY`).      |
| `translate_lang`     | DeepL target language code (default `JA`).                            |
| `highlight_authors`  | Authors to show in **bold** (case-insensitive substring match).       |
| `author_bonus`       | Score added when a highlighted author appears (set ≥ threshold to     |
|                      | guarantee your own papers always notify).                            |

Each paper is posted as a full-width message: a large linked title heading
(prefixed with 🔄 if the paper is a revision), then score & hit keywords,
authors (watch-listed names in **bold**), categories, the arXiv **Comments**
field (e.g. "Accepted to ApJ"), the journal reference when available, a direct
**PDF** link, the full English abstract, and — if enabled — the Japanese
abstract. Link previews are suppressed so they don't crowd the text. Long papers
are split across a couple of messages so the English abstract is always shown in
full. (Discord does not allow custom body font sizes; headings are the only
enlarged text.)

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
- Translation is best-effort: if DeepL errors or the key is missing, the run
  still sends the English version. LaTeX math in abstracts (`$...$`) may be
  rendered imperfectly by the translator.

## Author

Masayuki Yamaguchi ([@Y-Masayuki](https://github.com/Y-Masayuki)) ·
ORCID [0000-0002-8185-9882](https://orcid.org/0000-0002-8185-9882)

If you use this tool, a link back to this repository is appreciated.

## License

MIT — see [LICENSE](LICENSE).

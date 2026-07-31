# job-radar

A job-alert bot for new graduates in Saudi Arabia. It polls the
[JSearch API](https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch) (Google
for Jobs — which indexes LinkedIn, Indeed and Glassdoor), filters for
graduate-level software and data roles, and pushes each match to Telegram with
the reason it matched and an apply link.

Runs on the GitHub Actions free tier. **Standard library only** — no `pip
install`, anywhere, ever.

---

## Why we don't scrape LinkedIn

LinkedIn has no public jobs API. The endpoints its own web app calls are
private, unversioned, and actively defended: hitting them from a script is a
reliable way to get your account restricted or permanently banned, and the
markup changes often enough that a scraper is broken more weeks than it works.

JSearch sits on top of the Google for Jobs aggregate, which already contains
the LinkedIn postings you wanted — plus Indeed, Glassdoor and company career
pages — through a stable, documented, terms-of-service-compliant interface.
One legitimate aggregator beats three brittle scrapers and a banned account.

---

## Setup

### 1. Telegram bot

1. Message [@BotFather](https://t.me/BotFather) and send `/newbot`.
2. Pick a name and a username. BotFather replies with a token that looks like
   `123456789:AAH...`. That is `TELEGRAM_TOKEN`.
3. **Send your new bot any message** — say "hi". This matters: Telegram will
   not reveal a chat id until the chat exists.
4. Fetch your chat id:

   ```bash
   curl -s "https://api.telegram.org/bot<TOKEN>/getUpdates"
   ```

   Read `result[0].message.chat.id` from the JSON. That is
   `TELEGRAM_CHAT_ID`. If `result` is an empty array, you skipped step 3.

### 2. RapidAPI key

1. Sign up at [rapidapi.com](https://rapidapi.com), open the
   [JSearch API](https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch)
   and subscribe to the **Basic (free)** plan.
2. Copy the `X-RapidAPI-Key` value from the code snippet on that page. That is
   `RAPIDAPI_KEY`.

### 3. Local config

```bash
cp .env.example .env
# then fill in the three values
```

`.env` is gitignored. Never commit it.

### 4. Try it without sending anything

```bash
python -m jobradar --dry-run --show-rejected --date-posted week
```

### 5. Ship it

```bash
gh repo create job-radar --private --source=. --push

gh secret set RAPIDAPI_KEY
gh secret set TELEGRAM_TOKEN
gh secret set TELEGRAM_CHAT_ID

gh workflow run job-alerts
```

---

## Quota

The free JSearch plan allows roughly **200 requests/month**. One request is one
query on one run.

| queries | runs/day | working days | requests/month | free tier? |
|--------:|---------:|-------------:|---------------:|:-----------|
| 2       | 3        | 22           | **132**        | yes — the shipped default |
| 3       | 3        | 22           | 198            | yes, no headroom |
| 4       | 3        | 22           | 264            | no |
| 2       | 4        | 22           | 176            | yes, tight |
| 2       | 3        | 30           | 180            | yes, if you drop the weekday restriction |

The default schedule is 3 runs/day, Sunday–Thursday (the Saudi working week),
which leaves ~68 requests/month spare for manual runs. If you add a query,
subtract a run.

---

## Schedule

`.github/workflows/jobs.yml` runs at `0 6,11,16 * * 0-4` UTC:

| UTC   | Riyadh (UTC+3) |
|------:|---------------:|
| 06:00 | 09:00 |
| 11:00 | 14:00 |
| 16:00 | 19:00 |

Sunday through Thursday. GitHub cron is always UTC — there is no timezone
setting — so the local times shift if Saudi Arabia ever adopts DST (it does
not).

Note that GitHub's scheduler is best-effort on the free tier: runs can be
delayed by several minutes during busy periods, and very quiet repositories
have their schedules disabled after 60 days of no activity.

---

## CLI

```
python -m jobradar [options]

--dry-run                     print matches instead of sending; do not write state
--show-rejected               log every rejected posting and the layer that dropped it
--date-posted {all,today,3days,week,month}
                              override config.DATE_POSTED
--limit N                     max messages this run (default 12)
--verbose                     debug logging
```

---

## How filtering works

Five layers, cheapest first, in `jobradar/filters.py`:

1. **Title exclusions** — `senior`, `lead`, `principal`, `manager`, … This is
   the layer that actually removes senior roles. A description will mention
   "senior stakeholders"; a title does not lie about its own level.
2. **Body exclusions** — literal `5+ years` … `10+ years`, `at least 5 years`.
3. **Must-match** — at least one early-career term (`graduate`, `junior`,
   `تمهير`, …) or stack term (`software engineer`, `data analyst`, `power bi`,
   `مطور`, …). The first four hits become the "why it matched" line in your
   Telegram message.
4. **Parsed experience** — a regex sweep for `N+ years`, `N-M years`, `N yrs`,
   `N سنوات`. Takes the **first** number of a range and the **minimum** across
   the description. Descriptions are full of unrelated numbers ("3 year
   contract"), and a false rejection costs an opportunity while a false
   acceptance costs three seconds.
5. **Geography** — only when `CITIES` is non-empty; remote roles pass anyway if
   `ALLOW_REMOTE`.

Everything is tunable in `jobradar/config.py`. That is the only file you should
need to edit to change what you get alerted about.

---

## Behaviours worth knowing

**First run doesn't alert.** When `seen.json` is missing, every posting in the
window looks new — sending them individually means fifty notifications and a
muted bot. Instead the bot marks them all seen, sends one summary, and stops.
Real alerts start on the second run.

**Overflow is deferred, not dropped.** At most `MAX_MESSAGES_PER_RUN` (12) go
out per run, newest first. The rest are deliberately *not* marked seen, so they
come back through the pipeline next run and arrive then. You get a short
"N more deferred" note so you know they are queued.

**State lives in git.** `seen.json` is committed back by the workflow after
each run. No database, and the commit history doubles as a log of what the bot
noticed and when.

---

## Layout

```
jobradar/
  __init__.py
  __main__.py        python -m jobradar
  cli.py             orchestration: fetch -> dedup -> filter -> deliver
  config.py          every tunable, with comments
  filters.py         the five layers + the years parser
  http.py            urllib JSON client, retries 429/5xx only
  log.py             logging setup
  notify.py          Telegram formatting and delivery
  state.py           SeenStore — seen.json
  sources/
    __init__.py
    base.py          Job dataclass + Source ABC
    jsearch.py       JSearch (RapidAPI)
tests/
  test_filters.py    run: python tests/test_filters.py
.github/workflows/
  jobs.yml           the scheduled alert run
  tests.yml          tests on push and PR
```

## Adding a source

`build_sources()` in `cli.py` is the extension point. Everything downstream
consumes `Job`, so a new source is purely additive: subclass `Source`,
normalise into `Job`, append it to the list.

Next up are ATS sources — Workday, Greenhouse and Lever expose public,
unauthenticated JSON endpoints per company board, so they cost no API quota and
often list a role hours before an aggregator picks it up.

## License

MIT — see [LICENSE](LICENSE).

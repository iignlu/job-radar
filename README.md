# job-radar

A job-alert bot for new graduates in Saudi Arabia. It pulls postings from three
sources, filters for graduate-level software and data roles, and pushes each
match to Telegram with the reason it matched and an apply link.

Runs on the GitHub Actions free tier. **Standard library only** — no `pip
install`, anywhere, ever.

---

## Sources

| Source | What it is | API quota |
|:--|:--|:--|
| **JSearch** | The [JSearch API](https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch) over Google for Jobs, which indexes LinkedIn, Indeed, Glassdoor and company career pages | ~200 requests/month, free plan |
| **ATS boards** | Employers' own hiring systems — Greenhouse, Lever, Ashby, SmartRecruiters, Recruitee, Workable | free, unmetered |
| **LinkedIn alerts** | LinkedIn job-alert emails read from your own mailbox over IMAP | free |

Sources are additive and independently optional. Unset `RAPIDAPI_KEY` and
JSearch is skipped; unset `IMAP_USER` and LinkedIn is skipped; set
`ENABLE_ATS = False` and the boards are skipped. Whatever is configured runs.

### Why we don't scrape LinkedIn

LinkedIn has no public jobs API. The endpoints its own web app calls are
private, unversioned, and actively defended: hitting them from a script is a
reliable way to get your account restricted or permanently banned, and the
markup changes often enough that a scraper is broken more weeks than it works.

Two legitimate routes get you the same postings. JSearch sits on top of the
Google for Jobs aggregate, which already contains the LinkedIn postings you
wanted. And a **LinkedIn job alert** is mail you asked LinkedIn to send you —
it arrives in your inbox by design, and reading your own mail breaks nothing.
That second route reaches postings Google never indexed, which is the gap it
closes.

### Why ATS boards

An aggregator's apply link increasingly leads to a signup wall or a paid plan,
so a posting can match perfectly and still be unapplicable. An ATS board is the
employer's own hiring system, so every link from it *is* the company's
application form. The endpoints are public and unauthenticated, they cost
nothing against the JSearch quota, and roles usually appear on a company's own
board before an aggregator indexes them.

The watchlist is `config.ATS_BOARDS`, and only boards confirmed by
`tools/probe_ats.py` belong in it — a guessed slug returns the same empty
result as a company with no openings, which is a source that silently finds
nothing forever. To confirm a new company, add it to `config.ATS_COMPANIES` and
run the **probe-ats** workflow (or `python tools/probe_ats.py <slug>` locally).

---

## Setup

### 1. Telegram bot

1. Message [@BotFather](https://t.me/BotFather) and send `/newbot`.
2. Pick a name and a username. BotFather replies with a token that looks like
   `123456789:AAH...`. That is `TELEGRAM_TOKEN`.
3. **Send your new bot any message** — say "hi". This matters: Telegram will
   not reveal a chat id until the chat exists, so skipping this is the single
   most common setup failure.
4. You do **not** have to look up your chat id. If `TELEGRAM_CHAT_ID` is unset,
   the bot derives it from `getUpdates` itself on every run.

   To pin it explicitly (more robust — `getUpdates` only retains ~24h of
   history), run:

   ```bash
   python -m jobradar --resolve-chat-id
   ```

   or open `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and
   read `result[0].message.chat.id`.

### 2. RapidAPI key

1. Sign up at [rapidapi.com](https://rapidapi.com), open the
   [JSearch API](https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch)
   and subscribe to the **Basic (free)** plan.
2. Copy the `X-RapidAPI-Key` value from the code snippet on that page. That is
   `RAPIDAPI_KEY`.

### 3. LinkedIn alerts over IMAP *(optional)*

1. On LinkedIn, save a job search and turn its **alert** on. Tune it there —
   role, location, experience level — because the bot trusts that tuning (see
   [Pre-filtered sources](#pre-filtered-sources)).
2. Give the bot read access to the mailbox those alerts land in:
   - `IMAP_USER` — the mailbox address
   - `IMAP_PASSWORD` — on Gmail this must be an **App Password**
     (Google Account → Security → 2-Step Verification → App passwords),
     **never** your account password
3. Non-Gmail mailboxes: set `config.IMAP_HOST`.

Leave either variable unset and the source stays off.

Alert-mail HTML changes without notice, so before relying on it, look at what
the parser actually sees:

```bash
python -m jobradar --dump-linkedin
```

### 4. Local config

```bash
cp .env.example .env
# then fill in the values you want
```

`.env` is gitignored. Never commit it.

### 5. Try it without sending anything

No credentials needed — fixture postings, no network, no quota:

```bash
python -m jobradar --demo --dry-run --show-rejected
```

Check your credentials are actually good:

```bash
python -m jobradar --doctor
```

Then a real dry run against the live sources:

```bash
python -m jobradar --dry-run --show-rejected --date-posted week
```

### 6. Ship it

Push to a private repo, then add the secrets.

With the `gh` CLI:

```bash
gh repo create job-radar --private --source=. --push
gh secret set RAPIDAPI_KEY
gh secret set TELEGRAM_TOKEN
gh secret set TELEGRAM_CHAT_ID   # optional — see step 1
gh secret set IMAP_USER          # optional — see step 3
gh secret set IMAP_PASSWORD      # optional — see step 3
gh workflow run job-alerts
```

Or entirely in the browser, no CLI needed:

1. **Settings → Secrets and variables → Actions → New repository secret**
   - `RAPIDAPI_KEY`
   - `TELEGRAM_TOKEN`
   - `TELEGRAM_CHAT_ID` *(optional)*
   - `IMAP_USER`, `IMAP_PASSWORD` *(optional)*
2. **Actions → job-alerts → Run workflow**

Scheduled runs only fire from the repository's **default branch** — if you
push this to a side branch, either merge it to the default branch or change
the default in Settings, or the cron will never trigger.

---

## Quota

Only JSearch is metered. ATS boards and IMAP cost nothing, so you can add
companies and alerts freely — this table is about JSearch alone.

The free plan allows roughly **200 requests/month**. One request is one query
on one run.

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

The workflow also takes manual runs (**Actions → job-alerts → Run workflow**)
with three inputs: `date_posted` to widen the window, `limit` to raise the
message cap, and `dump_linkedin` to inspect alert mail instead of running.
Useful on a Friday or Saturday, when "today" is legitimately empty because the
Saudi working week has not started — an empty run is indistinguishable from a
broken one until you look further back.

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
--demo                        use built-in fixture postings; no key, no network
--doctor                      check credentials and connectivity, then exit
--dump-linkedin               print the structure of your LinkedIn alert emails and exit
--resolve-chat-id             print your Telegram chat id and exit
```

---

## How filtering works

Seven layers, cheapest first, in `jobradar/filters.py`. The order is the point:
a string check over a title costs almost nothing, a regex sweep over a
4000-character description costs more, so the layers that reject the most for
the least run first.

1. **Title exclusions** — `senior`, `lead`, `principal`, `manager`, … This is
   the layer that actually removes senior roles. A description will mention
   "senior stakeholders"; a title does not lie about its own level.
2. **Freshness** — drops postings older than `MAX_AGE_DAYS` (14). Costs one
   date parse, so it runs before any description scanning. Postings with no
   date at all are kept: an unknown date is not evidence of staleness.
3. **Working arrangement** — on-site and full-time. Remote and hybrid are
   dropped by flag or by title only, never by description, since a description
   saying "this role is not remote" would otherwise reject itself. The
   full-time check applies only when the source states a type, because most ATS
   boards omit it. `INTERN` and `TRAINEE` are deliberately allowed — تمهير and
   graduate programmes are routinely tagged that way.
4. **Body exclusions** — literal `5+ years` … `10+ years`, `at least 5 years`.
5. **Role and level** — the role term must be in the **title**
   (`software engineer`, `data analyst`, `power bi`, `مطور`, …). Descriptions
   are unreliable: on an employer's board every posting repeats the same
   graduate-scheme boilerplate, which let through Customer Care Advisor and
   Fraud Investigator. Level terms (`graduate`, `junior`, `تمهير`, …) are
   enrichment only — they can explain a match but never cause it. The first
   four hits become the "why it matched" line in your Telegram message.
6. **Parsed experience** — a regex sweep for `N+ years`, `N-M years`, `N yrs`,
   `N سنوات`. Takes the **first** number of a range and the **minimum** across
   the description. Descriptions are full of unrelated numbers ("3 year
   contract"), and a false rejection costs an opportunity while a false
   acceptance costs three seconds.
7. **Geography** — only when `CITIES` is non-empty; remote roles pass anyway if
   `ALLOW_REMOTE`.

Everything is tunable in `jobradar/config.py`. That is the only file you should
need to edit to change what you get alerted about.

### Pre-filtered sources

Sources named in `config.PRE_FILTERED_SOURCES` skip layer 5 only. A LinkedIn
alert is the result of a saved search you already tuned, and the alert mail
carries no description for the keyword layers to read anyway.

Skipping is deliberately narrow: seniority, freshness and working arrangement
still apply, because those express what *you* will accept and no upstream
search knows them. Dedup and the per-run cap also still apply, so a
pre-filtered source cannot spam you.

---

## Message format

Each alert carries the title, company, location, why it matched, and an apply
link. When a posting offers the employer's own careers page or ATS,
`PREFER_DIRECT_APPLY` leads with that instead of the aggregator link, since
aggregator links increasingly sit behind a signup or a paid plan. Up to
`MAX_ALTERNATE_APPLY_LINKS` (3) other routes are listed underneath, so a gated
primary link is never a dead end.

Every message ends with a footer set by `config.SIGNATURE`:

```
— Abdullah Alshehri's Job Radar
```

Change the string to reword it, or set it to `""` to switch it off. It is
HTML-escaped on the way out, so `&`, `<` and apostrophes are safe to type
literally.

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
each run, capped at `MAX_SEEN_KEYS` (4000) so it stays cheap to commit. No
database, and the commit history doubles as a log of what the bot noticed and
when.

**A silent source is a bug, not a quiet week.** Sources report what they found
rather than assuming a layout, because a parser that silently returns nothing
is indistinguishable from a slow hiring week — the failure that hid a JSearch
outage for four runs.

---

## Layout

```
jobradar/
  __init__.py
  __main__.py        python -m jobradar
  cli.py             orchestration: fetch -> dedup -> filter -> deliver
  config.py          every tunable, with comments
  filters.py         the seven layers + the years parser
  http.py            urllib JSON client, retries 429/5xx only
  log.py             logging setup
  notify.py          Telegram formatting and delivery
  state.py           SeenStore — seen.json
  sources/
    __init__.py
    base.py          Job dataclass + Source ABC
    jsearch.py       JSearch (RapidAPI)
    ats.py           employer ATS boards, six providers
    linkedin_email.py  LinkedIn job alerts over IMAP
    demo.py          fixture source for --demo, and a worked extension example
tests/
  test_filters.py    run: python tests/test_filters.py
tools/
  probe_ats.py       find which ATS provider a company uses
.github/workflows/
  jobs.yml           the scheduled alert run
  probe-ats.yml      manual ATS discovery run
  tests.yml          tests on push and PR
```

## Adding a source

`build_sources()` in `cli.py` is the extension point. Everything downstream
consumes `Job`, so a new source is purely additive: subclass `Source`,
normalise into `Job`, append it to the list. `sources/demo.py` is written as a
worked example.

Adding an employer usually needs no code at all — probe the company with
`tools/probe_ats.py` and add the confirmed `(name, provider, slug)` row to
`config.ATS_BOARDS`.

## License

MIT — see [LICENSE](LICENSE).

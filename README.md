# Travel Radar

A Claude Code plugin that plans trips from a plain-language request and keeps them under watch.

Tell it what you have in mind ("2-3 weeks in China in June, solo, not too touristy"). It works out the period, flights, itinerary options and local places, researching prices, deals, weather and safety online at that moment. It learns your preferences as you talk, warns you when a request goes against them and offers alternatives, and can monitor flight prices and travel risks on a schedule.

Everything happens inside Claude Code. There is no separate app or server, and no account or API key is needed.

## How it works

```text
you ──► Claude Code ──► travel-radar skill
                          │
                          ├─ web research at runtime: flights, deals, safety, costs, local places
                          ├─ tw CLI (deterministic, JSON)
                          │    ├─ climate normals + forecast   (Open-Meteo)
                          │    ├─ public holidays              (Nager.Date)
                          │    ├─ option scoring vs profile    (conflicts, exclusions)
                          │    ├─ dossier checklist + freshness
                          │    └─ price history, alerts, check cadence (SQLite)
                          └─ scheduled task ──► monitoring round ──► notification on alerts
```

- **The model** understands your request, researches online and builds the itineraries.
- **The CLI** owns everything that must be exact: scoring, thresholds, check cadence, validation and history.
- **You** pick the option and book it yourself. Travel Radar never books, logs in or pays.

## Install

In Claude Code:

```text
/plugin marketplace add aantenore/travel-radar
/plugin install travel-radar@travel-radar-marketplace
```

**Requirements**
- Python 3.9+ with PyYAML (`python3 -c "import yaml"`; if that fails, run `pip install pyyaml`).
- Internet access.

## Quick start

Just describe the trip:

```text
I'd like to spend 2-3 weeks in China in June 2027, travelling solo.
I want the unmissable sights but mostly local, non-touristic places.
What are the itinerary options, and should I book flights now or wait?
```

Travel Radar will:
1. Save what it learned about you, one line each (`saved: travel_style.party = solo`).
2. Ask only for what blocks the search, for example your departure airport.
3. Research climate, holidays and crowds, safety, costs and current flight prices.
4. Score the options against your profile and show up to 3, with sources, local places and risks.
5. Answer "book now or wait" from current prices and sources, not from guesses.
6. When you pick an option, build a destination dossier and **propose** a monitoring plan: which flights and risks to watch, how often, and that each round uses tokens. Nothing is monitored until you say yes; you can accept only part of it ("only flights").

You can also start with an explicit command: `/travel-radar:trip 2-3 weeks in China in June 2027, solo`.

## Auto-use

- **No command needed.** The skill triggers on any travel request: destination ideas, best period, flight timing, itinerary options, monitor status.
- **No setup interview.** The profile starts empty and fills in from what you say. Preferences are saved only from your own words, each with the sentence that justified it.
- **Requests that conflict with your profile.** If you ask for something your profile says you avoid (e.g. heat), you get the requested option with the conflicts spelled out, plus 1-2 alternatives that fit. Say "I want it anyway" and it simply follows you, without turning that into a general rule.
- **Monitoring, only with your yes.** After you pick an option it proposes the watches and one scheduled task (a desktop-app scheduled task, or `/loop` for the current session) and creates them only after your explicit yes, quoting your words in each watch. The same applies to `/travel-radar:monitor setup`, ideas turned into trips, and resuming a parked trip. Each round checks only the watches that are due. Cadence depends on how close departure is: daily when more than 60 days away, twice a day within 60 days, 4 times a day within 14. A notification arrives only when something happens:
  - price under budget;
  - new all-time low;
  - drop versus the median;
  - change in safety or entry rules.

## Always current: runtime discovery, code verification

Travel Radar keeps no fixed list of good sites, airlines, booking windows or entry rules: they would only be right for some destinations, and only for a while.
- **Code and config hold only rules, verification logic and thresholds.**
- **Everything perishable is researched when it is needed**, then stored with its sources, date and an expiry:
  - which sites are reliable;
  - which airlines fly a route;
  - the usual booking window for a route and season;
  - visas and safety.
  Once expired, it is researched again.
- **Code verifies what the model finds.** Sources are checked on Wikidata, prices must appear in the page text, and alerts need an independent confirmation.
- **How pages are read is learned.** Each attempt (plain fetch, rendered browser page, or an API you configured) is recorded per site. The next round tries first what has actually worked, so the system adapts when a site changes.

## Verified prices and "buy now or wait"

- **Only reliable sources count.**
  - A flight price is recorded only from an airline's own site (tier 1) or a travel search/booking site queried with exact dates (tier 2). Which sites qualify is verified at runtime on Wikidata, not taken from a fixed list.
  - Blogs, "flights from 380€" route pages and historical averages are context, never prices.
  - The tier is decided by code from Wikidata (an airline, or an online travel agency / metasearch engine / travel website, whose official website matches the page), cached for 30 days and then re-verified. A small list in config is used only offline, and prices that rely on it are marked `verified_by: fallback`.
- **The price must be on the page.** Every recorded price carries a verbatim excerpt of the page, and the number must appear in it. Who read it (the `fare-reader` agent or the main model) is stored for later evaluation.
- **No alert from a single reading.** A price alert fires only when an independent second observation confirms it within 24 hours: the same price (±5%) on another reliable site, or the same fare re-read by the main model. The page reader re-reading its own result does not count. Until then the alert stays `pending_confirmation` and is re-checked.
- **Buy or wait.** `/travel-radar:advise` gives `BUY_NOW`, `WAIT` or `WATCH_CLOSELY`, with a confidence and the reasons.
  - The verdict is computed by code from: days to departure versus the usual booking window (long or short haul, peak or not), where today's price sits in its own verified history, the 7-day trend, volatility and your budget.
  - With little history it says so instead of pretending.
  - It may add context found online (airline sales, surcharges, route changes, geopolitics). When it disagrees with the verdict it says so and shows the sources.
- **"I'll message you."** When the answer is "wait", it remembers the recheck date. On that day the regular monitoring round refreshes prices and tells you the new verdict. No extra scheduled tasks are created.
- **Shadow start.** For the first 14 days, buy/wait verdicts are logged and shown when you ask (`/travel-radar:advise`) instead of being pushed. Confirmed price alerts and safety or entry changes are always notified.
- **How to buy.** It suggests:
  - booking direct with the airline when the price is the same or close;
  - a changeable fare while entry rules are uncertain;
  - checking baggage, layovers and self-transfer.
  You book. It never does.

## Park, resume, ideas, export

- **Park a trip.** "Let's put the China trip on hold until March" pauses the trip and its watches, nothing is deleted, and stores a snapshot. With a date, the monitoring round reminds you when it's time to look again.
- **Resume it.** You first get a preview of "what changed since you parked it"; monitoring restarts only if you say yes. The preview covers:
  - price changes;
  - dossier items now too old, which are re-verified before any advice, safety and entry rules first;
  - safety or visa status changes;
  - new holidays in your window;
  - score changes.
- **Done and debrief.** When a trip is over, it asks 3-4 quick questions (pace, crowds, real spend vs budget, places to repeat or avoid) and learns from your answers.
- **Idea inbox.** "One day I'd like to see Patagonia" saves the idea with its best months. Ideas are checked at low frequency and flagged when their season is approaching or a deal shows up from your airports. When you're ready, an idea becomes a trip.
- **Export.**
  - `.ics` calendar with the trip, booking deadlines (e.g. visa lead time, with source) and reminders to re-check safety and entry rules before departure;
  - markdown trip brief with dates, places, best flights, dossier and sources.

## Commands

| Command | What it does |
|---|---|
| `/travel-radar:trip <request>` | Plan a trip from a free-form request |
| `/travel-radar:advise [watch]` | Book now or wait: verdict, confidence, reasons, reminder if waiting |
| `/travel-radar:status [watch]` | Trips by status, ideas, watches, best prices with links, alerts, stale dossier items |
| `/travel-radar:monitor [setup]` | Run one monitoring round now, or set up the scheduled task |
| `/travel-radar:profile [preference]` | Show what it learned about you, or tell it a preference |
| `/travel-radar:trips park|resume|done <trip>` | Park a trip for later, resume it with a "what changed" report, or close it with a debrief |
| `/travel-radar:idea <place or wish>` | Save an idea for some day; list ideas with no argument |

## Permissions

The skill declares the tools it needs (`allowed-tools`): the bundled CLI, `WebSearch` and `WebFetch`.

Unattended scheduled rounds have nobody there to approve a prompt, so they still need these rules. Add them to `~/.claude/settings.json` (or to the project `.claude/settings.json` you run the task from), under `permissions.allow`:

```json
{
  "permissions": {
    "allow": [
      "Bash(python3 *travel-radar*/scripts/tw.py *)",
      "WebSearch",
      "WebFetch"
    ]
  }
}
```

Alternatively, choose "always allow" the first time each one is requested.

## Your data

Everything you own lives outside the plugin, so updates never touch it:

```text
~/.travel-radar/            # override with TRAVEL_RADAR_HOME
├── profile.yaml            # learned preferences + audit log of what was learned and why
├── settings.yaml           # optional overrides of the defaults
├── trips/<id>/trip.yaml    # options with metrics and sources
├── trips/<id>/dossier.yaml # destination dossier
├── trips/<id>/<id>.ics     # calendar export
├── trips/<id>/brief.md     # trip brief export
├── watches/<id>.yaml       # monitored flights / safety / weather
└── data/radar.sqlite       # price history, checks, alerts, ideas (+ HTTP cache)
```

## Configuration

Defaults are in [`skills/travel-radar/config/defaults.yaml`](skills/travel-radar/config/defaults.yaml). Override any key in `~/.travel-radar/settings.yaml`; it is deep-merged, so you only write what changes:

```yaml
alerts:
  drop_pct: 15
research:
  monitor_every_h: 12
scoring:
  default_weights: {cost: 0.4, climate: 0.2, crowds: 0.2, authenticity: 0.1, logistics: 0.1}
```

- **Dossier checklist**: [`skills/travel-radar/config/dossier_checklist.yaml`](skills/travel-radar/config/dossier_checklist.yaml) lists what every destination dossier must cover and how fresh each item must be (safety: 7 days). Copy it to `~/.travel-radar/dossier_checklist.yaml` to customise it.
- **Ranking**: weights and thresholds drive the ranking, so it stays explainable and repeatable.

## Safety and limits

- **Read-only.** It never books, logs in, enters payment data or creates accounts.
- **Every number has a source.** Prices, costs and safety levels carry a link and a date. Missing data is marked `unknown`, never estimated.
- **Web content is data, not instructions.** Your profile changes only from your own words. Unattended rounds cannot change the profile, trips or watches.
- **Implausible prices are flagged, not celebrated.** A price far below history raises a `suspect` alert that is re-verified on the source, and is never notified as a deal.
- **Cost-aware monitoring.**
  - A round with nothing due stops before any web access.
  - Page reading runs in the plugin's `fare-reader` agent: read-only (web search and fetch only), on a cheaper model, capped per round.
  - The cheaper model is eval-gated (`evals/`). Agent model aliases are not version-pinned, so re-run the eval when they change.
  - Verifying alerts and the buy/wait message stay on the main model.
- **Access that adapts.** Plain page reading (fare-reader) comes first; client-rendered sites are read in the desktop app's browser pane when that is available. The browser path declines non-essential cookies, fills only the search form and never logs in. An API adapter is used only if you set its key yourself, e.g. `SERPAPI_KEY` for SerpApi Google Flights. The browser pane works only while the desktop app is open and can render; some consent walls (e.g. Google) may need the window on screen.
- **Polite browsing.** Each watch reads only a few pages per round (configurable), with no bulk scraping. Sites that block automation or need login are skipped and recorded as `no_data`.
- **Limits:**
  - Flight prices come from web research, so sites that block automated access or require login are skipped.
  - Desktop scheduled tasks do not run while the computer is off.
  - Each monitoring round uses tokens.

Design, ownership, controls and decisions: [`docs/architecture.md`](docs/architecture.md).

## Development

```bash
python3 -m unittest discover -s tests
```

Try the CLI directly, using a throwaway data home:

```bash
TRAVEL_RADAR_HOME=$(mktemp -d) python3 skills/travel-radar/scripts/tw.py climate --place Kyoto --months 4,11
```

Repository layout:

```text
.claude-plugin/         plugin.json, marketplace.json
agents/                 fare-reader (read-only price extraction)
commands/               slash commands
skills/travel-radar/    SKILL.md, config/, scripts/tw.py + tw/ package
tests/                  unit tests, fixtures, backtests
evals/                  extraction eval for model changes
docs/architecture.md
```

## License

MIT

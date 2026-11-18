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
6. When you pick an option, build a destination dossier and offer to monitor flights and safety.

You can also start with an explicit command: `/travel-radar:trip 2-3 weeks in China in June 2027, solo`.

## Auto-use

- **No command needed.** The skill triggers on any travel request: destination ideas, best period, flight timing, itinerary options, monitor status.
- **No setup interview.** The profile starts empty and fills in from what you say. Preferences are saved only from your own words, each with the sentence that justified it.
- **Requests that conflict with your profile.** If you ask for something your profile says you avoid (e.g. heat), you get the requested option with the conflicts spelled out, plus 1-2 alternatives that fit. Say "I want it anyway" and it simply follows you, without turning that into a general rule.
- **Automatic monitoring.** After you pick an option, it creates watches and one scheduled task: a desktop-app scheduled task, or `/loop` for the current session. Each round checks only the watches that are due. Cadence depends on how close departure is: daily when more than 60 days away, twice a day within 60 days, 4 times a day within 14. A notification arrives only when something happens:
  - price under budget;
  - new all-time low;
  - drop versus the median;
  - change in safety or entry rules.

## Commands

| Command | What it does |
|---|---|
| `/travel-radar:trip <request>` | Plan a trip from a free-form request |
| `/travel-radar:status [watch]` | Watches, best prices with links, alerts, stale dossier items |
| `/travel-radar:monitor [setup]` | Run one monitoring round now, or set up the scheduled task |
| `/travel-radar:profile [preference]` | Show what it learned about you, or tell it a preference |

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
├── watches/<id>.yaml       # monitored flights / safety / weather
└── data/radar.sqlite       # price history, checks, alerts (+ HTTP cache)
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
commands/               slash commands
skills/travel-radar/    SKILL.md, config/, scripts/tw.py + tw/ package
tests/                  unit tests
docs/architecture.md
```

## License

MIT

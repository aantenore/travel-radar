# Changelog

## 0.4.0 - 2026-11-18

Design review of 0.3.0 (agentic-architect, Standard depth).
- `fare-reader` agent shipped with the plugin: `model: sonnet`, tools WebSearch/WebFetch only, strict JSON with a verbatim excerpt.
- `tw watch record` requires `--excerpt` and `--extractor`; the price (or `--excerpt-price` for other currencies) must appear in the excerpt. Excerpt and extractor are stored.
- Independent confirmation: an alert is confirmed only by another tier 1/2 site or by a re-read from the main model, never by the same extractor again.
- Buy/wait follow-up without reminder tasks: `tw advise` stores `recheck_date`; `tw watch due` marks the watch due with reason `advise_recheck`. The 0.3.0 reminder tasks are removed.
- Booking windows marked as heuristics with a source; `advise.min_history` (renamed from `advisor.min_verified_obs`; the `advisor` config section is now `advise`).
- `research.max_subagent_calls_per_run` enforced by `tw watch due` (closest departures first, others deferred).
- Shadow rollout: for 14 days from first use, verdicts are logged (`tw verdicts`) and not notified.
- Evals: labelled fare excerpts, advise backtests, `evals/score_extraction.py` for model changes.

## 0.3.0 - 2026-11-18

- Verified prices: source tiers in config (tier 1 airline sites, tier 2 meta-search with exact dates); `tw watch record` rejects other sources and observations without exact dates inside the watch window; tier stored on each observation (`tw source-tier <url>` to check a URL). Existing databases are migrated automatically.
- Alert confirmation: `below_budget`, `new_low` and `drop` alerts need a second confirming observation within `confirm_window_h`; otherwise they are returned as `pending_confirmation` and re-checked. `suspect` unchanged.
- `tw advise --watch <id>`: deterministic BUY_NOW / WAIT / WATCH_CLOSELY with confidence, recheck date and reasons, based on booking windows by haul and season, price percentile, trend, volatility, budget and history size. One reminder task per watch when waiting.
- Monitoring round: exits when nothing is due; page reading delegated to a cheaper-model subagent, verification kept on the main model.
- Watch fields `haul`, `peak`, `one_way`. Command `/travel-radar:advise`. Skill guidance on how to buy.

## 0.2.0 - 2026-11-18

- Trip lifecycle with validated transitions (`tw trip list|set|park|resume|done`). Parking pauses the trip's watches and stores a snapshot; resuming reports price changes, stale dossier items to re-verify, dossier status changes, new holidays and score changes. `--until` adds a "time to look again" reminder to the monitoring round.
- Idea inbox (`tw idea add|list|checked|promote|drop`) with season-approaching flags and low-frequency deal checks.
- Post-trip debrief: `tw trip done` returns configurable debrief topics; answers are learned via `tw profile set`.
- Exports: `tw export ics` (trip, booking deadlines, re-check reminders) and `tw export brief` (markdown).
- Commands: `/travel-radar:trips`, `/travel-radar:idea`.

## 0.1.0 - 2026-11-18

- First release.
- `travel-radar` skill: end-to-end trip planning from a free-form request, profile learned from the conversation, runtime research of flights, deals, weather and safety.
- `tw` CLI: climate normals, forecast and geocoding (Open-Meteo), public holidays (Nager.Date), option scoring with profile conflicts, dossier checklist, price history, alerts with cooldown and suspect-price guard.
- Commands: `/travel-radar:trip`, `/travel-radar:status`, `/travel-radar:monitor`, `/travel-radar:profile`.
- User data in `~/.travel-radar` (override with `TRAVEL_RADAR_HOME`).

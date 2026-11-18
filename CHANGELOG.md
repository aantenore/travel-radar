# Changelog

## 0.7.0 - 2026-11-18

No shipped lists, not even as fallback.
- Removed every airline / travel-site domain list from config (`reliability.tier1`, `tier2`, `fallback`). Tiers are decided only at runtime and cached in the data home.
- Registry unreachable or entity unresolved → source `unverified` (tier 0, 1-day TTL): prices are context only and cannot be recorded. Second runtime check: `tw sources confirm` with at least two independent sites whose text names the domain (14-day TTL, `verified_by: independent_sources`).
- `reliability.tier1/tier2/tier1_extra/tier2_extra/fallback` in the user's `settings.yaml` are ignored with a deprecation warning.
- `tw source-tier` removed.
- Guard test: shipped defaults may not contain domain lists.

## 0.6.0 - 2026-11-18

Always current: runtime discovery, code verification.
- Principle (ADR-6): code and config hold only rules, verification logic and thresholds; perishable knowledge is discovered at runtime, stored with sources, date and TTL (`tw knowledge put|get|stale`) and re-verified on expiry.
- Source reliability verified on Wikidata (`tw sources verify --url --name --iata`): airline with matching official website → tier 1; online travel agency / metasearch engine / travel website with matching website → tier 2. Verdicts cached 30 days. The config tier lists are now an offline fallback only (`reliability.fallback`); observations relying on them are marked `verified_by: fallback`. `tw watch record` requires a fresh verdict for the source.
- Adaptive access cascade (ADR-7): outcomes recorded per (source, method) (`tw sources record-access`); `tw sources plan` orders attempts by measured success. Methods: webfetch (fare-reader), browser (rendered pages), API adapters with a user-configured key (SerpApi Google Flights: `tw sources api`).
- Booking windows researched per route and season and stored with TTL; `tw advise` uses them with confidence `researched` and returns `research_needed` when missing or expired. The config windows are a generic `heuristic` fallback.
- fare-reader reads only the planned sources and reports per-source attempts; the skill no longer names specific sites.
- Evals: mocked Wikidata verification (match, mismatch, unclassified, unreachable), TTL expiry, access-plan ordering, SerpApi parsing, rendered-page extraction fixtures (`--render browser`).
- `tier1_extra` / `tier2_extra` still work but only extend the offline fallback.

## 0.5.0 - 2026-11-18

Monitoring is proposed, never started on its own.
- After an option is chosen the skill proposes the monitoring plan (watches, cadence, scheduled task, token cost) and creates it only after the user's explicit yes; a partial yes creates only that part. Same for `/travel-radar:monitor setup`, promoted ideas and resumed trips.
- Code guard: `tw watch add <draft> --confirmed-by-user "<user's yes>"` is the only way to activate a watch; watches without `confirmed_by_user` are reported as invalid and never checked. `tw trip set --to monitoring` and `tw trip resume` also require `--confirmed-by-user`; `tw trip resume` without it is a preview.
- **Breaking**: watches created before 0.5.0 are reported as invalid until re-added with `tw watch add` and the user's yes.

## 0.4.1 - 2026-11-18

- Tier 1 sources: added Brussels Airlines, Sichuan Airlines, Saudia, Xiamen Airlines and Hainan (hnair.com); regional subdomains allowed for China Eastern, China Southern, Hainan, Sichuan, Xiamen and Cathay Pacific (e.g. it.ceair.com). Air China alternate domains (airchina.it, airchina.com.cn) already matched.
- Tests for www-stripping, case, regional subdomains and lookalike domains.

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

# Changelog

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

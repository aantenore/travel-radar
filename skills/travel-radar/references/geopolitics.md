# Geopolitical situation

Neutral, factual, sourced; never downplay an official warning, no political opinions.
- Sources found now: the official travel advisory of the traveller's own government (derived from `traveller.nationality`; verify its site with `tw sources verify`, it must come back `government: true`, or confirm it with `tw sources confirm --government`), at least one other government's advisory, and news from the last `geopolitics.news_days` days from several independent outlets.
- Cover, per country and per itinerary place:
  - armed conflicts and tensions, including bilateral tensions affecting the traveller's nationality;
  - protests, elections or anniversaries in the window;
  - terrorism;
  - sanctions affecting payments or cards;
  - airspace closures or rerouting on the found flight routes;
  - strikes;
  - epidemics and seasonal natural hazards;
  - internet or communication restrictions;
  - insurance consequences: check the user's policy only if they provide it, otherwise state the general risk.
- Write items to a YAML list and `tw geopolitics add <trip> --file <yaml>`. Each item: `{id, topic, scope: {country, places, routes, options}, severity: info|watch|warning|blocker, trend: improving|stable|escalating|unknown, impact: [entry, movement, flights, payments, insurance, connectivity, health, safety], what_to_do, sources: [{url, accessed}], advisory: {level, scale_max, source: {url, accessed}}}`. Report the advisory level and scale exactly as the source states them; code maps them to the 1-4 safety level.
- Code enforces: warning/blocker need 2 independent outlets (else `watch`), blocker needs an official source (else `warning`), items expire after `geopolitics.ttl_days`. Use `tw geopolitics check` before any advice and re-verify `stale` items first. Scoring: blocker → option excluded, warning → conflict.

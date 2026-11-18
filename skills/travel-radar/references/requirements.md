# Visas, permits and everything needed

Runtime research, official sources first; never fill any application or enter personal data: give the official link and the steps, the user applies.
- Needs `traveller.nationality` (and passport expiry): if missing, ask that one question (`tw profile gaps --step requirements`). Without it, nationality-dependent items (config `requirements.nationality_dependent`) are stored as `unknown` with `assumed_nationality`, and `tw requirements check` returns `ask: [traveller.nationality]`. Items researched for another nationality become stale when the profile changes.
- Cover for each option and each place:
  - entry: visa or exemption, type, eligibility for this nationality, where and how to apply (online, in person, biometrics), documents, cost, processing time;
  - passport validity and blank pages;
  - transit rules for the layovers of the found flights;
  - regional or special permits (restricted areas, protected sites, border zones) and who issues them;
  - registration on arrival;
  - health: vaccines, insurance requirements, medication import rules;
  - customs limits;
  - driving permits if relevant;
  - drones;
  - mandatory apps or real-name booking;
  - the home country's registration service for citizens abroad.
- `tw requirements add <trip> --file <yaml>` with items `{id, category, applies_to: {options, places}, status: required|recommended|not_required|unknown, action, how: [steps], lead_time_days, feasible_in_window: true|false|unknown, cost: {amount, currency, source}, sources: [{url, accessed}]}`. Categories and their TTLs are in config (`requirements.ttl_days`).
- Code enforces: official = verified government site at runtime; `required` from non-official sources only → `unknown` + verify flag; `required` needs `lead_time_days`; the deadline = departure − lead time − `requirements.safety_margin_days`. `tw requirements check <trip>` → blockers, deadlines by date, stale items, items to verify. Deadlines go into `tw export ics`.
- Scoring: a required item not feasible in the window excludes the option; uncertain feasibility is a conflict.

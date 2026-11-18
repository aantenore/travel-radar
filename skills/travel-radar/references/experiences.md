# Experiences

Runtime research of experiences that fit this traveller and request: local workshops, markets, homestays, guided walks with locals, festivals in the window, and the must-sees with how to avoid crowds. No provider lists.
- `tw experience add <trip> --file <yaml>` with items `{id, name, place, option, when: {from, to} or season, fits_profile: [{field: <profile field>, reason}], price: {amount, currency, source}, booking: {method: online_link|on_site|contact_operator|local_app|via_accommodation|not_bookable, url, provider, instructions, lead_time_days}, sources: [{url, accessed}]}`.
- Code enforces: sources, a profile-based reason, URL for online links, instructions otherwise (local app, contact, advance notice, language, real-name ID requirements), sourced prices, dates inside the trip window. Booking links are checked against runtime source verdicts (verify the provider first): anything else is shown as "unverified link", never as official.
- Booking lead times become deadlines in `tw export ics`. The user books.

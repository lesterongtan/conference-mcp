---
name: conference-scout
description: Find and assess conferences, calls for speakers, calls for proposals, and CFP deadlines using user-provided topic or industry keywords. Use for speaking-opportunity research; do not require a saved speaker persona.
---

# Conference Scout

Use the `conference-scout` MCP tools for current conference research.

## Search

- Treat the user's explicit keywords as authoritative. Do not replace them with a persona.
- When no usable keyword was supplied, ask for at least one topic, expertise area, audience, or industry.
- Pass 1-8 concise phrases to `start_conference_search`. Add geography or date constraints only when provided or clearly implied.
- Start one search, retain its `search_id`, and use `get_search_status` for that run. Do not launch duplicates while it is queued or running.
- Long searches are normal. Stop repeated status checks after a reasonable bounded attempt and tell the user the search ID they can resume.

## Results

- After completion, call `get_search_results` and prioritize topical match, open CFP status, submission URL, deadline, date, geography, and source evidence.
- Distinguish verified fields from missing or unknown fields. Never infer a CFP is open solely because the event is upcoming.
- Preserve source URLs so the user can verify deadlines and applications.
- A result score is a sorting aid, not proof that an event is a good fit.
- Present a compact shortlist unless the user requests the full result set.

## Data changes

`delete_search` permanently removes a stored run. Call it only when the user explicitly asks to delete that search.


---
name: conference-scout
description: Find and assess conferences, CFPs, deadlines, and public organizer contacts using user-provided topic or industry keywords. Use for speaking-opportunity research without requiring a saved speaker persona.
---

# Conference Scout

Use the `conference-scout` MCP tools for current conference research.

## Search

- Treat the user's explicit keywords as authoritative. Do not replace them with a persona.
- When no usable keyword was supplied, ask for at least one topic, expertise area, audience, or industry.
- Pass 1-8 concise phrases to `start_conference_search`. Add geography or date constraints only when provided or clearly implied.
- Use `enrichment_mode=basic` unless the user asks for contact research. Use `standard` for bounded Firecrawl enrichment and `deep` only when browser-agent escalation is worth its added time and provider cost.
- Supply a stable `idempotency_key` when retrying the same request so a transport retry does not create duplicate work or spend.
- Start one search, retain its `search_id`, and use `get_search_status` for that run. Do not launch duplicates while it is queued or running.
- Long searches are normal. Stop repeated status checks after a reasonable bounded attempt and tell the user the search ID they can resume.

## Results

- After completion, call `get_search_results` and prioritize topical match, open CFP status, submission URL, deadline, date, geography, and source evidence.
- Distinguish verified fields from missing or unknown fields. Never infer a CFP is open solely because the event is upcoming.
- Preserve source URLs so the user can verify deadlines and applications.
- A result score is a sorting aid, not proof that an event is a good fit.
- Present a compact shortlist unless the user requests the full result set.

## Contacts

- Treat contact fields as public-source research, not certainty. Preserve evidence URLs and confidence.
- If a useful result still lacks a contact, call `enrich_conference_contact` for that result and retain the returned `job_id`. Poll `get_enrichment_status`; then refresh the result with `get_conference`.
- Prefer `standard` before `deep`. Use `tier3` only as a deliberate manual retry after earlier approaches failed.
- Do not deep-enrich an entire result set by default. Select the highest-value unresolved results or follow the user's requested scope.
- Never invent a name, role, email, LinkedIn URL, deadline, or source. Report unresolved fields as unknown.

## Data changes

`delete_search` permanently removes a stored run and its results, enrichment jobs, and provider attempts. Call it only when the user explicitly asks to delete that search.

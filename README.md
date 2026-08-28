# Conference Scout MCP

A standalone hosted service and MCP server for keyword-driven conference, CFP, and public contact discovery. It does not require SpeakerAgent or a speaker persona.

## What it searches

- The free Confs.tech open-source dataset for supported technology topics
- Google results through Serper when `SERPER_API_KEY` is configured
- Conference pages for structured event data, CFP links, dates, locations, and public contacts
- Optional Firecrawl rendering and an owned contact-finder service for difficult contact searches

SQLite is supported for local work. Hosted deployments use PostgreSQL, durable leased jobs, customer-scoped API keys, quotas, provider evidence, and usage records.

## Install

Python 3.11 or newer is required.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

Copy `.env.example` to `.env` or set the variables in your MCP client environment. A Serper key is optional; without one, the server still searches supported technology conferences through Confs.tech.

## Run

Local MCP clients normally use the standard-input transport:

```powershell
conference-scout-mcp --transport stdio
```

For the combined REST API and hosted MCP endpoint:

```powershell
conference-scout-api
```

The REST API is under `/v1`; the remote MCP endpoint is `/mcp`. For a separate worker deployment, set `CONFERENCE_SCOUT_EMBEDDED_WORKER=false` on the API and run `conference-scout-worker` using the same database and provider configuration.

Before a production start, apply database migrations:

```powershell
alembic upgrade head
```

Create a customer credential from a trusted administrative environment:

```powershell
conference-scout-admin create-customer --customer-id acme --name "Acme"
conference-scout-admin create-key --customer-id acme --name production
```

The plaintext key is shown once. Customers send it as `Authorization: Bearer …` or `X-API-Key` to REST, and as a bearer token to the hosted MCP endpoint.

## MCP client configuration

After installing the package, point Claude, Codex, or another MCP client at:

```json
{
  "mcpServers": {
    "conference-scout": {
      "command": "conference-scout-mcp",
      "args": ["--transport", "stdio"],
      "env": {
        "SERPER_API_KEY": "optional-key",
        "CONFERENCE_SCOUT_DB": "C:/absolute/path/conference-scout.db"
      }
    }
  }
}
```

The repository also includes a Codex plugin manifest, a Claude plugin manifest, and the shared `conference-scout` skill.

## Tools

- `start_conference_search`: starts one asynchronous keyword search
- `get_search_status`: checks the saved run without starting another
- `get_search_results`: returns ranked, paginated results
- `get_conference`: returns one result with its evidence
- `enrich_conference_contact`: queues basic, standard, deep, or tier-3 contact enrichment
- `get_enrichment_status`: checks an enrichment job
- `delete_search`: permanently deletes one saved run

Example request:

> Search for healthcare AI, clinical innovation, and digital health leadership conferences in the United States. Only include open CFPs in the next year.

## Development

```powershell
python -m pytest --basetemp .pytest-tmp/local
ruff check .
```

## Production rules

- Set `CONFERENCE_SCOUT_REQUIRE_AUTH=true`, a long bootstrap key, and a separate pepper.
- Set `CONFERENCE_SCOUT_PUBLIC_URL` to the externally visible HTTPS origin. Hosted startup fails closed when it is left at the loopback default.
- Set `CONFERENCE_SCOUT_DATABASE_URL` to PostgreSQL and disable automatic schema creation after migrations are installed.
- Keep provider credentials on the server. Customers receive Conference Scout credentials only.
- Restrict outbound traffic at the container or network layer to public internet destinations and the configured provider APIs. Application URL checks are defense in depth, not a replacement for an egress firewall.
- Use the owned `CONTACT_FINDER_*` integration as the primary browser automation path.
- `CONTACT_FINDER_MAX_CONCURRENT` defaults to two and is enforced with shared database leases across worker replicas.
- Leave `EXTERNAL_SUPER_BROWSER_*` empty until commercial access is approved.

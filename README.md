# Conference Scout MCP

A standalone MCP server for keyword-driven conference and call-for-speakers discovery. It does not require SpeakerAgent, a speaker persona, Supabase, or a frontend.

## What it searches

- The free Confs.tech open-source dataset for supported technology topics
- Google results through Serper when `SERPER_API_KEY` is configured
- Conference pages for structured event data, CFP links, dates, locations, and public email addresses

Searches and results are stored in a local SQLite database so an agent can resume a long-running search using its `search_id`.

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

For a remotely hosted MCP endpoint:

```powershell
conference-scout-mcp --transport streamable-http
```

The HTTP server defaults to `127.0.0.1:8765`. Set `CONFERENCE_SCOUT_HTTP_HOST` and `CONFERENCE_SCOUT_HTTP_PORT` to change it.

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
- `delete_search`: permanently deletes one saved run

Example request:

> Search for healthcare AI, clinical innovation, and digital health leadership conferences in the United States. Only include open CFPs in the next year.

## Development

```powershell
python -m pytest
ruff check .
```


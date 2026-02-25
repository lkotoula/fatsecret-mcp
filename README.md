# FatSecret MCP Server

A remote MCP (Model Context Protocol) server that connects Claude to your FatSecret food diary. Describe what you ate, and Claude logs it for you.

## Features

- **Search foods** — query the FatSecret database (500k+ foods)
- **Get food details** — view full nutrition info and serving options
- **Log food entries** — add meals to your FatSecret diary
- **View diary** — see daily entries with macro totals
- **Delete entries** — remove incorrect entries
- **OAuth flow** — built-in authorization to connect your FatSecret account

## Quick Start

### 1. Prerequisites

- Python 3.11+
- A [FatSecret Platform API](https://platform.fatsecret.com/register) account (free)
- A FatSecret user account (free)

### 2. Local Setup

```bash
git clone https://github.com/YOUR_USERNAME/fatsecret-mcp-server.git
cd fatsecret-mcp-server
pip install -r requirements.txt

# Set your credentials
export FATSECRET_CONSUMER_KEY="your_key"
export FATSECRET_CONSUMER_SECRET="your_secret"

# Run locally
python server.py
```

The server starts on `http://localhost:8000` with streamable HTTP transport.

### 3. First-time Authorization

After the server is running, connect it to Claude (see below), then:

1. Ask Claude to run `fatsecret_start_auth`
2. Visit the authorization URL it gives you
3. Log in to FatSecret and authorize the app
4. Copy the PIN code
5. Tell Claude the PIN — it will run `fatsecret_complete_auth`
6. Save the access tokens as environment variables for persistence

### 4. Connect to Claude

#### Claude.ai (Remote MCP)
Add as a remote MCP server in Claude.ai settings:
- URL: `https://your-deployed-url.com/mcp`

#### Claude Code (Local)
Add to your `.mcp.json`:
```json
{
  "mcpServers": {
    "fatsecret": {
      "type": "url",
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

Or for stdio mode:
```json
{
  "mcpServers": {
    "fatsecret": {
      "command": "python",
      "args": ["path/to/server.py"],
      "env": {
        "MCP_TRANSPORT": "stdio",
        "FATSECRET_CONSUMER_KEY": "your_key",
        "FATSECRET_CONSUMER_SECRET": "your_secret",
        "FATSECRET_ACCESS_TOKEN": "your_token",
        "FATSECRET_TOKEN_SECRET": "your_secret"
      }
    }
  }
}
```

## Deployment

### Railway

1. Push this repo to GitHub
2. Go to [Railway](https://railway.app) and create a new project from your repo
3. Add environment variables:
   - `FATSECRET_CONSUMER_KEY`
   - `FATSECRET_CONSUMER_SECRET`
   - `FATSECRET_ACCESS_TOKEN` (after first auth)
   - `FATSECRET_TOKEN_SECRET` (after first auth)
4. Deploy — Railway will use the Dockerfile automatically
5. Note your deployment URL (e.g., `https://fatsecret-mcp-server-production.up.railway.app`)

### Render

1. Push to GitHub
2. Create a new **Web Service** on [Render](https://render.com)
3. Set build command: `pip install -r requirements.txt`
4. Set start command: `python server.py`
5. Add the same environment variables
6. Deploy

### Fly.io

```bash
fly launch
fly secrets set FATSECRET_CONSUMER_KEY=your_key
fly secrets set FATSECRET_CONSUMER_SECRET=your_secret
fly deploy
```

## Usage Examples

Once connected to Claude, just chat naturally:

> "I had 2 eggs and a slice of toast for breakfast"

Claude will:
1. Search for "eggs" and "toast" in FatSecret
2. Look up serving details
3. Log both items under breakfast

> "What did I eat today?"

Claude will fetch your diary and show totals.

> "Delete that last toast entry"

Claude will remove the entry from your diary.

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `FATSECRET_CONSUMER_KEY` | Yes | Your FatSecret Platform API consumer key |
| `FATSECRET_CONSUMER_SECRET` | Yes | Your FatSecret Platform API consumer secret |
| `FATSECRET_ACCESS_TOKEN` | No* | User OAuth access token |
| `FATSECRET_TOKEN_SECRET` | No* | User OAuth token secret |
| `MCP_TRANSPORT` | No | `streamable_http` (default) or `stdio` |
| `MCP_PORT` | No | Server port (default: 8000) |

*Required for diary operations. Obtained through the OAuth flow.

## License

MIT

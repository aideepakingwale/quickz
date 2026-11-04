# Assistant access (MCP server)

DiQuizBuzz includes a Model Context Protocol (MCP) server, so a host can let their own AI assistant (Claude, ChatGPT, Cursor and other MCP apps) write quizzes, open game lobbies and read results. The assistant does the thinking on the host's own AI account, so the platform pays for no AI at all.

It is off until a Super Admin switches it on, under **AI > Assistant access**.

## What the server offers

| Tool | Access | What it does |
| --- | --- | --- |
| `get_format_guide` | read | Explains the plain-text quiz format |
| `list_quizzes` | read | The host's quizzes (up to 50) |
| `get_quiz` | read | One quiz's questions, options and correct answers |
| `validate_quiz_text` | read | Checks quiz text and reports exact problems, saving nothing |
| `list_games` | read | The host's recent games with codes and player counts |
| `get_results` | read | Leaderboard (nicknames and scores) and how each question went, with the hardest three |
| `create_quiz_from_text` | write | Saves a new quiz, labelled as AI-made |
| `add_questions_to_quiz` | write | Appends questions; existing ones are never changed |
| `duplicate_quiz` | write | Copies a quiz |
| `start_game` | write | Opens a lobby and returns the code, player link, QR image and host console link |

Resource: `dqb://guide/quiz-text-format` (the same guide as `get_format_guide`).

Deliberately not offered: deleting anything, editing or removing existing questions, running a live game, managing accounts or settings, player emails, pictures and media, and any AI-settings or key access.

## For hosts

1. Open **Account settings > AI assistant access** and choose **Create access token**.
2. Give it a name, choose **Read only** or **Read and write**, and an expiry (30 days, 90 days or 1 year).
3. Copy the token. It is shown once and cannot be recovered.
4. Connect your AI app:
   - Apps that accept a web address and a header: address `https://your-server/mcp`, header `Authorization: Bearer dqb_...`.
   - Apps that start a local program (such as Claude Desktop): use `tools/dqb_mcp_bridge.py` (Python 3.9 or newer, no extra packages) with the settings the token dialog shows:

```json
{
  "mcpServers": {
    "diquizbuzz": {
      "command": "python",
      "args": ["/path/to/dqb_mcp_bridge.py"],
      "env": { "DQB_URL": "https://your-server/mcp", "DQB_TOKEN": "dqb_..." }
    }
  }
}
```

Then ask your assistant, for example: "Write a 10-question quiz on the solar system for 8 to 10 year olds, check it, save it, and open a lobby." Quizzes it creates carry the note "Created by an AI assistant" in their description. Open them in the quiz editor and review every question before a game.

Revoke a token any time from the same page. Tokens also stop working when the account is disabled, when its password is reset, and when the owner chooses **Sign out of all devices**.

## For the Super Admin

Under **AI > Assistant access**: the on/off switch, calls per token per minute (default 60), quizzes per token per day (default 20), open lobbies per host (default 5), and a log of recent activity. Every tool call is recorded with who, which tool, the result and the time taken. Entries follow the same retention period as the AI audit log.

## Protocol notes

- Transport: MCP Streamable HTTP, stateless. One JSON-RPC message per POST to `/mcp`, answered with one JSON reply. There is no server-sent event stream, so `GET` and `DELETE` return 405.
- Protocol versions answered: 2025-06-18, 2025-03-26 and 2024-11-05.
- Capabilities: tools and resources. No prompts, sampling or subscriptions.
- Sign-in is a bearer token only. Browser cookies are not accepted. OAuth is not implemented, so apps that require OAuth sign-in for remote servers cannot connect directly; use the local bridge instead.
- Requests with an `Origin` header from another site are refused.
- Tool problems (a bad quiz, a limit reached) come back as results with `isError` set, so the assistant can correct itself. Protocol problems come back as JSON-RPC errors.

## Security summary

- A token acts as one host and reaches only that host's own quizzes and games, even for a Super Admin.
- Tokens are random, stored only as a hash, expire, and can be revoked.
- Write tools are refused for read-only tokens.
- Calls are rate-limited per token, quiz creation and open lobbies are capped, and request size is capped.
- Player nicknames and quiz text are marked as data in tool results, because a player could type text meant to steer an assistant. Treat them as untrusted.
- Results exclude emails. Remember that anything a tool returns is seen by the AI service the host chose, so avoid connecting an assistant to results from events with sensitive data.

## For developers

- `quickz/mcp.py`: protocol handling, tools, rate limit, HTTP entry point.
- `quickz/db.py`: tables `api_tokens`, `mcp_audit`. `quickz/server.py`: `/api/tokens`, `/mcp`, `/api/admin/mcp/audit`.
- `quickz/static/js/ai.js`: the token page and admin card. `tools/dqb_mcp_bridge.py`: the stdio bridge.
- `tests/test_mcp.py`: protocol, permissions, limits and audit. The server was also checked against the official MCP Python SDK client (version 2.3.0) over real HTTP.

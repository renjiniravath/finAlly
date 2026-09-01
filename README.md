# FinAlly — AI Trading Workstation

FinAlly (Finance Ally) is a Bloomberg-style trading terminal for simulated trading: live-streaming market data, a virtual $10,000 portfolio, and an AI chat assistant that can analyze positions and execute trades on your behalf.

This is a capstone project for an agentic AI coding course, built entirely by coding agents. Full specification and architecture live in [`planning/PLAN.md`](planning/PLAN.md).

## Stack

- **Frontend**: Next.js (TypeScript, static export)
- **Backend**: FastAPI (Python, managed with `uv`)
- **Database**: SQLite (volume-mounted, lazily initialized)
- **Real-time data**: Server-Sent Events
- **AI**: LiteLLM → OpenRouter (Cerebras inference)
- **Deployment**: Single Docker container, single port (8000)

## Status

In development. See `planning/PLAN.md` for the full spec and `planning/REVIEW.md` for open review notes.

## Getting Started

Once built, the app runs via a single Docker command (see `scripts/start_mac.sh` / `scripts/start_windows.ps1`). Requires an `OPENROUTER_API_KEY` in a `.env` file at the project root — see `PLAN.md` §5 for all environment variables.

## License

MIT — see [LICENSE](LICENSE).

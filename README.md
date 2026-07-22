# Nail Salon Voice Agent

A production voice AI receptionist for a nail salon, built on [LiveKit Agents for Python](https://github.com/livekit/agents) and [LiveKit Cloud](https://cloud.livekit.io/). The agent answers calls, looks up services and pricing, checks real-time appointment availability, and books/cancels appointments directly through [Cal.com](https://cal.com/).

## What this agent does

- Answers customer questions about salon services and pricing from a built-in menu
- Checks live appointment availability against a connected Cal.com event type
- Books appointments end-to-end, collecting name, phone number, and confirming details before booking
- Ends the call automatically once a booking is confirmed
- Speaks all responses in natural, TTS-friendly language (no markdown, no raw 24-hour times)

## Stack

| Component | Provider |
|---|---|
| Voice orchestration | [LiveKit Agents](https://github.com/livekit/agents) |
| STT | Deepgram (`nova-2`) |
| LLM | Mistral AI |
| TTS | Deepgram |
| Scheduling | Cal.com API v2 |
| Deployment | Docker → LiveKit Cloud |

## Project structure

```
.
├── src/
│   └── nail_salon_agent.py    # Agent logic, tools, and entrypoint
├── tests/                     # Pytest suite
├── Dockerfile                 # Production container build
├── pyproject.toml             # Dependencies
├── uv.lock                    # Locked dependency versions (tracked in git)
├── livekit.toml                # LiveKit Cloud deployment config
└── .env                        # Local secrets (not tracked in git)
```

## Environment variables

Create a `.env` file (never commit this) with the following, **unquoted**:

```
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=your_livekit_api_key
LIVEKIT_API_SECRET=your_livekit_api_secret

DEEPGRAM_API_KEY=your_deepgram_api_key
MISTRAL_API_KEY=your_mistral_api_key

CAL_API_KEY=cal_live_xxxxxxxxxxxx
CAL_EVENT_TYPE_ID=123456
CAL_API_BASE=https://api.cal.com/v2
```

> **Note:** Values must be unquoted. Docker's `--env-file` reads lines literally — wrapping a value in quotes (e.g. `LIVEKIT_URL="wss://..."`) will pass the quote characters through as part of the value and break the connection.

## Local development

Install dependencies:

```console
uv sync
```

Run the agent interactively in your terminal (text/voice loop, no LiveKit room required):

```console
uv run python src/nail_salon_agent.py console
```

Run in dev mode, connected to a real LiveKit room for use with a frontend or the [Agents Playground](https://agents-playground.livekit.io/):

```console
uv run python src/nail_salon_agent.py dev
```

## Tests

```console
uv run pytest -v
```

CI runs this suite, along with [Ruff](https://docs.astral.sh/ruff/) linting/formatting checks, on every push.

## Production deployment

### Build the Docker image

```console
docker build -t nail-salon-agent .
```

### Run the container

```console
docker run --env-file .env.local nail-salon-agent
```

In production, the container runs the agent in `start` mode, which registers as a persistent worker and waits for LiveKit to dispatch it into rooms — it does not run an interactive session.

### Deploying to LiveKit Cloud

See LiveKit's [deploying to production](https://docs.livekit.io/deploy/agents/) guide. This project's `Dockerfile` is ready for that flow as-is.

## Connecting a frontend or telephony

This agent is compatible with any [LiveKit frontend](https://docs.livekit.io/frontends/) or [telephony integration](https://docs.livekit.io/telephony/) — web, mobile, or inbound/outbound phone calls. Pick the integration that matches how customers will actually reach the salon (e.g. a phone number via LiveKit SIP trunking for a real receptionist replacement).

## Cal.com setup notes

A few non-obvious Cal.com configuration details this project depends on:

- The event type's **Availability schedule** must be assigned correctly per event type — the account-level default schedule is not automatically used by every event type.
- If bookings unexpectedly fail with `"already has booking or not available"`, check for stray test bookings or synced external calendar events blocking the requested slot.
- Optional booking-question fields (e.g. a custom "Phone number" question, separate from the standard attendee phone field) can cause `invalid_number`-style errors if enabled and redundant with the agent's payload. Keep custom booking questions off unless intentionally wired up.

## License

MIT License — see [LICENSE](LICENSE) for details.

# ai-powered-link-analysis
AI-powered website analysis API built with Python, FastAPI, and LLMs, providing competitor insights, SEO recommendations, and target audience analysis.

This repository contains the project foundation, the Stage 1 public URL
validation with SSRF protections, and the Stage 2 safe website fetching
implementation. Website content extraction, LLM integration, and analysis
logic are intentionally not implemented yet.

## What is included

- FastAPI application entry point (`app/main.py`), served on port **4000**.
- Environment-based configuration via `pydantic-settings`.
- `GET /health` health-check endpoint.
- Pydantic response schemas.
- Separate API routes, services, and utilities packages.
- Reusable public URL validation with SSRF protections.
- Safe website fetching with SSRF protections, HTML-only content checks,
  streamed response-size limits, and bounded manual redirects.
- Pytest test suite.
- `.env.example`, `.gitignore`, and pinned `requirements.txt`.

## Project structure

```
.
├── app/
│   ├── __init__.py
│   ├── main.py                 # FastAPI app factory and entry point
│   ├── api/
│   │   ├── __init__.py
│   │   └── routes/
│   │       ├── __init__.py
│   │       └── health.py       # GET /health
│   ├── core/
│   │   ├── __init__.py
│   │   └── config.py           # Environment-based settings
│   ├── schemas/
│   │   ├── __init__.py
│   │   └── health.py           # HealthResponse model
│   ├── services/
│   │   ├── __init__.py         # Business logic services
│   │   ├── fetching.py          # Safe website fetching (SSRF-protected)
│   │   └── _pinned_transport.py  # IP-pinned HTTPX transport
│   └── utils/
│       ├── __init__.py
│       └── url_validation.py   # Public URL validation and SSRF checks
├── tests/
│   ├── test_config.py
│   ├── test_health.py
│   ├── test_fetching.py
│   ├── test_pinned_transport.py
│   └── test_url_validation.py
├── .env.example
├── .gitignore
├── README.md
└── requirements.txt
```

## Requirements

- Python 3.11+ (verified with Python 3.13)
- A virtual environment at `.venv`

## Setup

Windows (PowerShell):

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

macOS / Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` as needed. It is ignored by git; only `.env.example` is committed.

## Configuration

| Variable      | Default                                  | Description                                      |
| ------------- | ---------------------------------------- | ------------------------------------------------ |
| `APP_NAME`    | `AI-Powered Website Link Analysis API`   | Service name shown in docs and health response.  |
| `APP_VERSION` | `0.1.0`                                  | Application version.                             |
| `ENVIRONMENT` | `development`                            | One of `development`, `test`, `production`.      |
| `DEBUG`       | `false`                                  | FastAPI debug mode + reload; false in production. |
| `HOST`        | `0.0.0.0`                                | Bind host used by `python -m app.main`.          |
| `PORT`        | `4000`                                   | Bind port used by `python -m app.main`.          |
| `LLM_API_KEY` | _(empty)_                                | LLM provider credential; blank means unset.      |
| `FETCH_TIMEOUT_SECONDS` | `10.0`                         | Per-request timeout for outbound fetching.       |
| `FETCH_MAX_RESPONSE_BYTES` | `2000000`                     | Maximum accepted HTML response size in bytes.   |
| `FETCH_MAX_REDIRECTS` | `5`                                | Maximum number of manual redirects followed.    |

Environment variables take precedence over `.env` values. The `.env` file is
loaded from the project root, regardless of the current working directory.

When `ENVIRONMENT=production`, `DEBUG` must remain `false`. Enabling it causes
startup to fail with a validation error, so FastAPI debug pages and Uvicorn
auto-reload cannot be enabled accidentally in production.

## Running the API

From the project root, with the virtual environment active:

```powershell
python -m uvicorn app.main:app --host 0.0.0.0 --port 4000 --reload
```

Alternatively, run the entry point directly; it uses `HOST` and `PORT` from
the environment (default port `4000`):

```powershell
python -m app.main
```

Interactive docs: <http://127.0.0.1:4000/docs>

## Health check

```http
GET /health
```

```json
{
  "status": "ok",
  "service": "AI-Powered Website Link Analysis API",
  "version": "0.1.0"
}
```

## Tests

```powershell
python -m pytest
```

## Security notes

- API credentials are read exclusively from environment variables (or a
  local, git-ignored `.env` file). Nothing is hardcoded in source.
- `.env` and other local environment files are excluded by `.gitignore`.
- Only blank placeholders belong in `.env.example`.
- Outbound website fetching disables environment proxy inheritance and
  automatic redirects, and uses an IP-pinned transport that connects only to
  validated public addresses while preserving the hostname for HTTP Host and
  TLS SNI, and verifies the connected peer.

## URL validation (Stage 1)

`app/utils/url_validation.py` exposes `validate_public_url(url)`, which:

- accepts only HTTP and HTTPS URLs on their default ports (80 and 443);
- rejects malformed URLs, embedded credentials, local/metadata hostnames, and
  non-public IP addresses;
- rejects Unicode format characters (zero-width and bidirectional controls);
- rejects reserved hostnames under `.onion`, `.test`, `.example`, `.invalid`,
  and `.alt`;
- rejects non-canonical port text such as `:080` instead of normalizing it;
- normalizes hostnames to lowercase ASCII/IDNA form;
- resolves DNS once and rejects the URL if resolution fails or any result is
  non-public;
- returns a `ValidatedURL` containing the normalized URL, hostname, port, and
  resolved IP addresses, plus a `host_header` property that brackets IPv6
  hostnames and omits the default port.

Callers must build the request from the returned `ValidatedURL` and must never
re-parse the original raw input. Use `ValidatedURL.url` for the request,
`ValidatedURL.hostname` for TLS SNI, and `ValidatedURL.host_header` for the HTTP
`Host` header.

Security limitations:

- DNS can change between validation and connection (DNS rebinding). The
  fetching layer connects only to the validated addresses snapshot and
  verifies the connected peer, but the snapshot itself is time-sensitive.
- IDNA handling uses the standard library's IDNA 2003 codec, not full UTS #46.
  Confusable Unicode hostnames are not detected.

## Safe fetching (Stage 2)

`app/services/fetching.py` exposes `fetch_html(validated_url: ValidatedURL) -> FetchedPage`.

- Accepts a `ValidatedURL` produced by `validate_public_url`; raw URL strings
  are rejected.
- Makes an HTTP GET through the IP-pinned transport in
  `app/services/_pinned_transport.py`, which connects to the validated IP
  address instead of resolving the hostname again, and verifies the connected
  peer against the validated address set. The validated hostname is preserved
  for the HTTP `Host` header and TLS SNI.
- Disables environment proxies (`trust_env=False`) and automatic redirects
  (`follow_redirects=False`).
- Uses explicit connect/read/write/pool timeouts and bounded connection
  limits (max 10 connections, no keep-alive reuse, HTTP/1 only).
- Sends a fixed `User-Agent` and `Accept: text/html, ...` header.
- Follows redirects manually: bounded by `FETCH_MAX_REDIRECTS`, validates every
  target (including relative `Location` values) with `validate_public_url`,
  detects loops, and rejects unsafe destinations.
- Accepts only `text/html` and `application/xhtml+xml` responses; missing or
  unsupported content types are rejected.
- Streams the response body through `aiter_bytes()` (decoded, i.e. after
  gzip/deflate decompression) and enforces the configured
  `FETCH_MAX_RESPONSE_BYTES` limit.
- Translates network, timeout, protocol, and decoding failures into
  application-level exceptions (`FetchError` subclasses).
- Does not parse HTML yet; it only returns the fetched HTML text.

## Not implemented yet

- Website content extraction, metadata parsing, and analysis.
- LLM provider integration.
- Competitor, SEO, and target-audience analysis endpoints.

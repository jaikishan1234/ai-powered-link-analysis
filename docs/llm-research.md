# LLM Research & Cost Optimization — Zero-Budget Strategy

## 1. Current LLM Integration

The project currently integrates a single LLM provider through an OpenAI-compatible
Chat Completions client.

| Setting | Value | Source |
|---|---|---|
| Provider | NVIDIA NIM | `app/services/llm.py` |
| Base URL | `https://integrate.api.nvidia.com/v1` | `app/core/config.py` (`llm_base_url`) |
| Default model | `meta/llama-3.1-8b-instruct` | `app/core/config.py` (`llm_model`) |
| API key source | `LLM_API_KEY` environment variable (or `.env`) | `app/core/config.py` (`llm_api_key`) |
| Request timeout | `30.0` seconds | `app/core/config.py` (`llm_timeout_seconds`) |
| JSON / structured output | Supported via `response_format: {"type": "json_object"}` | `app/services/llm.py` (`chat_json`) |
| Client transport | `httpx2.AsyncClient` (existing dependency) | `app/services/llm.py` |
| Auth | Bearer token from `LLM_API_KEY` | `app/services/llm.py` |

**Notes:**
- The client is provider-agnostic by design: `base_url`, `model`, and `api_key` are
  all configurable, so switching providers requires only environment-variable
  changes, not code changes.
- JSON mode is implemented by setting `response_format` to `{"type": "json_object"}`
  and parsing the returned content with `json.loads()`.
- No API keys are hardcoded; all credentials come from environment variables.
- **NVIDIA NIM pricing:** The NVIDIA NIM documentation was reviewed
  (`docs.nvidia.com/nim/index.html`), but a public per-token pricing page for the
  `meta/llama-3.1-8b-instruct` model on `integrate.api.nvidia.com` was not
  directly verifiable during this research. NVIDIA NIM pricing varies by model,
  region, and deployment option (NVIDIA API catalog vs. self-hosted NIM
  microservices). Specific NIM token prices are therefore marked as "not
  verified from public docs" in this document.
- **No application code changes are proposed or required.** The current NVIDIA NIM
  integration remains the primary provider.

## 2. Zero-Budget Candidate Providers

The following providers offer genuinely available free API access suitable for
the website-analysis workload (structured JSON extraction of competitor, SEO,
services, and audience data from ~20K characters of extracted page text).
Free-tier access is **rate-limited and not unlimited** — it is suitable for
development and light usage, not production-scale analysis.

### Provider A — NVIDIA NIM (current, kept as primary)

- **Provider:** NVIDIA
- **Model:** `meta/llama-3.1-8b-instruct`
- **API:** OpenAI-compatible Chat Completions
- **Base URL:** `https://integrate.api.nvidia.com/v1`
- **Context window:** 128K tokens (Llama 3.1 8B family) — **unverified from public docs**
- **Structured JSON output:** `response_format: {"type": "json_object"}` (OpenAI-compatible)
- **Free-tier availability:** NVIDIA API catalog offers free credits for new users;
  ongoing free tier is **not publicly guaranteed** — **unverified**
- **Rate limits:** Not publicly documented for the API catalog; **unverified**
- **Published paid pricing:** Not directly verifiable from public NVIDIA docs;
  varies by deployment and region — **unverified**
- **Official documentation:** https://docs.nvidia.com/nim/index.html
- **Notes:** Kept as the current primary integration. No code or config changes
  are required to continue using it. The main limitation is the lack of
  publicly verifiable per-token pricing and rate limits, which makes zero-budget
  planning less predictable than with providers that publish free-tier limits.

### Provider B — Google Gemini API

- **Provider:** Google
- **Model:** `gemini-3.5-flash-lite` (free tier); `gemini-3.6-flash` also available
- **API:** Gemini Developer API (native; OpenAI-compatible SDKs exist but are not
  the default integration path)
- **Base URL:** `https://generativelanguage.googleapis.com/v1beta/`
- **Context window:** 1M tokens (gemini-3.5-flash-lite) — from published docs
- **Structured JSON output:** Native JSON mode and `response_schema` for structured
  output — from published docs
- **Free-tier availability:** Yes — free input and output tokens with rate limits.
  Free tier is **not unlimited**: rate limits apply and vary by model and region.
  Free-tier input is used to improve Google's products unless on a paid plan —
  from published docs
- **Rate limits:** Not specified as exact RPM/TPM figures on the public pricing
  page; enforced dynamically — **unverified exact limits**
- **Published paid pricing (Standard, per 1M tokens):**
  - Input: $0.30
  - Output: $2.50
  - Cached input: $0.03
- **Official documentation:** https://ai.google.dev/pricing

### Provider C — Groq

- **Provider:** Groq
- **Models (free plan examples):** `meta-llama/llama-prompt-guard-2-22m`,
  `meta-llama/llama-prompt-guard-2-86m`, `openai/gpt-oss-20b`,
  `openai/gpt-oss-120b`, `qwen/qwen3.8-27b` — from published rate-limit docs
- **API:** OpenAI-compatible Chat Completions
- **Base URL:** `https://api.groq.com/openai/v1` — **inferred from OpenAI
  compatibility docs; exact base URL not independently verified**
- **Context window:** Model-dependent; Llama 3.1 8B variants typically 128K —
  **unverified from public docs**
- **Structured JSON output:** Documented "Structured Outputs" feature — from
  published docs
- **Free-tier availability:** Yes — Free Plan (Developer plan) with rate limits.
  Free tier is **not unlimited**: RPM, RPD, TPM, and TPD limits apply per model
  and at the organization level. Higher limits available via paid Developer plan —
  from published docs
- **Rate limits (Free Plan / Developer plan, from published docs):**
  - `meta-llama/llama-prompt-guard-2-22m`: 30 RPM, 14.4K RPD, 15K TPM, 500K TPD
  - `meta-llama/llama-prompt-guard-2-86m`: 30 RPM, 14.4K RPD, 15K TPM, 500K TPD
  - `openai/gpt-oss-20b`: 30 RPM, 1K RPD, 8K TPM, 200K TPD
  - `openai/gpt-oss-120b`: 30 RPM, 1K RPD, 8K TPM, 200K TPD
  - `qwen/qwen3.8-27b`: 30 RPM, 1K RPD, 8K TPM, 200K TPD
  - Cached tokens do not count toward rate limits — from published docs
- **Published paid pricing:** Available via Groq console; not directly fetched
  during this research — **unverified exact paid prices**
- **Official documentation:** https://console.groq.com/docs/rate-limits

### Provider D — OpenRouter (free models)

- **Provider:** OpenRouter (model aggregator)
- **Free models:** Many models available with a `:free` variant (e.g.,
  `openrouter/free` auto-selects a free model). Specific free models and their
  rate limits vary and are listed at `https://openrouter.ai/models?max_price=0` —
  from published docs
- **API:** OpenAI-compatible Chat Completions (`/chat/completions`) and
  Responses API (`/api/v1/responses`)
- **Base URL:** `https://openrouter.ai/api/v1` — from published docs
- **Context window:** Model-dependent; varies by the underlying free model —
  **unverified per-model**
- **Structured JSON output:** Yes — OpenAI-compatible `response_format:
  {"type": "json_object"}`; JSON Schema support depends on the underlying
  provider/model — from published docs
- **Free-tier availability:** Yes — free models available with low rate limits.
  Free tier is **not unlimited**:
  - Without purchased credits: 50 free-model API requests per day
  - With at least 10 credits purchased: 1000 free-model requests per day
  - Free models are "usually not suitable for production use" — from published docs
- **Rate limits:** 50 RPD (no credits) or 1000 RPD (10+ credits) for free models —
  from published docs
- **Published paid pricing:** Pass-through pricing with no markup on inference;
  OpenRouter charges a 5.5% fee ($0.80 minimum) when purchasing credits — from
  published docs
- **Official documentation:** https://openrouter.ai/docs/faq.md

## 3. Comparison Table

| Criterion | NVIDIA NIM (current) | Google Gemini | Groq (free plan) | OpenRouter (free models) |
|---|---|---|---|---|
| Provider | NVIDIA | Google | Groq | OpenRouter |
| Base URL | `https://integrate.api.nvidia.com/v1` | `https://generativelanguage.googleapis.com/v1beta/` | `https://api.groq.com/openai/v1` (inferred) | `https://openrouter.ai/api/v1` |
| Free tier | Credits for new users (unverified) | Yes, rate-limited (unverified exact limits) | Yes, Developer plan limits (published) | Yes, 50–1000 RPD (published) |
| Free tier unlimited? | No — unverified | No — rate-limited | No — rate-limited | No — 50/1000 RPD |
| Available models | `meta/llama-3.1-8b-instruct` | `gemini-3.5-flash-lite`, `gemini-3.6-flash` | Llama 3.x, Qwen, GPT-OSS, etc. | Many models via `:free` variant |
| Context window | 128K (unverified) | 1M | 128K (typical, unverified) | Model-dependent (unverified) |
| Structured JSON | `json_object` (OpenAI-compat) | JSON mode + `response_schema` | Structured Outputs (published) | `json_object` (OpenAI-compat) |
| Paid pricing | Not verified (unverified) | $0.30 in / $2.50 out per 1M | Not fetched (unverified) | Pass-through + 5.5% credit fee |
| Code changes to switch | None (current) | Minimal (native Gemini or OpenAI-compat SDK) | None (OpenAI-compatible) | None (OpenAI-compatible) |
| Official docs | docs.nvidia.com/nim | ai.google.dev/pricing | console.groq.com/docs | openrouter.ai/docs |

**Important:** Free-tier access is **not unlimited**. All providers enforce rate
limits (requests per minute/day, tokens per minute/day). Exceeding limits
results in HTTP 429 responses. Free tiers are appropriate for development and
light evaluation, not high-volume production analysis.

**Speed note:** Response-speed figures are **estimated**, not measured. No
benchmarking was performed against any provider. Latency depends on model size,
input length, output length, provider load, and region.

**Quality note:** No accuracy measurements were performed. The 8B-instruct class
of models is sufficient for the structured extraction tasks in this project
(competitor, SEO, services, audience analysis), but larger models may produce
more consistent JSON at higher cost or tighter rate limits.

## 4. Recommended Zero-Budget Strategy

**Keep NVIDIA NIM (`meta/llama-3.1-8b-instruct`) as the primary provider.**

### Rationale

1. **No code changes required:** The existing integration already works with
   NVIDIA NIM. The `LLMClient` accepts `base_url`, `model`, and `api_key` as
   parameters, so the provider is fully configurable via environment variables
   (`LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`).
2. **Zero additional cost:** NVIDIA NIM is already integrated and requires no
   new accounts, API keys, or billing setup beyond what is already configured.
3. **OpenAI-compatible:** The client uses the OpenAI Chat Completions format,
   which means any of the alternative providers (Groq, OpenRouter) can be
   swapped in by changing only `LLM_BASE_URL` and `LLM_MODEL` — no code changes.
4. **Adequate for the workload:** The project truncates extracted text to
   `EXTRACT_MAX_TEXT_CHARS` (default 20,000 characters ≈ 5,000–7,000 tokens) and
   validates all LLM output against strict Pydantic schemas. An 8B-instruct
   model is sufficient for this structured extraction workload.

### Free-tier fallback providers (if NVIDIA NIM becomes unavailable)

If NVIDIA NIM free credits are exhausted or the service is unavailable, the
following zero-budget fallbacks are available by changing environment variables
only:

1. **Groq** — OpenAI-compatible, published free-plan rate limits, structured
   outputs supported. Best drop-in replacement if you need verified rate limits.
2. **OpenRouter** — OpenAI-compatible, free models via `:free` variant, model
   fallthrough for resilience. Best if you want automatic provider fallback.
3. **Google Gemini** — 1M context window, native JSON support, ongoing free
   tier. Best if you need the largest context, but requires a non-OpenAI-native
   integration path (native Gemini API or OpenAI-compatible SDK).

### Do not recommend paid models

Paid models such as OpenAI `gpt-5-nano` are **not recommended** as the primary
choice for a zero-budget deployment. They require paid API credits and do not
offer an ongoing free tier (only one-time introductory credits). The project's
structured extraction workload does not require a larger model, and the existing
NVIDIA NIM integration already satisfies the requirements at no additional code
cost.

## 5. Cost Optimization Strategies (Relevant to Zero-Budget)

These strategies are already relevant to the current implementation and help
stay within free-tier rate limits:

### Prompt efficiency

- **Keep system prompts concise:** The system prompt in
  `app/services/analysis.py` (~250 words) already emphasizes the security
  constraint and JSON-only output. A single shared system prompt is used for all
  five analysis sections — avoid repeating instructions.
- **Avoid few-shot examples:** Zero-shot JSON extraction works for this
  workload; examples increase input tokens without clear benefit and consume
  free-tier token budgets faster.

### Input truncation (already implemented)

- **`EXTRACT_MAX_TEXT_CHARS` cap:** The extraction service caps cleaned text at
  `EXTRACT_MAX_TEXT_CHARS` (default 20,000 characters ≈ 5,000–7,000 tokens).
  This is the single most effective cost control — it directly limits input
  tokens against free-tier TPM/TPD limits.
- **Pass only relevant sections:** The context builder includes URL, title,
  description, headings, and text. Raw HTML, scripts, styles, and boilerplate
  are already removed by the extraction service.

### Output limits

- **Set `max_tokens`:** `LLMClient.chat()` and `chat_json()` accept
  `max_tokens`. Capping output at ~1,000–2,000 tokens prevents runaway
  generation while still allowing all five analysis sections.
- **Request only needed fields:** Schema hints in `AnalysisService` request
  exactly the fields needed per section (no extra metadata or explanations).

### Avoid unnecessary repeated calls

- **Five sequential LLM calls:** The current design makes one call per analysis
  section (competitors, search queries, SEO, services, audience). This could be
  consolidated into a single call requesting all five sections in one JSON
  object, reducing per-call overhead (system prompt repetition, connection
  setup, rate-limit hits) — valuable when operating under tight free-tier RPM
  limits.
- **Cache repeated analyses:** Cache `AnalysisResponse` keyed by normalized URL
  to avoid redundant LLM calls for the same page. FastAPI dependency injection
  already uses `lru_cache` for service instantiation; response caching can be
  added at the route or service layer without changing the LLM client.
- **Batch inference where available:** Groq offers Batch and Flex processing;
  OpenRouter offers batch-priced models (`:batch` variant); Gemini offers Batch
  API (50% cost reduction). For non-interactive bulk analysis, these reduce
  per-request cost at the expense of higher latency.

## 6. Sources

- NVIDIA NIM documentation: https://docs.nvidia.com/nim/index.html (general
  product info; per-token pricing and rate limits for `meta/llama-3.1-8b-instruct`
  on `integrate.api.nvidia.com` not directly verifiable from public docs)
- Google Gemini Developer API pricing: https://ai.google.dev/pricing (published
  pricing, fetched 2026-11-22)
- Groq rate limits: https://console.groq.com/docs/rate-limits (published free
  plan limits, fetched 2026-11-22)
- OpenRouter FAQ: https://openrouter.ai/docs/faq.md (free model rate limits,
  credits, pricing, fetched 2026-11-22)
- Current project configuration: `app/core/config.py`
- Current LLM client: `app/services/llm.py`
- Current analysis service: `app/services/analysis.py`

---

**Disclaimer:** Pricing and rate-limit figures are from public documentation as
of the fetch date and are subject to change. Free-tier access is rate-limited
and **not unlimited** — do not treat any free API as completely free or without
limits. Response-speed and quality assessments are estimates, not measured
results. No benchmarking or accuracy testing was performed. API keys are not
included in this document. No application code was modified as part of this
research.

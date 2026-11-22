# LLM Research & Cost Optimization — Dxora Practical Assignment

## 1. Purpose and Scope

This document compares six current LLM models across four providers for the
website-analysis workload in the `ai-powered-link-analysis` project. It was
prepared as part of the Dxora practical assignment.

**Research date:** 2026-11-22

**No API calls were made and no API credits were purchased.** All pricing and
model information comes from official provider documentation fetched read-only.
Response-speed and quality assessments are estimates, not measured results.

## 2. Current Integration (NVIDIA NIM)

The project already integrates one LLM provider through an OpenAI-compatible
Chat Completions client in `app/services/llm.py`. This is the **existing
implementation**; no code changes are proposed.

| Setting | Value | Source |
|---|---|---|
| Provider | NVIDIA NIM | `app/services/llm.py` |
| Base URL | `https://integrate.api.nvidia.com/v1` | `app/core/config.py` (`llm_base_url`) |
| Default model | `meta/llama-3.1-8b-instruct` | `app/core/config.py` (`llm_model`) |
| API key source | `LLM_API_KEY` environment variable | `app/core/config.py` (`llm_api_key`) |
| Request timeout | 30.0 seconds | `app/core/config.py` (`llm_timeout_seconds`) |
| JSON / structured output | `response_format: {"type": "json_object"}` | `app/services/llm.py` (`chat_json`) |

The client accepts `base_url`, `model`, and `api_key` as constructor
parameters, so provider-specific values can be supplied at instantiation.
Switching providers is **not** guaranteed to work through environment variables
alone: provider compatibility (authentication header shape, response structure,
streaming behavior, JSON mode support, error codes) must be tested before any
provider change is adopted. No code changes to the LLM client are proposed in
this document.

**NVIDIA NIM pricing:** The NVIDIA NIM documentation
(`https://docs.nvidia.com/nim/index.html`) was reviewed, but a public per-token
pricing page for `meta/llama-3.1-8b-instruct` on `integrate.api.nvidia.com` was
not directly verifiable during this research. NVIDIA NIM pricing varies by
model, region, and deployment option (NVIDIA API catalog vs. self-hosted NIM
microservices). Specific NIM token prices are therefore marked as **not
verified from public docs** throughout this document.

## 3. Six-Model Comparison

### 3.1 NVIDIA NIM — `meta/llama-3.1-8b-instruct` (existing integration)

| Criterion | Detail |
|---|---|
| Provider | NVIDIA |
| Model ID | `meta/llama-3.1-8b-instruct` |
| Base URL | `https://integrate.api.nvidia.com/v1` |
| Input price (per 1M tokens) | Not verified from public docs |
| Output price (per 1M tokens) | Not verified from public docs |
| Context window | 131K tokens per official Llama 3.1 model documentation |
| Structured JSON output | `response_format: {"type": "json_object"}` (OpenAI-compatible) |
| Free tier | NVIDIA's listed free hosted endpoint on `integrate.api.nvidia.com` is
  deprecated; partner-hosted NIM microservices (self-managed or NVIDIA AI
  Enterprise deployments) may still be available. The project's existing
  integration continues to use `LLM_API_KEY` from the environment; this document
  does not claim that the existing key is invalid or that the endpoint is down.
  Ongoing free tier availability and per-token pricing are **not verified from
  public docs** — unverified |
| Response speed | Estimated — not measured |
| Quality and accuracy | Good for structured extraction; 8B-instruct class model. No benchmark results measured. |
| Suitability for five-section website analysis | Suitable. Already integrated; project truncates extracted text to ~20K characters (~5K–7K tokens) and validates output against Pydantic schemas. |
| Official docs | https://docs.nvidia.com/nim/index.html |

### 3.2 OpenAI — `gpt-4o-mini`

| Criterion | Detail |
|---|---|
| Provider | OpenAI |
| Model ID | `gpt-4o-mini` |
| Base URL | `https://api.openai.com/v1` |
| Input price (per 1M tokens) | $0.15 — verified from official OpenAI pricing (Standard tier) |
| Output price (per 1M tokens) | $0.60 — verified from official OpenAI pricing (Standard tier) |
| Cached input price (per 1M tokens) | $0.075 — verified from official OpenAI pricing (Standard tier) |
| Context window | 128K input tokens |
| Structured JSON output | `response_format: {"type": "json_object"}` or JSON Schema via `response_format: {"type": "json_schema", ...}` |
| Free tier | $5 in free credits for new accounts (one-time); no ongoing free tier |
| Response speed | Estimated — not measured |
| Quality and accuracy | Strong general-purpose model. Suitable for structured extraction. No benchmark results measured. |
| Suitability for five-section website analysis | Suitable. Higher cost than `gpt-5-nano`; provider compatibility with the
  existing `LLMClient` must be tested before adoption. |
| Official docs | https://platform.openai.com/docs/pricing |

### 3.3 OpenAI — `gpt-5-nano`

| Criterion | Detail |
|---|---|
| Provider | OpenAI |
| Model ID | `gpt-5-nano` |
| Base URL | `https://api.openai.com/v1` |
| Input price (per 1M tokens) | $0.05 — verified from official OpenAI pricing (Standard tier) |
| Output price (per 1M tokens) | $0.40 — verified from official OpenAI pricing (Standard tier) |
| Cached input price (per 1M tokens) | $0.005 — verified from official OpenAI pricing (Standard tier) |
| Context window | 128K input tokens (short context ≤272K) |
| Structured JSON output | `response_format: {"type": "json_object"}` or JSON Schema |
| Free tier | $5 in free credits for new accounts (one-time); no ongoing free tier |
| Response speed | Estimated — not measured |
| Quality and accuracy | Nano-tier model; adequate for structured extraction from cleaned text. No benchmark results measured. |
| Suitability for five-section website analysis | Suitable. Lowest published cost among verified OpenAI models; provider
  compatibility with the existing `LLMClient` must be tested before adoption. |
| Official docs | https://platform.openai.com/docs/pricing |

### 3.4 Google Gemini — `gemini-3.5-flash-lite` (Flash-Lite)

| Criterion | Detail |
|---|---|
| Provider | Google (Gemini Developer API) |
| Model ID | `gemini-3.5-flash-lite` |
| Base URL | `https://generativelanguage.googleapis.com/v1beta/` |
| Input price (per 1M tokens) | $0.30 |
| Output price (per 1M tokens) | $2.50 |
| Cached input price (per 1M tokens) | $0.03 |
| Context window | 1M tokens |
| Structured JSON output | Native JSON mode and `response_schema` for structured output |
| Free tier | Yes — free input and output tokens with rate limits; **not unlimited**. Free-tier input is used to improve Google's products unless on a paid plan. |
| Response speed | Estimated — not measured |
| Quality and accuracy | Cost-efficient Flash-Lite model optimized for high-volume agentic tasks and simple data processing. No benchmark results measured. |
| Suitability for five-section website analysis | Suitable. 1M context window comfortably accommodates the ~20K-character extraction cap. Native JSON support. Requires native Gemini SDK or OpenAI-compatible wrapper (not the project's current default integration path). |
| Official docs | https://ai.google.dev/pricing |

### 3.5 Google Gemini — `gemini-3.8-flash` (Flash)

| Criterion | Detail |
|---|---|
| Provider | Google (Gemini Developer API) |
| Model ID | `gemini-3.8-flash` |
| Base URL | `https://generativelanguage.googleapis.com/v1beta/` |
| Input price (per 1M tokens) | $0.75 through December 31, 2026; $1.50 starting January 1, 2027 |
| Output price (per 1M tokens) | $3.75 through December 31, 2026; $7.50 starting January 1, 2027 |
| Cached input price (per 1M tokens) | $0.075 through December 31, 2026; $0.15 starting January 1, 2027 |
| Context window | 1M tokens |
| Structured JSON output | Native JSON mode and `response_schema` for structured output |
| Free tier | Yes — free input and output tokens with rate limits; **not unlimited**. |
| Response speed | Estimated — not measured |
| Quality and accuracy | "Most intelligent Flash model" per Google docs; engineered for long-horizon software engineering and complex enterprise workflows. No benchmark results measured. |
| Suitability for five-section website analysis | Suitable but higher cost than Flash-Lite. 1M context window. Native JSON support. Requires native Gemini SDK or OpenAI-compatible wrapper. |
| Official docs | https://ai.google.dev/pricing |

### 3.6 Anthropic — `claude-haiku-5-5` (Claude Haiku)

| Criterion | Detail |
|---|---|
| Provider | Anthropic |
| Model ID | `claude-haiku-5-5` |
| Base URL | `https://api.anthropic.com` (Anthropic API) |
| Input price (per 1M tokens) | From $0.10 |
| Output price (per 1M tokens) | From $0.50 |
| Context window | 1M tokens |
| Structured JSON output | Not natively documented as `response_format: {"type": "json_object"}`. Anthropic uses tool use / structured output via the Messages API; JSON output is achieved through tool definitions and schema enforcement rather than a native `json_object` response format. **Unverified for exact JSON-mode behavior** — requires integration testing. |
| Free tier | Not documented as having an ongoing free tier on the public models page — **unverified** |
| Response speed | "Fastest" in the Claude lineup (comparative latency) — estimated, not measured |
| Quality and accuracy | Designed for "high-volume, latency-sensitive tasks such as classification, extraction, and routing." No benchmark results measured. |
| Suitability for five-section website analysis | Conditionally suitable. Strong for extraction tasks, but structured JSON output path differs from the project's existing OpenAI-compatible `LLMClient.chat_json()`, which expects `response_format: {"type": "json_object"}`. Switching would require code changes to the LLM client or an adapter layer. |
| Official docs | https://platform.claude.com/docs/en/models/overview |

## 4. Comparison Table

| Criterion | NVIDIA NIM Llama 3.1 8B | OpenAI gpt-4o-mini | OpenAI gpt-5-nano | Gemini 3.5 Flash-Lite | Gemini 3.8 Flash | Anthropic Claude Haiku 5.5 |
|---|---|---|---|---|---|---|
| Provider | NVIDIA | OpenAI | OpenAI | Google | Google | Anthropic |
| Model ID | `meta/llama-3.1-8b-instruct` | `gpt-4o-mini` | `gpt-5-nano` | `gemini-3.5-flash-lite` | `gemini-3.8-flash` | `claude-haiku-5-5` |
| Base URL | `https://integrate.api.nvidia.com/v1` | `https://api.openai.com/v1` | `https://api.openai.com/v1` | `https://generativelanguage.googleapis.com/v1beta/` | `https://generativelanguage.googleapis.com/v1beta/` | `https://api.anthropic.com` |
| Input price / 1M | Not verified | $0.15 (verified, OpenAI Standard) | $0.05 (verified, OpenAI Standard) | $0.30 | $0.75 ($1.50 from 2027-01-01) | From $0.10 |
| Output price / 1M | Not verified | $0.60 (verified, OpenAI Standard) | $0.40 (verified, OpenAI Standard) | $2.50 | $3.75 ($7.50 from 2027-01-01) | From $0.50 |
| Context window | 131K | 128K | 128K | 1M | 1M | 1M |
| Structured JSON | `json_object` (OpenAI-compat) | `json_object` + JSON Schema | `json_object` + JSON Schema | JSON mode + `response_schema` | JSON mode + `response_schema` | Tool-use schema (not `json_object`) — unverified |
| Free tier | Credits for new users (unverified ongoing) | $5 one-time credits | $5 one-time credits | Yes, rate-limited (not unlimited) | Yes, rate-limited (not unlimited) | Not documented — unverified |
| Speed | Estimated | Estimated | Estimated | Estimated | Estimated | Fastest (comparative, estimated) |
| Code change to switch | None (current) | Must be tested | Must be tested | Minimal (non-native SDK) | Minimal (non-native SDK) | Required (different JSON output path) |

## 5. Estimated Cost per Representative Analysis Request

**Token assumptions (stated explicitly):**
- Input: ~6,000 tokens (approximately 20,000 characters of cleaned page text at the project's `EXTRACT_MAX_TEXT_CHARS` default)
- Output: ~800 tokens (five-section JSON analysis response)

These are estimates; actual token counts vary with page content and model tokenization.

| Model | Input Cost | Output Cost | Total Est. Cost |
|---|---|---|---|
| NVIDIA NIM Llama 3.1 8B | Unverified | Unverified | Unverified |
| OpenAI `gpt-4o-mini` | 6,000 × $0.15/1M ≈ $0.0009 | 800 × $0.60/1M ≈ $0.00048 | ≈ $0.0014 |
| OpenAI `gpt-5-nano` | 6,000 × $0.05/1M ≈ $0.0003 | 800 × $0.40/1M ≈ $0.00032 | ≈ $0.0006 |
| Gemini `gemini-3.5-flash-lite` | 6,000 × $0.30/1M ≈ $0.0018 | 800 × $2.50/1M ≈ $0.0020 | ≈ $0.0038 |
| Gemini `gemini-3.8-flash` | 6,000 × $0.75/1M ≈ $0.0045 | 800 × $3.75/1M ≈ $0.0030 | ≈ $0.0075 |
| Anthropic `claude-haiku-5-5` | 6,000 × $0.10/1M ≈ $0.0006 | 800 × $0.50/1M ≈ $0.0004 | ≈ $0.0010 |

## 6. Recommendation

**Recommended: OpenAI `gpt-5-nano`**

### Rationale

1. **Lowest verified published cost:** At $0.05 input / $0.40 output per 1M tokens,
   `gpt-5-nano` is the cheapest verified option among the six models. A typical
   analysis request costs roughly $0.0006.
2. **Provider compatibility must be tested:** Although the existing
   `LLMClient` accepts `base_url`, `model`, and `api_key` as parameters,
   switching from NVIDIA NIM to OpenAI is **not** guaranteed to work through
   environment variables alone. Authentication header shape, response structure,
   JSON mode support, and error handling must be validated before adoption.
3. **Structured output compatibility:** Supports `response_format:
   {"type": "json_object"}`, which aligns with the existing
   `LLMClient.chat_json()` implementation; compatibility must still be confirmed
   through testing before any provider change.
4. **Adequate quality:** For structured extraction from already-cleaned website
   text (title, description, headings, ~20K-character body), a nano-tier model
   is sufficient. The project's Pydantic schema validation compensates for the
   smaller model's limitations.
5. **Published pricing:** Pricing confirmed directly from OpenAI's public
   documentation.

### Current provider (NVIDIA NIM) — no change required

No change is required to continue using NVIDIA NIM. It remains a valid option,
particularly if the organization already has NVIDIA credits or enterprise
arrangements. The main limitation is the lack of publicly verifiable per-token
pricing, which makes cost planning less predictable.

### Not recommended as primary

- **Google Gemini `gemini-3.8-flash`** and **`gemini-3.5-flash-lite`**: Higher
  published cost than `gpt-5-nano`; require a non-native integration path
  (native Gemini SDK or OpenAI-compatible wrapper).
- **Anthropic `claude-haiku-5-5`**: Structured JSON output path differs from
  the project's existing OpenAI-compatible client, requiring code changes.
  Pricing is "from" rates (unverified exact pricing).
- **OpenAI `gpt-4o-mini`**: Strong model but roughly 2.4× the cost of
  `gpt-5-nano` for this workload without a clear quality advantage for the
  structured extraction task.

**Important:** No benchmarking or accuracy testing was performed. Quality and
speed assessments are estimates based on provider documentation and model
tiering, not measured results.

## 7. Cost Optimization Strategies (Relevant to Current Implementation)

These strategies apply regardless of which provider is selected and help stay
within free-tier rate limits:

### Prompt efficiency
- **Keep system prompts concise:** The system prompt in `app/services/analysis.py`
  (~250 words) already emphasizes the security constraint and JSON-only output.
  A single shared system prompt is used for all five analysis sections.
- **Avoid few-shot examples:** Zero-shot JSON extraction works for this
  workload; examples increase input tokens without clear benefit.

### Input truncation (already implemented)
- **`EXTRACT_MAX_TEXT_CHARS` cap:** The extraction service caps cleaned text at
  `EXTRACT_MAX_TEXT_CHARS` (default 20,000 characters ≈ 5,000–7,000 tokens).
  This is the single most effective cost control for this workload.
- **Pass only relevant sections:** The context builder includes URL, title,
  description, headings, and text. Raw HTML, scripts, styles, and boilerplate
  are already removed by the extraction service.

### Output limits
- **Set `max_tokens`:** `LLMClient.chat()` and `chat_json()` accept
  `max_tokens`. Capping output at ~1,000–2,000 tokens prevents runaway
  generation while still allowing all five analysis sections.
- **Request only needed fields:** Schema hints in `AnalysisService` request
  exactly the fields needed per section.

### Avoid unnecessary repeated calls
- **Consolidate LLM calls:** The current design makes five sequential LLM calls
  (competitors, search queries, SEO, services, audience). Consolidating into a
  single call requesting all five sections in one JSON object reduces per-call
  overhead and rate-limit hits — valuable under tight free-tier RPM limits.
- **Cache repeated analyses:** Cache `AnalysisResponse` keyed by normalized URL.
- **Batch inference where available:** OpenAI Batch API, Gemini Batch API, and
  Groq Batch/Flex processing offer cost reductions for non-interactive bulk
  analysis at the expense of higher latency.

## 8. Sources and Research Date

| Source | URL | Fetched |
|---|---|---|
| OpenAI API pricing | https://platform.openai.com/docs/pricing | 2026-11-22 |
| Gemini Developer API pricing | https://ai.google.dev/pricing | 2026-11-22 |
| Anthropic Claude models overview | https://platform.claude.com/docs/en/models/overview | 2026-11-22 |
| NVIDIA NIM documentation | https://docs.nvidia.com/nim/index.html | 2026-11-22 |
| Project configuration | `app/core/config.py` | Local |
| Project LLM client | `app/services/llm.py` | Local |
| Project analysis service | `app/services/analysis.py` | Local |

---

**Disclaimer:** Pricing and rate-limit figures are from public documentation as
of 2026-11-22 and are subject to change. Free-tier access is rate-limited and
**not unlimited** — do not treat any free API as completely free or without
limits. Response-speed and quality assessments are estimates, not measured
results. No benchmarking, API calls, or credit purchases were performed. API
keys are not included in this document. No application code was modified as
part of this research.

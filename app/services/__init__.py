"""Business logic services.

Website fetching with SSRF protections lives in
:mod:`app.services.fetching`, backed by the IP-pinned transport in
:mod:`app.services._pinned_transport`. HTML content extraction lives in
:mod:`app.services.extraction`. LLM integration lives in
:mod:`app.services.llm`. Website analysis lives in
:mod:`app.services.analysis`.
"""

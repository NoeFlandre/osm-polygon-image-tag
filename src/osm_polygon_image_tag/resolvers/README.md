# Resolvers

This package converts canonical provider references into factual asset
metadata. Provider adapters use only the shared hardened HTTP client: URLs are
scheme-checked, credentials are not forwarded across origins, every DNS answer
must be globally routable, redirects are revalidated, connections use a
validated IP, and response bodies are streamed under strict limits.

Tests inject transports and DNS results; CI never calls live providers.

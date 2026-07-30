# Resolvers

This package converts canonical provider references into factual asset
metadata. Provider adapters use only the shared hardened HTTP client: URLs are
scheme-checked, credentials are not forwarded across origins, every DNS answer
must be globally routable, redirects are revalidated, connections use a
validated IP, and response bodies are streamed under strict limits.

Tests inject transports and DNS results; CI never calls live providers.

Commons files resolve directly and categories expand to at most 500 explicitly
labeled membership rows. Panoramax resolves UUIDs through the metacatalog.
Mapillary and Flickr use optional environment credentials and otherwise return
page-only rows. KartaView resolves its sequence/photo pair; Bing Streetside is
page-only. Provider concurrency and request rates are bounded independently,
and HTTP 429 responses become retryable cache records with cooldown progress.

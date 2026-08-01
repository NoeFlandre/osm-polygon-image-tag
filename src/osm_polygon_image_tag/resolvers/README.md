# Resolvers

This package converts canonical provider references into factual asset
metadata. Provider adapters use only the shared hardened HTTP client: URLs are
scheme-checked, credentials are not forwarded across origins, every DNS answer
must be globally routable, redirects are revalidated, connections use a
validated IP, and response bodies are streamed under strict limits.

Tests inject transports and DNS results; CI never calls live providers.

Commons files resolve directly and categories expand to at most 500 explicitly
labeled membership rows. Commons public reads use a fixed descriptive project
`User-Agent`; they do not require OAuth. Panoramax resolves UUIDs through the
metacatalog.

Mapillary uses optional `MAPILLARY_ACCESS_TOKEN`. Flickr uses optional
`FLICKR_API_KEY`, although Flickr currently restricts new key creation to PRO
accounts. Without those credentials the resolvers return page-only rows.
Credential capability is represented only as `public`, `anonymous`, or
`credentialed`; no credential-derived material is persisted. A credentialed
resume refreshes provider page-only/auth-required cache entries while stable
records remain reusable.

KartaView resolves its sequence/photo pair; Bing Streetside is page-only.
Provider concurrency and request rates are bounded independently, and HTTP 429
responses become retryable cache records with cooldown progress. A URL whose
DNS answers include a non-public address is rejected by the same policy even if
that rejection is wrapped by the HTTP transport; it becomes one cached
`invalid_reference` record with reason `unsafe_url`, emits an
`asset_provider_blocked` progress event, and is never retried. The connection
is never opened to the unsafe address.

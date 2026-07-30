# Credential-Aware Enrichment Design

**Date:** 2026-07-30
**Status:** Approved
**Project:** `NoeFlandre/osm-polygon-image-tag`

## Goal

Let a resumed enrichment run use newly supplied provider credentials to revisit
only results that could improve, without re-reading PBF files, leaking secrets,
or disturbing a currently running process.

## Provider credentials

- Mapillary direct image metadata uses `MAPILLARY_ACCESS_TOKEN`.
- Flickr direct image metadata uses `FLICKR_API_KEY`.
- Hugging Face publication uses the existing `hf auth login` credential or
  `HF_TOKEN`.
- Wikimedia Commons public metadata needs no OAuth token. Requests must carry a
  descriptive user agent identifying this public project.
- Panoramax, KartaView, Bing Streetside, and generic public image resolution
  need no configured credential.

Credentials remain process environment values. Their values, hashes, prefixes,
or lengths must never enter logs, cache rows, manifests, Parquet, statistics,
or publication artifacts.

## Resume contract

The registry exposes only non-secret provider capabilities:
`anonymous`, `credentialed`, or `public`.

An otherwise compatible asset manifest is not reusable when:

- it contains Mapillary rows and page-only results, and Mapillary is now
  credentialed;
- it contains Flickr rows and page-only results, and Flickr is now
  credentialed; or
- it contains `requires_auth` rows for a provider whose request capability can
  now improve.

While rebuilding such a shard, cached Mapillary/Flickr `resolved_page_only`
records are refreshed when the corresponding credential is present.
`requires_auth` records are refreshed when the provider is credentialed. Other
records remain cache hits.

The reuse check scans only the existing asset shard's provider, status, and
expiry columns in one pass. This precisely matches refreshable rows, avoids
rebuilding shards for unrelated page-only providers, and avoids a manifest
migration.

## Wikimedia request identity

Commons API requests send:

```text
User-Agent: osm-polygon-image-tag/0.1.0 (https://github.com/NoeFlandre/osm-polygon-image-tag)
```

The value contains no local or private information. It satisfies Wikimedia's
client-identification requirement and avoids incorrectly asking operators for
OAuth merely to read public metadata.

## Operational behavior

The change applies only to a newly started process. It never signals or mutates
an existing process. On resume, polygon manifests and Parquet remain the source;
PBF extraction stays skipped. Each newly rebuilt asset shard is published by
the existing publish-after-enrichment boundary.

If a supplied token is invalid, the result remains `requires_auth`. Replacing
the token and resuming retries it because authenticated `requires_auth` cache
records are never considered final.

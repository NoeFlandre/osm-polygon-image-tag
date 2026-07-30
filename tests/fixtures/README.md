# `tests/fixtures/`

Stable, committed OSM XML fixtures used by the integration tests. These files
are treated as immutable inputs to the test suite; do not edit them casually
because the tests assert exact behavioural expectations derived from them.

## What lives here

- `image_tag_coverage.osm`: a hand-curated OpenStreetMap XML document that
  exercises every supported image-reference tag form (`image`,
  `wikimedia_commons`, `mapillary`, `panoramax`, indexed `panoramax:<n>`,
  `kartaview`, `flickr`, `bubbleid`) and explicitly excludes
  lookalikes (`panoramax:left`, `panoramax:`, `panoramax:1:foo`,
  `image:license`, `area=no`, etc.).

## How the fixtures are used

Integration tests convert each fixture into a small `.osm.pbf` using
`osmium cat`, then drive the real extraction, transformation, storage,
cataloging, reporting, and publication-planning pipeline against it.

## Rules

- Keep fixtures small and deterministic. They run on every CI invocation.
- Never include real-world data here. Anything sensitive, large, or unstable
  belongs in a temporary scratch directory outside the repo.
- If a fixture needs an update, regenerate the integration tests that depend
  on it to match the new expected identities.

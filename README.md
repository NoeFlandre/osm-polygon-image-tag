# OSM Polygon Image Tag

An independent, reproducible pipeline for a GeoParquet dataset of OpenStreetMap
area features carrying raw image-reference tags.

The project reads PBF input from
`/Volumes/Seagate M3/projects/osm-polygon-wikidata-only/raw` and reserves
`/Volumes/Seagate M3/projects/osm-polygon-image-tag` for generated state. Input
PBFs are immutable. Phase 1 provides only a read-only preflight:

```bash
uv run osm-polygon-image-tag preflight \
  --source-root "/Volumes/Seagate M3/projects/osm-polygon-wikidata-only/raw" \
  --data-root "/Volumes/Seagate M3/projects/osm-polygon-image-tag"
```

No provider APIs are called and no images are downloaded.

## License

Pipeline code is Apache-2.0. OpenStreetMap-derived data remains subject to the
Open Database License; see the generated dataset card once publication support
is implemented.

# upcoming

Event data conversion for SHD — at Sherrerd Hall, and Beyond.

A refactor of [pu-orfe/upcoming](https://github.com/pu-orfe/upcoming), incorporating
lessons from [pubino/mae-upcoming](https://github.com/pubino/mae-upcoming), restructured
so that each data source produces its own `events.json`, and so that sources can be
combined or subtracted to produce derived feeds.

## Goals

- One `events.json` per data source, generated independently.
- Composable feeds: union, intersection, and difference across sources.
- Deterministic, reproducible output suitable for campus system ingest.

## Status

Scaffolding. Nothing implemented yet.

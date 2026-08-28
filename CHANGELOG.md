# CHANGELOG

<!-- version list -->

## Unreleased

- Added asynchronous DuckDuckGo, Bing, and Yahoo search providers.
- Added Tenacity retries for transient search failures.
- Added multi-query fanout and ordered per-query fallback modes.
- Added cross-engine URL deduplication and result provenance metadata.
- Bounded page downloads and pre-ranking fetch candidates to control memory and I/O.
- Moved HTML extraction off the event loop with separate parse concurrency.
- Reused Yahoo sessions across multi-query searches and reduced ranking grouping to linear time.

## v1.0.1 (2026-08-28)

### Bug Fixes

- Handle nested removed content nodes
  ([`888093c`](https://github.com/rafaelpierre/kestrelsearch/commit/888093c65ca1cce18ed3cc01068e2b491619be9a))

## v1.0.0 (2026-08-28)

- Initial Release

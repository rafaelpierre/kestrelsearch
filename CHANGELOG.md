# CHANGELOG

<!-- version list -->

## v1.1.1 (2026-08-28)

### Bug Fixes

- Allow query-only search options
  ([`0181bad`](https://github.com/rafaelpierre/kestrelsearch/commit/0181badd334132faeea27a65d4753a8ffa4280ee))

### Documentation

- Add Mintlify starter site
  ([`7a563eb`](https://github.com/rafaelpierre/kestrelsearch/commit/7a563ebb23ce2e0de7b05d72896264e0f9f8ae44))


## v1.1.0 (2026-08-28)

### Chores

- Add commit-scoped prek hooks
  ([`df982c4`](https://github.com/rafaelpierre/kestrelsearch/commit/df982c43851cbd9baae592ef81b22010440b21b6))

- Format benchmarks and sync lockfile
  ([`806cd4d`](https://github.com/rafaelpierre/kestrelsearch/commit/806cd4ddcd03574439ff7e68810e75340cd9ffb2))

### Features

- Add multi-provider async search
  ([`1abb39a`](https://github.com/rafaelpierre/kestrelsearch/commit/1abb39a464c82da90957771ae0ec8dd08b74d396))


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

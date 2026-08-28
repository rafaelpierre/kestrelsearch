# Codex + Phoenix

Use the repo launcher when benchmarking Kestrel Search against Codex's native
web search:

```bash
./scripts/codex-phoenix --search
```

It preserves your standard Codex login and sends OpenTelemetry traces to the
local Arize Phoenix OTLP gRPC collector at `http://127.0.0.1:4317`. Open
Phoenix at <http://127.0.0.1:6006> to inspect the resulting traces.

Port 6006 is the Phoenix UI/API server in this local installation; port 4317
is its OTLP collector. The launcher has been verified with a real Codex run.

The launcher applies the settings in `phoenix.toml` because Codex loads its
main configuration from `~/.codex/config.toml`, rather than automatically
loading a project-local `.codex/config.toml`.

# Deploy & operator guide for fpt-mcp

Operator-facing setup and deploy instructions. **Not loaded into the
LLM system prompt.** For LLM behavioural rules see `CLAUDE.md` at the
repo root.

This file was extracted from `CLAUDE.md` in chat 51 phase F6a (PR
linked from the same commit) so the per-turn prompt no longer carries
install/reinstall shell recipes the LLM never acts on. The F3b adversarial
suite (`tests/golden/sg_queries.jsonl`) is the load-bearing defense
against API misuse; this document is reference for the human installer.

---

## Prerequisites for local models

```bash
# Install Ollama (macOS)
brew install ollama
brew services start ollama

# Pull the model
ollama pull qwen3.5:9b
# On Mac 24GB (fallback):
ollama pull qwen3.5:4b
```

The Qwen3.5 9B model is aliased as `qwen3.5-mcp` via a custom Ollama
Modelfile (num_ctx 16384, temperature 0.7, top_p 0.8, top_k 20).
See `MODEL_STRATEGY.md` in the ecosystem root for the full
`ollama create` command and rationale.

---

## Reinstallation after code changes

After modifying `claude_worker.py`, `chat_window.py`, or any source file
under `src/fpt_mcp/`:

```bash
cd /path/to/fpt-mcp
pip install -e .
```

Then restart the Qt console or Claude Desktop to pick up the new code.

---

## Deploy workflow — server changes

### `src/fpt_mcp/server.py` only

```bash
git push
```

Claude Desktop / Claude Code respawns the server automatically on the
next tool call (stdio transport — no manual pkill needed).

### Qt console (`qt/` package) changes

```bash
git push
pip install -e .
# Relaunch the Qt console application
```

### Configuration

1. Copy `src/fpt_mcp/config.example.json` to `src/fpt_mcp/config.json`
2. Fill in ShotGrid credentials and Ollama URLs
3. Restart the server

---

## User environment (Abraham)

- Uses ShotGrid for VFX pipeline on local Mac
- glorfindel is a remote Linux server for GPU/Vision3D tasks
- **RULE**: NEVER mix Mac and glorfindel commands in the same code block

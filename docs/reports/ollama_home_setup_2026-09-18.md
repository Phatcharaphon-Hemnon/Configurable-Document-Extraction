# User-level Ollama for scb10x/llama3.2-typhoon2-3b-instruct — 2026-09-18

## Why a second server
- Root `/` (46G) is 100% full; the system Ollama models dir
  (`/usr/share/ollama/.ollama`) cannot take the 2GB download, and there is
  no passwordless sudo to relocate it, clean the journal, or edit the unit.
- The old `LLM_BASE_URL` (tailscale `100.123.255.72:11434`) is unreachable.
- `/home` (238G) has 88G free.

## Setup (running now)
- Server: user-level `ollama serve` on `127.0.0.1:11435`
  (`OLLAMA_MODELS=~/.ollama-models`, `OLLAMA_NUM_CTX=12288`), CPU inference.
- Models on `:11435`: `scb10x/llama3.2-typhoon2-3b-instruct` (2.0GB, base,
  `num_ctx 8192`) + `scb10x/llama3.2-typhoon2-3b-instruct-ctx12k` (variant
  sharing the same blobs, `PARAMETER num_ctx 12288` — required: OCR prompt
  ~2158 tokens + up to 8000 completion ≈ 10.2k > 8192).
- Verified: `/api/show` reports `num_ctx 12288` on the variant; JSON chat
  smoke test returned `{"ok": true}`.
- `api/.env`: `LLM_PROVIDER=ollama-local`,
  `LLM_MODEL=scb10x/llama3.2-typhoon2-3b-instruct-ctx12k`,
  `LLM_BASE_URL=http://127.0.0.1:11435/v1` (Settings resolution verified).

## After reboot / logout
`Linger=no`, so restart it manually (no sudo needed):
`bash ~/.ollama-models/start-ollama-home.sh` — then restart the API from
`api/` so `.env` resolves.

## Leftovers / warnings
- System `:11434` still holds `qwen2.5:3b/1.5b/1.5b-ctx8k` (~4GB on the full
  root disk). Removing them needs sudo (`sudo ollama rm ...`) — not done.
- RAM is 7.5GB with ~2.7GB free; 3B-Q4 + 12k ctx on CPU is tight. If loads
  OOM, lower `EXTRACTION_MAX_TOKENS` toward 6000 (prompt + output must stay
  under 12288).

# MODEL_STRATEGY.md — MCP Ecosystem

**Created**: 2026-04-06 (Chat 13)
**Last updated**: 2026-04-13 (Bucket D — Qwen context fix: num_ctx bump + SYSTEM_PROMPT_QWEN variant)

---

## 1 — Modelo local unificado: Qwen3.5 9B

**Modelo elegido**: `qwen3.5:9b` (Q4_K_M, 6.6 GB)

### Por qué Qwen3.5 9B

| Criterio | Qwen3.5 9B | GLM-4.7 Flash | Notas |
|---|---|---|---|
| **Tool calling** | 4B = 97.5% (1º de 13 modelos, eval J.D. Hodges). 9B iguala o supera | 95% (2º) | Benchmark independiente, 13 modelos evaluados |
| **IFBench** | 91.5% | — | Instruction following |
| **Context window** | 262K | 128K | 2× más contexto |
| **Memoria Q4_K_M** | 6.6 GB | 15-21 GB | 3× menos VRAM/RAM |
| **Multimodal** | Nativo (vision) | No | Relevante para maya-mcp viewport_capture |
| **Licencia** | Apache 2.0 | MIT | Ambas permisivas |
| **Thinking** | Desactivado por defecto (serie Small) | N/A | No añade latencia |

### Riesgos conocidos

- Bugs en Ollama corregidos en v0.17.6. Reportes esporádicos en versiones anteriores.
- Modelo reciente — menos battle-tested que GLM-4.7 en producción.

### Dónde GLM-4.7 Flash sigue ganando

- **Consistencia en coding complejo**: ELO 1572 en APEX Testing — más predecible en generación de código largo.
- **Recuperación de errores**: mejor handling de tool call failures y reintentos.
- **Madurez Ollama**: más tiempo en producción, menos edge cases.

**Decisión**: Qwen3.5 9B es el modelo principal. GLM-4.7 Flash se mantiene como alternativa pro para coding complejo.

---

## 2 — Configuración por máquina

### Mac M4 Pro 48GB (trabajo)

| Parámetro | Valor |
|---|---|
| **Modelo principal** | `qwen3.5:9b` (6.6 GB) |
| **Apps concurrentes** | Flame (20-30 GB) + Maya |
| **Margen libre** | 8-16 GB |
| **Ollama URL** | `http://localhost:11434` (offline) o `http://glorfindel:11434` (LAN) |

```bash
# ~/.zshrc
alias claude-local='ANTHROPIC_AUTH_TOKEN=ollama ANTHROPIC_BASE_URL=http://glorfindel:11434 ANTHROPIC_API_KEY="" claude --model qwen3.5:9b'
alias claude-offline='ANTHROPIC_AUTH_TOKEN=ollama ANTHROPIC_BASE_URL=http://localhost:11434 ANTHROPIC_API_KEY="" claude --model qwen3.5:9b'
```

### Mac M5 Pro 24GB (personal)

| Parámetro | Valor |
|---|---|
| **Modelo principal** | `qwen3.5:9b` (6.6 GB) |
| **Fallback** | `qwen3.5:4b` si hay swap con Maya |
| **Ollama URL** | `http://localhost:11434` (offline) o `http://glorfindel:11434` (LAN) |

```bash
# ~/.zshrc
alias claude-local='ANTHROPIC_AUTH_TOKEN=ollama ANTHROPIC_BASE_URL=http://glorfindel:11434 ANTHROPIC_API_KEY="" claude --model qwen3.5:9b'
alias claude-offline='ANTHROPIC_AUTH_TOKEN=ollama ANTHROPIC_BASE_URL=http://localhost:11434 ANTHROPIC_API_KEY="" claude --model qwen3.5:9b'
```

### glorfindel (RTX 3090 24GB VRAM, 64GB RAM)

| Parámetro | Valor |
|---|---|
| **Modelo principal** | `qwen3.5:9b` (6.6 GB) |
| **Modelo pro** | `glm-4.7-flash` (15-21 GB) para coding complejo |
| **OLLAMA_KEEP_ALIVE** | `30s` |
| **Servicios que compiten por GPU** | vision3d, ComfyUI, faceswapper, v2v — todos secuenciales |

### Configuración unificada (todas las máquinas)

```bash
OLLAMA_KEEP_ALIVE=30s
OLLAMA_NUM_PARALLEL=1
```

---

## 2b — Ollama Setup

### Creating the qwen3.5-mcp model

The ecosystem uses a custom Ollama model called `qwen3.5-mcp` based on `qwen3.5:9b` with tuned parameters for MCP tool calling.

**Modelfile** (save as `Modelfile.qwen35mcp`):

```
FROM qwen3.5:9b
PARAMETER num_ctx 16384
PARAMETER temperature 0.7
PARAMETER top_p 0.8
PARAMETER top_k 20
```

**Create the model:**

```bash
ollama pull qwen3.5:9b
ollama create qwen3.5-mcp -f Modelfile.qwen35mcp
```

### num_ctx 16384 (bumped from 8192 in Bucket D)

The previous Modelfile used `num_ctx 8192`. Combined with the fpt-mcp
SYSTEM_PROMPT (~2,300 tokens) plus Claude Code CLI tool descriptions
(~1,500-1,700 tokens for the ~30 tools across fpt-mcp + maya-mcp), the
static overhead is ~3,800-4,000 tokens — leaving roughly 4,000 tokens
of headroom for the user message, conversation history, and tool
outputs. On a multi-turn 3D-creation flow that polls Vision3D
repeatedly and downloads files, that headroom would run out and Qwen
would silently truncate or mis-order tool calls.

Bumping `num_ctx` to 16384 doubles the headroom. The VRAM cost depends
on the KV-cache dtype:

- **With `OLLAMA_KV_CACHE_TYPE=q8_0`** (the recommended Mac setting in
  section 2b below) the bump adds ~1-2 GB of VRAM for the doubled KV
  cache.
- **With the FP16 default** (no `OLLAMA_KV_CACHE_TYPE` set, e.g. on a
  default glorfindel setup) the bump adds closer to ~3-3.5 GB. Math:
  KV cache ≈ 2 × layers × seq_len × hidden × bytes_per_elem; for
  Qwen3.5 9B (28 layers, hidden 3584) at FP16, doubling seq_len from
  8192 to 16384 adds ~3.3 GB.

On glorfindel (RTX 3090 24GB) either dtype is fine — `OLLAMA_KEEP_ALIVE=30s`
unloads the model between calls so other GPU services (vision3d, ComfyUI)
recover the VRAM. On Mac M5 Pro 24GB, run with `OLLAMA_KV_CACHE_TYPE=q8_0`
to keep the bump cheap. Qwen3.5 9B Q4_K_M is 6.6 GB baseline regardless.

This bump complements the SYSTEM_PROMPT_QWEN variant in
`src/fpt_mcp/qt/claude_worker.py`, which strips the system prompt to
~1,372 tokens for the Ollama backends (40% reduction), giving Qwen
even more conversation headroom on top of the bigger context window.

**Apply the bump on every machine that runs Qwen for MCP**:

```bash
# On glorfindel (SSH first):
ssh glorfindel
cat > /tmp/Modelfile.qwen35mcp <<'EOF'
FROM qwen3.5:9b
PARAMETER num_ctx 16384
PARAMETER temperature 0.7
PARAMETER top_p 0.8
PARAMETER top_k 20
EOF
ollama create qwen3.5-mcp -f /tmp/Modelfile.qwen35mcp
exit

# On Mac M5 Pro (24GB) and Mac M4 Pro (48GB):
cat > /tmp/Modelfile.qwen35mcp <<'EOF'
FROM qwen3.5:9b
PARAMETER num_ctx 16384
PARAMETER temperature 0.7
PARAMETER top_p 0.8
PARAMETER top_k 20
EOF
ollama create qwen3.5-mcp -f /tmp/Modelfile.qwen35mcp
```

### Qwen output is non-deterministic by design

The Modelfile sets `temperature 0.7`, `top_p 0.8`, `top_k 20`, and
does NOT set a seed. Repeated identical prompts to the Qwen backend
will produce different (but semantically similar) tool calls.

This is acceptable for the interactive workflows the Qt console runs
(the user sees the result and can re-prompt), but it means the Ollama
backend is NOT suitable for batch automation that requires
reproducible outputs. For reproducible workflows, use the Anthropic
cloud backend with explicit `temperature=0` (configured in the
Anthropic SDK at request time, not in the Modelfile).

If you need to reduce Qwen variance for testing, append
`PARAMETER seed 42` to the Modelfile and rebuild. Note that GPU
non-determinism (cuBLAS, MPS) means even seeded inference is not
bit-exact across runs — only "much more consistent".

### Thinking mode: `think: false` is mandatory

The base `qwen3.5` model activates thinking (chain-of-thought) by default when using `ollama run`. However, the MCP ecosystem uses the Ollama **Anthropic Messages API** layer, not `ollama run`.

**Important:** `"think": false` must be set in every API request. The `ollama run` CLI does not support this parameter — it only works through the API. All three MCP servers (flame-mcp, maya-mcp, fpt-mcp) already include `"think": false` in their Ollama API calls. No manual configuration is needed if using the servers as intended.

If you are making direct API calls to Ollama outside the MCP servers, include it explicitly:

```json
{
  "model": "qwen3.5-mcp",
  "messages": [...],
  "think": false
}
```

### KEEP_ALIVE configuration (recommended)

Controls how long Ollama keeps the model loaded in memory after the last request. With `OLLAMA_KEEP_ALIVE=30s`, the model unloads 30 seconds after the last call, freeing VRAM for other GPU services (vision3d, ComfyUI, etc.).

**Linux (systemd):**

```bash
sudo systemctl edit ollama --force
# Add under [Service]:
#   Environment="OLLAMA_KEEP_ALIVE=30s"
sudo systemctl restart ollama
```

**macOS (Homebrew):**

```bash
/usr/libexec/PlistBuddy -c "Add :EnvironmentVariables:OLLAMA_KEEP_ALIVE string 30s" \
  ~/Library/LaunchAgents/homebrew.mxcl.ollama.plist
brew services restart ollama
```

**Optional recommended settings for macOS (improves inference speed):**

```bash
/usr/libexec/PlistBuddy -c "Add :EnvironmentVariables:OLLAMA_FLASH_ATTENTION string 1" \
  ~/Library/LaunchAgents/homebrew.mxcl.ollama.plist
/usr/libexec/PlistBuddy -c "Add :EnvironmentVariables:OLLAMA_KV_CACHE_TYPE string q8_0" \
  ~/Library/LaunchAgents/homebrew.mxcl.ollama.plist
brew services restart ollama
```

---

## 3 — Selección de modelo UX

| Interfaz | Cómo seleccionar modelo |
|---|---|
| **Claude Code CLI** | `ollama launch claude --model qwen3.5:9b` |
| **flame-mcp** | Widget desplegable en el panel Flame (AVAILABLE_MODELS) |
| **maya-mcp** | Qt console — selector de modelo en la barra superior |
| **fpt-mcp** | Qt console — selector de modelo en la barra superior |

---

## 4 — Arquitectura híbrida

```
┌─────────────────────────────────────────────────────────┐
│              Claude Opus / Sonnet (cloud)                │
│              Orquestación, razonamiento, UX              │
├─────────────────────────────────────────────────────────┤
│                         │                               │
│              ┌──────────▼───────────┐                   │
│              │   Qwen3.5 9B (local)  │                  │
│              │   Tool calls MCP      │                  │
│              │   6.6 GB, 262K ctx    │                  │
│              └──────────┬───────────┘                   │
│                         │                               │
│              ┌──────────▼───────────┐                   │
│              │  GLM-4.7 Flash (pro)  │                  │
│              │  Coding complejo      │                  │
│              │  15-21 GB, 128K ctx   │                  │
│              └──────────────────────┘                   │
└─────────────────────────────────────────────────────────┘
```

- **Opus/Sonnet** orquesta workflows complejos, razonamiento de alto nivel, y UX conversacional.
- **Qwen3.5 9B** ejecuta tool calls locales (sg_find, maya_create_primitive, search_flame_docs) — rápido, ligero, multimodal.
- **GLM-4.7 Flash** como alternativa pro: coding complejo, generación de código largo, recuperación de errores.

---

## 5 — Gestión VRAM glorfindel

### KEEP_ALIVE strategy

Con `OLLAMA_KEEP_ALIVE=30s`, Ollama descarga el modelo 30 segundos después del último request. Esto permite que los servicios GPU (vision3d, ComfyUI, faceswapper, v2v) recuperen la VRAM entre sesiones LLM.

### Tiempos de carga estimados

| Modelo | Cold start | Notas |
|---|---|---|
| `qwen3.5:9b` | ~2-3s | 6.6 GB → VRAM rápido |
| `glm-4.7-flash` | ~5-10s | 15-21 GB → más lento, depende de I/O |

### Servicios GPU en glorfindel

Todos estos servicios son secuenciales (no concurrentes) y deben esperar a que el modelo LLM se descargue:

- **vision3d** (Hunyuan3D-2) — generación 3D desde imagen/texto
- **ComfyUI** — pipelines de generación de imagen
- **faceswapper** — intercambio de rostros en video
- **v2v** — video-to-video stylization

---

## 6 — Modelos a instalar/borrar

### glorfindel

| Acción | Modelo | Comando |
|---|---|---|
| **INSTALAR** | `qwen3.5:9b` | `ssh glorfindel "ollama pull qwen3.5:9b"` |
| **MANTENER** | `glm-4.7-flash` | Ya instalado — alternativa pro |
| **BORRAR** | `glm-4.7-flash-8k` | `ssh glorfindel "ollama rm glm-4.7-flash-8k"` |
| **BORRAR** | `qwen3-flame` | `ssh glorfindel "ollama rm qwen3-flame"` (si existe) |
| **BORRAR** | `qwen3-coder` | `ssh glorfindel "ollama rm qwen3-coder"` (si existe) |

### Mac M4 Pro 48GB

| Acción | Modelo | Comando |
|---|---|---|
| **INSTALAR** | `qwen3.5:9b` | `ollama pull qwen3.5:9b` |

### Mac M5 Pro 24GB

| Acción | Modelo | Comando |
|---|---|---|
| **INSTALAR** | `qwen3.5:9b` | `ollama pull qwen3.5:9b` |
| **INSTALAR** | `qwen3.5:4b` | `ollama pull qwen3.5:4b` |

---

## 7 — Procedimiento de actualización

### Cuándo actualizar

Señales de que hay que evaluar un modelo nuevo:

1. Un modelo nuevo supera a Qwen3.5 9B en **τ²-Bench** (tool calling benchmark).
2. Un modelo nuevo supera en **SWE-bench Verified**.
3. **Ollama** cambia su recomendación oficial para Claude Code.
4. Un modelo nuevo ofrece rendimiento similar con menor footprint de memoria.

### Cómo actualizar

1. **Evaluar** en glorfindel: ejecutar 5-10 tool calls típicas del ecosistema MCP (sg_find, maya_create_primitive, search_flame_docs) y verificar que las completa sin alucinaciones.
2. **Instalar** en glorfindel: `ollama pull <nuevo-modelo>`
3. **Actualizar** AVAILABLE_MODELS en cada repo (flame-mcp, maya-mcp, fpt-mcp).
4. **Borrar** el modelo viejo: `ollama rm <viejo-modelo>`
5. **Actualizar** este documento (MODEL_STRATEGY.md) con la nueva recomendación.

---

## 8 — Modelo retirado: GLM-4.7 Flash

**GLM-4.7 Flash** ha sido retirado como modelo principal del ecosistema MCP.

**Razón**: Qwen3.5 9B supera en tool calling (97.5% vs 95%), contexto (262K vs 128K), eficiencia de memoria (6.6 GB vs 15-21 GB), y añade vision nativa.

**Se mantiene como alternativa pro** en glorfindel para:
- Coding complejo donde la consistencia es crítica (ELO 1572 APEX Testing).
- Recuperación de errores en tool call chains largos.
- Casos donde Qwen3.5 9B falla o produce resultados inconsistentes.

**No se instala en Macs** — el ahorro de memoria de Qwen3.5 9B hace innecesario tener GLM como fallback local.

---

*Documento de referencia para el ecosistema MCP. Mantener actualizado cuando cambie el hardware o salgan modelos nuevos.*

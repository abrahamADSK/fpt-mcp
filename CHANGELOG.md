# Changelog

All notable changes to **fpt-mcp** are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- Updated CLAUDE.md section 6 permissions to match actual maya-mcp dispatcher tool names
- Updated README to reflect dispatcher pattern and install.sh as primary installer

### Added
- `install.sh --doctor` subcommand for environment health checks
- Bucket E structural tests for tool labels, Pydantic models, and trust gates
- This CHANGELOG.md

## [1.4.2] - 2026-04-14

### Fixed
- Hardened reasoning pipeline on Qt console Claude subprocess
- Documented tank auth and bundle_cache prerequisites for launcher

## [1.4.1] - 2026-04-14

### Fixed
- Launcher builds version-specific tank command name (`<app>_<version>` preference)

## [1.4.0] - 2026-04-14

### Added
- `fpt_launch_app` MCP tool: launch a DCC scoped to a ShotGrid entity with OS-first
  discovery and Toolkit tank routing
- `software_resolver` module for OS-first application discovery

### Fixed
- `load_dotenv(override=True)` to beat stale parent env in client

## [1.3.0] - 2026-04-14

### Added
- Bucket E structural regression suite (20 tests) for SYSTEM_PROMPT invariants
- Placeholder `.env` credential detection in 3 defense layers
- Backend-specific SYSTEM_PROMPT variant for Qwen (40% smaller, same workflow)
- RAG soft-warning, missing telemetry, and filter list validator
- Safety module catches hallucinated status codes and bare integer entity refs
- RAG cache-hit telemetry and HyDE query expansion sanitization
- Trust gate runtime model for `learn_pattern` and `tk_publish` guard
- `Asset.description` as text-to-3D fallback with user-awareness rules
- Pre-approved MCP tools in `install.sh`

### Changed
- Extracted SYSTEM_PROMPT variants to standalone text files
- Dispatch pattern reduces visible tools from 18 to 13 for small LLMs
- Pinned `sentence-transformers` and `pydantic` to safe major ranges
- Relative sibling paths in `.mcp.json` for cross-machine portability

### Fixed
- Qt console uses maya-mcp dispatcher API and handles `vision3d_url_required` correctly
- Removed fabricated glorfindel hostname from Vision3D URL prompt
- Deferred Vision3D URL question with adaptive method bullets
- Disambiguated method options and compact markdown rendering
- QA review follow-ups across Buckets B, C, D (VRAM math, defensive backend match)
- Deterministic RRF tiebreaker via stable secondary sort
- Scrubbed Anthropic env vars when switching to Claude backend
- Removed deprecated dead code and fixed `.gitignore` drift
- Added `cwd` to Popen and updated `.mcp.json` paths
- Added `ulimit` warning in conftest.py for ChromaDB file descriptor exhaustion
- Generic agnostic "thinking" verbs in Qt console (no real process leakage)
- Action-aware progress labels for maya-mcp dispatcher tools

## [1.2.0] - 2026-04-07

### Added
- MODEL_STRATEGY.md with Ollama setup, Modelfile, and KEEP_ALIVE config
- Ollama as optional prerequisite in READMEs and `install.sh`
- Dispatch pattern: reduce visible MCP tools from 18 to 13 for small LLMs

## [1.1.0] - 2026-04-07

### Added
- Multi-backend support: Ollama and Anthropic model selection, Qt menu, `config.example.json`
- `app_store` and `git` descriptor resolution in `tk_config.py`
- Comprehensive test suite (137 tests: sg_ops, toolkit_paths, rag_search, safety, tk_publish)
- `install.sh` automated installer with venv, deps, RAG index, MCP registration
- `.mcp.json` configuration for Claude Desktop and Claude Code
- Ecosystem section with cross-repo links in README

### Changed
- Expanded documentation: Features, Usage, Self-Learning, Token Tracking, Requirements,
  Project Structure, Troubleshooting sections

### Fixed
- Replaced deprecated `get_event_loop` with `asyncio.run` in tests (Python 3.12+)
- Removed `.DS_Store` from tracking, added to `.gitignore`
- Fixed chromadb version range and `asyncio.run()` in `test_tk_publish`

## [1.0.0] - 2026-03-31

### Changed
- Translated all Spanish content to English (i18n)
- Removed pipeline-specific templates; generic publish path resolution
- Expanded RAG corpus to 311 chunks with complete SG_API, TK_API, REST_API reference

### Added
- 6 direct SG API tools: `sg_find`, `sg_create`, `sg_update`, `sg_schema`,
  `sg_upload`, `sg_download`

## [0.3.0] - 2026-03-30

### Added
- RAG anti-hallucination engine with ChromaDB semantic + BM25 lexical + HyDE + RRF fusion
- Safety module with 12+ regex patterns detecting dangerous operations
- Toolkit integration (`tk_resolve_path`, `tk_publish`) with dynamic PipelineConfiguration
- Granular Vision3D workflow with poll labels in Qt console
- Detailed quality presets in 3D creation prompt (low/medium/high/ultra)
- Conversation history for multi-turn context in Qt console
- Asset-creation workflow skill with reference discovery
- Text-to-3D option in system prompt
- Real-time progress log in console thinking bubble

### Fixed
- `tk_resolve_path`: `next_version_number` expects `Path`, not 4 strings
- Thumbnail URL handling in `sg_download_attachment`
- Full preset parameters shown in console

## [0.2.0] - 2026-03-27

### Added
- Native Qt console with `fpt-mcp://` protocol handler
- HTTP transport with stateless JSON mode, proxy, CORS
- Claude Code CLI integration via AMI console
- `launchd` service plists for macOS

### Fixed
- Working directory in setup script for `.env` loading
- MCP HTTP transport: stateless JSON mode, proxy, CORS

## [0.1.0] - 2026-03-27

### Added
- Initial MCP server for Autodesk Flow Production Tracking (ShotGrid) with 8 tools
- stdio transport for Claude Desktop and Claude Code

[Unreleased]: https://github.com/abrahamADSK/fpt-mcp/compare/v1.4.2...HEAD
[1.4.2]: https://github.com/abrahamADSK/fpt-mcp/compare/v1.4.1...v1.4.2
[1.4.1]: https://github.com/abrahamADSK/fpt-mcp/compare/v1.4.0...v1.4.1
[1.4.0]: https://github.com/abrahamADSK/fpt-mcp/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/abrahamADSK/fpt-mcp/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/abrahamADSK/fpt-mcp/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/abrahamADSK/fpt-mcp/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/abrahamADSK/fpt-mcp/compare/v0.3.0...v1.0.0
[0.3.0]: https://github.com/abrahamADSK/fpt-mcp/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/abrahamADSK/fpt-mcp/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/abrahamADSK/fpt-mcp/releases/tag/v0.1.0

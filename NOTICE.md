# Third-Party Notices

## fpt-mcp

Copyright (c) 2026 Abraham Borbujo
Licensed under the MIT License — see [LICENSE](LICENSE).

---

## Autodesk ShotGrid / Flow Production Tracking

fpt-mcp communicates with **Autodesk Flow Production Tracking** (formerly ShotGrid)
via its REST API and Python API (`shotgun_api3`). Flow Production Tracking is
proprietary software developed and owned by **Autodesk, Inc.**

This project does not include, redistribute, or modify any Autodesk server software.
It interacts with ShotGrid solely through its documented, user-accessible APIs.

- Flow Production Tracking: <https://www.autodesk.com/products/flow-production-tracking>
- ShotGrid Python API: <https://github.com/shotgunsoftware/python-api> (BSD-3-Clause)
- ShotGrid Developer Docs: <https://developers.shotgridsoftware.com/>

---

## Autodesk Toolkit (sgtk)

fpt-mcp reads Toolkit configuration files (`templates.yml`, `roots.yml`) for path resolution.
Toolkit (sgtk) is open-source software by **Autodesk, Inc.** licensed under the
**Shotgun Pipeline Toolkit Source Code License** (see sgtk repo for details).

- tk-core: <https://github.com/shotgunsoftware/tk-core>
- tk-config-default2: <https://github.com/shotgunsoftware/tk-config-default2>

Note: fpt-mcp does NOT import or bundle sgtk. It reads YAML config files directly.

---

## Python Dependencies

| Package | License |
|---|---|
| `mcp` | MIT |
| `shotgun-api3` | BSD-3-Clause |
| `pydantic` | MIT |
| `python-dotenv` | BSD-3-Clause |
| `httpx` | BSD-3-Clause |
| `pyyaml` | MIT |
| `PySide6` | LGPL-3.0 / Commercial (Qt) |
| `chromadb` | Apache-2.0 |
| `sentence-transformers` | Apache-2.0 |
| `rank-bm25` | Apache-2.0 |
| `hatchling` | MIT |

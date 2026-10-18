# qgis-mcp

[![Install via Agensi](https://img.shields.io/badge/Agensi-Get%20Agent%20Skill-blue?style=for-the-badge&logo=anthropic)](https://agensi.io/skills/qgis-autonomous-spatial-engine)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

**A headless MCP server for QGIS 4.x: load layers, run Processing algorithms, style and export maps from Claude, Cursor, and Copilot without opening QGIS Desktop.**

> 🚀 **Official Agent Skill Available:** To operate this MCP server autonomously with complete error-recovery workflows, CRS auto-validation, and cartographic rules, install the official [QGIS Autonomous Spatial Engine Skill on Agensi](https://agensi.io/skills/qgis-autonomous-spatial-engine).

Built on PyQGIS standalone and the [Model Context Protocol](https://modelcontextprotocol.io). It is in daily use with
Claude Code against **QGIS 4.2.1** for spatial overlay work in environmental and mining projects (project area vs.
restriction layers, buffers, zonal statistics, map figures).

> 📄 Notes in Spanish: [README.es.md](README.es.md)

## Why headless, not a Desktop plugin

A headless server doesn't depend on an open QGIS Desktop session with a plugin running, so it's sturdier for long-lived
agent use. It covers Processing, field calculation, vector overlay and static image export, which is nearly the whole
overlay use case. A future "desktop" mode (plugin + socket) could reuse the exact same tool interface; the
implementation underneath would change, but what Claude sees would not.

## Tools

| Module | Tools |
|---|---|
| `tools_project.py` | `qgis_ping`, `load_project`, `save_project`, `list_layers`, `get_layer_info`, `add_vector_layer`, `add_raster_layer`, `remove_layer`, `zoom_to_layer`, `set_extent`, `set_layer_style` |
| `tools_attributes.py` | `calculate_field`, `get_attribute_table` |
| `tools_processing.py` | `list_algorithms`, `get_algorithm_help`, `run_processing_algorithm` |
| `tools_visual.py` | `export_map_image`, `create_print_layout`, `export_layout_pdf` |
| `tools_escape.py` | `execute_pyqgis` (escape hatch) |

## Design decisions

- **Every write tool goes through `resolve_layer()`.** It accepts a real `layer_id` or a name, and fails explicitly on
  ambiguity or a missing layer; it never guesses.
- **`run_processing_algorithm` auto-resolves** any string parameter that matches a loaded layer id or name, so the
  agent only passes ids it already saw in `list_layers()`.
- **The `QgsProcessingFeedback` log is captured and returned**, so failures can be diagnosed instead of retried blindly.
- **Server instructions encode verification rules for the agent:** confirm CRS before any geometric operation between
  two layers, and verify the output layer after a Processing run, because an algorithm can finish without error and
  still produce 0 features.
- **QGIS 4 / Qt6 migration handled:** PyQt6 requires scoped enums (`QgsMapLayer.LayerType.VectorLayer`,
  `QMetaType.Type.Int`, `QgsLayoutExporter.ExportResult.Success`…) where QGIS 3.x accepted flat ones.
- **`execute_pyqgis` is deliberately unsandboxed** and documented as such, rather than wrapped in a layer of fake
  "security". The server is meant for local stdio use only, with no network surface.
- **Nothing overwrites files by default.** `save_project` without a path reuses the loaded path and never creates
  silent copies.

## Setup

PyQGIS is **not** installable with pip; use a Python interpreter that already ships with it.

```bash
# Option A: the Python bundled with QGIS (Windows)
"C:\Program Files\QGIS 4.2.1\bin\python-qgis.bat" -m pip install -e .

# Option B: conda-forge
conda create -n qgis-mcp -c conda-forge python=3.11 qgis
conda activate qgis-mcp
pip install -e .
```

Smoke test:

```bash
python -c "from qgis_mcp.qgis_backend import backend; backend.start(); print('OK', backend.qgs_app.processingRegistry().providers())"
```

Register with Claude Code:

```json
{
  "mcpServers": {
    "qgis": {
      "command": "cmd.exe",
      "args": ["/c", "C:\\Program Files\\QGIS 4.2.1\\bin\\python-qgis.bat", "-m", "qgis_mcp.server"]
    }
  }
}
```

## Roadmap

- Automated test suite (today it's validated through live use, not a test bench).
- Optional desktop mode (plugin + socket) reusing the same tool schema.
- Authentication and permissions if it ever stops being local-only.

---

## 🏛️ Autonomous Engineering Suite (Vectis Operations)

`qgis-mcp` is part of an integrated, production-grade autonomous engineering ecosystem designed for AI coding agents (Claude Code, Cursor, Copilot). 

The companion engines are available exclusively on **Agensi**:

| Engine | Description | Marketplace Link |
| :--- | :--- | :---: |
| 🗺️ **QGIS Spatial Engine** | Headless PyQGIS Processing, spatial overlay & automated cartography | [Get on Agensi ($34)](https://agensi.io/skills/qgis-autonomous-spatial-engine) |
| 🌊 **USACE HEC Hydro Suite** | Headless HEC-RAS (1D/2D unsteady flood) & HEC-HMS basin modeling | [Get on Agensi ($89)](https://agensi.io/skills/usace-hec-hydraulic-hydrologic-engine) |
| 🚁 **WebODM Drone Engine** | Photogrammetry, dense point clouds, DSM/DTM & volume calculations | [Get on Agensi ($59)](https://agensi.io/skills/webodm-drone-photogrammetry-engine) |
| 📐 **FreeCAD 3D CAD Engine** | Headless parametric 3D CAD, TechDraw drawings, SheetMetal & FEM | [Get on Agensi ($39)](https://agensi.io/skills/freecad-autonomous-engineering-engine) |

Explore the complete verified creator catalogue: **[Vectis Operations on Agensi](https://agensi.io/creators/daniel-montero)**.

---

## License

Copyright (c) 2026 Oscar Daniel Montero Miranda - Vectis Operations. All Rights Reserved.  
Commercial deployment and verified autonomous execution rules are licensed via [Agensi](https://agensi.io/skills/qgis-autonomous-spatial-engine). See [LICENSE](LICENSE) for full details.

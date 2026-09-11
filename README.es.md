# qgis-mcp

Servidor MCP headless para QGIS, construido sobre PyQGIS standalone
(sin abrir QGIS Desktop). Implementa el set de tools y las reglas de
operación descritas en `00-mcp-tools-y-modo-agente.md` y usa los ids de
Processing catalogados en `18-ids-algoritmos-processing.md`.

**Este código está escrito para QGIS 4.x (en uso diario contra
4.2.1), no 3.x.** QGIS 4.0 migró de Qt5 a Qt6, y PyQt6 exige enums "scoped"
donde QGIS 3.x/PyQt5 aceptaba la forma plana. Ejemplos ya corregidos en este
código: `QgsMapLayer.LayerType.VectorLayer` (no `QgsMapLayer.VectorLayer`),
`QMetaType.Type.Int` (no `QVariant.Int` — `QVariant.Type` ya no existe como
antes), `QImage.Format.Format_ARGB32`, `QgsLayoutExporter.ExportResult.Success`,
`QgsGraduatedSymbolRenderer.Mode.Jenks`. Si en tu 4.2.1 alguna llamada de
Processing devuelve `AttributeError` por un enum que no encontré, es
justamente este patrón — el proyecto de QGIS incluso publica un script
(`pyqt5_to_pyqt6.py`, en el repo de QGIS) que hace esta conversión
automáticamente; puedes correrlo sobre `qgis_mcp/` como chequeo cruzado.

## Por qué headless (y no un plugin dentro de QGIS Desktop)

Un servidor MCP headless no depende de que haya una sesión de QGIS Desktop
abierta con un plugin activo — es más robusto para correr indefinidamente.
Cubre Processing, cálculo de campos, overlay vectorial, y exportar imágenes
estáticas, que es prácticamente todo el caso de uso de superposición de
áreas mineras/ambientales. Si más adelante quieres reflejar cambios en tu
propia pantalla de QGIS Desktop, se puede añadir un modo "desktop" (plugin +
socket) que reuse exactamente el mismo esquema de tools — la implementación
de cada tool cambiaría por debajo, pero la interfaz que ve Claude no.

## 1. Requisito: un Python con PyQGIS

PyQGIS (el paquete `qgis`) **no se instala con pip**. Necesitas usar el
intérprete de Python que ya trae PyQGIS, de una de estas formas:

### Opción A — el Python que viene con tu instalación de QGIS

- **Linux** (QGIS instalado vía apt/paquete del sistema): normalmente es tu
  `/usr/bin/python3` del sistema, si QGIS se instaló contra ese Python.
- **Windows** (OSGeo4W / instalador oficial de QGIS): usa
  `C:\Program Files\QGIS <version>\bin\python-qgis.bat` o el
  `python3.exe` dentro de la carpeta `apps\Python3x` de esa instalación.
- **macOS** (instalador oficial): el Python está dentro de
  `/Applications/QGIS.app/Contents/MacOS/bin/python3`.

### Opción B — conda-forge (recomendado si quieres un entorno aislado)

```bash
conda create -n qgis-mcp -c conda-forge python=3.11 qgis
conda activate qgis-mcp
pip install -r requirements.txt
```

Esta es la opción más simple de reproducir igual en Linux/macOS/Windows.

## 2. Variables de entorno

Con el Python correcto activo, exporta (ajusta las rutas a tu sistema):

```bash
# Linux (ejemplo con QGIS del sistema)
export QGIS_PREFIX_PATH=/usr

# macOS (ejemplo con instalador oficial)
export QGIS_PREFIX_PATH=/Applications/QGIS.app/Contents/MacOS

# Windows (OSGeo4W, ejemplo — ajusta la versión)
set QGIS_PREFIX_PATH=C:\Program Files\QGIS 3.34\apps\qgis
```

Con conda-forge normalmente **no** necesitas fijar `QGIS_PREFIX_PATH` a mano;
el paquete `qgis` de conda-forge ya deja todo resuelto dentro del entorno.

## 3. Instalar el paquete del servidor

Con el Python correcto activo (ver paso 1/2), instala el proyecto en modo
editable — esto es lo que hace que `qgis_mcp` sea importable sin depender de
`sys.path`/`PYTHONPATH` a mano (frágil, y algunos lanzadores `.bat` de QGIS
limpian `PYTHONPATH` internamente antes de reconstruirlo, así que exportarlo
desde afuera no siempre sobrevive):

```bash
cd qgis-mcp
pip install -e .
```

## 4. Probar que arranca

```bash
python -c "from qgis_mcp.qgis_backend import backend; backend.start(); print('OK', backend.qgs_app.processingRegistry().providers())"
```

Si esto falla con `ImportError: No se pudo importar 'qgis.core'`, el
intérprete de Python que estás usando no es el que tiene PyQGIS — revisa el
paso 1. Si en cambio falla con `ModuleNotFoundError: No module named
'qgis_mcp'`, falta el paso 3 (`pip install -e .`).

## 5. Conectar con Claude Desktop / Claude Code

Agrega esto a la configuración de MCP servers (`claude_desktop_config.json`
o equivalente en Claude Code), ajustando `command` a la ruta del Python con
PyQGIS que confirmaste en el paso 4:

```json
{
  "mcpServers": {
    "qgis": {
      "command": "/ruta/a/tu/python-con-pyqgis",
      "args": ["-m", "qgis_mcp.server"],
      "env": {
        "QGIS_PREFIX_PATH": "/usr"
      }
    }
  }
}
```

Con conda-forge, `command` suele ser algo como
`~/miniconda3/envs/qgis-mcp/bin/python`.

## Estructura del proyecto

```
qgis_mcp/
  qgis_backend.py    # arranque/apagado de QgsApplication headless (único punto)
  state.py           # resolve_layer() y serialización compartida
  tools_project.py   # qgis_ping, load/save_project, list_layers, get_layer_info,
                      # add_vector/raster_layer, remove_layer, zoom_to_layer,
                      # set_extent, set_layer_style
  tools_attributes.py# calculate_field, get_attribute_table
  tools_processing.py# list_algorithms, get_algorithm_help, run_processing_algorithm
  tools_visual.py    # export_map_image, create_print_layout, export_layout_pdf
  tools_escape.py     # execute_pyqgis
  server.py          # registra todo con FastMCP, transporte stdio
```

## Notas de diseño que siguen el .md

- **Todas las tools de escritura pasan por `resolve_layer()`**, que acepta
  tanto `layer_id` real como nombre, y falla explícitamente si hay ambigüedad
  o si la capa no existe — nunca "adivina".
- **`run_processing_algorithm` resuelve automáticamente** cualquier parámetro
  de tipo string que coincida con un `layer_id`/nombre cargado, así Claude no
  necesita pasar objetos de capa — solo el id que ya vio en `list_layers()`.
- **El log de `QgsProcessingFeedback` se captura y se devuelve** en la
  respuesta de `run_processing_algorithm`, para poder diagnosticar sin
  reintentar a ciegas (regla #5 del `.md`).
- **`execute_pyqgis` es deliberadamente la única tool sin resolución de
  capas ni validación de parámetros** — es el escape hatch, y el `.md` pide
  usarla con cautela, no ponerle una capa extra de "seguridad" que dé una
  falsa sensación de sandboxing.
- **Nada sobrescribe archivos por defecto sin que Claude lo pida
  explícitamente** (`save_project` sin `path` reutiliza la ruta ya cargada,
  no crea copias silenciosas) — la confirmación antes de una operación
  destructiva (regla #8) queda a cargo de Claude en la conversación, esta
  capa de tools no la impone por sí misma.

## Pendiente / próximos pasos sugeridos

1. Probar el flujo completo de `18-ids-algoritmos-processing.md` (superposición
   área de proyecto vs. capa de restricción) contra datos reales.
2. Si se necesita reflejar cambios en una sesión de QGIS Desktop abierta:
   diseñar el modo "desktop" (plugin + socket) reusando esta misma interfaz
   de tools.
3. Autenticación/permisos si el MCP alguna vez deja de ser 100% local (por
   ahora se asume uso local vía stdio, sin superficie de red expuesta).

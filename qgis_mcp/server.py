"""
Servidor MCP de QGIS — modo headless (PyQGIS standalone).

Expone como tools MCP exactamente el set propuesto en
00-mcp-tools-y-modo-agente.md. Transporte: stdio (el estándar para conectar
con Claude Desktop / Claude Code vía configuración local).

Uso:
    python -m qgis_mcp.server

Requiere que el intérprete de Python tenga PyQGIS disponible (ver README.md).
"""

from __future__ import annotations

import threading
from typing import Any, Optional

from mcp.server import MCPServer

from .dispatcher import dispatcher
from .qgis_backend import backend
from .state import LayerNotFoundError
from . import tools_attributes, tools_escape, tools_processing, tools_project, tools_visual

# mcp>=2.0.0 renombró FastMCP -> MCPServer (y movió el módulo); la API de
# decoradores (@mcp.tool(), etc.) no cambió. Si tu entorno tiene mcp<2.0.0
# instalado, cambia esta línea por:
#   from mcp.server.fastmcp import FastMCP as MCPServer
mcp = MCPServer(
    "qgis-mcp",
    instructions=(
        "MCP headless de QGIS (PyQGIS standalone / qgis_process). Antes de operar "
        "sobre una capa por nombre, llama list_layers() para confirmar el layer_id "
        "real. Antes de un algoritmo de Processing no usado en la sesión, llama "
        "get_algorithm_help(). Verifica CRS con get_layer_info() antes de cualquier "
        "análisis geométrico entre dos capas. Después de run_processing_algorithm, "
        "verifica la capa de salida con get_layer_info()/get_attribute_table() antes "
        "de reportar éxito — un algoritmo puede terminar sin error y aun así producir "
        "0 objetos."
    ),
)


def _call(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Punto único de entrada a las tools: despacha al hilo dueño de QGIS y
    convierte 'la capa no existe' en una respuesta {"ok": False, ...}.

    Antes esa excepción subía tal cual y el cliente MCP la mostraba como un
    fallo del servidor, cuando es un error de uso previsto y perfectamente
    recuperable — y encima quedaba inconsistente con el resto de tools, que sí
    devuelven ok:False con un mensaje accionable.
    """
    try:
        resultado = dispatcher.call(fn, *args, **kwargs)
    except LayerNotFoundError as exc:
        return {"ok": False, "error": str(exc)}
    return resultado


# ---------------------------------------------------------------------------
# Proyecto y capas
# ---------------------------------------------------------------------------

@mcp.tool()
def qgis_ping() -> dict[str, Any]:
    """Versión de QGIS y confirmación de que el backend responde."""
    return _call(tools_project.qgis_ping)


@mcp.tool()
def load_project(path: str) -> dict[str, Any]:
    """Abre un proyecto .qgz/.qgs existente."""
    return _call(tools_project.load_project, path)


@mcp.tool()
def save_project(path: Optional[str] = None) -> dict[str, Any]:
    """Guarda el proyecto actual (mismo path si se omite)."""
    return _call(tools_project.save_project, path)


@mcp.tool()
def list_layers() -> dict[str, Any]:
    """Lista id, nombre, tipo, CRS y conteo de objetos de cada capa cargada."""
    return _call(tools_project.list_layers)


@mcp.tool()
def get_layer_info(layer_id: str) -> dict[str, Any]:
    """Esquema de campos, CRS, extensión y proveedor de una capa (id o nombre)."""
    return _call(tools_project.get_layer_info, layer_id)


@mcp.tool()
def add_vector_layer(path_or_uri: str, name: Optional[str] = None) -> dict[str, Any]:
    """Carga un vector (shapefile, GeoPackage, etc.) al proyecto."""
    return _call(tools_project.add_vector_layer, path_or_uri, name)


@mcp.tool()
def add_raster_layer(path_or_uri: str, name: Optional[str] = None) -> dict[str, Any]:
    """Carga un ráster al proyecto."""
    return _call(tools_project.add_raster_layer, path_or_uri, name)


@mcp.tool()
def remove_layer(layer_id: str) -> dict[str, Any]:
    """Quita una capa del proyecto (id o nombre)."""
    return _call(tools_project.remove_layer, layer_id)


@mcp.tool()
def zoom_to_layer(layer_id: str) -> dict[str, Any]:
    """Devuelve el extent de una capa (headless: úsalo como extent explícito
    para export_map_image, no hay lienzo interactivo real)."""
    return _call(tools_project.zoom_to_layer, layer_id)


@mcp.tool()
def set_extent(bbox: list[float], crs: str) -> dict[str, Any]:
    """Define un extent explícito [xmin, ymin, xmax, ymax] en el CRS indicado."""
    return _call(tools_project.set_extent, bbox, crs)


@mcp.tool()
def set_layer_style(
    layer_id: str,
    qml_path: Optional[str] = None,
    field: Optional[str] = None,
    classification_type: Optional[str] = None,
    ramp_name: Optional[str] = None,
) -> dict[str, Any]:
    """Aplica un QML, o una simbología simple (categorized/graduated) por campo."""
    return _call(tools_project.set_layer_style, layer_id, qml_path, field, classification_type, ramp_name)


# ---------------------------------------------------------------------------
# Atributos
# ---------------------------------------------------------------------------

@mcp.tool()
def calculate_field(
    layer_id: str,
    field_name: str,
    expression: str,
    field_type: str = "double",
) -> dict[str, Any]:
    """Equivalente a la calculadora de campos. field_type: int/double/string/bool."""
    return _call(tools_attributes.calculate_field, layer_id, field_name, expression, field_type)


@mcp.tool()
def get_attribute_table(
    layer_id: str,
    filter_expression: Optional[str] = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Lee datos de la tabla de atributos. Indispensable para verificar resultados."""
    return _call(tools_attributes.get_attribute_table, layer_id, filter_expression, limit)


# ---------------------------------------------------------------------------
# Processing
# ---------------------------------------------------------------------------

@mcp.tool()
def list_algorithms(search: Optional[str] = None) -> dict[str, Any]:
    """Busca el id exacto de un algoritmo de Processing por palabra clave."""
    return _call(tools_processing.list_algorithms, search)


@mcp.tool()
def get_algorithm_help(algorithm_id: str) -> dict[str, Any]:
    """Parámetros esperados de un algoritmo (tipos, opcionalidad, default).
    Llamar antes de usar un algoritmo que no se haya usado ya en la sesión."""
    return _call(tools_processing.get_algorithm_help, algorithm_id)


@mcp.tool()
def run_processing_algorithm(algorithm_id: str, parameters: dict[str, Any]) -> dict[str, Any]:
    """Ejecuta cualquier algoritmo nativo/GDAL/SAGA/GRASS de Processing.
    Los valores de 'parameters' que coincidan con un layer_id o nombre de capa
    cargada se resuelven automáticamente a la capa real."""
    return _call(tools_processing.run_processing_algorithm, algorithm_id, parameters)


# ---------------------------------------------------------------------------
# Verificación visual y layouts
# ---------------------------------------------------------------------------

@mcp.tool()
def export_map_image(
    path: str,
    layer_ids: Optional[list[str]] = None,
    extent: Optional[list[float]] = None,
    width: int = 1200,
    height: int = 800,
) -> dict[str, Any]:
    """Renderiza capas específicas (o todo el proyecto) a un PNG en 'path'."""
    return _call(tools_visual.export_map_image, path, layer_ids, extent, width, height)


@mcp.tool()
def create_print_layout(
    name: str,
    template: Optional[str] = None,
    layer_ids: Optional[list[str]] = None,
    extent: Optional[list[float]] = None,
    title: Optional[str] = None,
    legend: bool = True,
    scalebar: bool = True,
    grid_interval: Optional[float] = None,
    orientation: str = "landscape",
) -> dict[str, Any]:
    """Crea una lámina de impresión lista para exportar: mapa con las capas
    indicadas, título, leyenda, escala gráfica y grilla de coordenadas
    opcional. Sin 'layer_ids' usa todas las capas del proyecto; sin 'extent'
    usa el fijado con set_extent() o la extensión de las capas."""
    return _call(
        tools_visual.create_print_layout,
        name,
        template,
        layer_ids,
        extent,
        title,
        legend,
        scalebar,
        grid_interval,
        orientation,
    )


@mcp.tool()
def export_layout_pdf(layout_name: str, path: str) -> dict[str, Any]:
    """Exporta un layout existente a PDF."""
    return _call(tools_visual.export_layout_pdf, layout_name, path)


# ---------------------------------------------------------------------------
# Escape hatch
# ---------------------------------------------------------------------------

@mcp.tool()
def execute_pyqgis(code: str) -> dict[str, Any]:
    """Corre código PyQGIS arbitrario. Usar solo cuando ninguna tool estructurada
    cubre la necesidad. Variables disponibles: project, qgs_app, qgis_core,
    qt_core, processing. Deja el resultado en una variable llamada 'result'."""
    return _call(tools_escape.execute_pyqgis, code)


def main() -> None:
    """QGIS se queda en el hilo principal; el servidor MCP corre en un hilo
    aparte y le pasa el trabajo por la cola del despachador. Ver dispatcher.py
    para el porqué (layouts en blanco y modelos colgados si no se hace así)."""
    backend.start()
    dispatcher.bind_current_thread()

    server_done = threading.Event()

    def _serve() -> None:
        try:
            mcp.run(transport="stdio")
        finally:
            server_done.set()

    threading.Thread(target=_serve, name="mcp-stdio", daemon=True).start()
    try:
        dispatcher.serve_forever(lambda: not server_done.is_set())
    finally:
        dispatcher.shutdown()
        backend.stop()


if __name__ == "__main__":
    main()

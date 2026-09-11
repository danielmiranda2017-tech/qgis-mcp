"""
Helpers de resolución y serialización compartidos por todas las tools.

Regla de 00-mcp-tools-y-modo-agente.md #1: "No asumas el estado del proyecto".
resolve_layer() es el punto único por el que pasa cualquier referencia a una
capa que llegue desde Claude (por id o por nombre), así toda tool falla igual
y con el mismo mensaje si la capa no existe.
"""

from __future__ import annotations

from typing import Any, Optional

from qgis.core import QgsMapLayer, QgsProject, QgsWkbTypes


class LayerNotFoundError(Exception):
    pass


def resolve_layer(project: QgsProject, layer_ref: str) -> QgsMapLayer:
    """Acepta un layer_id real de QGIS o un nombre de capa cargada.

    Si hay más de una capa con el mismo nombre, se lanza un error explícito
    en vez de adivinar cuál — esto es justamente el tipo de ambigüedad que
    la regla "no asumas el estado del proyecto" busca evitar.
    """
    layer = project.mapLayer(layer_ref)
    if layer is not None:
        return layer

    matches = [lyr for lyr in project.mapLayers().values() if lyr.name() == layer_ref]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise LayerNotFoundError(
            f"Hay {len(matches)} capas llamadas '{layer_ref}'. Usa list_layers() "
            "y pasa el layer_id exacto en vez del nombre."
        )
    raise LayerNotFoundError(
        f"No hay ninguna capa cargada con id o nombre '{layer_ref}'. "
        "Llama list_layers() primero para ver qué está cargado realmente."
    )


def geometry_type_name(layer: QgsMapLayer) -> Optional[str]:
    if hasattr(layer, "geometryType"):
        try:
            return QgsWkbTypes.geometryDisplayString(layer.geometryType())
        except Exception:
            return None
    return None


def layer_summary(layer: QgsMapLayer) -> dict[str, Any]:
    """Resumen compacto usado por list_layers()."""
    info: dict[str, Any] = {
        "layer_id": layer.id(),
        "name": layer.name(),
        # QGIS 4.x / Qt6: los enums de PyQGIS ahora son "scoped" (LayerType.X
        # en vez del viejo QgsMapLayer.X plano de QGIS 3.x/Qt5).
        "type": "vector" if layer.type() == QgsMapLayer.LayerType.VectorLayer else (
            "raster" if layer.type() == QgsMapLayer.LayerType.RasterLayer else "otro"
        ),
        "crs": layer.crs().authid() if layer.crs() else None,
        "valid": layer.isValid(),
        "provider": layer.providerType() if hasattr(layer, "providerType") else None,
    }
    geom = geometry_type_name(layer)
    if geom:
        info["geometry_type"] = geom
    if hasattr(layer, "featureCount"):
        try:
            info["feature_count"] = layer.featureCount()
        except Exception:
            info["feature_count"] = None
    return info


def bbox_to_dict(rect) -> dict[str, float]:
    return {
        "xmin": rect.xMinimum(),
        "ymin": rect.yMinimum(),
        "xmax": rect.xMaximum(),
        "ymax": rect.yMaximum(),
    }

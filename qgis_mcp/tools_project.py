"""Tools de proyecto y capas (sección "Proyecto y capas" de 00-mcp-tools-y-modo-agente.md)."""

from __future__ import annotations

import os
from typing import Any, Optional

from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsRasterLayer,
    QgsRectangle,
    QgsVectorLayer,
)

from .qgis_backend import backend
from .state import bbox_to_dict, layer_summary, resolve_layer


def qgis_ping() -> dict[str, Any]:
    backend.ensure_started()
    return {
        "ok": True,
        "qgis_version": Qgis.QGIS_VERSION,
        "processing_providers": [
            p.id() for p in backend.qgs_app.processingRegistry().providers()
        ],
    }


def load_project(path: str) -> dict[str, Any]:
    backend.ensure_started()
    if not os.path.exists(path):
        return {"ok": False, "error": f"No existe el archivo: {path}"}
    with backend.lock():
        ok = backend.project.read(path)
    return {"ok": bool(ok), "path": path, "layers": len(backend.project.mapLayers())}


def save_project(path: Optional[str] = None) -> dict[str, Any]:
    backend.ensure_started()
    target = path or backend.project.fileName()
    if not target:
        return {
            "ok": False,
            "error": "El proyecto no tiene una ruta previa; pasa 'path' explícitamente.",
        }
    with backend.lock():
        ok = backend.project.write(target)
    return {"ok": bool(ok), "path": target}


def list_layers() -> dict[str, Any]:
    backend.ensure_started()
    layers = list(backend.project.mapLayers().values())
    return {"count": len(layers), "layers": [layer_summary(lyr) for lyr in layers]}


def get_layer_info(layer_id: str) -> dict[str, Any]:
    backend.ensure_started()
    layer = resolve_layer(backend.project, layer_id)
    info = layer_summary(layer)
    info["extent"] = bbox_to_dict(layer.extent())
    if isinstance(layer, QgsVectorLayer):
        info["fields"] = [
            {"name": f.name(), "type": f.typeName()} for f in layer.fields()
        ]
    elif isinstance(layer, QgsRasterLayer):
        info.update(_raster_details(layer))
    return info


# Muestra máxima para las estadísticas por banda. Sobre un MDE grande, recorrer
# todos los píxeles para dar min/max cuesta segundos que no valen la pena en una
# tool de inspección; con esta muestra el rango es representativo y la respuesta
# es inmediata.
MUESTRA_ESTADISTICAS = 250_000


def _raster_details(layer: QgsRasterLayer) -> dict[str, Any]:
    """Lo que hace falta saber de un ráster antes de operarlo y que el resumen
    genérico no trae: tamaño en píxeles, resolución, bandas, nodata y rango de
    valores. Sin esto había que recurrir a execute_pyqgis para algo básico."""
    from qgis.core import QgsRasterBandStats

    detalles: dict[str, Any] = {
        "tamaño_px": [layer.width(), layer.height()],
        "resolucion": [layer.rasterUnitsPerPixelX(), layer.rasterUnitsPerPixelY()],
        "bandas": layer.bandCount(),
    }
    provider = layer.dataProvider()
    bandas = []
    for i in range(1, layer.bandCount() + 1):
        banda: dict[str, Any] = {"banda": i, "nombre": layer.bandName(i)}
        try:
            if provider.sourceHasNoDataValue(i):
                banda["nodata"] = provider.sourceNoDataValue(i)
            stats = provider.bandStatistics(
                i,
                QgsRasterBandStats.Stats.Min | QgsRasterBandStats.Stats.Max,
                sampleSize=MUESTRA_ESTADISTICAS,
            )
            banda["min"] = stats.minimumValue
            banda["max"] = stats.maximumValue
        except Exception:  # noqa: BLE001 — un proveedor puede no soportar stats
            pass
        bandas.append(banda)
    detalles["bandas_detalle"] = bandas
    return detalles


def _adopt_crs_if_project_has_none(layer) -> Optional[str]:
    """Un QgsProject recién creado no tiene CRS. Si se deja así, los layouts
    calculan escalas absurdas y la exportación de proyección a GRASS se rompe.
    La primera capa válida que entra define el CRS del proyecto."""
    if backend.project.crs().isValid() or not layer.crs().isValid():
        return None
    backend.project.setCrs(layer.crs())
    return layer.crs().authid()


def add_vector_layer(path_or_uri: str, name: Optional[str] = None) -> dict[str, Any]:
    backend.ensure_started()
    layer_name = name or os.path.splitext(os.path.basename(path_or_uri))[0]
    layer = QgsVectorLayer(path_or_uri, layer_name, "ogr")
    if not layer.isValid():
        return {
            "ok": False,
            "error": (
                f"La capa no se pudo cargar como válida desde '{path_or_uri}'. "
                "Verifica que la ruta exista en el filesystem donde corre este "
                "servidor MCP (puede no ser el mismo filesystem donde corre Claude)."
            ),
        }
    with backend.lock():
        adopted = _adopt_crs_if_project_has_none(layer)
        backend.project.addMapLayer(layer)
    out = {"ok": True, "layer_id": layer.id(), **layer_summary(layer)}
    if adopted:
        out["crs_proyecto_asignado"] = adopted
    return out


def add_raster_layer(path_or_uri: str, name: Optional[str] = None) -> dict[str, Any]:
    backend.ensure_started()
    layer_name = name or os.path.splitext(os.path.basename(path_or_uri))[0]
    layer = QgsRasterLayer(path_or_uri, layer_name)
    if not layer.isValid():
        return {"ok": False, "error": f"La capa ráster no es válida: '{path_or_uri}'."}
    with backend.lock():
        adopted = _adopt_crs_if_project_has_none(layer)
        backend.project.addMapLayer(layer)
    out = {"ok": True, "layer_id": layer.id(), **layer_summary(layer)}
    if adopted:
        out["crs_proyecto_asignado"] = adopted
    if not layer.crs().isValid():
        out["aviso"] = (
            "El ráster no trae CRS. Asígnalo antes de cualquier análisis "
            "geométrico (gdal:assignprojection) o se superpondrá mal."
        )
    return out


def remove_layer(layer_id: str) -> dict[str, Any]:
    backend.ensure_started()
    layer = resolve_layer(backend.project, layer_id)
    real_id = layer.id()
    with backend.lock():
        backend.project.removeMapLayer(real_id)
    return {"ok": True, "removed_layer_id": real_id}


def zoom_to_layer(layer_id: str) -> dict[str, Any]:
    """En modo headless no hay lienzo interactivo; esto devuelve el extent
    de la capa para que quien llame (p.ej. export_map_image) lo use como
    extent explícito."""
    backend.ensure_started()
    layer = resolve_layer(backend.project, layer_id)
    return {"ok": True, "extent": bbox_to_dict(layer.extent()), "crs": layer.crs().authid()}


def set_extent(bbox: list[float], crs: str) -> dict[str, Any]:
    backend.ensure_started()
    if len(bbox) != 4:
        return {"ok": False, "error": "bbox debe tener 4 valores: [xmin, ymin, xmax, ymax]."}
    rect = QgsRectangle(*bbox)
    if rect.isEmpty():
        return {"ok": False, "error": "El bbox está vacío o invertido (¿xmin > xmax?)."}
    # Se guarda como extent activo del proyecto: export_map_image y
    # create_print_layout lo usan cuando no se les pasa uno explícito. Antes
    # esta tool solo devolvía el bbox sin efecto real sobre nada.
    backend.set_active_extent(rect, QgsCoordinateReferenceSystem(crs))
    return {"ok": True, "extent": bbox_to_dict(rect), "crs": crs, "aplicado_como": "extent activo"}


def set_layer_style(
    layer_id: str,
    qml_path: Optional[str] = None,
    field: Optional[str] = None,
    classification_type: Optional[str] = None,
    ramp_name: Optional[str] = None,
) -> dict[str, Any]:
    """Dos modos:
    - qml_path: aplica un archivo .qml existente (control total del usuario).
    - field + classification_type ("categorized" | "graduated") + ramp_name:
      construye una simbología simple sin necesitar un QML preexistente.
    """
    backend.ensure_started()
    layer = resolve_layer(backend.project, layer_id)

    if qml_path:
        if not os.path.exists(qml_path):
            return {"ok": False, "error": f"No existe el QML: {qml_path}"}
        msg, ok = layer.loadNamedStyle(qml_path)
        if ok:
            layer.triggerRepaint()
        return {"ok": bool(ok), "message": msg}

    if field and classification_type:
        from qgis.core import (
            QgsCategorizedSymbolRenderer,
            QgsGraduatedSymbolRenderer,
            QgsRendererCategory,
            QgsStyle,
            QgsSymbol,
        )

        ramp = QgsStyle.defaultStyle().colorRamp(ramp_name or "Viridis")
        if ramp is None:
            return {"ok": False, "error": f"No existe la rampa de color '{ramp_name}'."}

        if field not in [f.name() for f in layer.fields()]:
            return {
                "ok": False,
                "error": (
                    f"La capa no tiene un campo '{field}'. Campos disponibles: "
                    + ", ".join(f.name() for f in layer.fields())
                ),
            }

        # Símbolo base del tipo de geometría de la capa: ambos constructores de
        # renderer lo necesitan explícitamente en QGIS 4.x.
        base_symbol = QgsSymbol.defaultSymbol(layer.geometryType())

        if classification_type == "categorized":
            # QgsCategorizedSymbolRenderer NO tiene createRenderer(): hay que
            # construir las categorías a partir de los valores únicos.
            categories = []
            for value in sorted(layer.uniqueValues(layer.fields().indexOf(field)), key=str):
                symbol = base_symbol.clone()
                categories.append(QgsRendererCategory(value, symbol, str(value)))
            renderer = QgsCategorizedSymbolRenderer(field, categories)
            renderer.updateColorRamp(ramp)
        elif classification_type == "graduated":
            # QGIS 4.x / Qt6: el modo es scoped (Mode.Jenks, no el plano
            # QgsGraduatedSymbolRenderer.Jenks de QGIS 3.x) y la firma exige
            # también el símbolo base y la rampa.
            renderer = QgsGraduatedSymbolRenderer.createRenderer(
                layer, field, 5, QgsGraduatedSymbolRenderer.Mode.Jenks, base_symbol, ramp
            )
            if renderer is None or not renderer.ranges():
                return {
                    "ok": False,
                    "error": (
                        f"No se pudieron calcular clases para '{field}'. "
                        "¿Es un campo numérico con valores no nulos?"
                    ),
                }
        else:
            return {
                "ok": False,
                "error": "classification_type debe ser 'categorized' o 'graduated'.",
            }

        layer.setRenderer(renderer)
        layer.triggerRepaint()
        clases = (
            [r.label() for r in renderer.ranges()]
            if classification_type == "graduated"
            else [c.label() for c in renderer.categories()]
        )
        return {
            "ok": True,
            "classification_type": classification_type,
            "field": field,
            "ramp": ramp_name or "Viridis",
            "clases": clases,
        }

    return {
        "ok": False,
        "error": "Pasa 'qml_path', o 'field' + 'classification_type' (+ 'ramp_name' opcional).",
    }

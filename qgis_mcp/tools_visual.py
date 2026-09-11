"""Tools de verificación visual y layouts.

export_map_image es la tool detrás de la regla #7 de operación ("reporta con
evidencia concreta"): permite a Claude ver el resultado renderizado en vez de
solo confiar en números.

Nota headless (ver 00-mcp-tools-y-modo-agente.md, "Headless vs. Desktop"):
sin GUI no hay "lo que se ve en pantalla" — por eso extent/layer_ids son
obligatorios en la práctica: si se omiten, se usa el extent combinado de las
capas del proyecto, no el de ningún lienzo interactivo.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from qgis.core import (
    QgsLayout,
    QgsLayoutExporter,
    QgsLayoutItemMap,
    QgsMapSettings,
    QgsPrintLayout,
    QgsProject,
    QgsRectangle,
)
from qgis.PyQt.QtCore import QSize
from qgis.PyQt.QtGui import QColor, QImage, QPainter

from .qgis_backend import backend
from .state import resolve_layer


def export_map_image(
    path: str,
    layer_ids: Optional[list[str]] = None,
    extent: Optional[list[float]] = None,
    width: int = 1200,
    height: int = 800,
) -> dict[str, Any]:
    backend.ensure_started()

    if layer_ids:
        layers = [resolve_layer(backend.project, lid) for lid in layer_ids]
    else:
        layers = list(backend.project.mapLayers().values())

    if not layers:
        return {"ok": False, "error": "No hay capas para renderizar (proyecto vacío)."}

    settings = QgsMapSettings()
    settings.setLayers(layers)
    settings.setBackgroundColor(QColor(255, 255, 255))
    settings.setOutputSize(QSize(width, height))

    if extent:
        rect = QgsRectangle(*extent)
    elif backend.active_extent is not None:
        # Extent fijado con set_extent(): antes esa tool no tenía ningún efecto.
        rect = QgsRectangle(backend.active_extent)
    else:
        rect = layers[0].extent()
        for lyr in layers[1:]:
            rect.combineExtentWith(lyr.extent())
    settings.setExtent(rect)
    settings.setDestinationCrs(backend.project.crs())

    # QGIS 4.x / Qt6: QImage.Format_ARGB32 (plano) -> QImage.Format.Format_ARGB32
    image = QImage(QSize(width, height), QImage.Format.Format_ARGB32)
    image.fill(QColor(255, 255, 255).rgb())
    painter = QPainter(image)
    from qgis.core import QgsMapRendererCustomPainterJob

    job = QgsMapRendererCustomPainterJob(settings, painter)
    job.start()
    job.waitForFinished()
    painter.end()

    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    ok = image.save(path)
    return {"ok": bool(ok), "path": path, "layers_rendered": [lyr.name() for lyr in layers]}


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
    """Crea la lámina y, si se le pasan capas, la deja lista para exportar:
    mapa, título, leyenda, escala gráfica y grilla de coordenadas.

    Antes esta tool solo creaba un layout vacío y no había forma de poblarlo,
    así que el PDF salía siempre en blanco.
    """
    backend.ensure_started()
    manager = backend.project.layoutManager()

    if manager.layoutByName(name) is not None:
        return {
            "ok": False,
            "error": f"Ya existe un layout llamado '{name}'. Usa otro nombre o bórralo primero.",
        }

    layout = QgsPrintLayout(backend.project)
    layout.initializeDefaults()
    layout.setName(name)

    if template:
        if not os.path.exists(template):
            return {"ok": False, "error": f"No existe la plantilla: {template}"}
        from qgis.core import QgsReadWriteContext
        from qgis.PyQt.QtXml import QDomDocument

        doc = QDomDocument()
        with open(template, "r", encoding="utf-8") as fh:
            doc.setContent(fh.read())
        layout.loadFromTemplate(doc, QgsReadWriteContext())
        with backend.lock():
            manager.addLayout(layout)
        return {"ok": True, "name": layout.name(), "desde_plantilla": template}

    from qgis.core import (
        QgsLayoutItemLabel,
        QgsLayoutItemLegend,
        QgsLayoutItemScaleBar,
        QgsLayoutPoint,
        QgsLayoutSize,
        QgsUnitTypes,
    )
    from qgis.PyQt.QtCore import QRectF

    mm = QgsUnitTypes.LayoutUnit.LayoutMillimeters
    ancho, alto = (297, 210) if orientation != "portrait" else (210, 297)
    layout.pageCollection().page(0).setPageSize(QgsLayoutSize(ancho, alto, mm))

    margen = 10.0
    # Las anotaciones de la grilla se dibujan FUERA del marco del mapa: sin
    # este respiro quedan cortadas contra el borde de la hoja.
    respiro_grilla = 12.0 if grid_interval else 0.0
    ancho_leyenda = 62.0 if legend else 0.0
    y_mapa = margen + (14.0 if title else 0.0) + respiro_grilla
    ancho_mapa = ancho - 2 * margen - ancho_leyenda - 2 * respiro_grilla
    alto_mapa = alto - y_mapa - margen - respiro_grilla

    mapa = QgsLayoutItemMap(layout)
    mapa.setRect(QRectF(0, 0, ancho_mapa, alto_mapa))
    layout.addLayoutItem(mapa)
    mapa.attemptMove(QgsLayoutPoint(margen + respiro_grilla, y_mapa, mm))
    mapa.attemptResize(QgsLayoutSize(ancho_mapa, alto_mapa, mm))
    mapa.setFrameEnabled(True)

    if layer_ids:
        capas = [resolve_layer(backend.project, lid) for lid in layer_ids]
    else:
        capas = list(backend.project.mapLayers().values())
    if capas:
        mapa.setLayers(capas)

    # Sin CRS explícito el mapa cae en una escala absurda (se ha visto
    # 1:2.699.667.340 con el proyecto sin CRS).
    crs = backend.project.crs()
    if not crs.isValid() and capas:
        crs = capas[0].crs()
    if crs.isValid():
        mapa.setCrs(crs)

    rect = None
    if extent and len(extent) == 4:
        rect = QgsRectangle(*extent)
    elif backend.active_extent is not None:
        rect = QgsRectangle(backend.active_extent)
    elif capas:
        rect = QgsRectangle(capas[0].extent())
        for capa in capas[1:]:
            rect.combineExtentWith(capa.extent())
        rect.scale(1.05)
    if rect is not None and not rect.isEmpty():
        mapa.setExtent(rect)

    if grid_interval:
        grid = mapa.grid()
        grid.setEnabled(True)
        grid.setIntervalX(grid_interval)
        grid.setIntervalY(grid_interval)
        grid.setAnnotationEnabled(True)
        grid.setAnnotationPrecision(0)

    if title:
        etiqueta = QgsLayoutItemLabel(layout)
        etiqueta.setText(title)
        fuente = etiqueta.font()
        fuente.setPointSize(16)
        fuente.setBold(True)
        etiqueta.setFont(fuente)
        layout.addLayoutItem(etiqueta)
        etiqueta.attemptMove(QgsLayoutPoint(margen, margen, mm))
        etiqueta.adjustSizeToText()

    if legend:
        leyenda = QgsLayoutItemLegend(layout)
        leyenda.setTitle("Leyenda")
        leyenda.setLinkedMap(mapa)
        layout.addLayoutItem(leyenda)

        # El modelo por defecto refleja TODO el árbol de capas del proyecto, no
        # solo las del mapa: la lámina salía listando capas intermedias
        # ('output', 'zonal', el MDE crudo…) que no se ven en el mapa. Se congela
        # el modelo y se podan las capas ajenas.
        leyenda.setAutoUpdateModel(False)
        ids_mapa = {capa.id() for capa in capas}
        raiz = leyenda.model().rootGroup()
        for nodo in list(raiz.findLayers()):
            if nodo.layerId() not in ids_mapa:
                (nodo.parent() or raiz).removeChildNode(nodo)

        # Franja reservada abajo para la barra de escala: sin esto la leyenda
        # crece con el número de capas hasta pisarla y salirse de la hoja.
        alto_escala = 22.0 if scalebar else 0.0
        alto_leyenda = max(30.0, alto_mapa - alto_escala)

        # Con muchas capas la tipografía por defecto no cabe en la columna.
        if len(capas) > 3:
            try:
                from qgis.core import QgsLegendStyle

                for estilo, puntos in (
                    (QgsLegendStyle.Style.Title, 10),
                    (QgsLegendStyle.Style.Group, 8),
                    (QgsLegendStyle.Style.Subgroup, 8),
                    (QgsLegendStyle.Style.SymbolLabel, 7),
                ):
                    fuente = leyenda.styleFont(estilo)
                    fuente.setPointSizeF(puntos)
                    leyenda.setStyleFont(estilo, fuente)
                leyenda.setSymbolHeight(3.0)
                leyenda.setSymbolWidth(5.0)
            except Exception:  # noqa: BLE001 — la lámina es válida igual sin esto
                pass

        # Con redimensionado automático la leyenda ignora el alto asignado y se
        # desborda; fijándolo, el contenido que no cabe se recorta dentro de su
        # marco en vez de invadir la escala o el borde de la hoja.
        leyenda.setResizeToContents(False)
        leyenda.attemptMove(
            QgsLayoutPoint(margen + respiro_grilla + ancho_mapa + respiro_grilla + 4, y_mapa, mm)
        )
        leyenda.attemptResize(QgsLayoutSize(ancho_leyenda - 6, alto_leyenda, mm))

    if scalebar:
        escala = QgsLayoutItemScaleBar(layout)
        escala.setStyle("Single Box")
        escala.setLinkedMap(mapa)
        escala.applyDefaultSize()
        layout.addLayoutItem(escala)
        x_escala = (
            margen + respiro_grilla + ancho_mapa + respiro_grilla + 4 if legend else margen
        )
        escala.attemptMove(QgsLayoutPoint(x_escala, alto - margen - 18, mm))

    with backend.lock():
        manager.addLayout(layout)

    return {
        "ok": True,
        "name": layout.name(),
        "pagina_mm": [ancho, alto],
        "capas_en_el_mapa": [capa.name() for capa in capas],
        "escala": round(mapa.scale()) if capas else None,
        "crs": crs.authid() if crs.isValid() else None,
        "elementos": ["mapa"]
        + (["titulo"] if title else [])
        + (["leyenda"] if legend else [])
        + (["escala_grafica"] if scalebar else [])
        + (["grilla"] if grid_interval else []),
    }


def export_layout_pdf(layout_name: str, path: str) -> dict[str, Any]:
    backend.ensure_started()
    manager = backend.project.layoutManager()
    layout = manager.layoutByName(layout_name)
    if layout is None:
        return {"ok": False, "error": f"No existe un layout llamado '{layout_name}'."}

    exporter = QgsLayoutExporter(layout)
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    result = exporter.exportToPdf(path, QgsLayoutExporter.PdfExportSettings())
    # QGIS 4.x / Qt6: QgsLayoutExporter.Success (plano) -> .ExportResult.Success
    ok = result == QgsLayoutExporter.ExportResult.Success
    if not ok:
        return {"ok": False, "path": path, "result_code": int(result),
                "error": "QgsLayoutExporter no pudo escribir el PDF."}

    respuesta: dict[str, Any] = {
        "ok": True,
        "path": path,
        "result_code": int(result),
        "bytes": os.path.getsize(path) if os.path.exists(path) else 0,
    }
    blanco = _layout_sale_en_blanco(exporter)
    if blanco:
        respuesta["ok"] = False
        respuesta["error"] = (
            "El PDF se escribió pero la lámina sale en blanco: el layout no "
            "tiene elementos, o el mapa quedó fuera de la extensión de sus "
            "capas. Recrea el layout con create_print_layout(layer_ids=..., "
            "extent=...)."
        )
    return respuesta


def _layout_sale_en_blanco(exporter: "QgsLayoutExporter") -> bool:
    """Renderiza la página a baja resolución y mira si tiene algún contenido.
    Un PDF de hoja vacía se exporta 'con éxito' y solo se detecta mirándolo."""
    try:
        image = exporter.renderPageToImage(0, QSize(320, 240))
        if image.isNull():
            return True
        colores = set()
        for x in range(0, image.width(), 7):
            for y in range(0, image.height(), 7):
                colores.add(image.pixel(x, y))
                if len(colores) > 2:
                    return False
        return True
    except Exception:
        return False

"""Tools de atributos (sección "Atributos" de 00-mcp-tools-y-modo-agente.md).

get_attribute_table es la tool clave para la regla #4 de operación
("Verifica el resultado, no solo el código de retorno"): es la forma de leer
de vuelta valores calculados (áreas, conteos) antes de reportar éxito.
"""

from __future__ import annotations

from typing import Any, Optional

from qgis.core import QgsExpression, QgsExpressionContext, QgsExpressionContextUtils, QgsField
from qgis.PyQt.QtCore import QMetaType

from .qgis_backend import backend
from .state import resolve_layer

# QGIS 4.x / Qt6: QgsField ya no toma un QVariant.Type (QVariant.Int, etc,
# que en Qt6/PyQt6 dejó de existir como enum de tipos) — ahora toma un
# QMetaType.Type. "string" mapea a QMetaType.Type.QString, no a un
# QMetaType.Type.String genérico.
_FIELD_TYPE_MAP = {
    "int": QMetaType.Type.Int,
    "double": QMetaType.Type.Double,
    "string": QMetaType.Type.QString,
    "bool": QMetaType.Type.Bool,
}


def calculate_field(
    layer_id: str,
    field_name: str,
    expression: str,
    field_type: str = "double",
) -> dict[str, Any]:
    backend.ensure_started()
    layer = resolve_layer(backend.project, layer_id)

    expr = QgsExpression(expression)
    if expr.hasParserError():
        return {"ok": False, "error": f"Expresión inválida: {expr.parserErrorString()}"}

    qmeta_type = _FIELD_TYPE_MAP.get(field_type)
    if qmeta_type is None:
        return {
            "ok": False,
            "error": f"field_type debe ser uno de {list(_FIELD_TYPE_MAP)}.",
        }

    with backend.lock():
        if layer.fields().indexFromName(field_name) < 0:
            layer.dataProvider().addAttributes([QgsField(field_name, qmeta_type)])
            layer.updateFields()

        field_idx = layer.fields().indexFromName(field_name)
        context = QgsExpressionContext()
        context.appendScope(QgsExpressionContextUtils.layerScope(layer))
        expr.prepare(context)

        layer.startEditing()
        updated = 0
        errors = 0
        for feature in layer.getFeatures():
            context.setFeature(feature)
            value = expr.evaluate(context)
            if expr.hasEvalError():
                errors += 1
                continue
            layer.changeAttributeValue(feature.id(), field_idx, value)
            updated += 1
        ok = layer.commitChanges()

    return {
        "ok": bool(ok),
        "field_name": field_name,
        "features_updated": updated,
        "features_with_eval_error": errors,
    }


def get_attribute_table(
    layer_id: str,
    filter_expression: Optional[str] = None,
    limit: int = 50,
) -> dict[str, Any]:
    backend.ensure_started()
    layer = resolve_layer(backend.project, layer_id)

    request = None
    if filter_expression:
        from qgis.core import QgsFeatureRequest

        expr = QgsExpression(filter_expression)
        if expr.hasParserError():
            return {
                "ok": False,
                "error": f"filter_expression inválida: {expr.parserErrorString()}",
            }
        request = QgsFeatureRequest(expr)

    field_names = [f.name() for f in layer.fields()]
    rows: list[dict[str, Any]] = []
    total_matched = 0

    features = layer.getFeatures(request) if request else layer.getFeatures()
    for feature in features:
        total_matched += 1
        if len(rows) < limit:
            rows.append(dict(zip(field_names, feature.attributes())))

    return {
        "ok": True,
        "fields": field_names,
        "total_matched": total_matched,
        "returned": len(rows),
        "truncated": total_matched > len(rows),
        "rows": rows,
    }

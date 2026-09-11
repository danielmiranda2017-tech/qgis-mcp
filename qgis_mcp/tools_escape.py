"""Escape hatch (usar con cautela) — ver 00-mcp-tools-y-modo-agente.md.

execute_pyqgis corre código PyQGIS arbitrario. Solo tiene sentido con
QgsApplication ya inicializada (que es justamente lo que garantiza
qgis_backend.ensure_started()). Es la tool más potente y la más fácil de
usar mal: no hay sandboxing real más allá de lo que ofrece este proceso.

Restricciones deliberadas de esta implementación (ajustables si el usuario
decide asumir el riesgo explícitamente):
- No hay import de módulos fuera de los ya expuestos en el namespace de
  ejecución (qgis.core, qgis.PyQt, project, processing) salvo que el propio
  código los importe explícitamente — Python no lo impide, así que esto es
  una convención de uso, no un sandbox real. No tratar esta tool como segura
  contra código adversarial.
- El resultado esperado es que el código deje su salida en una variable
  llamada `result`: así la tool siempre devuelve algo estructurado en vez de
  depender de que el código imprima con print().
"""

from __future__ import annotations

from typing import Any

from .qgis_backend import backend


def execute_pyqgis(code: str) -> dict[str, Any]:
    backend.ensure_started()

    # UN SOLO diccionario para globals y locals. Con dos diccionarios distintos,
    # las asignaciones e imports del código caen en 'locals', que no forma parte
    # del ámbito global de las funciones que ese mismo código define: un script
    # que importa os en la línea 1 y lo usa dentro de una función revienta con
    # NameError. Lo mismo afecta a comprensiones y clases.
    namespace: dict[str, Any] = {
        "project": backend.project,
        "qgs_app": backend.qgs_app,
    }
    try:
        import qgis.core as qgis_core
        import qgis.PyQt.QtCore as qt_core
        import processing

        namespace["qgis_core"] = qgis_core
        namespace["qt_core"] = qt_core
        namespace["processing"] = processing
    except ImportError:
        pass

    with backend.lock():
        try:
            exec(code, namespace, namespace)  # noqa: S102 (escape hatch documentado)
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    result = namespace.get("result", "El código no definió una variable 'result'.")
    return {"ok": True, "result": result}

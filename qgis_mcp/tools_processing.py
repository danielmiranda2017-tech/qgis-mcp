"""Tools de Processing (sección "Processing" de 00-mcp-tools-y-modo-agente.md).

Estas tres tools son el motor real de casi todo análisis. Reglas de operación
que dependen directamente de ellas (ver 00-...md #2 y #3):
- Llamar get_algorithm_help() antes de un algoritmo no usado antes en la
  sesión, para no adivinar nombres de parámetros (fallan silenciosamente
  en algunos providers como SAGA/GRASS).
- list_algorithms() es la forma de encontrar el id real en vez de adivinarlo
  (ver también 18-ids-algoritmos-processing.md, que trae los ids más comunes
  de este dominio ya resueltos).
"""

from __future__ import annotations

import os
import re
import tempfile
from typing import Any, Optional

from qgis.core import QgsProcessingFeedback, QgsProcessingParameterDefinition

from .qgis_backend import backend
from .state import resolve_layer

# OJO: 'processing' NO se importa aquí a nivel de módulo a propósito.
# server.py hace 'from . import tools_processing' al arrancar, y ese import
# se ejecuta ANTES de que backend.start() alcance a agregar la carpeta de
# plugins de QGIS al sys.path — así que un 'import processing' aquí arriba
# fallaría con ModuleNotFoundError incluso en un entorno bien configurado.
# Se importa de forma perezosa dentro de cada función, después de
# backend.ensure_started().


class _CollectingFeedback(QgsProcessingFeedback):
    """Junta el log de ejecución para devolverlo en la respuesta de la tool,
    en vez de que se pierda en stdout — necesario para poder "diagnosticar
    antes de reintentar" (regla #5)."""

    def __init__(self) -> None:
        super().__init__()
        self.log_lines: list[str] = []

    def pushInfo(self, info: str) -> None:  # noqa: N802 (nombre de la API de Qt)
        self.log_lines.append(info)

    def pushWarning(self, warning: str) -> None:  # noqa: N802
        self.log_lines.append(f"WARNING: {warning}")

    def reportError(self, error: str, fatalError: bool = False) -> None:  # noqa: N802, N803
        self.log_lines.append(f"ERROR{' (fatal)' if fatalError else ''}: {error}")


# Sin 'search' hay ~772 algoritmos: la respuesta completa supera el límite de
# tokens de la tool y no le sirve a nadie. Se devuelve el resumen por provider
# y se pide acotar.
MAX_ALGORITHMS = 120


def list_algorithms(search: Optional[str] = None) -> dict[str, Any]:
    backend.ensure_started()
    registry = backend.qgs_app.processingRegistry()
    results = []
    for alg in registry.algorithms():
        alg_id = alg.id()
        display = alg.displayName()
        if search and search.lower() not in alg_id.lower() and search.lower() not in display.lower():
            continue
        results.append({"id": alg_id, "name": display, "provider": alg.provider().id()})

    total = len(results)
    if total > MAX_ALGORITHMS:
        por_provider: dict[str, int] = {}
        for item in results:
            por_provider[item["provider"]] = por_provider.get(item["provider"], 0) + 1
        return {
            "count": total,
            "truncado": True,
            "algorithms": results[:MAX_ALGORITHMS],
            "por_provider": por_provider,
            "nota": (
                f"Se muestran {MAX_ALGORITHMS} de {total}. Afina 'search' "
                "(p. ej. 'buffer', 'slope', 'pdal') para ver el resto."
            ),
        }
    return {"count": total, "algorithms": results}


def get_algorithm_help(algorithm_id: str) -> dict[str, Any]:
    backend.ensure_started()
    registry = backend.qgs_app.processingRegistry()
    alg = registry.algorithmById(algorithm_id)
    if alg is None:
        return {
            "ok": False,
            "error": (
                f"'{algorithm_id}' no existe en este backend. Si esperabas que "
                "existiera (p.ej. un id de grass7:/saga:), es probable que ese "
                "provider no esté habilitado en el entorno del MCP — usa "
                "list_algorithms(search=...) para confirmar qué hay disponible."
            ),
        }

    params = []
    for p in alg.parameterDefinitions():
        # QGIS 4.x / Qt6: el flag es scoped (Flag.FlagOptional, no el plano
        # p.FlagOptional de QGIS 3.x). Ojo también: en Python, '&' tiene
        # menor precedencia que '==', así que se necesitan paréntesis.
        is_optional = bool(
            p.flags() & QgsProcessingParameterDefinition.Flag.FlagOptional
        )
        params.append(
            {
                "name": p.name(),
                "description": p.description(),
                "type": type(p).__name__,
                "optional": is_optional,
                "default": p.defaultValue(),
            }
        )
    return {
        "ok": True,
        "id": alg.id(),
        "name": alg.displayName(),
        "provider": alg.provider().id(),
        "short_help": alg.shortHelpString(),
        "parameters": params,
    }


# Señales inequívocas de que el algoritmo falló, aunque processing.run() no
# lance excepción. Ojo: no basta con buscar "ERROR" — GRASS escupe líneas
# ERROR por avisos de proyección y aun así produce salidas válidas. Por eso
# la autoridad final es si el archivo de salida existe y abre bien.
_FAIL_MARKERS = (
    "returned error code",
    "traceback (most recent call last)",
    "failed to parse arguments",
    "cannot proceed",
)


def _looks_like_output_path(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    if value.startswith("memory:") or "://" in value:
        return False
    return bool(os.path.splitext(value)[1]) and os.path.isabs(value)


def _verify_output(path: str) -> dict[str, Any]:
    """Comprueba que la salida existe de verdad y que abre como capa válida.
    Sin esto, un algoritmo puede fallar y el MCP reportar éxito con una ruta
    que no existe (ver informe: pdal:exportrastertin, gdal:rastercalculator)."""
    from qgis.core import QgsRasterLayer, QgsVectorLayer

    if not os.path.exists(path):
        return {"existe": False}

    info: dict[str, Any] = {"existe": True, "bytes": os.path.getsize(path)}
    ext = os.path.splitext(path)[1].lower()
    if ext in (".tif", ".tiff", ".img", ".asc", ".vrt"):
        lyr = QgsRasterLayer(path, "check")
        info["valida"] = lyr.isValid()
        if lyr.isValid():
            info["crs"] = lyr.crs().authid() or None
            info["tamaño_px"] = [lyr.width(), lyr.height()]
    elif ext in (".gpkg", ".shp", ".geojson", ".json", ".gml"):
        lyr = QgsVectorLayer(path, "check", "ogr")
        info["valida"] = lyr.isValid()
        if lyr.isValid():
            info["crs"] = lyr.crs().authid() or None
            info["objetos"] = lyr.featureCount()
    return info


# Salidas que son un informe, no una capa: pdal:info escribe un HTML, otros
# algoritmos escriben txt/json/csv. Su contenido es TODA la respuesta útil, así
# que se lee y se devuelve — si no, el agente recibe una ruta y nada más.
_EXT_INFORME = (".html", ".htm", ".txt", ".json", ".csv", ".xml")
MAX_CONTENIDO = 8000


def _leer_informe(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            texto = fh.read(MAX_CONTENIDO * 4)
    except OSError:
        return None
    if path.lower().endswith((".html", ".htm")):
        texto = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", "", texto)
        texto = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|h\d|li)>", "\n", texto)
        texto = re.sub(r"<[^>]+>", "", texto)
        texto = re.sub(r"&nbsp;", " ", texto)
        texto = re.sub(r"&amp;", "&", texto)
        texto = re.sub(r"\n[ \t]*\n+", "\n", texto)
    texto = texto.strip()
    if len(texto) > MAX_CONTENIDO:
        texto = texto[:MAX_CONTENIDO] + "\n… (contenido truncado)"
    return texto or None


def _nombre_salida(algorithm_id: str, key: str, nombres_en_uso: set) -> str:
    """Nombre trazable para una capa en memoria.

    Processing bautiza 'output' a todas sus salidas temporales: tras tres o
    cuatro algoritmos el proyecto —y la leyenda de cualquier lámina— queda con
    varias capas homónimas imposibles de distinguir, y resolve_layer las rechaza
    por ambiguas si se las nombra.
    """
    base = algorithm_id.split(":")[-1]
    if key.upper() != "OUTPUT":
        base = f"{base}_{key.lower()}"
    nombre, n = base, 2
    while nombre in nombres_en_uso:
        nombre = f"{base}_{n}"
        n += 1
    return nombre


def _repair_missing_crs(path: str, crs_authid: str) -> bool:
    """GRASS devuelve ráster con el geotransform correcto pero sin proyección
    ("WKT not recognized"). Se le reasigna el CRS de la entrada."""
    try:
        from osgeo import gdal, osr

        dataset = gdal.Open(path, gdal.GA_Update)
        if dataset is None:
            return False
        srs = osr.SpatialReference()
        if srs.SetFromUserInput(crs_authid) != 0:
            return False
        dataset.SetProjection(srs.ExportToWkt())
        dataset = None
        return True
    except Exception:
        return False


def run_processing_algorithm(algorithm_id: str, parameters: dict[str, Any]) -> dict[str, Any]:
    """Ejecuta cualquier algoritmo nativo/GDAL/SAGA/GRASS.

    'parameters' acepta layer_id o nombre de capa para cualquier valor que
    espere una capa (se resuelve con resolve_layer antes de pasarlo a
    Processing), y también rutas/valores literales tal cual.
    """
    backend.ensure_started()
    registry = backend.qgs_app.processingRegistry()
    alg = registry.algorithmById(algorithm_id)
    if alg is None:
        return {
            "ok": False,
            "error": f"'{algorithm_id}' no existe. Usa list_algorithms(search=...) primero.",
        }

    resolved_params: dict[str, Any] = {}
    for key, value in parameters.items():
        if isinstance(value, str):
            layer = None
            try:
                layer = resolve_layer(backend.project, value)
            except Exception:
                pass
            resolved_params[key] = layer if layer is not None else value
        else:
            resolved_params[key] = value

    # Destinos de ARCHIVO no especificados (informes HTML/CSV/TXT): se les asigna
    # un temporal para poder leer su contenido después. Antes esto solo se hacía
    # con un parámetro llamado exactamente 'OUTPUT', así que algoritmos que
    # escriben su informe en otro parámetro (native:rasterlayeruniquevaluesreport
    # usa OUTPUT_HTML_FILE) corrían sin generar nada y el resultado útil —que es
    # justamente el informe— se perdía en silencio.
    from qgis.core import QgsProcessingParameterFileDestination

    for p in alg.parameterDefinitions():
        if p.name() in resolved_params:
            continue
        if isinstance(p, QgsProcessingParameterFileDestination):
            ext = (p.defaultFileExtension() or "html").lstrip(".")
            handle, ruta_tmp = tempfile.mkstemp(prefix="qgis_mcp_", suffix="." + ext)
            os.close(handle)
            resolved_params[p.name()] = ruta_tmp

    # Salida en memoria por defecto para el OUTPUT de capa, para no obligar a
    # Claude a inventar una ruta cuando solo quiere el resultado.
    if (
        any(p.name() == "OUTPUT" for p in alg.parameterDefinitions())
        and "OUTPUT" not in resolved_params
    ):
        resolved_params["OUTPUT"] = "memory:"

    feedback = _CollectingFeedback()
    with backend.lock():
        try:
            import processing  # import perezoso: ver nota al inicio del archivo

            result = processing.run(algorithm_id, resolved_params, feedback=feedback)
        except Exception as exc:
            return {
                "ok": False,
                "error": str(exc),
                "log": feedback.log_lines,
            }

    # CRS de la primera capa de entrada: sirve para reparar salidas de GRASS,
    # que llegan sin proyección aunque el geotransform sea correcto.
    input_crs = None
    for value in resolved_params.values():
        if hasattr(value, "crs") and callable(value.crs):
            try:
                if value.crs().isValid():
                    input_crs = value.crs().authid()
                    break
            except Exception:
                pass

    output_layers: dict[str, Any] = {}
    checks: dict[str, Any] = {}
    faltantes: list[str] = []
    avisos: list[str] = []
    informes: dict[str, str] = {}

    for key, value in result.items():
        # Muchas salidas de Processing son objetos de capa directamente
        # cuando OUTPUT='memory:'; para otras (rutas, valores) se devuelven
        # tal cual.
        if hasattr(value, "id") and hasattr(value, "isValid"):
            with backend.lock():
                if value.name() in ("", "output", "Output", "OUTPUT"):
                    en_uso = {lyr.name() for lyr in backend.project.mapLayers().values()}
                    value.setName(_nombre_salida(algorithm_id, key, en_uso))
                backend.project.addMapLayer(value)
            output_layers[key] = {"layer_id": value.id(), "name": value.name()}

            # Una capa en memoria también puede salir vacía o inválida. Antes se
            # devolvía solo su id, sin conteo ni aviso, así que un join sin
            # coincidencias o una intersección sin solape se reportaban como
            # éxito limpio — el mismo falso positivo que ya se controla en las
            # salidas a archivo.
            check_mem: dict[str, Any] = {"en_memoria": True, "valida": bool(value.isValid())}
            try:
                check_mem["crs"] = value.crs().authid() or None
            except Exception:  # noqa: BLE001
                pass
            if hasattr(value, "featureCount"):
                try:
                    check_mem["objetos"] = int(value.featureCount())
                except Exception:  # noqa: BLE001
                    pass
            if not check_mem["valida"]:
                avisos.append(f"{key}: la capa en memoria resultante no es válida.")
            elif check_mem.get("objetos") == 0:
                avisos.append(f"{key}: la capa en memoria resultante tiene 0 objetos.")
            checks[key] = check_mem
            continue

        output_layers[key] = value
        if not _looks_like_output_path(value):
            continue

        check = _verify_output(value)
        if not check.get("existe"):
            faltantes.append(f"{key} -> {value}")
        else:
            if value.lower().endswith(_EXT_INFORME):
                if check.get("bytes"):
                    contenido = _leer_informe(value)
                    if contenido:
                        informes[key] = contenido
                    else:
                        avisos.append(f"{key}: el informe existe pero está vacío.")
                else:
                    avisos.append(f"{key}: el informe se creó con 0 bytes.")
            if check.get("valida") is False:
                avisos.append(f"{key}: el archivo existe pero no abre como capa válida.")
            if check.get("objetos") == 0:
                avisos.append(f"{key}: la capa resultante tiene 0 objetos.")
            if check.get("crs") in (None, "") and input_crs:
                if _repair_missing_crs(value, input_crs):
                    check["crs"] = input_crs
                    check["crs_reparado"] = True
                    avisos.append(
                        f"{key}: la salida venía sin CRS (típico de GRASS); "
                        f"se le asignó {input_crs} tomado de la entrada."
                    )
                else:
                    avisos.append(f"{key}: la salida no tiene CRS y no se pudo reparar.")
        checks[key] = check

    log_lower = " \n".join(feedback.log_lines).lower()
    marcador = next((m for m in _FAIL_MARKERS if m in log_lower), None)

    if faltantes:
        return {
            "ok": False,
            "algorithm_id": algorithm_id,
            "error": (
                "El algoritmo terminó sin excepción pero no generó su salida: "
                + "; ".join(faltantes)
                + (f". El log delata el fallo ('{marcador}')." if marcador else ".")
            ),
            "outputs": output_layers,
            "salidas_verificadas": checks,
            "log": feedback.log_lines,
        }

    respuesta: dict[str, Any] = {
        "ok": True,
        "algorithm_id": algorithm_id,
        "outputs": output_layers,
        "log": [line for line in feedback.log_lines if line.strip() not in ("ERROR:", "ERROR")],
    }
    if checks:
        respuesta["salidas_verificadas"] = checks
    if informes:
        respuesta["contenido"] = informes
    if marcador:
        avisos.append(
            f"El log contiene '{marcador}' aunque las salidas existen: revísalas antes de reportar."
        )
    if avisos:
        respuesta["avisos"] = avisos
    return respuesta

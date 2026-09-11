"""
Backend headless de QGIS.

Inicializa una instancia standalone de QgsApplication (sin interfaz gráfica),
al estilo de un script PyQGIS que corre fuera de QGIS Desktop. Este módulo es
el único lugar del proyecto que sabe cómo arrancar/apagar QGIS; todo lo demás
(los archivos tools_*.py) recibe una referencia a QgsProject ya inicializada.

Por qué headless primero (ver 00-mcp-tools-y-modo-agente.md):
- Es más robusto para un servidor MCP de larga duración: no depende de que
  haya una sesión de QGIS Desktop abierta con un plugin activo.
- Cubre el 90% de los casos de este dominio (Processing, cálculo de campos,
  overlay vectorial, exportar imágenes estáticas) sin necesitar un "lienzo
  interactivo" real.

Variables de entorno relevantes (deben apuntar a tu instalación de QGIS):
    QGIS_PREFIX_PATH   -> prefijo de instalación de QGIS (ej. /usr en Linux,
                           o la carpeta "apps/qgis" dentro de OSGeo4W en Windows)
    PYTHONPATH         -> debe incluir la carpeta python/ de QGIS si no se
                           instaló qgis-mcp en el mismo intérprete que trae PyQGIS
"""

from __future__ import annotations

import os
import sys
import threading
from dataclasses import dataclass, field
from typing import Optional

# Import diferido y con mensaje claro: PyQGIS no es un paquete de PyPI normal,
# viene con la instalación de QGIS. Si esto falla, es un problema de entorno,
# no del servidor (ver README.md, sección "Configurar el entorno PyQGIS").
try:
    from qgis.core import (
        QgsApplication,
        QgsProject,
        QgsVectorLayer,
        QgsRasterLayer,
        QgsCoordinateReferenceSystem,
    )
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "No se pudo importar 'qgis.core'. Este servidor MCP necesita correr "
        "con un intérprete de Python que tenga PyQGIS disponible (el mismo "
        "que usa QGIS Desktop, o una instalación de qgis-headless/conda-forge "
        "con 'qgis' instalado). Revisa QGIS_PREFIX_PATH y PYTHONPATH en el "
        "README.md."
    ) from exc


@dataclass
class QgisBackend:
    """Contenedor único del estado vivo de QGIS para todo el proceso del MCP."""

    qgs_app: Optional["QgsApplication"] = None
    project: Optional["QgsProject"] = None
    # Reentrante: con el despachador, todo corre en el mismo hilo y un Lock
    # simple se autobloquearía en cuanto una tool llamara a otra.
    _lock: threading.RLock = field(default_factory=threading.RLock)
    active_extent: Optional[object] = None
    active_extent_crs: Optional[object] = None

    def set_active_extent(self, rect, crs) -> None:
        self.active_extent = rect
        self.active_extent_crs = crs

    def start(self) -> None:
        if self.qgs_app is not None:
            return  # ya inicializado, idempotente

        prefix_path = os.environ.get("QGIS_PREFIX_PATH")
        if prefix_path:
            QgsApplication.setPrefixPath(prefix_path, True)

        # False = sin GUI. Necesario en un servidor headless / sin display.
        self.qgs_app = QgsApplication([], False)
        self.qgs_app.initQgis()

        # Habilitar providers de Processing (native, gdal, y GRASS/SAGA si
        # están instalados en el sistema). Sin esto, list_algorithms() y
        # run_processing_algorithm() no van a encontrar nada.
        #
        # 'processing' es un plugin "core" de QGIS, no un módulo de qgis.core:
        # vive en <prefijo>/python/plugins/processing y esa carpeta de plugins
        # no se agrega sola al sys.path en modo headless (a diferencia de
        # cuando corre dentro de QGIS Desktop). Hay que agregarla a mano antes
        # de importar 'processing', o el import falla con ModuleNotFoundError.
        self._ensure_processing_plugin_on_path()
        import processing  # noqa: F401  (registra el provider "native"/"qgis")
        from qgis.analysis import QgsNativeAlgorithms

        self.qgs_app.processingRegistry().addProvider(QgsNativeAlgorithms())

        # Sin esto, processing.run(...) no encuentra ningún algoritmo aunque
        # los providers estén registrados: falta inicializar el framework de
        # Processing en sí (carga la lista de algoritmos de cada provider).
        from processing.core.Processing import Processing

        Processing.initialize()

        try:
            # Provider de GDAL (viene con QGIS). Processing.initialize() ya
            # suele registrarlo solo; solo lo agregamos si hace falta, para
            # no ensuciar el log con "Duplicate provider gdal registered".
            from processing.algs.gdal.GdalAlgorithmProvider import GdalAlgorithmProvider

            registry = self.qgs_app.processingRegistry()
            if registry.providerById("gdal") is None:
                registry.addProvider(GdalAlgorithmProvider())
        except Exception:
            pass  # no crítico; get_algorithm_help/list_algorithms lo reportarán como ausente

        self._ensure_grass_configured()

        self.project = QgsProject.instance()

    @staticmethod
    def _ensure_grass_configured() -> None:
        """Los ~300 algoritmos grass: aparecen listados aunque GRASS no esté
        configurado, y fallan al ejecutarse con "GRASS folder is not
        configured". En Windows, GrassUtils lee GISBASE del entorno; si no
        está, busca carpetas 'grass-7.x' y no encuentra las grass8x que trae
        QGIS 4. Se resuelve apuntando GISBASE a la instalación que viene con
        QGIS."""
        if os.environ.get("GISBASE"):
            return

        import glob

        roots = []
        prefix = os.environ.get("QGIS_PREFIX_PATH")
        if prefix:
            roots.append(os.path.join(os.path.dirname(prefix), "grass"))
        osgeo_root = os.environ.get("OSGEO4W_ROOT")
        if osgeo_root:
            roots.append(os.path.join(osgeo_root, "apps", "grass"))
        try:
            roots.append(os.path.join(os.path.dirname(QgsApplication.prefixPath()), "grass"))
        except Exception:
            pass

        for root in roots:
            matches = sorted(glob.glob(os.path.join(root, "grass*")), reverse=True)
            matches = [m for m in matches if os.path.isdir(os.path.join(m, "bin"))]
            if not matches:
                continue
            os.environ["GISBASE"] = matches[0]
            try:
                from grassprovider.grass_utils import GrassUtils

                GrassUtils.path = matches[0]
            except Exception:
                pass
            return

    @staticmethod
    def _ensure_processing_plugin_on_path() -> None:
        """Agrega la carpeta de plugins core de QGIS a sys.path si hace falta,
        para que 'import processing' funcione en modo headless."""
        candidates = []

        prefix = os.environ.get("QGIS_PREFIX_PATH")
        if prefix:
            candidates.append(os.path.join(prefix, "python", "plugins"))

        osgeo_root = os.environ.get("OSGEO4W_ROOT")
        if osgeo_root:
            candidates.append(os.path.join(osgeo_root, "apps", "qgis", "python", "plugins"))
            candidates.append(os.path.join(osgeo_root, "apps", "qgis-ltr", "python", "plugins"))

        try:
            candidates.append(
                os.path.join(QgsApplication.pkgDataPath(), "python", "plugins")
            )
        except Exception:
            pass

        for candidate in candidates:
            if os.path.isdir(candidate) and candidate not in sys.path:
                sys.path.append(candidate)

    def stop(self) -> None:
        if self.qgs_app is not None:
            self.qgs_app.exitQgis()
            self.qgs_app = None
            self.project = None

    def ensure_started(self) -> None:
        if self.qgs_app is None:
            self.start()

    def lock(self):
        """PyQGIS/Processing no son thread-safe entre llamadas concurrentes;
        todas las tools deben serializar el acceso al backend con esto."""
        return self._lock


# Instancia única compartida por todo el servidor (un proceso MCP = un backend).
backend = QgisBackend()

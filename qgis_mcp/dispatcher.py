"""Despachador de llamadas al hilo dueño de QGIS.

Por qué existe (ver informe de pruebas):
FastMCP atiende cada tool en un hilo worker de AnyIO, distinto del hilo donde
se creó QgsApplication. Los objetos Qt (QGraphicsScene de los layouts, capas de
memoria que crean los modelos del Modelador) solo pueden usarse desde el hilo
que los creó. Usarlos desde otro hilo produce fallos silenciosos —un layout que
exporta una hoja transparente— o directamente un deadlock permanente que deja
el servidor inutilizable.

Solución: QGIS vive en el hilo principal, el servidor MCP corre en un hilo
aparte, y toda tool se ejecuta en el hilo principal a través de esta cola.
"""

from __future__ import annotations

import os
import queue
import threading
from concurrent.futures import Future
from typing import Any, Callable

# Tope de espera por llamada. Un algoritmo legítimamente largo (hidrología sobre
# un MDE grande, indexado de una nube de puntos) puede tardar minutos; pasado
# este tope se devuelve error en vez de dejar al agente esperando sin señal.
DEFAULT_TIMEOUT = float(os.environ.get("QGIS_MCP_CALL_TIMEOUT", "900"))


class QgisDispatcher:
    def __init__(self) -> None:
        self._queue: "queue.Queue[tuple]" = queue.Queue()
        self._owner_thread: int | None = None
        self._shutdown = threading.Event()

    def bind_current_thread(self) -> None:
        """Marca el hilo actual como dueño de QGIS. Lo llama main() antes de
        arrancar el servidor MCP."""
        self._owner_thread = threading.get_ident()

    def call(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Ejecuta fn en el hilo dueño de QGIS y devuelve su resultado.

        Si ya estamos en ese hilo (o el despachador no está activo, p. ej. en
        tests que importan las tools directamente), ejecuta en línea.
        """
        if self._owner_thread is None or threading.get_ident() == self._owner_thread:
            return fn(*args, **kwargs)

        future: Future = Future()
        self._queue.put((fn, args, kwargs, future))
        return future.result(timeout=DEFAULT_TIMEOUT)

    def serve_forever(self, keep_running: Callable[[], bool]) -> None:
        """Bucle del hilo dueño: atiende llamadas hasta que keep_running()
        deje de ser cierto."""
        while keep_running() and not self._shutdown.is_set():
            try:
                fn, args, kwargs, future = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if not future.set_running_or_notify_cancel():
                continue
            try:
                future.set_result(fn(*args, **kwargs))
            except BaseException as exc:  # noqa: BLE001 — se reenvía al hilo que llamó
                future.set_exception(exc)

    def shutdown(self) -> None:
        self._shutdown.set()


dispatcher = QgisDispatcher()

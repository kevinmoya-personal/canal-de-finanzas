"""
cache.py — Almacén de assets direccionado por contenido y por consulta.

El fallo de coherencia de la v1 estaba aquí:

    # fondos.py:150-159
    unused_fondos = [f for f in local_fondos if f not in used_list]
    if unused_fondos:
        selected_file = unused_fondos[0]   # ← ignora 'keywords'

Es decir: agarraba CUALQUIER fondo descargado que no se hubiera usado antes,
sin mirar el tema. Las keywords solo entraban en juego cuando la carpeta
local se agotaba. Por eso un video de gatitos podía salir con un fondo de
"dark city night rain".

Aquí la caché se indexa POR CONSULTA. Un archivo solo se reutiliza para la
consulta que lo trajo. Se ahorra ancho de banda sin perder coherencia.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import Path

from .. import config

_candado = threading.Lock()


def clave(consulta: str) -> str:
    """Identificador estable y corto para una consulta de búsqueda."""
    normalizada = re.sub(r"\s+", " ", consulta.strip().lower())
    return hashlib.sha1(normalizada.encode("utf-8")).hexdigest()[:12]


def carpeta(proveedor: str, consulta: str) -> Path:
    d = config.DIR_CACHE / proveedor / clave(consulta)
    d.mkdir(parents=True, exist_ok=True)
    return d


# Sufijo de las descargas en curso. Nunca deben considerarse assets válidos:
# un archivo a medio bajar produce un clip truncado o un render caído.
SUFIJO_PARCIAL = ".parcial"


def guardados(proveedor: str, consulta: str) -> list[Path]:
    """Assets COMPLETOS ya descargados para esta consulta exacta."""
    d = carpeta(proveedor, consulta)
    return sorted(
        p for p in d.iterdir()
        if p.is_file()
        and p.suffix not in (".json", SUFIJO_PARCIAL)
        and SUFIJO_PARCIAL not in p.suffixes
    )


def limpiar_parciales() -> int:
    """Barre descargas interrumpidas de ejecuciones anteriores."""
    n = 0
    if not config.DIR_CACHE.exists():
        return 0
    for p in config.DIR_CACHE.rglob(f"*{SUFIJO_PARCIAL}*"):
        if p.is_file():
            p.unlink(missing_ok=True)
            n += 1
    return n


def ruta_para(proveedor: str, consulta: str, id_remoto: str, sufijo: str) -> Path:
    return carpeta(proveedor, consulta) / f"{id_remoto}{sufijo}"


# ── Registro de uso por nicho ─────────────────────────────────────────────
# Evita que el mismo clip aparezca en dos videos seguidos del mismo canal.
def _ruta_registro(nicho: str) -> Path:
    d = config.DIR_CACHE / "registro"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{nicho}.json"


def cargar_usados(nicho: str) -> dict[str, str]:
    ruta = _ruta_registro(nicho)
    if not ruta.exists():
        return {}
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except Exception:
        return {}


def marcar_usado(nicho: str, id_asset: str, video_id: str) -> None:
    with _candado:
        datos = cargar_usados(nicho)
        datos[str(id_asset)] = video_id
        _ruta_registro(nicho).write_text(
            json.dumps(datos, ensure_ascii=False, indent=0), encoding="utf-8"
        )


def limpiar_consulta(proveedor: str, consulta: str) -> int:
    """Borra lo cacheado de una consulta. Útil si los resultados no gustaron."""
    d = carpeta(proveedor, consulta)
    n = 0
    for p in d.iterdir():
        p.unlink()
        n += 1
    return n

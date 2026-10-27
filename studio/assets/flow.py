"""
flow.py — Biblioteca de clips propios hechos en Google Flow (Veo).

Los clips de Flow se generan a mano (o con el navegador) una sola vez y se
reutilizan: son únicos del canal, al contrario que el stock de Pexels, que
cualquiera puede usar.

Estructura:
    assets_globales/flow/<nicho>/<keyword>/*.mp4

`<keyword>` es una clave de `stock.mapa_keywords` del nicho (ahorro,
inflacion, deuda…). En cada video, la PRIMERA toma de stock de cada keyword
usa un clip de Flow si hay; el resto sigue con Pexels. Así un clip propio no
se repite dentro del mismo video, y entre videos se rota: siempre se elige
el que menos veces se ha usado.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from .. import config

_candado = threading.Lock()
EXT_VIDEO = {".mp4", ".mov", ".webm"}


def carpeta(nicho: str) -> Path:
    return config.RAIZ / "assets_globales" / "flow" / nicho


def _ruta_uso(nicho: str) -> Path:
    d = config.DIR_CACHE / "registro"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"flow_{nicho}.json"


def _usos(nicho: str) -> dict[str, int]:
    ruta = _ruta_uso(nicho)
    try:
        return json.loads(ruta.read_text(encoding="utf-8")) if ruta.exists() else {}
    except Exception:
        return {}


def clips(nicho: str, keyword: str) -> list[Path]:
    d = carpeta(nicho) / keyword
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir() if p.suffix.lower() in EXT_VIDEO)


def elegir(nicho: str, keyword: str) -> Path | None:
    """El clip de esa keyword menos usado hasta ahora, y lo anota."""
    disponibles = clips(nicho, keyword)
    if not disponibles:
        return None
    with _candado:
        usos = _usos(nicho)
        clip = min(disponibles, key=lambda p: (usos.get(p.name, 0), p.name))
        usos[clip.name] = usos.get(clip.name, 0) + 1
        _ruta_uso(nicho).write_text(json.dumps(usos, ensure_ascii=False, indent=0),
                                    encoding="utf-8")
    return clip


def inventario(nicho: str) -> dict[str, int]:
    base = carpeta(nicho)
    if not base.is_dir():
        return {}
    return {d.name: len(clips(nicho, d.name)) for d in sorted(base.iterdir()) if d.is_dir()}

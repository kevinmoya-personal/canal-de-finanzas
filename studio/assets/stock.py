"""
stock.py — Búsqueda y descarga de b-roll en Pexels, con puntuación real.

Diferencias con `fondos.py` de la v1:

  · La v1 se quedaba con `videos[0]`: el primer resultado, sin mirar
    resolución, duración ni orientación. Aquí se puntúan los candidatos.
  · La v1 reutilizaba cualquier archivo local ignorando el tema (ver
    cache.py). Aquí la caché va por consulta.
  · La v1 buscaba una sola vez y si no había nada, fondo negro. Aquí hay
    consulta de respaldo.
  · Descargas en paralelo: un video de 100 tomas no puede bajar los assets
    de uno en uno.
"""
from __future__ import annotations

import concurrent.futures
import threading
import uuid
from pathlib import Path

import requests

from .. import config
from ..niche import Nicho
from ..storyboard import Storyboard
from . import cache

API_PEXELS = "https://api.pexels.com/videos/search"
TIEMPO_ESPERA = 30

_candado_red = threading.Semaphore(4)   # Pexels limita a 200 peticiones/hora


# ── Búsqueda ──────────────────────────────────────────────────────────────
def buscar(
    consulta: str, orientacion: str = "portrait", cantidad: int = 15
) -> list[dict]:
    """Consulta la API de Pexels y devuelve los videos crudos."""
    api_key = config.secreto("PEXELS_API_KEY", obligatorio=True)
    try:
        with _candado_red:
            r = requests.get(
                API_PEXELS,
                headers={"Authorization": api_key},
                params={
                    "query": consulta,
                    "orientation": orientacion,
                    "per_page": cantidad,
                    "size": "medium",
                },
                timeout=TIEMPO_ESPERA,
            )
        r.raise_for_status()
        return r.json().get("videos", [])
    except Exception as e:
        print(f"    ⚠️  Pexels falló para '{consulta}': {e}")
        return []


def _puntuar(
    video: dict, dur_necesaria: float, vertical: bool, posicion: int = 0
) -> float:
    """
    Qué tan bien sirve este clip. Más alto es mejor.

    `posicion` es el puesto en que lo devolvió Pexels. Importa mucho: su
    buscador es lo único que entiende de qué va la consulta, mientras que
    nosotros solo sabemos medir píxeles y segundos. Ignorarlo hacía que un
    clip irrelevante pero técnicamente perfecto adelantara a uno pertinente
    — así es como un video sobre manipulación acabó ilustrado con un
    dinosaurio de juguete.
    """
    # Decae rápido: el puesto 1 vale 45, el 5 unos 25, el 15 unos 8.
    puntos = 45.0 / (1.0 + posicion * 0.38)
    ancho = video.get("width", 0)
    alto = video.get("height", 0)
    dur = video.get("duration", 0)

    # Orientación: recortar un horizontal a 9:16 tira el 70% de la imagen.
    if vertical and alto > ancho:
        puntos += 40
    elif not vertical and ancho > alto:
        puntos += 40
    elif ancho == alto:
        puntos += 10

    # Resolución: nos hace falta margen para el recorte y el zoom.
    menor = min(ancho, alto)
    if menor >= 1080:
        puntos += 30
    elif menor >= 720:
        puntos += 12

    # Duración: que cubra la toma con holgura, pero sin ser eterno.
    if dur >= dur_necesaria + 2:
        puntos += 20
        if 5 <= dur <= 25:
            puntos += 10      # rango cómodo para elegir un tramo interesante
    elif dur >= dur_necesaria:
        puntos += 8
    else:
        puntos -= 15          # habría que repetirlo en bucle

    return puntos


def _mejor_archivo(video: dict) -> str | None:
    """Elige el archivo de mayor calidad razonable dentro del resultado."""
    archivos = video.get("video_files", [])
    if not archivos:
        return None
    # Preferimos el de mayor altura que no pase de 1920: 4K no aporta nada
    # y multiplica el tiempo de descarga y decodificación.
    utiles = [f for f in archivos if f.get("height") and f["height"] <= 1920]
    elegido = max(utiles or archivos, key=lambda f: f.get("height") or 0)
    return elegido.get("link")


def _descargar(url: str, destino: Path) -> Path | None:
    """
    Descarga a un temporal y renombra al final.

    El temporal lleva un sufijo único: si dos hilos van a por el mismo clip
    a la vez, cada uno escribe en su propio archivo. Compartiendo el nombre
    (como se hacía antes) uno intentaba renombrar mientras el otro seguía
    escribiendo, y Windows lo rechaza con WinError 32.
    """
    temporal = destino.with_name(
        f"{destino.name}.{uuid.uuid4().hex[:8]}{cache.SUFIJO_PARCIAL}"
    )
    try:
        with _candado_red:
            r = requests.get(url, stream=True, timeout=120)
            r.raise_for_status()
            with open(temporal, "wb") as f:
                for trozo in r.iter_content(1 << 20):
                    f.write(trozo)

        if destino.exists():
            # Otro hilo llegó primero. Su copia vale igual.
            temporal.unlink(missing_ok=True)
            return destino
        temporal.replace(destino)
        return destino
    except Exception as e:
        temporal.unlink(missing_ok=True)
        print(f"    ⚠️  Descarga fallida: {e}")
        return None


# ── Obtención de un asset para una toma ───────────────────────────────────
def obtener(
    consulta: str,
    dur_necesaria: float,
    nicho: Nicho,
    vertical: bool,
    excluidos: set[str],
    evitar: set[str] | None = None,
) -> Path | None:
    """
    Devuelve la ruta de un clip apropiado para esta consulta.

    Dos niveles de exclusión, y la diferencia importa:

    `excluidos` — ids ya usados en ESTE video. Prohibición ABSOLUTA: ver el
        mismo plano dos veces en una pieza de 50 segundos es descarado.

    `evitar` — ids usados en videos ANTERIORES de este canal. Preferencia
        FUERTE, no prohibición: si el canal se queda sin material nuevo para
        un tema, es mejor repetir un clip de hace diez videos que dejar la
        toma en negro.

    Sin el segundo nivel, todos los videos del canal acaban compartiendo el
    mismo puñado de clips y la parrilla se ve clonada.
    """
    evitar = evitar or set()

    # 1. Cacheado para esta consulta, priorizando lo que nunca se ha usado.
    guardados = [r for r in cache.guardados("pexels", consulta) if r.stem not in excluidos]
    frescos = [r for r in guardados if r.stem not in evitar]
    if frescos:
        return frescos[0]

    # 2. Buscar en Pexels algo que no hayamos usado nunca.
    orientacion = "portrait" if vertical else "landscape"
    cantidad = int(nicho.stock.get("candidatos_por_busqueda", 15))
    resultados = buscar(consulta, orientacion, cantidad)

    if not resultados:
        respaldo = nicho.stock.get("mapa_keywords", {}).get("default")
        if respaldo and respaldo != consulta:
            print(f"    ↩️  Sin resultados, probando respaldo: '{respaldo}'")
            return obtener(respaldo, dur_necesaria, nicho, vertical, excluidos, evitar)
        # Antes de rendirnos, aceptamos repetir algo ya visto.
        return guardados[0] if guardados else None

    ordenados = sorted(
        enumerate(resultados),
        key=lambda par: _puntuar(par[1], dur_necesaria, vertical, par[0]),
        reverse=True,
    )
    ordenados = [v for _, v in ordenados]

    # Primera pasada: solo material nunca usado en el canal.
    # Segunda: relajamos y permitimos repetir de videos antiguos.
    for permitir_repetido in (False, True):
        for video in ordenados:
            id_remoto = str(video.get("id"))
            if id_remoto in excluidos:
                continue
            if not permitir_repetido and id_remoto in evitar:
                continue
            url = _mejor_archivo(video)
            if not url:
                continue
            destino = cache.ruta_para("pexels", consulta, id_remoto, ".mp4")
            if destino.exists():
                return destino
            if _descargar(url, destino):
                return destino

    return guardados[0] if guardados else None


# ── Resolución de todo un storyboard ──────────────────────────────────────
def resolver(
    sb: Storyboard, nicho: Nicho, hilos: int = 4
) -> tuple[int, int]:
    """
    Rellena `visual.archivo` de todas las tomas. En paralelo.

    Devuelve (resueltas, fallidas).
    """
    vertical = sb.alto > sb.ancho
    excluidos: set[str] = set()
    candado = threading.Lock()

    # ⭐ Lo que ya se usó en videos anteriores de este canal. Sin esto, cada
    # video vuelve a coger el primer clip cacheado de cada consulta y toda la
    # parrilla del canal acaba con el mismo b-roll.
    evitar = set(cache.cargar_usados(nicho.nombre))
    if evitar:
        print(f"  🔁 Evitando {len(evitar)} clips ya usados en este canal")

    # Las tomas que comparten consulta se agrupan: así una sola búsqueda
    # sirve para varias, y podemos repartirles clips distintos.
    pendientes = [t for t in sb.tomas if t.visual.fuente == "stock"]

    def trabajo(toma):
        with candado:
            copia_excluidos = set(excluidos)
        ruta = obtener(
            toma.visual.consulta, toma.dur, nicho, vertical, copia_excluidos, evitar
        )
        if ruta:
            with candado:
                excluidos.add(ruta.stem)
            toma.visual.archivo = str(ruta)
            # Punto de entrada estable pero variado dentro del clip origen.
            try:
                dur_fuente = config.duracion(ruta)
                if dur_fuente > toma.dur + 1.0:
                    inicio = ((toma.id * 1.7) % (dur_fuente - toma.dur - 0.5))
                    toma.visual.recorte = [round(inicio, 2), round(inicio + toma.dur, 2)]
            except Exception:
                pass
        return toma, ruta

    resueltas = fallidas = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=hilos) as pool:
        for toma, ruta in pool.map(trabajo, pendientes):
            if ruta:
                resueltas += 1
                cache.marcar_usado(nicho.nombre, ruta.stem, sb.video_id)
            else:
                fallidas += 1
                print(f"    ⚠️  Toma {toma.id} sin asset ('{toma.visual.consulta}')")

    return resueltas, fallidas

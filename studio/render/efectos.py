"""
efectos.py — Construcción de cadenas de filtros de FFmpeg.

Todo lo que la v1 no hacía y que separa "plantilla" de "producido":
movimiento de cámara, color grade con identidad de canal, viñeta y grano.

Cada función devuelve un trozo de filtergraph en texto. El motor los encadena.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from ..niche import Nicho
from ..storyboard import Camara, Toma

# Luminancia media objetivo (escala 0-255) antes de aplicar el grade.
# No buscamos aplanar la imagen: un canal oscuro DEBE verse oscuro. Solo
# evitamos que una toma se vaya a negro puro y no se distinga nada.
LUMA_OBJETIVO = 82.0
LUMA_MINIMA = 38.0        # por debajo de esto, la toma está subexpuesta
GAMMA_MAXIMO = 1.85

_cache_luma: dict[str, float] = {}


def ruta_filtro(ruta: Path | str) -> str:
    """
    Escapa una ruta de Windows para meterla dentro de un filtergraph.

    FFmpeg usa ':' para separar opciones de filtro y '\\' para escapar, así
    que "D:\\x\\y.ass" hay que convertirlo en "D\\:/x/y.ass".
    """
    texto = str(ruta).replace("\\", "/")
    return texto.replace(":", "\\:")


# ── Encuadre ──────────────────────────────────────────────────────────────
def rellenar(ancho: int, alto: int) -> str:
    """
    Escala y recorta para cubrir el frame sin deformar ni dejar bandas.

    `increase` garantiza que el clip cubra el marco; el crop centrado
    devuelve el tamaño exacto.
    """
    return (
        f"scale={ancho}:{alto}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={ancho}:{alto}"
    )


# ── Movimiento de cámara ──────────────────────────────────────────────────
def ken_burns(cam: Camara, ancho: int, alto: int, fps: int, dur: float) -> str:
    """
    Zoom lento sobre imagen fija.

    Dos detalles que importan:
      · Pre-escalamos a ~2.4x antes de zoompan. zoompan trabaja con offsets
        enteros y sobre la resolución nativa produce un temblor visible;
        con más píxeles de origen el salto queda por debajo del píxel final.
      · Interpolamos con `on` (número de frame de salida) en vez de acumular
        `zoom+paso`, que deriva y no llega nunca al valor final exacto.
    """
    n = max(2, int(round(dur * fps)))
    z0, z1 = cam.zoom_desde, cam.zoom_hasta
    z = f"{z0:.4f}+({z1 - z0:.4f})*on/{n - 1}"

    # Hacia dónde se descentra el encuadre mientras hace zoom.
    if cam.origen == "arriba":
        y = "0"
    elif cam.origen == "abajo":
        y = "ih-(ih/zoom)"
    else:
        y = "ih/2-(ih/zoom/2)"
    x = "iw/2-(iw/zoom/2)"

    previo = int(ancho * 2.4) // 2 * 2
    return (
        f"scale={previo}:-2:flags=lanczos,"
        f"zoompan=z='{z}':x='{x}':y='{y}':d=1:s={ancho}x{alto}:fps={fps}"
    )


def push(cam: Camara, ancho: int, alto: int, fps: int, dur: float) -> str:
    """Empuje suave sobre video. Solo si el nicho lo pide: el b-roll ya se mueve."""
    return ken_burns(cam, ancho, alto, fps, dur)


# ── Exposición automática ─────────────────────────────────────────────────
def medir_luma(ruta: Path, inicio: float = 0.0) -> float | None:
    """
    Luminancia media del clip (0-255), medida con signalstats.

    Hace falta porque el b-roll de stock llega con exposiciones muy
    dispares: en un nicho oscuro, la mitad de las búsquedas devuelven
    material tan cerrado que al aplicarle el grade se va a negro puro y no
    se ve nada. Es exactamente lo que le pasaba al render de prueba.
    """
    clave = f"{ruta}|{inicio:.1f}"
    if clave in _cache_luma:
        return _cache_luma[clave]

    from .. import config

    try:
        proceso = subprocess.run(
            [
                config.ffmpeg(), "-hide_banner", "-v", "error",
                "-ss", f"{inicio:.2f}", "-t", "1.0", "-i", str(ruta),
                "-vf", "signalstats,metadata=print:key=lavfi.signalstats.YAVG:file=-",
                "-f", "null", "-",
            ],
            capture_output=True, text=True, timeout=60,
        )
        valores = [
            float(m) for m in re.findall(
                r"lavfi\.signalstats\.YAVG=([\d.]+)", proceso.stdout
            )
        ]
        if not valores:
            return None
        media = sum(valores) / len(valores)
        _cache_luma[clave] = media
        return media
    except Exception:
        return None


def compensar_exposicion(luma: float | None) -> str:
    """
    Devuelve el filtro que sube una toma subexpuesta hasta un nivel legible.

    Usamos gamma en vez de brillo: el brillo desplaza toda la curva y lava
    los negros; la gamma abre las sombras dejando las luces donde están.
    """
    if luma is None or luma >= LUMA_MINIMA:
        return ""
    factor = min(GAMMA_MAXIMO, (LUMA_OBJETIVO / max(luma, 6.0)) ** 0.42)
    if factor <= 1.02:
        return ""
    return f"eq=gamma={factor:.3f}"


# ── Color grade ───────────────────────────────────────────────────────────
def grade(nicho: Nicho) -> str:
    """
    La firma de color del canal.

    Es un puñado de filtros baratos, pero es lo que hace que todos los
    videos se vean del mismo sitio — o sea, branding.
    """
    look = nicho.look
    partes: list[str] = []

    lut = look.get("lut")
    if lut:
        from .. import config

        ruta = config.DIR_LUTS / lut
        if ruta.exists():
            partes.append(f"lut3d=file='{ruta_filtro(ruta)}'")

    sat = float(look.get("saturacion", 1.0))
    con = float(look.get("contraste", 1.0))
    bri = float(look.get("brillo", 0.0))
    gam = float(look.get("gamma", 1.0))
    if (sat, con, bri, gam) != (1.0, 1.0, 0.0, 1.0):
        partes.append(
            f"eq=saturation={sat}:contrast={con}:brightness={bri}:gamma={gam}"
        )

    # Temperatura: negativo = sombras frías (teal), positivo = cálido.
    temp = float(look.get("temperatura", 0.0))
    if abs(temp) > 0.001:
        partes.append(f"colorbalance=rs={temp:.3f}:bs={-temp:.3f}")

    # Negros levantados: mata el negro plano digital, da look de película.
    lift = float(look.get("negros_levantados", 0.0))
    if lift > 0.001:
        partes.append(
            f"colorlevels=romin={lift:.3f}:gomin={lift:.3f}:bomin={lift:.3f}"
        )

    vin = float(look.get("vineta", 0.0))
    if vin > 0.01:
        # 0→sin viñeta, 1→muy marcada. PI/5 .. PI/2.6 es el rango útil.
        angulo = 0.628 + vin * 0.580
        partes.append(f"vignette=a={angulo:.4f}")

    grano = float(look.get("grano", 0.0))
    if grano > 0.001:
        partes.append(f"noise=alls={int(grano * 100)}:allf=t+u")

    return ",".join(partes)


# ── Cadena completa de una toma ───────────────────────────────────────────
def cadena_toma(
    toma: Toma,
    nicho: Nicho,
    ancho: int,
    alto: int,
    fps: int,
    dur: float,
    es_imagen: bool,
    compensacion: str = "",
) -> str:
    """Filtergraph completo para convertir un asset en una toma terminada."""
    partes: list[str] = []

    # La exposición se corrige ANTES del grade, para que el look artístico
    # trabaje siempre sobre una imagen en un rango parecido.
    if compensacion:
        partes.append(compensacion)

    if es_imagen and toma.camara.efecto in ("ken_burns", "push"):
        partes.append(ken_burns(toma.camara, ancho, alto, fps, dur))
    elif (not es_imagen) and toma.camara.efecto == "push":
        partes.append(rellenar(ancho, alto))
        partes.append(push(toma.camara, ancho, alto, fps, dur))
    else:
        partes.append(rellenar(ancho, alto))

    g = grade(nicho)
    if g:
        partes.append(g)

    partes.append(f"fps={fps}")
    partes.append("setsar=1")
    partes.append("format=yuv420p")
    return ",".join(p for p in partes if p)


# ── Traducción de nuestros nombres a los de xfade ─────────────────────────
XFADE = {
    "crossfade": "fade",
    "flash": "fadewhite",
    "fadeblack": "fadeblack",
    "fadewhite": "fadewhite",
    "whip": "slideleft",
    "glitch": "pixelize",
    "circulo": "circleopen",
}


def transicion_xfade(tipo: str) -> str:
    return XFADE.get(tipo, "fade")

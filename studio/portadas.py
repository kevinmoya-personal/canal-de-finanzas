"""
portadas.py — Miniaturas generadas desde el propio video.

La v1 necesitaba que le dibujaras a mano un `portada_base_<id>.png` por cada
video, y encima el borde del texto se pintaba con un bucle de 121 llamadas a
`draw.text()`. Si no habías preparado la base, no había miniatura.

Aquí la miniatura sale del video que acabamos de renderizar: se buscan varios
frames candidatos, se elige el que mejor se ve, y se le compone el texto
encima. Cero trabajo manual por video.

Se generan DOS variantes por video. La miniatura decide si alguien hace clic,
y es lo más barato que puedes probar en A/B.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from . import config
from .niche import Nicho
from .render import efectos

ANCHO, ALTO = 1280, 720


# ── Elección del frame ────────────────────────────────────────────────────
def _puntuar_frame(video: Path, t: float) -> float:
    """
    Qué tan buena es esta marca de tiempo para una miniatura.

    Preferimos frames con brillo medio-alto y mucho contraste: un frame
    oscuro y plano se ve como un rectángulo negro en la parrilla de YouTube.
    """
    try:
        proceso = subprocess.run(
            [
                config.ffmpeg(), "-hide_banner", "-v", "error",
                "-ss", f"{t:.2f}", "-t", "0.2", "-i", str(video),
                "-vf", "signalstats,metadata=print:file=-",
                "-f", "null", "-",
            ],
            capture_output=True, text=True, timeout=45,
        )
        def leer(clave: str) -> float | None:
            m = re.findall(rf"lavfi\.signalstats\.{clave}=([\d.]+)", proceso.stdout)
            return sum(float(x) for x in m) / len(m) if m else None

        brillo = leer("YAVG")
        if brillo is None:
            return 0.0
        # YHIGH-YLOW aproxima el rango dinámico del frame.
        alto_ = leer("YHIGH") or brillo
        bajo = leer("YLOW") or brillo
        contraste = alto_ - bajo

        # Castigamos tanto lo muy oscuro como lo quemado.
        penalizacion = abs(brillo - 115) / 115
        return contraste * (1.0 - min(penalizacion, 0.9))
    except Exception:
        return 0.0


def _candidatos(duracion: float, cuantos: int = 7) -> list[float]:
    """Marcas repartidas por el video, evitando el primer y último segundo."""
    inicio, fin = 1.0, max(1.5, duracion - 1.0)
    if fin <= inicio:
        return [duracion / 2]
    paso = (fin - inicio) / (cuantos + 1)
    return [inicio + paso * (i + 1) for i in range(cuantos)]


def _extraer(video: Path, t: float, destino: Path, nicho: Nicho) -> Path:
    """Saca un frame, lo lleva a 1280x720 y le aplica el grade del canal."""
    filtros = [
        f"scale={ANCHO}:{ALTO}:force_original_aspect_ratio=increase:flags=lanczos",
        f"crop={ANCHO}:{ALTO}",
    ]
    grade = efectos.grade(nicho)
    if grade:
        filtros.append(grade)

    config.correr(
        [
            config.ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{t:.2f}", "-i", str(video),
            "-frames:v", "1", "-vf", ",".join(filtros),
            "-q:v", "2", str(destino),
        ],
        descripcion=f"extrayendo frame en {t:.1f}s",
    )
    return destino


# ── Composición del texto ─────────────────────────────────────────────────
def _fuente(tam: int) -> ImageFont.FreeTypeFont:
    for nombre in ("Anton-Regular.ttf", "Montserrat-ExtraBold.ttf"):
        ruta = config.DIR_FUENTES / nombre
        if ruta.exists():
            return ImageFont.truetype(str(ruta), tam)
    for sistema in ("ariblk.ttf", "impact.ttf", "arialbd.ttf"):
        ruta = config.ruta_fuente(sistema)
        if ruta:
            return ImageFont.truetype(str(ruta), tam)
    return ImageFont.load_default()


def _ajustar(texto: str, ancho_max: int, tam_inicial: int) -> tuple[list[str], ImageFont.FreeTypeFont]:
    """
    Baja el tamaño hasta que el texto quepa.

    Respeta los saltos de línea que traiga el texto de portada; solo parte
    las líneas que aun así se salgan del ancho.
    """
    lineas_base = [l.strip() for l in texto.upper().split("\n") if l.strip()]

    for tam in range(tam_inicial, 40, -4):
        fuente = _fuente(tam)
        lineas: list[str] = []
        for base in lineas_base:
            palabras = base.split()
            actual = ""
            for palabra in palabras:
                prueba = f"{actual} {palabra}".strip()
                if fuente.getlength(prueba) <= ancho_max:
                    actual = prueba
                else:
                    if actual:
                        lineas.append(actual)
                    actual = palabra
            if actual:
                lineas.append(actual)

        alto_total = len(lineas) * (tam + 12)
        if alto_total <= ALTO * 0.55 and all(
            fuente.getlength(l) <= ancho_max for l in lineas
        ):
            return lineas, fuente

    return lineas_base, _fuente(44)


def _componer(
    frame: Path, texto: str, nicho: Nicho, destino: Path, variante: int
) -> Path:
    img = Image.open(frame).convert("RGB")

    # Un poco más de punch que el video: la miniatura se ve en miniatura.
    img = ImageEnhance.Contrast(img).enhance(1.12)
    img = ImageEnhance.Color(img).enhance(1.10)

    cfg = nicho.subtitulos
    color = "#" + cfg.get("color_activo", "FFD700")
    borde = "#" + cfg.get("color_borde", "000000")

    margen = int(ANCHO * 0.06)
    lineas, fuente = _ajustar(texto, ANCHO - margen * 2, 150)
    alto_linea = fuente.size + 12
    alto_bloque = len(lineas) * alto_linea

    # Variante 0: texto abajo. Variante 1: arriba. Cambiar la posición prueba
    # dos composiciones distintas sin tocar el resto.
    arriba = variante % 2 == 1
    y = margen if arriba else ALTO - alto_bloque - margen

    # Oscurecemos SOLO la banda del texto, con degradado, para que se lea sin
    # apagar la imagen entera.
    velo = Image.new("L", (1, ALTO), 0)
    pintor_velo = ImageDraw.Draw(velo)
    borde_banda = int(alto_bloque + margen * 1.6)
    for i in range(borde_banda):
        alfa = int(190 * (1 - i / borde_banda) ** 0.8)
        fila = i if arriba else ALTO - 1 - i
        pintor_velo.point((0, fila), fill=alfa)
    mascara = velo.resize((ANCHO, ALTO)).filter(ImageFilter.GaussianBlur(2))
    img = Image.composite(Image.new("RGB", img.size, (0, 0, 0)), img, mascara)

    pintor = ImageDraw.Draw(img)
    for linea in lineas:
        x = (ANCHO - fuente.getlength(linea)) / 2
        # stroke_width hace en una llamada lo que la v1 hacía con 121.
        pintor.text(
            (x, y), linea, font=fuente, fill=color,
            stroke_width=max(4, fuente.size // 16), stroke_fill=borde,
        )
        y += alto_linea

    destino.parent.mkdir(parents=True, exist_ok=True)
    img.save(destino, "JPEG", quality=92, optimize=True)
    return destino


# ── API pública ───────────────────────────────────────────────────────────
def generar(
    video: Path,
    nicho: Nicho,
    texto: str,
    video_id: str,
    variantes: int = 2,
) -> list[Path]:
    """
    Crea las miniaturas de un video. Devuelve las rutas, la mejor primero.

    YouTube acepta hasta 2 MB por miniatura; a 1280x720 con calidad 92 nos
    quedamos muy por debajo.
    """
    # ⭐ Si existe el montaje sin subtítulos (etapa 2 del render), sacamos el
    # frame de ahí. Del video final saldría con el karaoke quemado encima, y
    # una miniatura con los subtítulos del propio video se ve descuidada.
    mudo = config.DIR_TRABAJO / nicho.nombre / video_id / "video_mudo.mp4"
    origen = mudo if mudo.exists() else video
    if origen is video:
        print("  ⚠️  Sin montaje intermedio: la miniatura puede salir con subtítulos.")

    duracion = config.duracion(origen)
    marcas = _candidatos(duracion)

    print(f"  🖼️  Evaluando {len(marcas)} frames candidatos…")
    puntuadas = sorted(
        ((t, _puntuar_frame(origen, t)) for t in marcas),
        key=lambda p: p[1], reverse=True,
    )

    tmp = config.DIR_TRABAJO / nicho.nombre / video_id / "portadas"
    tmp.mkdir(parents=True, exist_ok=True)
    salida = nicho.dir_salida / "portadas"

    resultados: list[Path] = []
    for i in range(min(variantes, len(puntuadas))):
        t, puntos = puntuadas[i]
        frame = _extraer(origen, t, tmp / f"frame_{i}.jpg", nicho)
        destino = salida / f"{video_id}_{chr(97 + i)}.jpg"
        resultados.append(_componer(frame, texto, nicho, destino, i))
        print(f"     {chr(97 + i)}) frame en {t:.1f}s (calidad {puntos:.0f}) → {destino.name}")

    return resultados

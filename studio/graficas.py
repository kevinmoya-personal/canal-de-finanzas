"""
graficas.py — B-roll propio: gráficas animadas con los datos del guion.

Por qué existe: el stock tiene techo. Con ~25 keywords y ~15 clips por
búsqueda, un canal que publica varios videos al día agota los clips
distintos en un par de semanas y empieza a repetirse. Una gráfica hecha con
los números del propio guion es única por construcción, refuerza la promesa
"con datos" y no depende de ninguna API.

El guionista rellena, en las secciones que tienen números, un campo
`grafica`:
    {"tipo": "barras" | "numero" | "ninguna",
     "titulo": "...", "etiquetas": [...], "valores": [...],
     "prefijo": "$", "sufijo": " %"}

`storyboard.construir` convierte la primera toma de esa sección en
`Visual(fuente="grafica")` y aquí se renderiza como un mp4 de la duración
exacta de la toma: las barras crecen, la cifra cuenta hacia arriba.

La gráfica ocupa la parte superior del cuadro: los subtítulos karaoke van
a la altura `sub_pos_y` del formato y no deben taparla.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from . import config
from .niche import Nicho

if TYPE_CHECKING:   # storyboard importa este módulo: evitar el ciclo
    from .storyboard import Storyboard

FONDO_1 = (11, 31, 58)
FONDO_2 = (15, 52, 92)
VERDE = (43, 217, 124)
AZUL_CLARO = (120, 160, 210)
BLANCO = (255, 255, 255)
GRIS = (190, 204, 222)

ANIM_SEG = 1.3          # lo que tarda en crecer; el resto de la toma queda quieta


# ── Utilidades ────────────────────────────────────────────────────────────
def _fuente(nombre: str, tam: int):
    from PIL import ImageFont

    ruta = config.ruta_fuente(nombre) or config.ruta_fuente("arialbd.ttf")
    return ImageFont.truetype(str(ruta), tam)


def _suave(t: float) -> float:
    """easeOutCubic: arranca rápido y frena al llegar."""
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def formatear(valor: float, prefijo: str = "", sufijo: str = "") -> str:
    """Formato colombiano: punto de miles, coma decimal."""
    if abs(valor - round(valor)) < 1e-9 or abs(valor) >= 100:
        cuerpo = f"{round(valor):,}".replace(",", ".")
    else:
        cuerpo = f"{valor:,.1f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{prefijo}{cuerpo}{sufijo}"


def es_valida(g: dict | None) -> bool:
    if not g or g.get("tipo") not in ("barras", "numero"):
        return False
    valores = g.get("valores") or []
    if g["tipo"] == "numero":
        return len(valores) >= 1
    return 2 <= len(valores) <= 6 and len(valores) == len(g.get("etiquetas") or [])


# ── Dibujo de un cuadro ───────────────────────────────────────────────────
def _fondo(ancho: int, alto: int):
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (ancho, alto), FONDO_1)
    d = ImageDraw.Draw(img)
    for y in range(alto):
        t = y / alto
        d.line([(0, y), (ancho, y)],
               fill=tuple(int(FONDO_1[i] + (FONDO_2[i] - FONDO_1[i]) * t) for i in range(3)))
    paso = max(ancho, alto) // 21
    for x in range(0, ancho, paso):
        d.line([(x, 0), (x, alto)], fill=(28, 50, 80))
    for y in range(0, alto, paso):
        d.line([(0, y), (ancho, y)], fill=(28, 50, 80))

    # Firma del canal al pie, bajo la zona de subtítulos.
    f = _fuente("Anton-Regular.ttf", int(46 * ancho / 1080))
    a, b = "PLATA CON ", "DATOS"
    x = (ancho - d.textlength(a + b, font=f)) / 2
    y = int(alto * 0.90)
    d.text((x, y), a, font=f, fill=BLANCO)
    d.text((x + d.textlength(a, font=f), y), b, font=f, fill=VERDE)
    return img


def _titulo(d, texto: str, ancho: int, y: int, tam: int) -> int:
    """Titular centrado en una o dos líneas. Devuelve la y siguiente."""
    f = _fuente("Anton-Regular.ttf", tam)
    palabras, lineas, actual = texto.upper().split(), [], ""
    for p in palabras:
        prueba = f"{actual} {p}".strip()
        if d.textlength(prueba, font=f) <= ancho * 0.86:
            actual = prueba
        else:
            lineas.append(actual)
            actual = p
    lineas.append(actual)
    for linea in lineas[:2]:
        d.text(((ancho - d.textlength(linea, font=f)) / 2, y), linea, font=f, fill=BLANCO)
        y += int(tam * 1.15)
    return y


def _cuadro_barras(g: dict, t: float, ancho: int, alto: int, zona_alto: int):
    from PIL import ImageDraw

    img = _fondo(ancho, alto)
    d = ImageDraw.Draw(img)
    escala = ancho / 1080
    y = _titulo(d, g.get("titulo", ""), ancho, int(zona_alto * 0.08), int(70 * escala))

    valores = [float(v) for v in g["valores"]]
    etiquetas = g["etiquetas"]
    n = len(valores)
    tope = max(valores) or 1.0
    destacada = valores.index(max(valores))

    base = int(zona_alto * 0.92)
    techo = y + int(110 * escala)
    alto_max = base - techo
    margen = int(ancho * 0.1)
    hueco = (ancho - 2 * margen) / n
    ancho_barra = hueco * 0.62

    f_val = _fuente("Anton-Regular.ttf", int(54 * escala))
    f_etq = _fuente("segoeuib.ttf", int(36 * escala))
    d.line([(margen, base), (ancho - margen, base)], fill=GRIS, width=max(2, int(3 * escala)))

    for i, (v, etq) in enumerate(zip(valores, etiquetas)):
        # Cada barra arranca un poco después que la anterior: lectura de izquierda a derecha.
        progreso = _suave((t - i * 0.12) / ANIM_SEG)
        h = alto_max * (v / tope) * progreso
        x0 = margen + i * hueco + (hueco - ancho_barra) / 2
        color = VERDE if i == destacada else AZUL_CLARO
        if h > 1:
            d.rounded_rectangle([x0, base - h, x0 + ancho_barra, base],
                                radius=int(10 * escala), fill=color)
        if progreso > 0.05:
            texto = formatear(v * progreso, g.get("prefijo", ""), g.get("sufijo", ""))
            w = d.textlength(texto, font=f_val)
            d.text((x0 + ancho_barra / 2 - w / 2, base - h - int(70 * escala)), texto,
                   font=f_val, fill=BLANCO)
        w = d.textlength(etq, font=f_etq)
        if w > hueco * 0.95:
            f_peq = _fuente("segoeuib.ttf", int(36 * escala * hueco * 0.95 / w))
            w = d.textlength(etq, font=f_peq)
            d.text((x0 + ancho_barra / 2 - w / 2, base + int(16 * escala)), etq, font=f_peq, fill=GRIS)
        else:
            d.text((x0 + ancho_barra / 2 - w / 2, base + int(16 * escala)), etq, font=f_etq, fill=GRIS)
    return img


def _cuadro_numero(g: dict, t: float, ancho: int, alto: int, zona_alto: int):
    from PIL import ImageDraw

    img = _fondo(ancho, alto)
    d = ImageDraw.Draw(img)
    escala = ancho / 1080
    y = _titulo(d, g.get("titulo", ""), ancho, int(zona_alto * 0.18), int(64 * escala))

    valor = float(g["valores"][0]) * _suave(t / ANIM_SEG)
    texto = formatear(valor, g.get("prefijo", ""), g.get("sufijo", ""))
    final = formatear(float(g["valores"][0]), g.get("prefijo", ""), g.get("sufijo", ""))
    # El tamaño se fija con la cifra final para que no "baile" mientras cuenta.
    tam = int(200 * escala)
    while tam > 60 and d.textlength(final, font=_fuente("Anton-Regular.ttf", tam)) > ancho * 0.86:
        tam -= 8
    f = _fuente("Anton-Regular.ttf", tam)
    y += int(60 * escala)
    d.text(((ancho - d.textlength(texto, font=f)) / 2, y), texto, font=f, fill=VERDE)
    y += int(tam * 1.25)

    nota = (g.get("etiquetas") or [""])[0]
    if nota:
        f_n = _fuente("segoeuisl.ttf", int(44 * escala))
        d.text(((ancho - d.textlength(nota, font=f_n)) / 2, y), nota, font=f_n, fill=GRIS)
    return img


# ── Render a mp4 ──────────────────────────────────────────────────────────
def renderizar(g: dict, ancho: int, alto: int, fps: int, dur: float,
               sub_pos_y: float, destino: Path) -> Path:
    """Escribe un mp4 de `dur` segundos con la gráfica animándose."""
    # La gráfica vive por encima de los subtítulos.
    zona_alto = int(alto * max(0.45, sub_pos_y - 0.06))
    dibujar = _cuadro_barras if g["tipo"] == "barras" else _cuadro_numero

    frames = max(1, int(round(dur * fps)))
    proceso = subprocess.Popen(
        [config.ffmpeg(), "-v", "error", "-y",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{ancho}x{alto}", "-r", str(fps),
         "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
         "-pix_fmt", "yuv420p", str(destino)],
        stdin=subprocess.PIPE,
    )
    ultimo = None
    for i in range(frames):
        t = i / fps
        # Terminada la animación, el cuadro no cambia: se reutiliza.
        if t <= ANIM_SEG + 0.8 or ultimo is None:
            ultimo = dibujar(g, t, ancho, alto, zona_alto).tobytes()
        proceso.stdin.write(ultimo)
    proceso.stdin.close()
    if proceso.wait() != 0:
        raise RuntimeError(f"FFmpeg no pudo escribir la gráfica {destino.name}")
    return destino


def resolver(sb: "Storyboard", nicho: Nicho, trabajo: Path) -> int:
    """Renderiza las tomas de gráfica del storyboard. Devuelve cuántas."""
    tomas = [t for t in sb.tomas if t.visual.fuente == "grafica"]
    if not tomas:
        return 0
    formato = nicho.formato(sb.formato)
    carpeta = trabajo / "graficas"
    carpeta.mkdir(exist_ok=True)

    for toma in tomas:
        g = json.loads(toma.visual.consulta)
        # Algo más larga que la toma: la transición de salida se come un trozo.
        dur = toma.dur + 0.6
        firma = hashlib.sha1(f"{toma.visual.consulta}|{dur:.2f}|{sb.ancho}x{sb.alto}"
                             .encode()).hexdigest()[:10]
        destino = carpeta / f"grafica_{toma.id:04d}_{firma}.mp4"
        if not destino.exists():
            renderizar(g, sb.ancho, sb.alto, sb.fps, dur, formato.sub_pos_y, destino)
        toma.visual.archivo = str(destino)
    return len(tomas)

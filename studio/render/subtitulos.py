"""
subtitulos.py — Subtítulos karaoke en formato ASS.

La v1 dibujaba el texto con Pillow: creaba un PNG por sección y, para el
borde, hacía un bucle de hasta 121 llamadas a `draw.text()` por línea
(ensamblador.py:118-121). Además mostraba el párrafo entero, estático,
durante los 40 segundos de la sección.

Aquí generamos un archivo .ass que libass renderiza en C dentro de FFmpeg:
  · dos órdenes de magnitud más rápido
  · antialiasing y kerning reales
  · 2-4 palabras en pantalla, con la palabra que SUENA resaltada

Formato ASS, cosas a saber:
  · Los colores van como &HAABBGGRR — invertido respecto a HTML, y con
    alfa delante (00 = opaco).
  · \\an5 = anclado al centro-medio; con \\pos(x,y) situamos el bloque.
  · \\t(t0,t1,tags) interpola: lo usamos para el rebote de entrada.
"""
from __future__ import annotations

from pathlib import Path

from ..niche import Nicho
from ..storyboard import Storyboard

# Cuánto se queda el último grupo en pantalla tras dejar de sonar, para que
# no parpadee en las pausas.
COLA_SEGUNDOS = 0.35


# ── Utilidades de formato ─────────────────────────────────────────────────
def _color_ass(hex_rgb: str, alfa: str = "00") -> str:
    """'FFD700' → '&H0000D7FF' (ASS usa AABBGGRR)."""
    h = hex_rgb.lstrip("#").strip()
    if len(h) != 6:
        h = "FFFFFF"
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alfa}{b}{g}{r}"


def _tiempo_ass(t: float) -> str:
    """Segundos → H:MM:SS.cc (centisegundos, como manda el formato)."""
    t = max(0.0, t)
    horas, resto = divmod(t, 3600)
    minutos, segundos = divmod(resto, 60)
    centis = int(round((segundos - int(segundos)) * 100))
    seg_int = int(segundos)
    if centis == 100:            # redondeo al alza que se desborda
        centis = 0
        seg_int += 1
    return f"{int(horas):d}:{int(minutos):02d}:{seg_int:02d}.{centis:02d}"


def _escapar(texto: str) -> str:
    """Neutraliza los caracteres que ASS interpreta como marcado."""
    return (
        texto.replace("\\", "∖")
        .replace("{", "(")
        .replace("}", ")")
        .replace("\n", " ")
    )


# ── Generación ────────────────────────────────────────────────────────────
def generar(sb: Storyboard, nicho: Nicho, destino: Path) -> Path:
    """Escribe el .ass del video completo a partir del storyboard."""
    cfg = nicho.subtitulos
    formato = nicho.formato(sb.formato)

    fuente = cfg.get("fuente", "Arial Black")
    tam = formato.sub_tam
    color_base = _color_ass(cfg.get("color_base", "FFFFFF"))
    color_activo = _color_ass(cfg.get("color_activo", "FFD700"))
    color_borde = _color_ass(cfg.get("color_borde", "000000"))
    grosor = int(cfg.get("grosor_borde", 5))
    sombra = int(cfg.get("sombra", 3))
    mayusculas = bool(cfg.get("mayusculas", True))
    pop = float(cfg.get("pop_escala", 1.09))
    pop_ms = int(cfg.get("pop_ms", 110))

    margen = int(sb.ancho * 0.07)
    pos_x = sb.ancho // 2
    pos_y = int(sb.alto * formato.sub_pos_y)

    cabecera = f"""[Script Info]
; Generado por video-studio v2
ScriptType: v4.00+
PlayResX: {sb.ancho}
PlayResY: {sb.alto}
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Karaoke,{fuente},{tam},{color_base},{color_activo},{color_borde},&H80000000,0,0,0,0,100,100,1,0,1,{grosor},{sombra},5,{margen},{margen},0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    # Aplanamos todos los grupos del video en una sola línea de tiempo, para
    # poder calcular cuándo entra el siguiente y evitar parpadeos.
    grupos = [g for toma in sb.tomas for g in toma.subtitulos]

    lineas: list[str] = []
    for i, grupo in enumerate(grupos):
        palabras = grupo.palabras
        inicio_siguiente = (
            grupos[i + 1].palabras[0]["t_in"]
            if i + 1 < len(grupos)
            else sb.duracion
        )

        textos = [
            (p["texto"].upper() if mayusculas else p["texto"]) for p in palabras
        ]
        textos = [_escapar(t) for t in textos]

        for j, palabra in enumerate(palabras):
            t_in = palabra["t_in"]
            # Cada palabra se muestra hasta que entra la siguiente: sin
            # huecos, sin parpadeo entre palabras de un mismo grupo.
            if j + 1 < len(palabras):
                t_out = palabras[j + 1]["t_in"]
            else:
                t_out = min(inicio_siguiente, palabra["t_out"] + COLA_SEGUNDOS)
            if t_out <= t_in:
                continue

            # Solo la palabra activa cambia de color. El tamaño NO cambia por
            # palabra: escalar una palabra suelta reflota la línea entera y
            # produce un temblor muy feo.
            partes = []
            for k, texto in enumerate(textos):
                color = color_activo if k == j else color_base
                partes.append(f"{{\\c{color}}}{texto}")
            cuerpo = " ".join(partes)

            # El rebote se aplica una sola vez, al entrar el grupo.
            if j == 0 and pop > 1.0:
                escala = int(pop * 100)
                animacion = (
                    f"\\fscx100\\fscy100"
                    f"\\t(0,{pop_ms},\\fscx{escala}\\fscy{escala})"
                    f"\\t({pop_ms},{pop_ms * 2},\\fscx100\\fscy100)"
                )
            else:
                animacion = ""

            prefijo = f"{{\\an5\\pos({pos_x},{pos_y}){animacion}}}"
            lineas.append(
                f"Dialogue: 0,{_tiempo_ass(t_in)},{_tiempo_ass(t_out)},"
                f"Karaoke,,0,0,0,,{prefijo}{cuerpo}"
            )

    destino.parent.mkdir(parents=True, exist_ok=True)
    # libass espera UTF-8; el BOM le molesta.
    destino.write_text(cabecera + "\n".join(lineas) + "\n", encoding="utf-8")
    return destino


def contar_eventos(ruta: Path) -> int:
    return sum(
        1 for l in ruta.read_text(encoding="utf-8").splitlines()
        if l.startswith("Dialogue:")
    )

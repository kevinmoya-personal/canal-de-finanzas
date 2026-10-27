"""
publicaciones.py — Publicaciones de imagen para la página de Facebook.

Flujo:  Gemini propone N candidatas → cada una trae su autoevaluación con
criterios de alcance → se eligen las mejores (de formatos distintos) →
se dibuja una tarjeta 1080×1350 con la marca del canal → texto + imagen
listos para `facebook.enviar`.

╔══════════════════════════════════════════════════════════════════════╗
║  Lo que NO hacemos: cebo de interacción                              ║
║                                                                      ║
║  Facebook rebaja el alcance de lo que pide reacciones, comentarios,  ║
║  compartidos o etiquetas a cambio de nada ("comenta SÍ", "comparte   ║
║  si estás de acuerdo", "etiqueta a alguien"). El alcance aquí se     ║
║  busca con contenido que la gente quiere guardar y mandar por sí     ║
║  misma: datos que sorprenden, listas útiles, situaciones con las     ║
║  que se identifica.                                                  ║
╚══════════════════════════════════════════════════════════════════════╝

La autoevaluación del modelo es una heurística, no una predicción: sirve
para descartar lo flojo. Lo que de verdad funciona lo dirán las métricas
de la página después de unas semanas.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from . import config
from .niche import Nicho

FORMATOS = ["dato", "lista", "mito", "situacion", "pregunta", "comparacion"]
CRITERIOS = ["identificacion", "utilidad", "guardable", "compartible", "sorpresa"]

ETIQUETAS = {
    "dato": "DATO",
    "lista": "GUÁRDALO",
    "mito": "MITO VS. REALIDAD",
    "situacion": "¿TE HA PASADO?",
    "pregunta": "PREGUNTA",
    "comparacion": "COMPARA",
}

ANCHO, ALTO = 1080, 1350          # 4:5, el formato que más ocupa en el feed
FONDO_1 = (11, 31, 58)            # azul marino del logo
FONDO_2 = (15, 52, 92)
VERDE = (43, 217, 124)
BLANCO = (255, 255, 255)
GRIS = (190, 204, 222)


# ── Esquema y prompt ──────────────────────────────────────────────────────
def _esquema() -> dict:
    return {
        "type": "object",
        "properties": {
            "candidatas": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "formato": {"type": "string", "enum": FORMATOS},
                        "titular": {"type": "string", "description":
                                    "Texto grande de la tarjeta. Máximo 12 palabras."},
                        "subtitulo": {"type": "string", "description":
                                      "Una línea de apoyo bajo el titular. Máximo 20 palabras. "
                                      "Puede ir vacío."},
                        "puntos": {"type": "array", "items": {"type": "string"},
                                   "description": "Solo para lista/comparacion/mito: 3 a 5 "
                                   "puntos de máximo 9 palabras. Vacío en los demás. En "
                                   "comparacion y mito, el último punto es la conclusión "
                                   "con el número que sorprende."},
                        "texto": {"type": "string", "description":
                                  "Texto de la publicación (caption)."},
                        "hashtags": {"type": "array", "items": {"type": "string"}},
                        "puntajes": {
                            "type": "object",
                            "properties": {c: {"type": "integer"} for c in CRITERIOS},
                            "required": CRITERIOS,
                        },
                    },
                    "required": ["formato", "titular", "subtitulo", "puntos",
                                 "texto", "hashtags", "puntajes"],
                },
            }
        },
        "required": ["candidatas"],
    }


INSTRUCCIONES = """\
Eres el community manager de "{titulo}", una página de Facebook de finanzas
personales explicadas con datos reales, para público colombiano y
latinoamericano.

Propón {n} publicaciones de imagen distintas. Cada una es una tarjeta (titular
grande + subtítulo + hasta cinco puntos) más el texto que la acompaña.

QUÉ HACE QUE UNA PUBLICACIÓN DE FINANZAS LLEGUE LEJOS EN FACEBOOK
- Identificación: la persona piensa "esto me pasa a mí" (la quincena que no
  alcanza, la prima que se esfuma, la tarjeta que nunca baja).
- Utilidad inmediata: algo que puede aplicar hoy. Las listas que dan ganas
  de guardar se comparten solas.
- Sorpresa con números: un cálculo sencillo que cambia cómo se ve algo
  cotidiano (cuánto suma un gasto pequeño en un año, cuánto cuesta de verdad
  una cuota).
- Que alguien quiera mandarla a su pareja, a su mamá o al grupo de la familia
  por iniciativa propia.

PROHIBIDO (Facebook baja el alcance de esto)
- Pedir reacciones, comentarios, compartidos, etiquetas o votos a cambio de
  nada: "comenta SÍ", "dale like si", "comparte si estás de acuerdo",
  "etiqueta a alguien que...". Una pregunta genuina al final sí vale.
- Clickbait que la tarjeta no cumple, promesas de hacerse rico, mayúsculas
  sostenidas en el texto.

REGLAS DE CONTENIDO
- Solo cifras ampliamente conocidas y verificables, o cálculos que cualquiera
  puede rehacer con una calculadora. Si dudas de una cifra, no la uses.
  Nunca inventes estudios, encuestas ni porcentajes.
- Educa, no recomiendes: nada de bancos, acciones, criptomonedas ni productos
  concretos.
- Aquí SÍ se escriben las cifras con dígitos y en formato colombiano:
  $1.500.000, 20 %, 12 meses.
- Ortografía completa del español con tildes. Sin emojis en la tarjeta; en el
  texto, como mucho uno o dos.

EL TEXTO DE LA PUBLICACIÓN
- La primera línea es lo único que se ve antes de "Ver más": tiene que
  enganchar sola.
- Entre 40 y 110 palabras, párrafos cortos.
- Puede cerrar con una pregunta genuina o con "Guárdalo para tu próxima
  quincena" si es una lista útil.
- Dos o tres hashtags relevantes, sin #.

AUTOEVALUACIÓN
Puntúa cada candidata de 1 a 10 en: {criterios}. Sé exigente: un 9 o un 10
solo si de verdad la mandarías al grupo de tu familia.

Usa formatos variados entre las candidatas.
{previas}
Devuelve únicamente el JSON del esquema."""


@dataclass
class Publicacion:
    formato: str
    titular: str
    subtitulo: str
    puntos: list[str]
    texto: str
    hashtags: list[str]
    puntajes: dict[str, int]
    imagen: Path | None = None

    @property
    def puntaje(self) -> float:
        return sum(self.puntajes.values()) / max(len(self.puntajes), 1)

    @property
    def texto_completo(self) -> str:
        etiquetas = " ".join("#" + h.lstrip("#").replace(" ", "") for h in self.hashtags[:3])
        return f"{self.texto.strip()}\n\n{etiquetas}".strip()


def _dir_posts(nicho: Nicho) -> Path:
    d = nicho.dir_salida / "posts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _historial(nicho: Nicho) -> Path:
    return _dir_posts(nicho) / "historial.jsonl"


def _titulares_previos(nicho: Nicho, limite: int = 40) -> list[str]:
    ruta = _historial(nicho)
    if not ruta.exists():
        return []
    lineas = ruta.read_text(encoding="utf-8").splitlines()[-limite:]
    return [json.loads(l)["titular"] for l in lineas if l.strip()]


# ── Generación ────────────────────────────────────────────────────────────
def proponer(nicho: Nicho, cuantas: int = 2, candidatas: int = 6) -> list[Publicacion]:
    """Pide `candidatas` a Gemini y devuelve las `cuantas` mejores, de formatos distintos."""
    from .guionista import _generar_gemini

    previos = _titulares_previos(nicho)
    previas = ""
    if previos:
        previas = ("\nYA PUBLICADAS — no repitas ninguna ni su idea central:\n"
                   + "\n".join(f"- {t}" for t in previos) + "\n")

    sistema = INSTRUCCIONES.format(
        titulo=nicho.titulo, n=candidatas, criterios=", ".join(CRITERIOS), previas=previas,
    )
    datos, uso = _generar_gemini(
        sistema, f"Propón {candidatas} publicaciones nuevas.", _esquema()
    )
    print(f"  🤖 {len(datos['candidatas'])} candidatas · {uso}")

    todas = [Publicacion(**c) for c in datos["candidatas"]]
    todas.sort(key=lambda p: p.puntaje, reverse=True)

    elegidas: list[Publicacion] = []
    for p in todas:                      # primero, formatos distintos
        if len(elegidas) < cuantas and p.formato not in {e.formato for e in elegidas}:
            elegidas.append(p)
    for p in todas:                      # si faltan, las siguientes mejores
        if len(elegidas) < cuantas and p not in elegidas:
            elegidas.append(p)

    for p in todas:
        marca = "✅" if p in elegidas else "  "
        print(f"   {marca} {p.puntaje:4.1f}  [{p.formato}] {p.titular}")
    return elegidas


def producir(nicho: Nicho, cuantas: int = 2, prefijo: str | None = None) -> list[Publicacion]:
    """Genera, dibuja y registra `cuantas` publicaciones."""
    prefijo = prefijo or date.today().isoformat()
    elegidas = proponer(nicho, cuantas)
    for i, p in enumerate(elegidas, 1):
        p.imagen = dibujar(p, nicho, _dir_posts(nicho) / f"{prefijo}_{i}.png")
        (p.imagen.with_suffix(".txt")).write_text(p.texto_completo, encoding="utf-8")
        with open(_historial(nicho), "a", encoding="utf-8") as f:
            f.write(json.dumps({"fecha": prefijo, "formato": p.formato,
                                "titular": p.titular, "puntaje": round(p.puntaje, 1),
                                "imagen": str(p.imagen)}, ensure_ascii=False) + "\n")
        print(f"  🖼️  {p.imagen.name}")
    return elegidas


# ── Dibujo de la tarjeta ──────────────────────────────────────────────────
def _fuente(nombre: str, tam: int):
    from PIL import ImageFont

    ruta = config.ruta_fuente(nombre) or config.ruta_fuente("arialbd.ttf")
    return ImageFont.truetype(str(ruta), tam)


def _envolver(draw, texto: str, fuente, ancho_max: int) -> list[str]:
    """Parte el texto en líneas que caben en `ancho_max` píxeles."""
    palabras, lineas, actual = texto.split(), [], ""
    for p in palabras:
        prueba = f"{actual} {p}".strip()
        if draw.textlength(prueba, font=fuente) <= ancho_max:
            actual = prueba
        else:
            if actual:
                lineas.append(actual)
            actual = p
    if actual:
        lineas.append(actual)
    return lineas


def _titular_ajustado(draw, texto: str, ancho_max: int, alto_max: int):
    """El tamaño más grande al que el titular cabe en la caja."""
    for tam in range(132, 56, -4):
        fuente = _fuente("Anton-Regular.ttf", tam)
        lineas = _envolver(draw, texto.upper(), fuente, ancho_max)
        alto = len(lineas) * int(tam * 1.12)
        if alto <= alto_max:
            return fuente, lineas, int(tam * 1.12)
    fuente = _fuente("Anton-Regular.ttf", 56)
    return fuente, _envolver(draw, texto.upper(), fuente, ancho_max), 63


def dibujar(p: Publicacion, nicho: Nicho, destino: Path) -> Path:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (ANCHO, ALTO), FONDO_1)
    d = ImageDraw.Draw(img)

    # Degradado vertical y cuadrícula sutil, como el banner del canal.
    for y in range(ALTO):
        t = y / ALTO
        color = tuple(int(FONDO_1[i] + (FONDO_2[i] - FONDO_1[i]) * t) for i in range(3))
        d.line([(0, y), (ANCHO, y)], fill=color)
    rejilla = Image.new("RGBA", (ANCHO, ALTO), (0, 0, 0, 0))
    dr = ImageDraw.Draw(rejilla)
    for x in range(0, ANCHO, 90):
        dr.line([(x, 0), (x, ALTO)], fill=(255, 255, 255, 14))
    for y in range(0, ALTO, 90):
        dr.line([(0, y), (ANCHO, y)], fill=(255, 255, 255, 14))
    img = Image.alpha_composite(img.convert("RGBA"), rejilla)
    d = ImageDraw.Draw(img)

    margen = 80
    ancho_util = ANCHO - 2 * margen

    # Etiqueta del formato.
    etiqueta = ETIQUETAS.get(p.formato, "FINANZAS")
    f_etq = _fuente("segoeuib.ttf", 34)
    w = d.textlength(etiqueta, font=f_etq)
    d.rounded_rectangle([margen, 90, margen + w + 48, 150], radius=30, fill=VERDE)
    d.text((margen + 24, 96), etiqueta, font=f_etq, fill=FONDO_1)

    # Titular, tan grande como quepa.
    hay_puntos = bool(p.puntos)
    caja_titular = 430 if hay_puntos else 620
    f_tit, lineas, paso = _titular_ajustado(d, p.titular, ancho_util, caja_titular)
    y = 200
    for linea in lineas:
        d.text((margen, y), linea, font=f_tit, fill=BLANCO)
        y += paso
    # Anton tiene descendentes y acentos altos: la caja real de la última
    # línea baja más que el paso de línea.
    y = int(d.textbbox((margen, y - paso), lineas[-1], font=f_tit)[3]) + 28
    d.rectangle([margen, y, margen + 140, y + 10], fill=VERDE)
    y += 50

    # Subtítulo.
    if p.subtitulo.strip():
        f_sub = _fuente("segoeuisl.ttf", 42)
        for linea in _envolver(d, p.subtitulo, f_sub, ancho_util)[:4]:
            d.text((margen, y), linea, font=f_sub, fill=GRIS)
            y += 56
        y += 30

    # Puntos.
    f_pto = _fuente("segoeuib.ttf", 40)
    for i, punto in enumerate(p.puntos[:5], 1):
        if y > ALTO - 260:
            break
        d.ellipse([margen, y + 4, margen + 48, y + 52], fill=VERDE)
        num = str(i)
        d.text((margen + 24 - d.textlength(num, font=f_pto) / 2, y + 2), num,
               font=f_pto, fill=FONDO_1)
        for j, linea in enumerate(_envolver(d, punto, f_pto, ancho_util - 76)[:2]):
            d.text((margen + 76, y + j * 50), linea, font=f_pto, fill=BLANCO)
            if j:
                y += 50
        y += 78

    # Pie de marca.
    d.line([(margen, ALTO - 150), (ANCHO - margen, ALTO - 150)], fill=(255, 255, 255, 60), width=2)
    f_marca = _fuente("Anton-Regular.ttf", 54)
    d.text((margen, ALTO - 125), "PLATA CON", font=f_marca, fill=BLANCO)
    d.text((margen + d.textlength("PLATA CON ", font=f_marca), ALTO - 125), "DATOS",
           font=f_marca, fill=VERDE)
    f_user = _fuente("segoeui.ttf", 34)
    usuario = "@PlataconDatos"
    d.text((ANCHO - margen - d.textlength(usuario, font=f_user), ALTO - 110), usuario,
           font=f_user, fill=GRIS)

    destino.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(destino, "PNG", optimize=True)
    return destino

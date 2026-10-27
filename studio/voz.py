"""
voz.py — Síntesis de voz CON timings palabra por palabra.

╔══════════════════════════════════════════════════════════════════════╗
║  Este módulo es la razón por la que la v2 puede hacer karaoke.       ║
║                                                                      ║
║  La v1 llamaba a edge-tts por consola:                               ║
║      subprocess.run(["python","-m","edge_tts","--write-media",...])  ║
║  y se quedaba solo con el .mp3. Al hacerlo, tiraba a la basura los   ║
║  eventos WordBoundary, que traen el offset y la duración EXACTOS de  ║
║  cada palabra pronunciada.                                           ║
║                                                                      ║
║  Usando la librería en streaming los recuperamos gratis: sin         ║
║  Whisper, sin GPU, sin costo, y con precisión mayor que la de un     ║
║  modelo de alineación (esto no estima — es la verdad del sintetizador)║
╚══════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import edge_tts

from . import config
from .niche import Nicho

# edge-tts reporta el tiempo en unidades de 100 nanosegundos (ticks de .NET)
TICKS_POR_SEGUNDO = 10_000_000


# ── Modelo de datos ───────────────────────────────────────────────────────
@dataclass
class Palabra:
    """Una palabra hablada, con su ventana temporal exacta en el audio final."""

    texto: str
    t_in: float
    t_out: float
    bloque: int = 0          # índice de la sección del guion a la que pertenece

    @property
    def dur(self) -> float:
        return self.t_out - self.t_in


@dataclass
class Bloque:
    """Una sección del guion ya locutada, situada en la línea de tiempo."""

    indice: int
    nombre: str              # "hook", "punto_1", "cierre"…
    texto: str
    t_in: float
    t_out: float
    keywords: list[str] = field(default_factory=list)

    @property
    def dur(self) -> float:
        return self.t_out - self.t_in


@dataclass
class Locucion:
    """Resultado completo: el audio y su mapa temporal."""

    audio: Path
    duracion: float
    palabras: list[Palabra]
    bloques: list[Bloque]

    def guardar_timings(self, ruta: Path) -> None:
        datos = {
            "audio": self.audio.name,
            "duracion": round(self.duracion, 3),
            "bloques": [asdict(b) for b in self.bloques],
            "palabras": [asdict(p) for p in self.palabras],
        }
        ruta.write_text(
            json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8"
        )

    @classmethod
    def cargar_timings(cls, ruta: Path) -> "Locucion":
        d = json.loads(ruta.read_text(encoding="utf-8"))
        return cls(
            audio=ruta.parent / d["audio"],
            duracion=d["duracion"],
            palabras=[Palabra(**p) for p in d["palabras"]],
            bloques=[Bloque(**b) for b in d["bloques"]],
        )


# ── Síntesis de un bloque ─────────────────────────────────────────────────
async def _sintetizar_bloque(
    texto: str, voz: str, velocidad: str, tono: str, destino: Path
) -> list[dict]:
    """
    Sintetiza un texto y devuelve los WordBoundary crudos.

    Consumimos el stream en vez de usar `--write-media` precisamente para
    poder quedarnos con los eventos de palabra.

    ⚠️  OJO con la versión: desde edge-tts 7.x el parámetro `boundary` viene
    por defecto en "SentenceBoundary", que devuelve UN evento por frase
    entera. Sin pedir "WordBoundary" explícitamente no hay karaoke posible.
    """
    com = edge_tts.Communicate(
        texto, voice=voz, rate=velocidad, pitch=tono, boundary="WordBoundary"
    )

    eventos: list[dict] = []
    with open(destino, "wb") as f:
        async for chunk in com.stream():
            tipo = chunk.get("type")
            if tipo == "audio":
                f.write(chunk["data"])
            elif tipo in ("WordBoundary", "SentenceBoundary"):
                eventos.append(
                    {
                        "texto": chunk["text"],
                        "t_in": chunk["offset"] / TICKS_POR_SEGUNDO,
                        "t_out": (chunk["offset"] + chunk["duration"])
                        / TICKS_POR_SEGUNDO,
                    }
                )

    if destino.stat().st_size == 0:
        raise RuntimeError(
            f"edge-tts devolvió audio vacío para la voz '{voz}'. "
            f"¿Existe esa voz? Lista: python -m edge_tts --list-voices"
        )
    return eventos


def _normalizar_a_wav(origen: Path, destino: Path, pausa_final: float) -> None:
    """
    Pasa el mp3 de edge-tts a WAV 48kHz mono y le añade la pausa de respiración.

    Trabajamos en WAV entre etapas para no acumular pérdidas de recodificación;
    el mp3/aac se genera una sola vez, al final.
    """
    filtros = ["aresample=48000", "aformat=channel_layouts=mono"]
    if pausa_final > 0:
        filtros.append(f"apad=pad_dur={pausa_final:.3f}")

    config.correr(
        [
            config.ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(origen),
            "-af", ",".join(filtros),
            "-c:a", "pcm_s16le",
            str(destino),
        ],
        descripcion=f"normalizando {origen.name}",
    )


# ── API pública ───────────────────────────────────────────────────────────
def locutar(
    secciones: list[dict],
    nicho: Nicho,
    dir_trabajo: Path,
    forzar: bool = False,
) -> Locucion:
    """
    Convierte las secciones de un guion en una locución continua con timings.

    `secciones` es una lista de dicts con al menos {"nombre", "texto"} y
    opcionalmente {"keywords"}.

    Sintetizamos bloque a bloque en vez de todo de golpe por tres razones:
      1. Si una sección falla, no se pierde el resto.
      2. Podemos meter pausas dramáticas controladas entre secciones.
      3. Sabemos a qué sección pertenece cada palabra — el storyboard lo
         necesita para elegir el b-roll correcto.
    """
    dir_trabajo.mkdir(parents=True, exist_ok=True)
    salida_audio = dir_trabajo / "voz.wav"
    salida_timings = dir_trabajo / "timings.json"

    pausa = float(nicho.audio.get("pausa_dramatica_seg", 0.35))
    voz_id, velocidad, tono = nicho.voz_id, nicho.voz_velocidad, nicho.voz_tono

    # Caché: si ya está locutado y NI el texto NI la voz cambiaron, no se
    # rehace. La firma es lo que lo garantiza: antes bastaba con que existiera
    # el wav, y un guion corregido salía con la locución vieja.
    firma = hashlib.sha1(json.dumps(
        [[s.get("nombre"), s.get("texto")] for s in secciones]
        + [voz_id, velocidad, tono, pausa], ensure_ascii=False,
    ).encode("utf-8")).hexdigest()
    ruta_firma = dir_trabajo / "voz_firma.txt"
    firma_previa = ruta_firma.read_text().strip() if ruta_firma.exists() else None
    if (salida_timings.exists() and salida_audio.exists() and not forzar
            and firma_previa == firma):
        print("  🎙️  Locución en caché, se reutiliza.")
        return Locucion.cargar_timings(salida_timings)
    ruta_firma.unlink(missing_ok=True)

    dir_bloques = dir_trabajo / "voz_bloques"
    dir_bloques.mkdir(exist_ok=True)

    palabras: list[Palabra] = []
    bloques: list[Bloque] = []
    wavs: list[Path] = []
    reloj = 0.0                       # posición acumulada en la línea de tiempo

    for i, seccion in enumerate(secciones):
        texto = (seccion.get("texto") or "").strip()
        if not texto:
            continue
        nombre = seccion.get("nombre", f"seccion_{i}")

        print(f"  🎙️  [{i + 1}/{len(secciones)}] {nombre}: {texto[:52]}…")

        mp3 = dir_bloques / f"b{i:03d}.mp3"
        wav = dir_bloques / f"b{i:03d}.wav"

        eventos = asyncio.run(
            _sintetizar_bloque(texto, voz_id, velocidad, tono, mp3)
        )

        # El hook respira más largo: la pausa antes de la revelación es gratis
        # y funciona. El último bloque no necesita cola.
        es_ultimo = i == len(secciones) - 1
        pausa_bloque = 0.0 if es_ultimo else (pausa * 1.6 if nombre == "hook" else pausa)

        _normalizar_a_wav(mp3, wav, pausa_bloque)
        dur_real = config.duracion(wav)

        for ev in eventos:
            palabras.append(
                Palabra(
                    texto=ev["texto"],
                    t_in=round(reloj + ev["t_in"], 3),
                    t_out=round(reloj + ev["t_out"], 3),
                    bloque=len(bloques),
                )
            )

        bloques.append(
            Bloque(
                indice=len(bloques),
                nombre=nombre,
                texto=texto,
                t_in=round(reloj, 3),
                t_out=round(reloj + dur_real, 3),
                keywords=seccion.get("keywords") or seccion.get("keywords_fondo") or [],
            )
        )

        wavs.append(wav)
        reloj += dur_real

    if not wavs:
        raise RuntimeError("Ninguna sección del guion tenía texto.")

    _concatenar(wavs, salida_audio)
    duracion_total = config.duracion(salida_audio)

    # Los WordBoundary del último bloque no deben rebasar el audio real.
    for p in palabras:
        p.t_out = min(p.t_out, duracion_total)

    loc = Locucion(
        audio=salida_audio,
        duracion=duracion_total,
        palabras=palabras,
        bloques=bloques,
    )
    loc.guardar_timings(salida_timings)
    ruta_firma.write_text(firma)

    print(
        f"  ✅ Locución lista: {duracion_total:.1f}s · "
        f"{len(palabras)} palabras con timing · {len(bloques)} bloques"
    )
    return loc


def _concatenar(wavs: list[Path], destino: Path) -> None:
    """Une los WAV de cada bloque en un único archivo, sin recodificar."""
    lista = destino.parent / "_concat_voz.txt"
    lista.write_text(
        "\n".join(f"file '{w.as_posix()}'" for w in wavs), encoding="utf-8"
    )
    config.correr(
        [
            config.ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", str(lista),
            "-c", "copy", str(destino),
        ],
        descripcion="concatenando la locución",
    )
    lista.unlink(missing_ok=True)


def listar_voces(idioma: str = "es") -> list[tuple[str, str]]:
    """Voces disponibles para un idioma, para elegir la del nicho."""
    voces = asyncio.run(edge_tts.list_voices())
    return sorted(
        (v["ShortName"], v.get("Gender", "?"))
        for v in voces
        if v["Locale"].startswith(idioma)
    )


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "voces":
        for nombre, genero in listar_voces("es"):
            print(f"  {nombre:32} {genero}")
        raise SystemExit

    # Prueba rápida de extremo a extremo
    nicho = Nicho.cargar("psicologia")
    secciones = [
        {
            "nombre": "hook",
            "texto": "Si alguien te mira a los ojos más de siete segundos, "
                     "no está siendo amable.",
            "keywords": ["mirada"],
        },
        {
            "nombre": "punto_1",
            "texto": "Está midiendo cuánto puede presionarte antes de que "
                     "apartes la vista.",
            "keywords": ["manipulacion"],
        },
    ]
    loc = locutar(secciones, nicho, config.DIR_TRABAJO / "_prueba_voz", forzar=True)
    print(f"\n  Audio: {loc.audio}")
    for p in loc.palabras[:12]:
        print(f"    {p.t_in:6.2f} → {p.t_out:6.2f}  {p.texto}")

"""
engine.py — Motor de render sobre FFmpeg.

La v1 usaba MoviePy: cada frame se decodificaba a un array de numpy y volvía
a Python. Para 8 minutos a 30fps son 14.400 frames pasando por el
intérprete, y encima exportaba con `preset="ultrafast"` — el peor perfil de
calidad de x264, que es de donde salían los bloques en las zonas oscuras.

Aquí el trabajo lo hace FFmpeg en C, y la codificación el chip NVENC de la
GPU. Mismo video: de ~20 minutos a ~2.5.

El render va en cuatro etapas, cada una con su artefacto en disco. Si algo
falla en la etapa 3, no se rehacen la 1 ni la 2.

    1. tomas    → clips/clip_NNN.mp4   (cámara + grade, sin audio)
    2. clips    → video_mudo.mp4       (cortes y transiciones)
    3. voz+mús. → mezcla.m4a           (ducking + loudnorm)
    4. todo     → final.mp4            (subtítulos quemados + audio)
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from .. import config
from ..niche import Nicho
from ..storyboard import Storyboard, Toma
from . import efectos

EXT_IMAGEN = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
COLOR_RESPALDO = "0x0F0F14"


# ══ Etapa 1 · Cada toma se convierte en un clip terminado ═════════════════
def _entrada_de_toma(
    toma: Toma, dur: float, ancho: int, alto: int, fps: int
) -> tuple[list[str], bool]:
    """Argumentos de entrada de FFmpeg para esta toma. Devuelve (args, es_imagen)."""
    archivo = toma.visual.archivo
    ruta = Path(archivo) if archivo else None

    if ruta is None or not ruta.exists():
        # Sin asset no abortamos el video: metemos un fondo del color del
        # nicho. Es mejor una toma sobria que un render caído.
        return (
            [
                "-f", "lavfi",
                "-i", f"color=c={COLOR_RESPALDO}:s={ancho}x{alto}:r={fps}:d={dur:.3f}",
            ],
            False,
        )

    if ruta.suffix.lower() in EXT_IMAGEN:
        return (
            ["-loop", "1", "-framerate", str(fps), "-t", f"{dur:.3f}", "-i", str(ruta)],
            True,
        )

    # Video: si la fuente da de sí, entramos en un punto aleatorio estable
    # para no ver siempre el mismo primer segundo del mismo clip.
    try:
        dur_fuente = config.duracion(ruta)
    except Exception:
        dur_fuente = 0.0

    if dur_fuente >= dur + 0.2:
        inicio = 0.0
        if toma.visual.recorte:
            inicio = float(toma.visual.recorte[0])
        inicio = max(0.0, min(inicio, dur_fuente - dur - 0.1))
        return (["-ss", f"{inicio:.3f}", "-t", f"{dur:.3f}", "-i", str(ruta)], False)

    # Fuente más corta que la toma: la repetimos en bucle.
    return (["-stream_loop", "-1", "-t", f"{dur:.3f}", "-i", str(ruta)], False)


def renderizar_tomas(
    sb: Storyboard, nicho: Nicho, dir_trabajo: Path, forzar: bool = False
) -> list[Path]:
    """Etapa 1: convierte cada toma del storyboard en un clip normalizado."""
    dir_clips = dir_trabajo / "clips"
    dir_clips.mkdir(parents=True, exist_ok=True)
    enc = config.codificador()

    clips: list[Path] = []
    for toma in sb.tomas:
        # ⭐ El clip se alarga con la duración de su transición de salida:
        # xfade consume ese solape, así que sin esto el video final saldría
        # más corto que el audio y todo se desincronizaría.
        dur = toma.dur + toma.transicion_salida.dur
        destino = dir_clips / f"clip_{toma.id:04d}.mp4"

        # La caché es por CONTENIDO, no por nombre: si la locución o el
        # storyboard cambiaron, la toma N ya no dura ni muestra lo mismo, y
        # reutilizar clip_N desincronizaría el video con la voz.
        firma = hashlib.sha1(json.dumps(
            [asdict(toma.visual), asdict(toma.camara), round(dur, 3),
             sb.ancho, sb.alto, sb.fps, nicho.look],
            sort_keys=True, default=str,
        ).encode("utf-8")).hexdigest()
        ruta_firma = destino.with_suffix(".firma")
        if (destino.exists() and not forzar and ruta_firma.exists()
                and ruta_firma.read_text().strip() == firma):
            clips.append(destino)
            continue

        entrada, es_imagen = _entrada_de_toma(toma, dur, sb.ancho, sb.alto, sb.fps)

        # Medimos la exposición del material real antes de decidir el filtro.
        compensacion = ""
        ruta_asset = Path(toma.visual.archivo) if toma.visual.archivo else None
        if ruta_asset and ruta_asset.exists():
            inicio = toma.visual.recorte[0] if toma.visual.recorte else 0.0
            compensacion = efectos.compensar_exposicion(
                efectos.medir_luma(ruta_asset, inicio)
            )

        cadena = efectos.cadena_toma(
            toma, nicho, sb.ancho, sb.alto, sb.fps, dur, es_imagen, compensacion
        )

        config.correr(
            [
                config.ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
                *entrada,
                "-vf", cadena,
                "-an",
                "-t", f"{dur:.3f}",
                *enc.args,
                # Timebase y GOP idénticos en todos los clips: es lo que
                # permite unir los cortes duros por copia, sin recodificar.
                "-video_track_timescale", "90000",
                "-g", str(sb.fps * 2),
                # ⭐ Metadatos de color idénticos también. El stock llega con
                # bt709 o bt470bg según el clip y FFmpeg los hereda; un cambio
                # a mitad de un segmento reinicia el grafo de xfade y CORTA el
                # video ahí (fin_008 salió con 16s de imagen sobre 50s de voz).
                "-color_primaries", "bt709", "-color_trc", "bt709",
                "-colorspace", "bt709", "-color_range", "tv",
                str(destino),
            ],
            descripcion=f"toma {toma.id}",
        )
        ruta_firma.write_text(firma)
        clips.append(destino)

    return clips


# ══ Etapa 2 · Cortes y transiciones ══════════════════════════════════════
def _concatenar_copia(clips: list[Path], destino: Path) -> Path:
    """Une clips por corte duro sin recodificar. Instantáneo."""
    if len(clips) == 1:
        return clips[0]
    lista = destino.with_suffix(".txt")
    lista.write_text(
        "\n".join(f"file '{c.as_posix()}'" for c in clips), encoding="utf-8"
    )
    config.correr(
        [
            config.ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", str(lista),
            "-c", "copy", str(destino),
        ],
        descripcion="uniendo cortes duros",
    )
    lista.unlink(missing_ok=True)
    return destino


def unir(
    sb: Storyboard, clips: list[Path], dir_trabajo: Path
) -> Path:
    """
    Etapa 2: monta los clips respetando cada tipo de transición.

    Estrategia: los cortes duros (la mayoría) se unen por copia de flujo,
    que es instantáneo. Solo los tramos que llevan transición vistosa pasan
    por `xfade`, que sí recodifica. Así un video de 100 tomas con 8
    transiciones solo recodifica 8 veces, no 100.
    """
    destino = dir_trabajo / "video_mudo.mp4"
    dir_seg = dir_trabajo / "segmentos"
    dir_seg.mkdir(parents=True, exist_ok=True)

    # Partimos en segmentos: cada transición no-corte cierra un segmento.
    segmentos: list[list[Path]] = []
    transiciones: list[tuple[str, float]] = []
    actual: list[Path] = []

    for toma, clip in zip(sb.tomas, clips):
        actual.append(clip)
        tr = toma.transicion_salida
        es_ultima = toma.id == sb.tomas[-1].id
        if tr.tipo != "corte" and tr.dur > 0 and not es_ultima:
            segmentos.append(actual)
            transiciones.append((tr.tipo, tr.dur))
            actual = []
    if actual:
        segmentos.append(actual)

    rutas_seg = [
        _concatenar_copia(grupo, dir_seg / f"seg_{i:03d}.mp4")
        for i, grupo in enumerate(segmentos)
    ]

    if len(rutas_seg) == 1:
        if rutas_seg[0] != destino:
            config.correr(
                [
                    config.ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
                    "-i", str(rutas_seg[0]), "-c", "copy", str(destino),
                ],
                descripcion="copiando el montaje",
            )
        return destino

    # Encadenado con xfade. El offset se calcula sobre la duración real
    # acumulada (medida, no estimada) para que no se acumule deriva.
    entradas: list[str] = []
    for ruta in rutas_seg:
        # Segunda defensa: si un segmento cambia de parámetros a mitad (color,
        # rango), no reconstruir el grafo, porque xfade termina la salida ahí.
        entradas += ["-reinit_filter", "0", "-i", str(ruta)]

    duraciones = [config.duracion(r) for r in rutas_seg]
    pasos: list[str] = []
    etiqueta = "0:v"
    acumulado = duraciones[0]

    for i in range(1, len(rutas_seg)):
        tipo, dur_tr = transiciones[i - 1]
        offset = max(0.0, acumulado - dur_tr)
        salida = f"x{i}"
        pasos.append(
            f"[{etiqueta}][{i}:v]xfade="
            f"transition={efectos.transicion_xfade(tipo)}:"
            f"duration={dur_tr:.3f}:offset={offset:.3f}[{salida}]"
        )
        etiqueta = salida
        acumulado += duraciones[i] - dur_tr

    enc = config.codificador()
    config.correr(
        [
            config.ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
            *entradas,
            "-filter_complex", ";".join(pasos),
            "-map", f"[{etiqueta}]",
            "-an",
            *enc.args,
            "-video_track_timescale", "90000",
            str(destino),
        ],
        descripcion="aplicando transiciones",
    )
    return destino


# ══ Etapa 4 · Subtítulos quemados y audio ════════════════════════════════
def finalizar(
    video_mudo: Path,
    audio: Path,
    subtitulos: Path,
    destino: Path,
    sb: Storyboard,
    nicho: Nicho,
) -> Path:
    """Etapa 4: quema el karaoke y pega la mezcla de audio."""
    enc = config.codificador()
    destino.parent.mkdir(parents=True, exist_ok=True)

    # libass necesita encontrar la tipografía. Se la damos por carpeta en vez
    # de exigir que esté instalada en el sistema.
    filtro_ass = (
        f"ass=filename='{efectos.ruta_filtro(subtitulos)}'"
        f":fontsdir='{efectos.ruta_filtro(config.DIR_FUENTES)}'"
    )

    config.correr(
        [
            config.ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(video_mudo),
            "-i", str(audio),
            "-vf", filtro_ass,
            "-map", "0:v:0", "-map", "1:a:0",
            *enc.args,
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            "-t", f"{sb.duracion:.3f}",
            str(destino),
        ],
        descripcion="pase final",
    )
    return destino

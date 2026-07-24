"""
audio_mix.py — Mezcla de voz y música con ducking, y normalización.

Es el 50% invisible del resultado. La v1 no tenía nada de esto: la voz salía
tal cual del TTS, sin música, sin normalizar. Eso significa que cada video
sonaba a un volumen distinto y que los silencios entre frases sonaban a
grabación en un cuarto vacío.

Lo que hace este módulo:
  1. Normaliza la voz a -16 LUFS (el estándar de facto de YouTube).
  2. Pone la música por debajo y le aplica DUCKING de cadena lateral: la
     música baja sola cuando el narrador habla y vuelve a subir en las
     pausas. Es lo que hace un ingeniero de sonido a mano.
"""
from __future__ import annotations

import random
from pathlib import Path

from .. import config
from ..niche import Nicho

EXTENSIONES_MUSICA = (".mp3", ".wav", ".m4a", ".ogg", ".flac", ".opus")


def elegir_musica(nicho: Nicho, semilla: str) -> Path | None:
    """
    Busca una pista acorde al mood del nicho.

    Convención de carpetas: assets_globales/musica/<mood>/*.mp3
    Si no hay subcarpeta por mood, sirve cualquier pista suelta.
    """
    moods = nicho.audio.get("mood_musica", [])
    candidatas: list[Path] = []

    for mood in moods:
        carpeta = config.DIR_MUSICA / mood
        if carpeta.is_dir():
            candidatas += [
                p for p in carpeta.iterdir()
                if p.suffix.lower() in EXTENSIONES_MUSICA
            ]

    if not candidatas and config.DIR_MUSICA.is_dir():
        candidatas = [
            p for p in config.DIR_MUSICA.rglob("*")
            if p.suffix.lower() in EXTENSIONES_MUSICA
        ]

    if not candidatas:
        return None
    return random.Random(semilla).choice(sorted(candidatas))


def mezclar(
    voz: Path,
    destino: Path,
    nicho: Nicho,
    musica: Path | None = None,
    duracion: float | None = None,
) -> Path:
    """Produce la pista de audio final del video."""
    cfg = nicho.audio
    lufs_voz = float(cfg.get("voz_lufs", -16))

    if musica is None or not musica.exists():
        # Sin música: al menos dejamos la voz normalizada y con cabeza/cola.
        config.correr(
            [
                config.ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(voz),
                "-af", f"loudnorm=I={lufs_voz}:TP=-1.5:LRA=11,aresample=48000",
                "-c:a", "aac", "-b:a", "192k",
                str(destino),
            ],
            descripcion="normalizando la voz",
        )
        return destino

    db_musica = float(cfg.get("musica_db", -22))
    ratio = float(cfg.get("ducking_ratio", 8))
    release = int(cfg.get("ducking_release_ms", 300))
    usa_ducking = bool(cfg.get("ducking", True))
    dur = duracion or config.duracion(voz)

    # La música se repite hasta cubrir el video y se corta con fundidos.
    cadena_musica = (
        f"[1:a]aloop=loop=-1:size=2e9,atrim=0:{dur:.3f},"
        f"volume={db_musica}dB,"
        f"afade=t=in:st=0:d=1.5,afade=t=out:st={max(0, dur - 2):.3f}:d=2,"
        f"aresample=48000,aformat=channel_layouts=stereo[mus]"
    )
    cadena_voz = (
        f"[0:a]loudnorm=I={lufs_voz}:TP=-1.5:LRA=11,"
        f"aresample=48000,aformat=channel_layouts=stereo[voz]"
    )

    if usa_ducking:
        # sidechaincompress usa la VOZ como disparador para comprimir la
        # MÚSICA. Attack corto para que reaccione rápido, release largo para
        # que la música vuelva con suavidad y no "bombee".
        cadena = (
            f"{cadena_voz};{cadena_musica};"
            f"[voz]asplit=2[voz_out][voz_sc];"
            f"[mus][voz_sc]sidechaincompress="
            f"threshold=0.03:ratio={ratio}:attack=5:release={release}[mus_duck];"
            f"[voz_out][mus_duck]amix=inputs=2:duration=first:normalize=0[out]"
        )
    else:
        cadena = (
            f"{cadena_voz};{cadena_musica};"
            f"[voz][mus]amix=inputs=2:duration=first:normalize=0[out]"
        )

    config.correr(
        [
            config.ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(voz),
            "-i", str(musica),
            "-filter_complex", cadena,
            "-map", "[out]",
            "-c:a", "aac", "-b:a", "192k",
            str(destino),
        ],
        descripcion="mezclando voz y música",
    )
    return destino


def medir_lufs(ruta: Path) -> float | None:
    """Loudness integrado real del archivo. Lo usa el control de calidad."""
    import json
    import subprocess

    proceso = subprocess.run(
        [
            config.ffmpeg(), "-hide_banner", "-nostats", "-i", str(ruta),
            "-af", "loudnorm=print_format=json", "-f", "null", "-",
        ],
        capture_output=True, text=True,
    )
    salida = proceso.stderr
    inicio = salida.rfind("{")
    fin = salida.rfind("}")
    if inicio == -1 or fin == -1:
        return None
    try:
        return float(json.loads(salida[inicio : fin + 1])["input_i"])
    except Exception:
        return None

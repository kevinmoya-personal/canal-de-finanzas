"""
pipeline.py — Orquestador de la producción de un video.

Sustituye al `ensamblador.py` de la v1, que era una función de ~200 líneas
que hacía todo de una vez: si fallaba en el minuto 7, se perdía todo.

Aquí cada etapa deja su artefacto en `trabajo/<nicho>/<video_id>/`:

    voz.wav · timings.json · storyboard.json · clips/ · segmentos/
    video_mudo.mp4 · subs.ass · mezcla.m4a

Volver a lanzar la producción reaprovecha lo que ya existe. Se puede
re-renderizar cambiando solo el estilo de subtítulo, sin re-locutar ni
re-descargar un solo asset.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from . import portadas as portadas_mod
from . import storyboard as sb_mod
from . import tracker
from . import voz as voz_mod
from .assets import cache, stock
from .niche import Nicho
from .render import audio_mix, engine
from .render import subtitulos as subs_mod


@dataclass
class Resultado:
    video: Path
    storyboard: sb_mod.Storyboard
    segundos: float
    avisos: list[str]
    portadas: list[Path]
    video_db_id: int | None


def cargar_guion(nicho: Nicho, identificador: str) -> dict:
    """Acepta un id ('psi_001') o una ruta a un .json."""
    ruta = Path(identificador)
    if not ruta.exists():
        ruta = nicho.dir_guiones / f"{identificador}.json"
    if not ruta.exists():
        disponibles = [p.stem for p in nicho.dir_guiones.glob("*.json")]
        raise FileNotFoundError(
            f"No encuentro el guion '{identificador}'.\n"
            f"Disponibles en {nicho.dir_guiones}: {', '.join(disponibles) or 'ninguno'}"
        )
    return json.loads(ruta.read_text(encoding="utf-8"))


def producir(
    nicho_nombre: str,
    guion_id: str,
    formato: str | None = None,
    forzar: bool = False,
    solo_plan: bool = False,
) -> Resultado | sb_mod.Storyboard:
    inicio = time.time()
    nicho = Nicho.cargar(nicho_nombre)
    guion = cargar_guion(nicho, guion_id)
    formato = formato or guion.get("formato", "short")
    video_id = str(guion["id"])
    trabajo = nicho.dir_trabajo(video_id)

    print(f"\n{'═' * 66}")
    print(f"🎬  {guion.get('titulo', video_id)}")
    print(f"    nicho={nicho.nombre}  formato={formato}  id={video_id}")
    print(f"{'═' * 66}")

    # ── 1. Locución con timings ──────────────────────────────────────────
    print("\n▸ 1/7  Locución")
    locucion = voz_mod.locutar(guion["secciones"], nicho, trabajo, forzar=forzar)

    # ── 2. Storyboard ────────────────────────────────────────────────────
    print("\n▸ 2/7  Storyboard")
    sb = sb_mod.construir(locucion, nicho, video_id, formato, guion)
    avisos = sb_mod.validar(sb, nicho)
    print(sb.resumen())
    for a in avisos:
        print(f"     ⚠️  {a}")

    if solo_plan:
        sb.guardar(trabajo / "storyboard.json")
        print(f"\n  Plan guardado en {trabajo / 'storyboard.json'} (sin renderizar).")
        return sb

    # ── 3. Assets ────────────────────────────────────────────────────────
    print("\n▸ 3/7  Assets")
    huerfanos = cache.limpiar_parciales()
    if huerfanos:
        print(f"  🧹 {huerfanos} descargas incompletas de ejecuciones previas")
    resueltas, fallidas = stock.resolver(sb, nicho)
    print(f"  📦 {resueltas} tomas con b-roll · {fallidas} sin asset")
    sb.guardar(trabajo / "storyboard.json")

    # ── 4. Render de tomas y montaje ─────────────────────────────────────
    print(f"\n▸ 4/7  Render de {len(sb.tomas)} tomas")
    t0 = time.time()
    clips = engine.renderizar_tomas(sb, nicho, trabajo, forzar=forzar)
    print(f"  🎞️  {len(clips)} clips en {time.time() - t0:.1f}s")

    t0 = time.time()
    video_mudo = engine.unir(sb, clips, trabajo)
    print(f"  ✂️  Montaje en {time.time() - t0:.1f}s")

    # ── 5. Subtítulos y audio ────────────────────────────────────────────
    print("\n▸ 5/7  Subtítulos y audio")
    archivo_subs = subs_mod.generar(sb, nicho, trabajo / "subs.ass")
    print(f"  💬 {subs_mod.contar_eventos(archivo_subs)} eventos de karaoke")

    pista = audio_mix.elegir_musica(nicho, video_id)
    print(f"  🎵 Música: {pista.name if pista else 'ninguna (carpeta vacía)'}")
    mezcla = audio_mix.mezclar(
        locucion.audio, trabajo / "mezcla.m4a", nicho, pista, sb.duracion
    )

    # ── 6. Pase final ────────────────────────────────────────────────────
    print("\n▸ 6/7  Pase final")
    carpeta = nicho.dir_salida / ("shorts" if formato == "short" else "largos")
    destino = carpeta / f"{video_id}.mp4"
    t0 = time.time()
    engine.finalizar(video_mudo, mezcla, archivo_subs, destino, sb, nicho)
    print(f"  🔥 Quemado en {time.time() - t0:.1f}s")

    # ── 7. Miniaturas y registro ─────────────────────────────────────────
    print("\n▸ 7/7  Miniaturas y registro")
    texto_portada = guion.get("texto_portada") or guion.get("titulo", video_id)
    try:
        rutas_portada = portadas_mod.generar(destino, nicho, texto_portada, video_id)
    except Exception as e:
        # Una miniatura fallida no debe tirar un video que ya está renderizado.
        print(f"  ⚠️  No se pudieron generar las miniaturas: {e}")
        rutas_portada = []

    video_db_id = tracker.registrar(
        nicho=nicho.nombre,
        guion_id=video_id,
        tipo=formato,
        titulo=guion.get("titulo", video_id),
        archivo=destino,
        duracion=sb.duracion,
        tomas=len(sb.tomas),
        ancho=sb.ancho,
        alto=sb.alto,
        portada=rutas_portada[0] if rutas_portada else None,
    )
    print(f"  🗃️  Registrado con id {video_db_id} (estado: local, sin publicar)")

    total = time.time() - inicio
    tam_mb = destino.stat().st_size / 1e6
    print(f"\n{'═' * 66}")
    print(f"✅  {destino}")
    print(
        f"    {sb.duracion:.1f}s · {tam_mb:.1f} MB · {len(sb.tomas)} tomas "
        f"· producido en {total:.1f}s"
    )
    # El pipeline nunca publica. Subir es siempre una acción manual explícita.
    print(f"    Para revisar antes de publicar:  python cli.py subir "
          f"{nicho.nombre} {video_id}")
    print(f"{'═' * 66}\n")

    return Resultado(
        video=destino,
        storyboard=sb,
        segundos=total,
        avisos=avisos,
        portadas=rutas_portada,
        video_db_id=video_db_id,
    )

"""
cli.py — Control remoto del estudio.

Sustituye a `daniel.py` de la v1. Aquel era un bucle interactivo con
`input()`, lo que impedía automatizarlo: no se puede programar en el
Task Scheduler algo que espera que alguien escriba "s".

Aquí todo son subcomandos, así que sirve igual a mano que desatendido.

    python cli.py doctor
    python cli.py nichos
    python cli.py voces --idioma es
    python cli.py plan     psicologia psi_001
    python cli.py producir psicologia psi_001
    python cli.py producir psicologia psi_001 --formato largo --forzar
"""
from __future__ import annotations

import argparse
import sys

from studio import config, pipeline
from studio.niche import Nicho


def cmd_doctor(_) -> int:
    config.asegurar_carpetas()
    print("── Entorno ──")
    print(f"  raíz        {config.RAIZ}")
    try:
        print(f"  ffmpeg      {config.ffmpeg()}")
        print(f"  ffprobe     {config.ffprobe()}")
    except RuntimeError as e:
        print(f"  ❌ {e}")
        return 1

    enc = config.codificador()
    print(f"  codificador {enc.nombre}  ({'GPU · NVENC' if enc.usa_gpu else 'CPU'})")

    print("\n── Claves ──")
    for nombre, etapa in [
        ("PEXELS_API_KEY", "b-roll"),
        ("ANTHROPIC_API_KEY", "guionista IA"),
        ("FAL_KEY", "imágenes IA"),
    ]:
        estado = "✅" if config.secreto(nombre) else "⬜"
        print(f"  {estado} {nombre:20} {etapa}")

    print("\n── Recursos ──")
    fuentes = list(config.DIR_FUENTES.glob("*.ttf")) + list(config.DIR_FUENTES.glob("*.otf"))
    musica = [
        p for p in config.DIR_MUSICA.rglob("*")
        if p.suffix.lower() in audio_exts()
    ]
    print(f"  tipografías {len(fuentes)}  {[f.name for f in fuentes] or '— falta al menos una'}")
    print(f"  música      {len(musica)} pistas en {config.DIR_MUSICA}")
    if not musica:
        print("              (sin música el video se produce igual, solo con voz)")

    print("\n── Nichos ──")
    for n in Nicho.listar():
        nicho = Nicho.cargar(n)
        guiones = list(nicho.dir_guiones.glob("*.json"))
        print(f"  {n:14} voz={nicho.voz_id:24} guiones={len(guiones)}")
    return 0


def audio_exts():
    return {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".opus"}


def cmd_nichos(_) -> int:
    for n in Nicho.listar():
        nicho = Nicho.cargar(n)
        short = nicho.formato("short")
        print(f"\n  {nicho.titulo}  ({n})")
        print(f"    voz     {nicho.voz_id} {nicho.voz_velocidad}")
        print(f"    short   {short.relacion}@{short.fps}fps · "
              f"tomas de {short.toma_min}-{short.toma_max}s")
        print(f"    look    saturación {nicho.look.get('saturacion')} · "
              f"viñeta {nicho.look.get('vineta')}")
        for g in sorted(nicho.dir_guiones.glob("*.json")):
            print(f"      · {g.stem}")
    return 0


def cmd_voces(args) -> int:
    from studio.voz import listar_voces

    for nombre, genero in listar_voces(args.idioma):
        print(f"  {nombre:34} {genero}")
    return 0


def cmd_plan(args) -> int:
    pipeline.producir(args.nicho, args.guion, args.formato, solo_plan=True)
    return 0


def cmd_producir(args) -> int:
    resultado = pipeline.producir(
        args.nicho, args.guion, args.formato, forzar=args.forzar
    )
    return 0 if resultado else 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="video-studio",
        description="Fábrica de contenido automatizada v2",
    )
    sub = p.add_subparsers(dest="comando", required=True)

    sub.add_parser("doctor", help="revisa el entorno").set_defaults(func=cmd_doctor)
    sub.add_parser("nichos", help="lista canales y guiones").set_defaults(func=cmd_nichos)

    pv = sub.add_parser("voces", help="voces de edge-tts disponibles")
    pv.add_argument("--idioma", default="es")
    pv.set_defaults(func=cmd_voces)

    pp = sub.add_parser("plan", help="construye el storyboard sin renderizar")
    pp.add_argument("nicho")
    pp.add_argument("guion")
    pp.add_argument("--formato", default=None)
    pp.set_defaults(func=cmd_plan)

    pr = sub.add_parser("producir", help="produce el video completo")
    pr.add_argument("nicho")
    pr.add_argument("guion")
    pr.add_argument("--formato", default=None, choices=[None, "short", "largo"])
    pr.add_argument("--forzar", action="store_true",
                    help="ignora la caché y rehace todas las etapas")
    pr.set_defaults(func=cmd_producir)

    args = p.parse_args(argv)
    try:
        return args.func(args)
    except (FileNotFoundError, RuntimeError, KeyError) as e:
        print(f"\n❌ {e}\n", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

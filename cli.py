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

    python cli.py guion psicologia "por qué mentimos sobre cosas pequeñas"
    python cli.py guion mascotas "señales de que tu perro te elige a ti" --producir
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


def cmd_cortar(args) -> int:
    """Deriva shorts de un guion largo y los produce."""
    from studio import cortador

    if args.solo_plan:
        cortador.derivar(Nicho.cargar(args.nicho), args.guion, args.segundos)
        print("\n  Guiones derivados listos. Prodúcelos con 'producir', o repite "
              "este comando sin --solo-plan.")
        return 0

    producidos = cortador.producir_todos(args.nicho, args.guion, args.segundos)
    print(f"\n  {len(producidos)} shorts producidos.")
    return 0


def cmd_estado(args) -> int:
    """Qué se ha producido y qué está sin publicar."""
    from studio import tracker

    print("\n── Producción ──")
    print(tracker.resumen())

    sin_subir = tracker.sin_subir(args.nicho)
    print(f"\n── Sin publicar ({len(sin_subir)}) ──")
    for v in sin_subir[:25]:
        print(f"  [{v.id:>3}] {v.nicho:<12} {v.tipo:<6} {v.duracion or 0:>5.0f}s  {v.titulo[:44]}")
    if len(sin_subir) > 25:
        print(f"  … y {len(sin_subir) - 25} más")

    if args.excel:
        ruta = tracker.exportar_excel()
        print(f"\n  📊 Exportado a {ruta}")
    return 0


def cmd_subir(args) -> int:
    """
    Prepara la publicación de un video.

    Sin --subir-de-verdad NO se conecta a YouTube: solo enseña lo que se
    enviaría. Es deliberado.
    """
    from studio import facebook, subidor

    a_youtube = args.destino in ("youtube", "ambos")
    a_facebook = args.destino in ("facebook", "ambos")

    if args.subir_de_verdad:
        donde = " y ".join(d for d, si in (("YouTube", a_youtube), ("Facebook", a_facebook)) if si)
        print(f"\n  ⚠️  Vas a PUBLICAR en {donde}. Esto es difícil de deshacer.")
        respuesta = input(f"     Escribe el id del video para confirmar ({args.guion}): ")
        if respuesta.strip() != args.guion:
            print("  Cancelado: el id no coincide.")
            return 1

    fallo = False
    if a_youtube:
        resultado = subidor.subir(
            args.nicho, args.guion, args.formato, confirmado=args.subir_de_verdad
        )
        fallo |= args.subir_de_verdad and not resultado
    if a_facebook:
        try:
            facebook.subir(args.nicho, args.guion, args.formato,
                           confirmado=args.subir_de_verdad)
        except RuntimeError as e:
            print(f"\n  ❌ Facebook: {e}")
            fallo = True
    return 1 if fallo else 0


def cmd_diario(args) -> int:
    """Produce y programa el contenido de un día (mañana por defecto)."""
    from datetime import date

    from studio import diario

    fecha = date.fromisoformat(args.fecha) if args.fecha else None
    return 1 if diario.ejecutar(args.nicho, fecha, simular=args.simular) else 0


def cmd_autorizar(args) -> int:
    """Abre el navegador para dar acceso a YouTube y dice a qué canal quedó ligado."""
    from studio import subidor

    nicho = Nicho.cargar(args.nicho)
    servicio = subidor._servicio(nicho)
    canales = servicio.channels().list(part="snippet", mine=True).execute().get("items", [])
    for c in canales:
        print(f"  ✅ Autorizado: {c['snippet']['title']}  (id {c['id']})")
    if not canales:
        print("  ⚠️  La cuenta autorizada no tiene canal de YouTube.")
    return 0


def cmd_posts(args) -> int:
    """Genera publicaciones de imagen para Facebook, sin subirlas."""
    from studio import publicaciones

    nicho = Nicho.cargar(args.nicho)
    for p in publicaciones.producir(nicho, args.cuantas):
        print(f"\n  [{p.formato}] {p.imagen}\n  {p.texto_completo}")
    return 0


def cmd_guion(args) -> int:
    """Escribe un guion con Claude y, opcionalmente, lo produce en el acto."""
    from studio import guionista

    nicho = Nicho.cargar(args.nicho)

    if args.comparar:
        guionista.comparar(nicho, args.tema, args.formato)
        print("\n  (comparación: no se guardó ningún guion)")
        return 0

    guion = guionista.escribir(
        nicho, args.tema, args.formato, proveedor=args.proveedor
    )

    if args.producir:
        pipeline.producir(args.nicho, guion["id"], args.formato)
    else:
        print(f"\n  Para producirlo:  python cli.py producir {args.nicho} {guion['id']}")
    return 0


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

    pg = sub.add_parser("guion", help="escribe un guion nuevo con Claude")
    pg.add_argument("nicho")
    pg.add_argument("tema", help="de qué trata el video")
    pg.add_argument("--formato", default="short", choices=["short", "largo"])
    pg.add_argument("--producir", action="store_true",
                    help="produce el video inmediatamente después de escribirlo")
    pg.add_argument("--proveedor", default=None, choices=["gemini", "claude"],
                    help="por defecto, el de GUIONISTA en .env")
    pg.add_argument("--comparar", action="store_true",
                    help="escribe el tema con AMBOS proveedores y enseña los hooks "
                         "lado a lado, sin guardar nada")
    pg.set_defaults(func=cmd_guion)

    pc = sub.add_parser("cortar", help="deriva shorts de un guion largo")
    pc.add_argument("nicho")
    pc.add_argument("guion")
    pc.add_argument("--segundos", type=float, default=50.0,
                    help="duración objetivo de cada short (por defecto 50)")
    pc.add_argument("--solo-plan", action="store_true",
                    help="crea los guiones derivados sin renderizarlos")
    pc.set_defaults(func=cmd_cortar)

    pe = sub.add_parser("estado", help="qué se ha producido y qué falta por publicar")
    pe.add_argument("--nicho", default=None)
    pe.add_argument("--excel", action="store_true", help="exporta control.xlsx")
    pe.set_defaults(func=cmd_estado)

    ps = sub.add_parser(
        "subir",
        help="prepara la publicación (SIMULA por defecto, no sube nada)",
    )
    ps.add_argument("nicho")
    ps.add_argument("guion")
    ps.add_argument("--formato", default="short", choices=["short", "largo"])
    ps.add_argument("--destino", default="ambos", choices=["ambos", "youtube", "facebook"],
                    help="dónde publicar (por defecto en los dos)")
    ps.add_argument(
        "--subir-de-verdad", action="store_true",
        help="PUBLICA de verdad. Sin este flag solo se muestra qué se enviaría.",
    )
    ps.set_defaults(func=cmd_subir)

    pd = sub.add_parser("diario", help="produce y programa un día de contenido (mañana)")
    pd.add_argument("nicho")
    pd.add_argument("--fecha", default=None, help="AAAA-MM-DD; por defecto, mañana")
    pd.add_argument("--simular", action="store_true",
                    help="produce todo pero no sube ni programa nada")
    pd.set_defaults(func=cmd_diario)

    pa = sub.add_parser("autorizar", help="da acceso a YouTube para un nicho")
    pa.add_argument("nicho")
    pa.set_defaults(func=cmd_autorizar)

    pf = sub.add_parser("posts", help="genera publicaciones de imagen para Facebook")
    pf.add_argument("nicho")
    pf.add_argument("--cuantas", type=int, default=2)
    pf.set_defaults(func=cmd_posts)

    args = p.parse_args(argv)
    try:
        return args.func(args)
    except (FileNotFoundError, RuntimeError, KeyError) as e:
        print(f"\n❌ {e}\n", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

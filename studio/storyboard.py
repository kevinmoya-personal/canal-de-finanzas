"""
storyboard.py — El cerebro de la v2.

Convierte (guion + timings de voz) en un PLAN DE RODAJE explícito:
qué se ve en cada segundo, con qué movimiento de cámara, con qué transición
y con qué subtítulo.

╔══════════════════════════════════════════════════════════════════════╗
║  Por qué existe este archivo intermedio                              ║
║                                                                      ║
║  En la v1, guion → video era una sola función monolítica. Si el      ║
║  render fallaba en el minuto 7, se perdía todo. Y no se podía        ║
║  revisar nada antes de gastar 20 minutos de CPU.                     ║
║                                                                      ║
║  Con el storyboard como JSON:                                        ║
║    · lo abres y ves qué pasa en el segundo 34                        ║
║    · corriges una toma sin tocar código ni re-locutar                ║
║    · re-renderizas sin volver a descargar assets                     ║
║    · validas ANTES de renderizar (¿alguna toma de 12s? ¿keyword      ║
║      vacía?)                                                         ║
║                                                                      ║
║  El ritmo de corte, el zoom, el grade y las transiciones dejan de    ║
║  ser código y pasan a ser DATOS.                                     ║
╚══════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .niche import Formato, Nicho
from .voz import Locucion, Palabra

# Un silencio mayor que esto se considera final de frase.
# Los WordBoundary de edge-tts dejan huecos de ~0.01-0.03s entre palabras
# de la misma frase, y de 0.2s+ en las comas y puntos.
UMBRAL_FRASE = 0.16


# ── Modelo del plan ───────────────────────────────────────────────────────
@dataclass
class Visual:
    """Qué se ve en esta toma y de dónde sale."""

    fuente: str                    # "stock" | "ai_image" | "ai_video" | "color"
    consulta: str = ""             # término de búsqueda o prompt
    keyword: str = ""              # tema original, en español
    archivo: str | None = None     # se rellena en la etapa de assets
    recorte: list[float] | None = None   # [inicio, fin] dentro del clip origen


@dataclass
class Camara:
    efecto: str = "ninguno"        # "ken_burns" | "push" | "ninguno"
    zoom_desde: float = 1.0
    zoom_hasta: float = 1.0
    origen: str = "centro"         # hacia dónde se mueve el encuadre


@dataclass
class Transicion:
    tipo: str = "corte"            # corte | crossfade | flash | fadeblack | fadewhite
    dur: float = 0.0


@dataclass
class GrupoSub:
    """Un grupo de 2-4 palabras que aparecen juntas en pantalla."""

    palabras: list[dict]           # [{texto, t_in, t_out}, …]

    @property
    def t_in(self) -> float:
        return self.palabras[0]["t_in"]

    @property
    def t_out(self) -> float:
        return self.palabras[-1]["t_out"]


@dataclass
class Toma:
    """Una toma: la unidad de edición."""

    id: int
    t_in: float
    t_out: float
    bloque: int
    bloque_nombre: str
    visual: Visual
    camara: Camara
    transicion_salida: Transicion
    subtitulos: list[GrupoSub] = field(default_factory=list)

    @property
    def dur(self) -> float:
        return self.t_out - self.t_in

    @property
    def texto(self) -> str:
        return " ".join(
            p["texto"] for g in self.subtitulos for p in g.palabras
        )


@dataclass
class Storyboard:
    video_id: str
    nicho: str
    formato: str
    ancho: int
    alto: int
    fps: int
    duracion: float
    audio_voz: str
    tomas: list[Toma]

    # ---- Persistencia ----
    def guardar(self, ruta: Path) -> None:
        ruta.write_text(
            json.dumps(_a_dict(self), ensure_ascii=False, indent=1),
            encoding="utf-8",
        )

    @classmethod
    def cargar(cls, ruta: Path) -> "Storyboard":
        d = json.loads(ruta.read_text(encoding="utf-8"))
        tomas = [
            Toma(
                id=t["id"], t_in=t["t_in"], t_out=t["t_out"],
                bloque=t["bloque"], bloque_nombre=t["bloque_nombre"],
                visual=Visual(**t["visual"]),
                camara=Camara(**t["camara"]),
                transicion_salida=Transicion(**t["transicion_salida"]),
                subtitulos=[GrupoSub(**g) for g in t["subtitulos"]],
            )
            for t in d["tomas"]
        ]
        return cls(
            video_id=d["video_id"], nicho=d["nicho"], formato=d["formato"],
            ancho=d["ancho"], alto=d["alto"], fps=d["fps"],
            duracion=d["duracion"], audio_voz=d["audio_voz"], tomas=tomas,
        )

    # ---- Diagnóstico ----
    def resumen(self) -> str:
        durs = [t.dur for t in self.tomas]
        fuentes: dict[str, int] = {}
        transiciones: dict[str, int] = {}
        for t in self.tomas:
            fuentes[t.visual.fuente] = fuentes.get(t.visual.fuente, 0) + 1
            tt = t.transicion_salida.tipo
            transiciones[tt] = transiciones.get(tt, 0) + 1
        return (
            f"  📋 Storyboard: {len(self.tomas)} tomas en {self.duracion:.1f}s\n"
            f"     Duración de toma: min {min(durs):.1f}s · "
            f"media {sum(durs) / len(durs):.1f}s · max {max(durs):.1f}s\n"
            f"     Visuales: {fuentes}\n"
            f"     Transiciones: {transiciones}\n"
            f"     Grupos de subtítulo: {sum(len(t.subtitulos) for t in self.tomas)}"
        )


def _a_dict(sb: Storyboard) -> dict:
    d = asdict(sb)
    return d


# ── Etapa 1: palabras → frases ────────────────────────────────────────────
def _agrupar_en_frases(palabras: list[Palabra]) -> list[list[Palabra]]:
    """
    Corta donde el narrador respira.

    Esto es lo que hace que la edición se sienta humana: los cortes caen en
    las pausas naturales del habla, no cada N segundos fijos.
    """
    if not palabras:
        return []

    frases: list[list[Palabra]] = []
    actual: list[Palabra] = [palabras[0]]

    for previa, palabra in zip(palabras, palabras[1:]):
        cambio_de_bloque = palabra.bloque != previa.bloque
        hay_pausa = (palabra.t_in - previa.t_out) > UMBRAL_FRASE
        if cambio_de_bloque or hay_pausa:
            frases.append(actual)
            actual = [palabra]
        else:
            actual.append(palabra)

    frases.append(actual)
    return frases


# ── Etapa 2: frases → tomas ───────────────────────────────────────────────
def _partir_frase_larga(
    frase: list[Palabra], maximo: float
) -> list[list[Palabra]]:
    """
    Una frase sin pausas más larga que el máximo se parte por palabras.

    Cortar a mitad de frase es normal en edición real; lo que no es normal
    es dejar 12 segundos del mismo plano.
    """
    dur = frase[-1].t_out - frase[0].t_in
    if dur <= maximo or len(frase) < 4:
        return [frase]

    partes = max(2, round(dur / maximo))
    objetivo = dur / partes

    trozos: list[list[Palabra]] = []
    actual: list[Palabra] = []
    inicio = frase[0].t_in

    for p in frase:
        actual.append(p)
        if (p.t_out - inicio) >= objetivo and len(trozos) < partes - 1:
            trozos.append(actual)
            actual = []
            inicio = p.t_out

    if actual:
        trozos.append(actual)
    return trozos


def _construir_tomas(
    frases: list[list[Palabra]], formato: Formato
) -> list[list[Palabra]]:
    """
    Acumula frases hasta llegar al objetivo de duración de toma.

    ⭐ Aquí está la mitad del salto de calidad respecto a la v1: donde antes
    había 1 plano por sección de ~40s, aquí hay un plano cada 2-6s.
    """
    unidades: list[list[Palabra]] = []
    for frase in frases:
        unidades.extend(_partir_frase_larga(frase, formato.toma_max))

    tomas: list[list[Palabra]] = []
    actual: list[Palabra] = []

    for unidad in unidades:
        candidata = actual + unidad
        dur = candidata[-1].t_out - candidata[0].t_in

        if not actual:
            actual = unidad
            continue

        # Cambio de bloque: siempre corta, para que el b-roll siga al tema.
        if unidad[0].bloque != actual[-1].bloque:
            tomas.append(actual)
            actual = unidad
            continue

        if dur > formato.toma_max:
            tomas.append(actual)
            actual = unidad
        else:
            actual = candidata
            if dur >= formato.toma_min:
                tomas.append(actual)
                actual = []

    if actual:
        # Una cola muy corta se funde con la toma anterior en vez de dejar
        # un parpadeo de medio segundo.
        if tomas and (actual[-1].t_out - actual[0].t_in) < formato.toma_min * 0.6:
            tomas[-1] = tomas[-1] + actual
        else:
            tomas.append(actual)

    return tomas


# ── Etapa 3: asignar visual, cámara y transición ──────────────────────────
def _consulta_stock(keyword: str, nicho: Nicho) -> str:
    mapa: dict[str, str] = nicho.stock.get("mapa_keywords", {})
    clave = (keyword or "default").lower().strip()
    # Si el tema no está en el mapa, usamos la keyword tal cual: sirve para
    # temas nuevos sin tener que editar el YAML primero.
    return mapa.get(clave, mapa.get("default", keyword or "cinematic"))


def _agrupar_subtitulos(
    palabras: list[Palabra], por_grupo: int
) -> list[GrupoSub]:
    """
    Parte las palabras de la toma en grupos de 2-4 para el karaoke.

    Además del tamaño, respetamos las PAUSAS: si el narrador se detiene
    entre dos palabras, ahí se cierra el grupo. Sin esto salen carteles
    como "ESTO SÍGUEME", que juntan el final de una frase con el principio
    de la siguiente y se leen raro.
    """
    def _d(p: Palabra) -> dict:
        return {"texto": p.texto, "t_in": p.t_in, "t_out": p.t_out}

    grupos: list[GrupoSub] = []
    actual: list[Palabra] = []

    for i, palabra in enumerate(palabras):
        actual.append(palabra)
        siguiente = palabras[i + 1] if i + 1 < len(palabras) else None

        lleno = len(actual) >= por_grupo
        pausa = siguiente is not None and (siguiente.t_in - palabra.t_out) > UMBRAL_FRASE

        if lleno or pausa or siguiente is None:
            grupos.append(GrupoSub(palabras=[_d(p) for p in actual]))
            actual = []

    # Una palabra suelta al final se pega al grupo anterior en vez de
    # parpadear sola en pantalla.
    if len(grupos) >= 2 and len(grupos[-1].palabras) == 1:
        hueco = grupos[-1].palabras[0]["t_in"] - grupos[-2].palabras[-1]["t_out"]
        if hueco <= UMBRAL_FRASE:
            grupos[-2].palabras.extend(grupos[-1].palabras)
            grupos.pop()

    return grupos


# ── API pública ───────────────────────────────────────────────────────────
def construir(
    locucion: Locucion,
    nicho: Nicho,
    video_id: str,
    formato_nombre: str = "short",
    guion: dict[str, Any] | None = None,
) -> Storyboard:
    formato = nicho.formato(formato_nombre)
    cfg_cam = nicho.camara
    cfg_tr = nicho.transiciones
    cfg_ia = nicho.imagen_ia
    por_grupo = int(nicho.subtitulos.get("palabras_por_grupo", 3))

    # Semilla derivada del id: dos ejecuciones del mismo video dan el mismo
    # resultado, pero videos distintos no comparten el patrón de edición.
    rng = random.Random(f"{nicho.nombre}:{video_id}:{formato_nombre}")

    frases = _agrupar_en_frases(locucion.palabras)
    grupos_tomas = _construir_tomas(frases, formato)

    por_bloque = {b.indice: b for b in locucion.bloques}
    usa_ia = bool(cfg_ia.get("activo"))
    prop_ia = float(cfg_ia.get("proporcion_por_video", 0.0)) if usa_ia else 0.0

    # Contador por bloque: rota las keywords para que un bloque largo no
    # repita el mismo b-roll. Este es el bug de coherencia de la v1.
    rotacion: dict[int, int] = {}
    zoom_in = True

    tomas: list[Toma] = []
    for i, palabras in enumerate(grupos_tomas):
        idx_bloque = palabras[0].bloque
        bloque = por_bloque.get(idx_bloque)
        nombre_bloque = bloque.nombre if bloque else "?"

        t_in = palabras[0].t_in
        # La toma se extiende hasta donde empieza la siguiente: nunca hay
        # huecos negros entre planos.
        siguiente = grupos_tomas[i + 1] if i + 1 < len(grupos_tomas) else None
        t_out = siguiente[0].t_in if siguiente else locucion.duracion

        # -- Visual --
        keywords = (bloque.keywords if bloque else []) or ["default"]
        k = rotacion.get(idx_bloque, 0)
        rotacion[idx_bloque] = k + 1
        keyword = keywords[k % len(keywords)]

        es_hook = nombre_bloque == "hook"
        # El hook siempre pide imagen IA cuando está activa: los primeros 3
        # segundos necesitan una imagen que nadie haya visto antes.
        quiere_ia = usa_ia and (es_hook or rng.random() < prop_ia)

        if quiere_ia:
            visual = Visual(
                fuente="ai_image",
                keyword=keyword,
                consulta=f"{cfg_ia.get('prefijo', '').strip()}, {keyword}",
            )
        else:
            visual = Visual(
                fuente="stock",
                keyword=keyword,
                consulta=_consulta_stock(keyword, nicho),
            )

        # -- Cámara --
        # Las imágenes fijas SIEMPRE se mueven (si no, se ven muertas).
        # El video de stock no: ya tiene movimiento propio, y encimarle zoom
        # se ve mareado.
        if visual.fuente in ("ai_image", "color") and cfg_cam.get("ken_burns_imagenes", True):
            z0 = float(cfg_cam.get("zoom_min", 1.0))
            z1 = float(cfg_cam.get("zoom_max", 1.12))
            camara = Camara(
                efecto="ken_burns",
                zoom_desde=z0 if zoom_in else z1,
                zoom_hasta=z1 if zoom_in else z0,
                origen=rng.choice(["centro", "arriba", "abajo"]),
            )
            if cfg_cam.get("alternar_direccion", True):
                zoom_in = not zoom_in
        elif cfg_cam.get("mover_video", False):
            camara = Camara(efecto="push", zoom_desde=1.05, zoom_hasta=1.0)
        else:
            camara = Camara()

        # -- Transición de salida --
        transicion = Transicion(tipo="corte", dur=0.0)
        if siguiente is not None:
            cambia_bloque = siguiente[0].bloque != idx_bloque
            if es_hook and cambia_bloque:
                transicion = Transicion(
                    tipo=cfg_tr.get("tras_hook", "flash"),
                    dur=float(cfg_tr.get("flash_dur", 0.18)),
                )
            elif cambia_bloque:
                transicion = Transicion(
                    tipo="crossfade", dur=float(cfg_tr.get("crossfade_dur", 0.35))
                )
            elif rng.random() > float(cfg_tr.get("proporcion_corte", 0.7)):
                transicion = Transicion(
                    tipo="crossfade", dur=float(cfg_tr.get("crossfade_dur", 0.35))
                )

        tomas.append(
            Toma(
                id=i,
                t_in=round(t_in, 3),
                t_out=round(t_out, 3),
                bloque=idx_bloque,
                bloque_nombre=nombre_bloque,
                visual=visual,
                camara=camara,
                transicion_salida=transicion,
                subtitulos=_agrupar_subtitulos(palabras, por_grupo),
            )
        )

    # La primera toma arranca en 0, no en la primera palabra: si no, el
    # video empieza con un frame negro antes de que entre la voz.
    if tomas:
        tomas[0].t_in = 0.0

    return Storyboard(
        video_id=video_id,
        nicho=nicho.nombre,
        formato=formato_nombre,
        ancho=formato.ancho,
        alto=formato.alto,
        fps=formato.fps,
        duracion=round(locucion.duracion, 3),
        audio_voz=locucion.audio.name,
        tomas=tomas,
    )


# ── Validación previa al render ───────────────────────────────────────────
def validar(sb: Storyboard, nicho: Nicho) -> list[str]:
    """
    Revisa el plan ANTES de gastar CPU. Devuelve la lista de avisos.

    Barato de correr, y evita descubrir a los 20 minutos que una toma dura
    14 segundos.
    """
    formato = nicho.formato(sb.formato)
    avisos: list[str] = []

    if not sb.tomas:
        avisos.append("CRÍTICO: el storyboard no tiene ninguna toma.")
        return avisos

    for t in sb.tomas:
        if t.dur > formato.toma_max * 2:
            avisos.append(
                f"Toma {t.id} dura {t.dur:.1f}s "
                f"(el objetivo es {formato.toma_min}-{formato.toma_max}s)."
            )
        if t.dur < 0.5:
            avisos.append(f"Toma {t.id} dura solo {t.dur:.2f}s: va a parpadear.")
        if not t.visual.consulta.strip():
            avisos.append(f"Toma {t.id} no tiene consulta visual.")
        if not t.subtitulos:
            avisos.append(f"Toma {t.id} no tiene subtítulos.")

    # Continuidad: no puede haber huecos ni solapes en la línea de tiempo.
    for a, b in zip(sb.tomas, sb.tomas[1:]):
        if abs(a.t_out - b.t_in) > 0.001:
            avisos.append(
                f"Hueco/solape entre la toma {a.id} y la {b.id} "
                f"({a.t_out:.3f} vs {b.t_in:.3f})."
            )

    if not (formato.dur_min <= sb.duracion <= formato.dur_max):
        avisos.append(
            f"Duración total {sb.duracion:.1f}s fuera del rango del formato "
            f"'{sb.formato}' ({formato.dur_min}-{formato.dur_max}s)."
        )

    return avisos

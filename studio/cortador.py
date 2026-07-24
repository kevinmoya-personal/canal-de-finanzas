"""
cortador.py — Deriva shorts verticales de un guion largo.

La v1 recortaba el video ya montado. Eso obliga a elegir entre dos males:
recortar un 16:9 a 9:16 tira el 70% del encuadre, o dejas bandas negras.

Aquí el cortador no toca ningún mp4. Genera GUIONES DERIVADOS y los pasa por
el pipeline normal en formato short. Consecuencias:

  · el b-roll se busca en orientación vertical, no se recorta un horizontal
  · el ritmo de corte usa el objetivo de shorts (2-3.5s), no el de largos
  · los subtítulos usan el tamaño y la posición de shorts
  · los assets ya descargados se reutilizan desde la caché

O sea: cuesta lo mismo que recortar y el resultado es nativo.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .guionista import PALABRAS_POR_MINUTO
from .niche import Nicho

# Margen sobre el máximo del formato: preferimos shorts de ~50s a rozar el
# límite de 60 que aplican TikTok e Instagram para el alcance completo.
OBJETIVO_SEGUNDOS = 50.0


def _segundos(texto: str) -> float:
    return len(texto.split()) / PALABRAS_POR_MINUTO * 60


def _frases(texto: str) -> list[str]:
    partes = re.split(r"(?<=[.!?])\s+", texto.strip())
    return [p.strip() for p in partes if p.strip()]


def _partir_seccion(seccion: dict, maximo: float) -> list[dict]:
    """
    Una sección que por sí sola no cabe en un short se parte por frases.

    Cada trozo hereda las keywords y el prompt de imagen de la sección madre,
    así el b-roll sigue siendo coherente con el tema.
    """
    if _segundos(seccion["texto"]) <= maximo:
        return [seccion]

    trozos: list[dict] = []
    actual: list[str] = []
    for frase in _frases(seccion["texto"]):
        candidato = actual + [frase]
        if actual and _segundos(" ".join(candidato)) > maximo:
            trozos.append({**seccion, "texto": " ".join(actual)})
            actual = [frase]
        else:
            actual = candidato
    if actual:
        trozos.append({**seccion, "texto": " ".join(actual)})
    return trozos


def _agrupar(secciones: list[dict], maximo: float) -> list[list[dict]]:
    """Acumula secciones hasta llenar un short, sin partir una a la mitad."""
    unidades: list[dict] = []
    for s in secciones:
        unidades.extend(_partir_seccion(s, maximo))

    grupos: list[list[dict]] = []
    actual: list[dict] = []
    for unidad in unidades:
        candidato = actual + [unidad]
        dur = sum(_segundos(u["texto"]) for u in candidato)
        if actual and dur > maximo:
            grupos.append(actual)
            actual = [unidad]
        else:
            actual = candidato
    if actual:
        grupos.append(actual)
    return grupos


def derivar(
    nicho: Nicho,
    guion_id: str,
    objetivo: float = OBJETIVO_SEGUNDOS,
    guardar: bool = True,
) -> list[dict]:
    """
    Convierte un guion largo en varios guiones de short.

    Devuelve los guiones derivados, ya guardados y listos para producir.
    """
    ruta = nicho.dir_guiones / f"{guion_id}.json"
    if not ruta.exists():
        raise FileNotFoundError(f"No existe el guion {ruta}")

    guion = json.loads(ruta.read_text(encoding="utf-8"))
    secciones = guion["secciones"]

    hook_original = next((s for s in secciones if s["nombre"] == "hook"), None)
    cierre_original = next((s for s in secciones if s["nombre"] == "cierre"), None)
    cuerpo = [s for s in secciones if s["nombre"] not in ("hook", "cierre")]

    if not cuerpo:
        raise RuntimeError(
            f"El guion '{guion_id}' no tiene secciones de cuerpo que cortar."
        )

    # El cierre se repite en todos los shorts, así que su duración se
    # descuenta del presupuesto de cada uno.
    coste_cierre = _segundos(cierre_original["texto"]) if cierre_original else 0.0
    presupuesto = max(15.0, objetivo - coste_cierre)

    grupos = _agrupar(cuerpo, presupuesto)
    derivados: list[dict] = []

    print(f"  ✂️  '{guion.get('titulo', guion_id)}' → {len(grupos)} shorts")

    for i, grupo in enumerate(grupos, 1):
        # La primera sección del grupo pasa a ser el hook: así el storyboard
        # le aplica el flash de salida y el subtítulo grande.
        secciones_short = [{**grupo[0], "nombre": "hook"}]
        for j, s in enumerate(grupo[1:], 1):
            secciones_short.append({**s, "nombre": f"punto_{j}"})
        if cierre_original:
            secciones_short.append({**cierre_original, "nombre": "cierre"})

        dur = sum(_segundos(s["texto"]) for s in secciones_short)
        nuevo_id = f"{guion_id}_s{i}"
        derivado = {
            "id": nuevo_id,
            "titulo": f"{guion.get('titulo', guion_id)} · Parte {i}",
            "tema": guion.get("tema", ""),
            "formato": "short",
            "estado": "pendiente",
            "texto_portada": guion.get("texto_portada", ""),
            "derivado_de": guion_id,
            "secciones": secciones_short,
            "tags": guion.get("tags", []),
        }

        if guardar:
            (nicho.dir_guiones / f"{nuevo_id}.json").write_text(
                json.dumps(derivado, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        print(f"     {nuevo_id}: {len(secciones_short)} secciones · ~{dur:.0f}s")
        derivados.append(derivado)

    return derivados


def producir_todos(
    nicho_nombre: str, guion_id: str, objetivo: float = OBJETIVO_SEGUNDOS
) -> list[Path]:
    """Deriva los shorts y los renderiza todos, uno tras otro."""
    from . import pipeline

    nicho = Nicho.cargar(nicho_nombre)
    derivados = derivar(nicho, guion_id, objetivo)

    producidos: list[Path] = []
    for d in derivados:
        try:
            resultado = pipeline.producir(nicho_nombre, d["id"], "short")
            producidos.append(resultado.video)
        except Exception as e:
            print(f"  ❌ {d['id']}: {e}")
    return producidos

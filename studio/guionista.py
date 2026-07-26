"""
guionista.py — Generación de guiones con IA. Gemini o Claude, a elección.

Este módulo ataca el cuello de botella real de la v1: los guiones se
escribían a mano en `guiones.json`. El motor puede producir un video en
2 minutos, pero de nada sirve si escribir el guion tarda media hora.

╔══════════════════════════════════════════════════════════════════════╗
║  Decisión de diseño: keywords restringidas por esquema                ║
║                                                                      ║
║  El esquema de salida declara `keywords` como un enum con el          ║
║  vocabulario exacto de `mapa_keywords` del nicho. La API garantiza    ║
║  que la respuesta valida contra el esquema, así que el modelo NO      ║
║  puede inventarse una keyword que luego no tenga traducción a una     ║
║  búsqueda de Pexels.                                                  ║
║                                                                      ║
║  Sin esto, un guion perfectamente escrito puede pedir b-roll de       ║
║  "disonancia_cognitiva" y dejar la toma sin asset. Con esto, la       ║
║  coherencia visual está garantizada por construcción, no por suerte.  ║
║                                                                      ║
║  Verificado en ambos proveedores: se les pidió a propósito un tema    ║
║  que empujaba fuera del vocabulario y ninguno se salió del enum.      ║
╚══════════════════════════════════════════════════════════════════════╝

El proveedor se elige con GUIONISTA=gemini|claude en el .env, o por
parámetro. El resto del proyecto no sabe ni le importa quién escribió el
guion: `escribir()` devuelve siempre el mismo dict.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from . import config
from .niche import Nicho

# Modelos fijados a propósito, no alias tipo 'gemini-flash-latest': en un
# pipeline de contenido no quieres que la voz del canal cambie sola cuando
# Google promueva otra versión detrás del alias.
MODELO_GEMINI = "gemini-3.6-flash"
MODELO_CLAUDE = "claude-opus-4-8"

# Cuántas palabras por minuto habla la voz. Sirve para pedirle al modelo
# una longitud de guion acorde a la duración objetivo del formato.
PALABRAS_POR_MINUTO = 165


# ── Esquema de salida ─────────────────────────────────────────────────────
def _esquema(
    vocabulario: list[str], secciones_min: int, proveedor: str = "gemini"
) -> dict[str, Any]:
    """
    Esquema JSON del guion, adaptado al dialecto de cada proveedor.

    Los dos validadores comparten el 95%, pero difieren en los bordes:
      · Claude exige `additionalProperties: false` y que `required` liste
        todas las propiedades.
      · Gemini usa el subconjunto de OpenAPI 3.0: no conoce
        `additionalProperties`, y en cambio acepta `propertyOrdering`, que
        fija el orden de generación y hace la salida más estable.

    Ninguno admite `minLength`, `maxLength` ni `minItems`, así que la
    longitud se pide en el prompt y se comprueba después en `_validar`.
    """
    base = {
        "type": "object",
        "properties": {
            "titulo": {
                "type": "string",
                "description": "Título para YouTube. Máximo 70 caracteres.",
            },
            "titulos_alternativos": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Otras 2 variantes del título para hacer pruebas A/B.",
            },
            "tema": {"type": "string"},
            "texto_portada": {
                "type": "string",
                "description": "Texto de la miniatura. 2-4 palabras, en MAYÚSCULAS, "
                "puede llevar un salto de línea.",
            },
            "secciones": {
                "type": "array",
                "description": f"Al menos {secciones_min} secciones. La primera "
                "SIEMPRE se llama 'hook' y la última 'cierre'.",
                "items": {
                    "type": "object",
                    "properties": {
                        "nombre": {
                            "type": "string",
                            "description": "hook | punto_1 | punto_2 | ... | cierre",
                        },
                        "texto": {
                            "type": "string",
                            "description": "Lo que dice el narrador. Español neutro, "
                            "sin emojis, sin markdown, sin acotaciones entre "
                            "paréntesis. Se lee tal cual.",
                        },
                        "keywords": {
                            "type": "array",
                            "description": "2-3 temas visuales para el b-roll, en "
                            "orden de aparición. SOLO de la lista permitida.",
                            "items": {"type": "string", "enum": vocabulario},
                        },
                        "prompt_ia": {
                            "type": "string",
                            "description": "Prompt en INGLÉS para generar una imagen "
                            "que ilustre esta sección. Sin estilo ni cámara: el "
                            "nicho ya añade su prefijo.",
                        },
                    },
                    "required": ["nombre", "texto", "keywords", "prompt_ia"],
                },
            },
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "titulo", "titulos_alternativos", "tema",
            "texto_portada", "secciones", "tags",
        ],
    }

    if proveedor == "claude":
        base["additionalProperties"] = False
        base["properties"]["secciones"]["items"]["additionalProperties"] = False
    else:
        # Fijar el orden hace que el modelo escriba el título antes que las
        # secciones, así el resto del guion se genera coherente con él.
        base["propertyOrdering"] = [
            "titulo", "titulos_alternativos", "tema",
            "texto_portada", "secciones", "tags",
        ]
        base["properties"]["secciones"]["items"]["propertyOrdering"] = [
            "nombre", "texto", "keywords", "prompt_ia",
        ]

    return base


# ── Prompt ────────────────────────────────────────────────────────────────
INSTRUCCIONES = """\
Eres guionista de un canal de YouTube en español llamado "{titulo_canal}".

Escribes guiones que se convierten en video automáticamente: un motor toma tu
texto, lo locuta con una voz sintética, lo corta en tomas de 2 a 6 segundos y
le busca b-roll usando las keywords que tú indiques. Nadie revisa tu texto
antes de que salga al aire.

Por eso el texto debe leerse EN VOZ ALTA sin tropiezos:
- ORTOGRAFÍA COMPLETA DEL ESPAÑOL, con todas las tildes y la eñe. Esto no es
  opcional: el sintetizador de voz cambia la pronunciación según la tilde.
  "Desarmarás" y "desarmaras" son palabras distintas; "míralos" y "miralos"
  suenan diferente. Escribe "por qué", "psicología", "próxima", "suscríbete".
- Sin emojis, sin markdown, sin viñetas, sin comillas decorativas.
- Sin acotaciones entre paréntesis ni indicaciones de escena.
- Sin cifras escritas en dígitos: escribe "siete", no "7".
- Sin siglas deletreadas ni URLs.
- Frases cortas. El punto y seguido es tu mejor herramienta: cada pausa que
  escribes se convierte en un corte de plano.

El TÍTULO va en mayúscula solo al principio y en los nombres propios, como
manda el español. No pongas Cada Palabra En Mayúscula: eso es del inglés y
en español se ve mal.

ESTRUCTURA
- La primera sección se llama exactamente "hook". Tiene que funcionar en los
  primeros tres segundos: una afirmación concreta e inquietante, o una
  observación que el espectador pueda comprobar en su propia vida. Nada de
  "en este video vamos a hablar de...".
- Las secciones intermedias se llaman "punto_1", "punto_2", etc. Cada una
  desarrolla UNA idea con un ejemplo concreto y cotidiano.
- La última se llama exactamente "cierre". Remata la idea y pide seguir al
  canal de una forma que se gane el clic, no que lo mendigue.

LONGITUD
El video objetivo dura unos {duracion} segundos, así que el texto completo
debe rondar las {palabras} palabras repartidas en {secciones} secciones.

KEYWORDS
Solo puedes usar keywords de la lista permitida del esquema. Elige 2 o 3 por
sección, en el orden en que deberían verse. Varíalas entre secciones: si
repites la misma en todo el guion, el video se ve monótono.

{anti_repeticion}
Devuelve únicamente el JSON del esquema."""


def _hooks_previos(nicho: Nicho, limite: int = 30) -> list[str]:
    """Hooks ya usados en este canal, para no repetirse."""
    hooks: list[str] = []
    for ruta in sorted(nicho.dir_guiones.glob("*.json")):
        try:
            guion = json.loads(ruta.read_text(encoding="utf-8"))
        except Exception:
            continue
        for seccion in guion.get("secciones", []):
            if seccion.get("nombre") == "hook":
                hooks.append(seccion.get("texto", "")[:120])
    return hooks[-limite:]


# ── Validación posterior ──────────────────────────────────────────────────
def _validar(guion: dict, nicho: Nicho, formato: str) -> list[str]:
    """
    Comprueba lo que el esquema JSON no puede expresar.

    El validador de la API no admite restricciones de longitud, así que las
    verificamos aquí antes de gastar CPU renderizando.
    """
    avisos: list[str] = []
    secciones = guion.get("secciones", [])

    if not secciones:
        return ["CRÍTICO: el guion no tiene secciones."]
    if secciones[0]["nombre"] != "hook":
        avisos.append(f"La primera sección es '{secciones[0]['nombre']}', no 'hook'.")
    if secciones[-1]["nombre"] != "cierre":
        avisos.append(f"La última sección es '{secciones[-1]['nombre']}', no 'cierre'.")

    palabras = sum(len(s["texto"].split()) for s in secciones)
    espec = nicho.formato(formato)
    dur_estimada = palabras / PALABRAS_POR_MINUTO * 60
    if not (espec.dur_min * 0.7 <= dur_estimada <= espec.dur_max * 1.2):
        avisos.append(
            f"Duración estimada {dur_estimada:.0f}s ({palabras} palabras), fuera "
            f"del rango del formato '{formato}' ({espec.dur_min}-{espec.dur_max}s)."
        )

    if len(guion.get("titulo", "")) > 70:
        avisos.append(f"Título de {len(guion['titulo'])} caracteres (YouTube corta a 70).")

    # Señales de que el texto no se va a leer bien en voz alta.
    for s in secciones:
        texto = s["texto"]
        if any(c in texto for c in "*#_`"):
            avisos.append(f"Sección '{s['nombre']}' contiene markdown.")
        if "(" in texto:
            avisos.append(f"Sección '{s['nombre']}' contiene una acotación entre paréntesis.")
        if any(ch.isdigit() for ch in texto):
            avisos.append(f"Sección '{s['nombre']}' contiene dígitos (el TTS los lee raro).")

    # Tildes ausentes. Un texto español de cierta longitud sin una sola tilde
    # casi con seguridad las perdió, y el TTS lo va a pronunciar mal:
    # "desarmaras" (subjuntivo) no suena como "desarmarás" (futuro).
    texto_completo = " ".join(s["texto"] for s in secciones)
    if len(texto_completo.split()) > 60:
        if not any(c in texto_completo for c in "áéíóúüñÁÉÍÓÚÑ¿¡"):
            avisos.append(
                "El guion no tiene NI UNA tilde. El TTS va a pronunciar mal; "
                "reescríbelo con ortografía completa."
            )

    if guion.get("titulo", "").istitle():
        avisos.append(
            "El título usa Mayúscula En Cada Palabra (convención inglesa). "
            "En español va en mayúscula solo la primera palabra."
        )

    return avisos


# ── Proveedores ───────────────────────────────────────────────────────────
# Ambos devuelven (guion_dict, linea_de_uso). El resto del módulo no sabe
# cuál se usó.
def _generar_gemini(sistema: str, peticion: str, esquema: dict) -> tuple[dict, str]:
    from google import genai
    from google.genai import types

    clave = config.secreto("GEMINI_API_KEY", obligatorio=True)
    cliente = genai.Client(api_key=clave)

    respuesta = cliente.models.generate_content(
        model=MODELO_GEMINI,
        contents=peticion,
        config=types.GenerateContentConfig(
            system_instruction=sistema,
            response_mime_type="application/json",
            response_schema=esquema,
            # Temperatura alta a propósito: si todos los guiones salen con la
            # misma estructura, el canal se vuelve predecible y aburrido.
            temperature=1.0,
        ),
    )

    if not respuesta.text:
        motivo = getattr(respuesta.candidates[0], "finish_reason", "desconocido") \
            if respuesta.candidates else "sin candidatos"
        raise RuntimeError(f"Gemini no devolvió texto (motivo: {motivo}).")

    uso = respuesta.usage_metadata
    pensados = getattr(uso, "thoughts_token_count", None) or 0
    detalle = (
        f"{MODELO_GEMINI} · {uso.prompt_token_count}+{uso.candidates_token_count} tokens"
        + (f" (+{pensados} de razonamiento)" if pensados else "")
    )
    return json.loads(respuesta.text), detalle


def _generar_claude(sistema: str, peticion: str, esquema: dict) -> tuple[dict, str]:
    import anthropic

    clave = config.secreto("ANTHROPIC_API_KEY", obligatorio=True)
    cliente = anthropic.Anthropic(api_key=clave)

    respuesta = cliente.messages.create(
        model=MODELO_CLAUDE,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        system=sistema,
        output_config={
            "effort": "high",
            "format": {"type": "json_schema", "schema": esquema},
        },
        messages=[{"role": "user", "content": peticion}],
    )

    if respuesta.stop_reason == "refusal":
        detalle = getattr(respuesta, "stop_details", None)
        raise RuntimeError(
            "El modelo declinó el tema"
            f"{f' ({detalle.category})' if detalle else ''}. Reformúlalo."
        )

    texto = next(b.text for b in respuesta.content if b.type == "text")
    uso = respuesta.usage
    coste = uso.input_tokens / 1e6 * 5 + uso.output_tokens / 1e6 * 25
    return json.loads(texto), (
        f"{MODELO_CLAUDE} · {uso.input_tokens}+{uso.output_tokens} tokens · ~${coste:.3f}"
    )


PROVEEDORES = {"gemini": _generar_gemini, "claude": _generar_claude}


def proveedor_por_defecto() -> str:
    elegido = os.getenv("GUIONISTA", "gemini").strip().lower()
    if elegido not in PROVEEDORES:
        raise RuntimeError(
            f"GUIONISTA='{elegido}' no reconocido. Usa: {', '.join(PROVEEDORES)}"
        )
    return elegido


# ── API pública ───────────────────────────────────────────────────────────
def escribir(
    nicho: Nicho,
    tema: str,
    formato: str = "short",
    guion_id: str | None = None,
    guardar: bool = True,
    proveedor: str | None = None,
) -> dict:
    """Genera un guion completo y lo deja listo para producir."""
    proveedor = (proveedor or proveedor_por_defecto()).lower()
    if proveedor not in PROVEEDORES:
        raise RuntimeError(f"Proveedor '{proveedor}' desconocido.")
    generar = PROVEEDORES[proveedor]

    espec = nicho.formato(formato)
    # 'default' es el respaldo interno de stock.py cuando una búsqueda no
    # devuelve nada; no es una elección creativa que ofrecerle al modelo.
    vocabulario = sorted(
        k for k in nicho.stock.get("mapa_keywords", {}) if k != "default"
    )
    if not vocabulario:
        raise RuntimeError(
            f"El nicho '{nicho.nombre}' no define 'stock.mapa_keywords'. "
            f"Sin vocabulario no se puede restringir el b-roll."
        )

    duracion = int((espec.dur_min + espec.dur_max) / 2)
    n_secciones = 5 if formato == "short" else 9
    palabras = int(duracion / 60 * PALABRAS_POR_MINUTO)

    previos = _hooks_previos(nicho)
    anti_repeticion = ""
    if previos:
        lista = "\n".join(f"- {h}" for h in previos)
        anti_repeticion = (
            f"HOOKS YA USADOS EN ESTE CANAL — no repitas ninguno ni su estructura:\n"
            f"{lista}\n\n"
        )

    sistema = INSTRUCCIONES.format(
        titulo_canal=nicho.titulo,
        duracion=duracion,
        palabras=palabras,
        secciones=n_secciones,
        anti_repeticion=anti_repeticion,
    )

    print(
        f"  🤖 [{proveedor}] Escribiendo sobre '{tema}' "
        f"({formato}, ~{palabras} palabras)…"
    )

    guion, uso = generar(
        sistema,
        f"Escribe el guion sobre: {tema}",
        _esquema(vocabulario, n_secciones, proveedor),
    )

    # Campos que añade el sistema, no el modelo.
    guion["id"] = guion_id or _siguiente_id(nicho)
    guion["formato"] = formato
    guion["estado"] = "pendiente"
    guion["proveedor"] = proveedor   # queda anotado para poder comparar después

    print(f"  ✅ '{guion['titulo']}'")
    print(f"     {len(guion['secciones'])} secciones · {uso}")

    for aviso in _validar(guion, nicho, formato):
        print(f"     ⚠️  {aviso}")

    if guardar:
        destino = nicho.dir_guiones / f"{guion['id']}.json"
        destino.write_text(
            json.dumps(guion, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"  💾 {destino}")

    return guion


def _siguiente_id(nicho: Nicho) -> str:
    """Correlativo por nicho: psi_001, psi_002…"""
    prefijo = nicho.nombre[:3]
    existentes = [
        int(p.stem.split("_")[-1])
        for p in nicho.dir_guiones.glob(f"{prefijo}_*.json")
        if p.stem.split("_")[-1].isdigit()
    ]
    return f"{prefijo}_{max(existentes, default=0) + 1:03d}"


def escribir_lote(
    nicho: Nicho, temas: list[str], formato: str = "short",
    proveedor: str | None = None,
) -> list[dict]:
    """Varios guiones de una tacada. Si uno falla, los demás siguen."""
    guiones = []
    for i, tema in enumerate(temas, 1):
        print(f"\n[{i}/{len(temas)}] {tema}")
        try:
            guiones.append(escribir(nicho, tema, formato, proveedor=proveedor))
        except Exception as e:
            print(f"  ❌ {e}")
    return guiones


def comparar(nicho: Nicho, tema: str, formato: str = "short") -> dict[str, dict]:
    """
    Escribe el mismo tema con los dos proveedores para poder juzgarlos.

    No guarda nada: es para leer los hooks lado a lado y decidir con
    evidencia cuál escribe mejor para tu nicho, en vez de por corazonada.
    """
    resultados: dict[str, dict] = {}
    for nombre in PROVEEDORES:
        print(f"\n──── {nombre} ────")
        try:
            resultados[nombre] = escribir(
                nicho, tema, formato, guardar=False, proveedor=nombre
            )
        except Exception as e:
            print(f"  ❌ {e}")

    print(f"\n{'═' * 66}\n  HOOKS LADO A LADO — tema: {tema}\n{'═' * 66}")
    for nombre, guion in resultados.items():
        hook = next(
            (s["texto"] for s in guion["secciones"] if s["nombre"] == "hook"), "—"
        )
        print(f"\n  [{nombre}] {guion['titulo']}")
        print(f"     «{hook}»")
    return resultados

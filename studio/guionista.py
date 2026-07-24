"""
guionista.py — Generación de guiones con Claude API.

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
╚══════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import config
from .niche import Nicho

MODELO = "claude-opus-4-8"

# Cuántas palabras por minuto habla la voz. Sirve para pedirle al modelo
# una longitud de guion acorde a la duración objetivo del formato.
PALABRAS_POR_MINUTO = 165


# ── Esquema de salida ─────────────────────────────────────────────────────
def _esquema(vocabulario: list[str], secciones_min: int) -> dict[str, Any]:
    """
    Esquema JSON del guion.

    Nota sobre las limitaciones del validador: no admite `minLength`,
    `maxLength`, `minItems` ni restricciones numéricas, así que la longitud
    se pide en el prompt y se verifica después en `_validar`.
    """
    return {
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
                    "additionalProperties": False,
                },
            },
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "titulo", "titulos_alternativos", "tema",
            "texto_portada", "secciones", "tags",
        ],
        "additionalProperties": False,
    }


# ── Prompt ────────────────────────────────────────────────────────────────
INSTRUCCIONES = """\
Eres guionista de un canal de YouTube en español llamado "{titulo_canal}".

Escribes guiones que se convierten en video automáticamente: un motor toma tu
texto, lo locuta con una voz sintética, lo corta en tomas de 2 a 6 segundos y
le busca b-roll usando las keywords que tú indiques. Nadie revisa tu texto
antes de que salga al aire.

Por eso el texto debe leerse EN VOZ ALTA sin tropiezos:
- Sin emojis, sin markdown, sin viñetas, sin comillas decorativas.
- Sin acotaciones entre paréntesis ni indicaciones de escena.
- Sin cifras escritas en dígitos: escribe "siete", no "7".
- Sin siglas deletreadas ni URLs.
- Frases cortas. El punto y seguido es tu mejor herramienta: cada pausa que
  escribes se convierte en un corte de plano.

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

    return avisos


# ── API pública ───────────────────────────────────────────────────────────
def escribir(
    nicho: Nicho,
    tema: str,
    formato: str = "short",
    guion_id: str | None = None,
    guardar: bool = True,
) -> dict:
    """Genera un guion completo y lo deja listo para producir."""
    import anthropic

    clave = config.secreto("ANTHROPIC_API_KEY", obligatorio=True)
    cliente = anthropic.Anthropic(api_key=clave)

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

    print(f"  🤖 Escribiendo guion sobre '{tema}' ({formato}, ~{palabras} palabras)…")

    respuesta = cliente.messages.create(
        model=MODELO,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        system=sistema,
        output_config={
            "effort": "high",
            "format": {
                "type": "json_schema",
                "schema": _esquema(vocabulario, n_secciones),
            },
        },
        messages=[{"role": "user", "content": f"Escribe el guion sobre: {tema}"}],
    )

    if respuesta.stop_reason == "refusal":
        detalle = getattr(respuesta, "stop_details", None)
        raise RuntimeError(
            f"El modelo declinó el tema '{tema}'"
            f"{f' ({detalle.category})' if detalle else ''}. Reformúlalo."
        )

    texto = next(b.text for b in respuesta.content if b.type == "text")
    guion = json.loads(texto)

    # Campos que añade el sistema, no el modelo.
    guion["id"] = guion_id or _siguiente_id(nicho)
    guion["formato"] = formato
    guion["estado"] = "pendiente"

    uso = respuesta.usage
    coste = uso.input_tokens / 1e6 * 5 + uso.output_tokens / 1e6 * 25
    print(
        f"  ✅ '{guion['titulo']}' · {len(guion['secciones'])} secciones · "
        f"{uso.input_tokens}+{uso.output_tokens} tokens · ~${coste:.3f}"
    )

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
    nicho: Nicho, temas: list[str], formato: str = "short"
) -> list[dict]:
    """Varios guiones de una tacada. Si uno falla, los demás siguen."""
    guiones = []
    for i, tema in enumerate(temas, 1):
        print(f"\n[{i}/{len(temas)}] {tema}")
        try:
            guiones.append(escribir(nicho, tema, formato))
        except Exception as e:
            print(f"  ❌ {e}")
    return guiones

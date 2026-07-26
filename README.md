# video-studio v2

Fábrica de contenido automatizada para YouTube / TikTok / Reels.
Sucede a la v1 (que sigue intacta y funcionando en `.gemini\antigravity\scratch\video_generator`).

## Qué hace

```
tema  →  guion (Claude)  →  voz + timings  →  storyboard  →  assets  →  video.mp4
```

Un comando produce un short vertical de 1080×1920 con locución IA, b-roll
contextual, subtítulos karaoke sincronizados palabra por palabra, color grade
por canal y audio normalizado.

## Instalación

```bash
pip install -r requirements.txt
winget install Gyan.FFmpeg
```

Copia `.env.example` a `.env` y rellena las claves. Comprueba el entorno:

```bash
python cli.py doctor
```

## Uso

```bash
# Escribir un guion nuevo con IA y producirlo del tirón
python cli.py guion psicologia "por qué mentimos sobre cosas pequeñas" --producir

# Comparar cómo escriben Gemini y Claude el mismo tema, sin guardar nada
python cli.py guion psicologia "el silencio incómodo" --comparar

# Producir un guion que ya existe
python cli.py producir psicologia psi_001

# Ver el plan de tomas sin renderizar (rápido, para revisar antes de gastar CPU)
python cli.py plan psicologia psi_001

# Partir un guion largo en shorts verticales nativos
python cli.py cortar psicologia psi_007
python cli.py cortar psicologia psi_007 --solo-plan   # solo derivar, sin renderizar

# Qué se ha producido y qué falta por publicar
python cli.py estado
python cli.py estado --excel        # exporta control.xlsx

# Listar canales y sus guiones · voces disponibles
python cli.py nichos
python cli.py voces
```

### Publicar

**Nada se sube solo.** El pipeline nunca llama al subidor. `subir` sin flags
solo te enseña qué se enviaría y se detiene:

```bash
python cli.py subir psicologia psi_001
```

Para publicar de verdad hace falta el flag explícito **y** teclear el id del
video como confirmación:

```bash
python cli.py subir psicologia psi_001 --subir-de-verdad
```

Los nichos vienen con `privacidad: private`, así que un video subido queda
oculto hasta que tú lo hagas visible en YouTube. Cámbialo en `nicho.yaml`
cuando confíes en el pipeline.

## La idea central: el storyboard

Entre el guion y el render hay un archivo intermedio, `storyboard.json`, que es
el plan de rodaje: cada toma con su timestamp exacto, su visual, su movimiento
de cámara, su transición y sus subtítulos.

```
guion → voz.wav + timings.json → storyboard.json → video.mp4
                                       ↑
                       lo abres, lo revisas, lo corriges
                       y re-renderizas sin re-locutar
```

Esto es lo que convierte el ritmo de corte, el zoom, el grade y las
transiciones en **datos configurables** (en `nichos/<canal>/nicho.yaml`) en vez
de código nuevo cada vez.

## Estructura

```
studio/
  config.py        resuelve FFmpeg/NVENC, secretos, rutas
  niche.py         el canal como objeto (sin estado global)
  guionista.py     Gemini o Claude → guion, con keywords restringidas por esquema
  voz.py           edge-tts en streaming → audio + timings palabra por palabra
  storyboard.py    guion + timings → plan de tomas
  pipeline.py      orquestador reanudable
  cortador.py      guion largo → guiones de short (no recorta el mp4)
  portadas.py      miniaturas A/B desde el mejor frame del propio video
  tracker.py       registro SQLite de todo lo producido
  subidor.py       YouTube — SIMULA por defecto, nunca publica solo
  assets/
    stock.py       Pexels con puntuación real (relevancia + criterios técnicos)
    cache.py       caché indexada POR CONSULTA
  render/
    subtitulos.py  karaoke en ASS/libass
    efectos.py     ken burns, grade, viñeta, grano, autoexposición
    engine.py      render en 4 etapas cacheables, FFmpeg + NVENC
    audio_mix.py   ducking sidechain + loudnorm a -16 LUFS

nichos/<canal>/
  nicho.yaml       toda la identidad visual y sonora del canal
  guiones/         los guiones
  output/          los videos terminados

assets_globales/   fuentes, música, SFX, LUTs, overlays
trabajo/           artefactos intermedios (cacheables, reanudables)
cache/             assets descargados, por consulta
```

## Etapas del render

Cada una deja su artefacto en `trabajo/<nicho>/<video_id>/`. Si falla la 3, no
se rehacen la 1 ni la 2.

| # | Entrada | Salida |
|---|---|---|
| 1 | tomas del storyboard | `clips/clip_NNNN.mp4` (cámara + grade) |
| 2 | clips | `video_mudo.mp4` (cortes y transiciones) |
| 3 | voz + música | `mezcla.m4a` (ducking + loudnorm) |
| 4 | todo + `subs.ass` | `output/shorts/<id>.mp4` |

## Notas técnicas que cuesta redescubrir

- **edge-tts 7.x** viene con `boundary="SentenceBoundary"` por defecto. Hay que
  pedir `boundary="WordBoundary"` explícitamente o no hay timings por palabra
  y, por tanto, no hay karaoke. Ver `voz.py`.
- **`xfade` acorta el video** por la duración de la transición. Cada toma se
  renderiza con su solape incluido (`toma.dur + transicion.dur`) para que la
  línea de tiempo siga cuadrando con la voz y los subtítulos. Ver `engine.py`.
- **La caché de assets va por consulta**, no por carpeta plana. Un clip solo se
  reutiliza para la búsqueda que lo trajo. Ver `cache.py`.
- **`zoompan` tiembla** sobre resolución nativa: se pre-escala a 2.4× antes de
  aplicarlo. Ver `efectos.py`.
- **El b-roll de stock llega con exposiciones muy dispares.** Se mide la
  luminancia con `signalstats` y se compensa con gamma antes del grade, o en un
  nicho oscuro la mitad de las tomas se van a negro puro.
- **Los modelos escriben español sin tildes si no se lo prohíbes.** Y eso
  rompe el TTS: "desarmarás" y "desarmaras" son palabras distintas. El prompt
  lo exige explícitamente y `_validar` avisa si el guion no tiene ni una.
- **Claude y Gemini usan dialectos de esquema distintos.** Claude exige
  `additionalProperties: false`; Gemini usa el subconjunto de OpenAPI y no lo
  conoce, pero acepta `propertyOrdering`. Ver `_esquema()`.

## Añadir un canal nuevo

Copia `nichos/psicologia/nicho.yaml`, cambia la voz, la paleta, el mapa de
keywords y los tags. No hay que tocar código.

## Pendiente

Escrito pero **nunca ejecutado de verdad** — no lo des por hecho:

- La rama Claude del guionista no se ha probado (no hay `ANTHROPIC_API_KEY`).
  La de Gemini sí: `psi_002` se escribió y produjo entera con ella.
- `subidor.py` solo se ha probado en modo simulación (a propósito)
- El ducking de música nunca se ha ejercitado: `assets_globales/musica/` vacía
- El formato `largo` (16:9) nunca se ha renderizado
- El nicho `mascotas` no tiene ningún guion ni video

Sin empezar:

- Imágenes IA (FLUX vía fal.ai), video IA para tomas hero, LUTs, overlays
- `ideas.py` con scoring de tendencias
- `qc.py` — validar el mp4 antes de dejarlo publicable
- TikTok e Instagram Reels
- Programador para producción nocturna desatendida

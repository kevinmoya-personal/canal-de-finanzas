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

## Producción diaria automática (Plata con Datos)

```bash
python cli.py diario finanzas             # produce y programa MAÑANA en YouTube y Facebook
python cli.py diario finanzas --simular   # lo mismo sin subir nada
python cli.py diario finanzas --fecha 2026-11-02
python cli.py autorizar finanzas          # da acceso a YouTube y dice a qué canal quedó ligado
python cli.py posts finanzas              # publicaciones de imagen para Facebook, sin subir
python cli.py subir finanzas fin_001 --destino facebook
```

- El calendario (fases, horas, cuántos shorts y largos) vive en
  `nichos/finanzas/nicho.yaml → calendario`.
- `diario` usa primero los shorts ya producidos y sin programar; si faltan,
  saca temas de `nichos/finanzas/temas.json` (se rellena solo con Gemini).
- Cada envío queda en `nichos/finanzas/calendario.jsonl`. Relanzar el comando
  solo hace lo que falta: nunca duplica.
- Publica solo si `publicacion_automatica: true`. Si falta el token de YouTube
  o el de Facebook, ese destino se salta con aviso y lo demás sigue.
- La tarea de Windows ejecuta `tareas/diario_finanzas.bat` cada noche; el
  registro queda en `logs/diario_finanzas.log`.

### En la nube (GitHub Actions)

`.github/workflows/diario-finanzas.yml` hace lo mismo sin el PC: cada noche a
las 22:00 de Bogotá produce y programa el día siguiente en un runner de
Ubuntu (FFmpeg por CPU, fuentes Noto/Liberation en lugar de las de Windows).
Se lanza también a mano desde la pestaña Actions, con fecha o en simulación.

- **Estado:** al terminar hace commit de `estudio.db`, `calendario.jsonl`,
  `temas.json`, los guiones, el historial de posts y `cache/registro/`. Así la
  noche siguiente sabe qué se programó y qué clips ya salieron. No ejecutes el
  diario en el PC y en la nube el mismo día: los dos estados se separarían.
- **Videos:** no van a git; quedan 7 días como artefacto del run.
- **Secretos** (Settings → Secrets and variables → Actions):
  `GEMINI_API_KEY`, `PEXELS_API_KEY`, `FACEBOOK_PAGE_ID`, `FACEBOOK_PAGE_TOKEN`,
  `YOUTUBE_CLIENT_SECRETS_FINANZAS` (contenido de `client_secrets.json`) y
  `YOUTUBE_TOKEN_FINANZAS` (contenido de `youtube_token.json`, que se genera
  una sola vez en el PC con `python cli.py autorizar finanzas`).
- Si el token de YouTube caduca, el run lo dice y sigue con Facebook.
  Reautoriza en el PC y pega el `youtube_token.json` nuevo en el secreto.

### Facebook

`studio/facebook.py` publica en la página: shorts como Reels, largos como
video y tarjetas de imagen como foto, todos con hora programada. Necesita
`FACEBOOK_PAGE_ID` y `FACEBOOK_PAGE_TOKEN` (token de página) en `.env`.

### Gráficas animadas

Con `graficas: true` en el nicho, el guionista añade datos de gráfica a las
secciones con números y la primera toma de esa sección es una gráfica
animada (barras o cifra) en vez de stock. Ver `studio/graficas.py`.

### Publicaciones de imagen

`studio/publicaciones.py`: Gemini propone 6 candidatas, se autoevalúa con
criterios de alcance y se quedan las 2 mejores de formatos distintos. Se
dibujan tarjetas 1080×1350 con la marca. No usa cebo de interacción
("comenta SÍ"…), que Facebook penaliza.

### Clips propios de Flow

`assets_globales/flow/<nicho>/<keyword>/*.mp4` — clips generados en Google
Flow (Veo). En cada video, la primera toma de cada keyword usa uno de estos
antes que Pexels; entre videos se rota el menos usado
(`cache/registro/flow_<nicho>.json`). La carpeta de cada clip debe llamarse
igual que una clave de `stock.mapa_keywords`. Ver `studio/assets/flow.py`.

### Verificación de cifras

Con `guionista.verificar_cifras: true`, cada guion pasa por una segunda
llamada a Gemini que recalcula los números con supuestos realistas para
Colombia y reescribe las secciones que no cuadran. Lo que corrigió queda en
el campo `verificacion` del guion.

### Cachés por contenido

La locución (`voz_firma.txt`) y cada clip (`clip_NNNN.firma`) guardan una
firma de lo que los produjo. Si cambias el texto de un guion, basta con
`producir` de nuevo: se rehace solo lo que cambió. `--forzar` sigue
rehaciéndolo todo.

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

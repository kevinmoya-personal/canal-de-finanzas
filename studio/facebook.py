"""
facebook.py — Publicación en una página de Facebook (Graph API).

Tres tipos de publicación:
  · short  → Reel de la página (API de Reels, en tres fases)
  · largo  → video normal de la página (graph-video, una sola petición)
  · imagen → publicación de foto con texto (tarjetas de `publicaciones.py`)

Las tres aceptan `publicar_en`: con hora, Facebook las guarda programadas y
las publica él mismo; sin hora, salen al aire (o como borrador, según
`facebook.estado` del nicho).

Mismo contrato que `subidor.py`: SIMULA por defecto y solo contacta con
Facebook con `confirmado=True`.

Credenciales en .env, nunca en el nicho.yaml:
  FACEBOOK_PAGE_ID     id numérico de la página
  FACEBOOK_PAGE_TOKEN  token de PÁGINA (no de usuario) con permisos
                       pages_manage_posts, pages_read_engagement y
                       pages_show_list
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config
from .niche import Nicho
from .subidor import _descripcion

VERSION_API = "v23.0"
GRAPH = f"https://graph.facebook.com/{VERSION_API}"
GRAPH_VIDEO = f"https://graph-video.facebook.com/{VERSION_API}"
RUPLOAD = f"https://rupload.facebook.com/video-upload/{VERSION_API}"

# Límites de Facebook.
REEL_SEG_MIN = 3
REEL_SEG_MAX = 90
PROGRAMAR_MIN = timedelta(minutes=15)   # Facebook exige al menos 10 minutos
PROGRAMAR_MAX = timedelta(days=29)      # y como mucho unos 30 días
ESTADOS = ("DRAFT", "PUBLISHED")


@dataclass
class Envio:
    tipo: str                 # short | largo | imagen
    archivo: Path
    texto: str
    titulo: str = ""
    estado: str = "PUBLISHED"
    publicar_en: datetime | None = None
    duracion: float | None = None

    def problemas(self) -> list[str]:
        fallos = []
        if not self.archivo.exists():
            fallos.append(f"El archivo no existe: {self.archivo}")
        if self.estado not in ESTADOS:
            fallos.append(f"Estado '{self.estado}' no válido. Usa {' o '.join(ESTADOS)}.")
        if self.tipo == "short" and self.duracion is not None \
                and not (REEL_SEG_MIN <= self.duracion <= REEL_SEG_MAX):
            fallos.append(
                f"Dura {self.duracion:.0f}s; un Reel debe durar entre "
                f"{REEL_SEG_MIN} y {REEL_SEG_MAX}s."
            )
        if self.publicar_en:
            margen = self.publicar_en - datetime.now(timezone.utc)
            if not (PROGRAMAR_MIN <= margen <= PROGRAMAR_MAX):
                fallos.append(
                    f"Facebook solo programa entre 15 minutos y 29 días por delante "
                    f"(pedido: {self.publicar_en:%Y-%m-%d %H:%M})."
                )
        return fallos

    def imprimir(self) -> None:
        if self.archivo.exists():
            print(f"    archivo     {self.archivo.name} ({self.archivo.stat().st_size / 1e6:.1f} MB)")
        else:
            print(f"    archivo     {self.archivo} (NO EXISTE)")
        print(f"    tipo        {self.tipo}")
        if self.publicar_en:
            print(f"    programado  {self.publicar_en:%Y-%m-%d %H:%M %Z}")
        else:
            print(f"    estado      {self.estado}"
                  f"{'  (queda como borrador)' if self.estado == 'DRAFT' else ''}")
        if self.duracion is not None:
            print(f"    duración    {self.duracion:.1f}s")
        primera = self.texto.split("\n")[0]
        print(f"    texto       {primera[:70]}…  ({len(self.texto)} car.)")


def _duracion(ruta: Path) -> float | None:
    if not ruta.exists():
        return None
    try:
        salida = subprocess.run(
            [config.ffprobe(), "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(ruta)],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        return float(salida)
    except Exception:
        return None


def _estado_por_defecto(nicho: Nicho) -> str:
    return str(nicho.datos.get("facebook", {}).get("estado", "PUBLISHED")).upper()


# ── Preparación (nunca contacta con Facebook) ─────────────────────────────
def preparar_video(nicho: Nicho, guion_id: str, tipo: str = "short") -> Envio:
    ruta_guion = nicho.dir_guiones / f"{guion_id}.json"
    guion = json.loads(ruta_guion.read_text(encoding="utf-8")) if ruta_guion.exists() else {}
    carpeta = "shorts" if tipo == "short" else "largos"
    archivo = nicho.dir_salida / carpeta / f"{guion_id}.mp4"

    titulo = guion.get("titulo", "")
    cuerpo = _descripcion(guion, nicho)
    # En un Reel no hay campo de título: va como primera línea del texto.
    texto = f"{titulo}\n\n{cuerpo}" if (titulo and tipo == "short") else cuerpo

    return Envio(tipo=tipo, archivo=archivo, texto=texto, titulo=titulo,
                 estado=_estado_por_defecto(nicho), duracion=_duracion(archivo))


def preparar_imagen(nicho: Nicho, imagen: Path, texto: str) -> Envio:
    return Envio(tipo="imagen", archivo=imagen, texto=texto,
                 estado=_estado_por_defecto(nicho))


# ── API pública ───────────────────────────────────────────────────────────
def subir(
    nicho_nombre: str,
    guion_id: str,
    tipo: str = "short",
    confirmado: bool = False,
    publicar_en: datetime | None = None,
) -> str | None:
    """Publica un video (short → Reel, largo → video). Simula sin `confirmado`."""
    nicho = Nicho.cargar(nicho_nombre)
    if not nicho.datos.get("facebook", {}).get("activo"):
        print(f"\n  ⏭️  Facebook no está activo para '{nicho_nombre}' (nicho.yaml → facebook.activo).")
        return None
    envio = preparar_video(nicho, guion_id, tipo)
    envio.publicar_en = publicar_en
    return enviar(envio, nicho, confirmado)


def enviar(envio: Envio, nicho: Nicho, confirmado: bool = False) -> str | None:
    print(f"\n  📤 Facebook · {nicho.titulo}:")
    envio.imprimir()

    problemas = envio.problemas()
    for p in problemas:
        print(f"     ⚠️  {p}")
    faltan = [n for n in ("FACEBOOK_PAGE_ID", "FACEBOOK_PAGE_TOKEN") if not config.secreto(n)]
    if faltan:
        print(f"     ⚠️  Falta en .env: {', '.join(faltan)}")

    if not confirmado:
        print("  🔒 MODO SIMULACIÓN — no se ha contactado con Facebook.")
        return None
    if problemas:
        raise RuntimeError(
            "No se publica con problemas pendientes:\n  - " + "\n  - ".join(problemas)
        )

    publicar = {"short": _publicar_reel, "largo": _publicar_video, "imagen": _publicar_imagen}
    return publicar[envio.tipo](envio)


# ── Envío real ────────────────────────────────────────────────────────────
def _credenciales() -> tuple[str, str]:
    return (
        config.secreto("FACEBOOK_PAGE_ID", obligatorio=True),
        config.secreto("FACEBOOK_PAGE_TOKEN", obligatorio=True),
    )


def _revisar(respuesta, paso: str) -> dict:
    try:
        datos = respuesta.json()
    except ValueError:
        datos = {}
    if not respuesta.ok or "error" in datos:
        error = datos.get("error", {})
        raise RuntimeError(
            f"Facebook rechazó el paso '{paso}' ({respuesta.status_code}): "
            f"{error.get('message') or respuesta.text[:300]}"
        )
    return datos


def _momento(envio: Envio) -> dict:
    """Campos de programación comunes a videos e imágenes."""
    if envio.publicar_en:
        return {"published": "false",
                "scheduled_publish_time": str(int(envio.publicar_en.timestamp()))}
    if envio.estado == "DRAFT":
        return {"published": "false"}
    return {"published": "true"}


def _publicar_reel(envio: Envio) -> str:
    import requests

    pagina, token = _credenciales()

    # 1 · start: Facebook reserva el video_id
    inicio = _revisar(
        requests.post(f"{GRAPH}/{pagina}/video_reels",
                      data={"upload_phase": "start", "access_token": token}, timeout=60),
        "start",
    )
    video_id = inicio["video_id"]

    # 2 · el binario
    print("  ⬆️  Subiendo Reel…")
    with open(envio.archivo, "rb") as f:
        _revisar(
            requests.post(
                f"{RUPLOAD}/{video_id}",
                headers={
                    "Authorization": f"OAuth {token}",
                    "offset": "0",
                    "file_size": str(envio.archivo.stat().st_size),
                },
                data=f,
                timeout=600,
            ),
            "subida",
        )

    # 3 · finish: texto y momento de publicación
    final = {
        "upload_phase": "finish",
        "video_id": video_id,
        "description": envio.texto,
        "access_token": token,
    }
    if envio.publicar_en:
        final["video_state"] = "SCHEDULED"
        final["scheduled_publish_time"] = str(int(envio.publicar_en.timestamp()))
    else:
        final["video_state"] = envio.estado
    _revisar(requests.post(f"{GRAPH}/{pagina}/video_reels", data=final, timeout=60), "finish")

    _informar("Reel", video_id, envio)
    return video_id


def _publicar_video(envio: Envio) -> str:
    import requests

    pagina, token = _credenciales()
    print("  ⬆️  Subiendo video largo…")
    with open(envio.archivo, "rb") as f:
        datos = _revisar(
            requests.post(
                f"{GRAPH_VIDEO}/{pagina}/videos",
                data={"title": envio.titulo, "description": envio.texto,
                      "access_token": token, **_momento(envio)},
                files={"source": (envio.archivo.name, f, "video/mp4")},
                timeout=3600,
            ),
            "video",
        )
    _informar("Video", datos["id"], envio)
    return datos["id"]


def _publicar_imagen(envio: Envio) -> str:
    import requests

    pagina, token = _credenciales()
    with open(envio.archivo, "rb") as f:
        datos = _revisar(
            requests.post(
                f"{GRAPH}/{pagina}/photos",
                data={"message": envio.texto, "access_token": token, **_momento(envio)},
                files={"source": (envio.archivo.name, f, "image/png")},
                timeout=120,
            ),
            "imagen",
        )
    ident = datos.get("post_id") or datos["id"]
    _informar("Publicación", ident, envio)
    return ident


# ── Token permanente de la página ─────────────────────────────────────────
def token_permanente(app_id: str, app_secret: str, token_usuario: str,
                     pagina: str | None = None) -> tuple[str, str, str]:
    """
    Convierte el token corto del Graph API Explorer (1-2 horas) en un token
    de PÁGINA que no caduca: corto → usuario de 60 días → token de página.
    Devuelve (id de página, nombre, token).
    """
    import requests

    largo = _revisar(requests.get(f"{GRAPH}/oauth/access_token", params={
        "grant_type": "fb_exchange_token", "client_id": app_id,
        "client_secret": app_secret, "fb_exchange_token": token_usuario,
    }, timeout=30), "canje de token")["access_token"]

    paginas = _revisar(requests.get(f"{GRAPH}/me/accounts", params={
        "fields": "id,name,access_token,tasks", "access_token": largo,
    }, timeout=30), "páginas")["data"]
    if not paginas:
        raise RuntimeError("El token no da acceso a ninguna página. ¿Marcaste la página al autorizar?")
    elegida = next((p for p in paginas if pagina and pagina.lower() in p["name"].lower()), None)
    if not elegida:
        if len(paginas) > 1:
            nombres = ", ".join(p["name"] for p in paginas)
            raise RuntimeError(f"Hay varias páginas ({nombres}). Indica cuál con --pagina.")
        elegida = paginas[0]

    info = _revisar(requests.get(f"{GRAPH}/debug_token", params={
        "input_token": elegida["access_token"], "access_token": f"{app_id}|{app_secret}",
    }, timeout=30), "verificación")["data"]
    faltan = {"pages_manage_posts", "pages_read_engagement", "pages_show_list"} - set(info.get("scopes", []))
    if faltan:
        raise RuntimeError(f"Al token le faltan permisos: {', '.join(sorted(faltan))}.")
    if info.get("expires_at", 0) not in (0, None):
        print("  ⚠️  Este token de página SÍ caduca; repite el proceso con un token de usuario nuevo.")
    return elegida["id"], elegida["name"], elegida["access_token"]


def _informar(que: str, ident: str, envio: Envio) -> None:
    if envio.publicar_en:
        print(f"  ✅ {que} {ident} programado para {envio.publicar_en:%Y-%m-%d %H:%M}.")
    elif envio.estado == "DRAFT":
        print(f"  ✅ {que} {ident} guardado como BORRADOR.")
    else:
        print(f"  ✅ {que} publicado: {ident}")

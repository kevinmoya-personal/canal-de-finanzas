"""
subidor.py — Publicación en YouTube.

╔══════════════════════════════════════════════════════════════════════╗
║  ⚠️  ESTE MÓDULO NO SUBE NADA POR DEFECTO                            ║
║                                                                      ║
║  Kevin pidió expresamente que no se publicara ningún video mientras  ║
║  se construía la v2. Por eso `subir()` funciona en modo SIMULACIÓN   ║
║  salvo que reciba `confirmado=True`, y lanza un error si alguien     ║
║  intenta publicar sin pasar por ahí.                                 ║
║                                                                      ║
║  El pipeline NUNCA llama a este módulo. Publicar es siempre una      ║
║  acción manual y explícita:                                          ║
║      python cli.py subir psicologia psi_001 --subir-de-verdad        ║
║                                                                      ║
║  Sin ese flag, el comando te enseña exactamente qué se enviaría      ║
║  (título, descripción, tags, privacidad, miniatura) y se detiene.    ║
╚══════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from . import tracker
from .niche import Nicho

AMBITOS = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]

# Límites que impone YouTube y que conviene comprobar antes de gastar la subida.
MAX_TITULO = 100
MAX_DESCRIPCION = 5000
MAX_PORTADA_MB = 2.0


class PublicacionNoConfirmada(RuntimeError):
    """Se intentó publicar sin confirmación explícita."""


@dataclass
class Publicacion:
    """Todo lo que se enviaría a YouTube. Se puede inspeccionar sin publicar."""

    archivo: Path
    titulo: str
    descripcion: str
    tags: list[str]
    categoria: str
    privacidad: str
    portada: Path | None

    def problemas(self) -> list[str]:
        fallos = []
        if not self.archivo.exists():
            fallos.append(f"El video no existe: {self.archivo}")
        if len(self.titulo) > MAX_TITULO:
            fallos.append(f"Título de {len(self.titulo)} caracteres (máximo {MAX_TITULO}).")
        if len(self.descripcion) > MAX_DESCRIPCION:
            fallos.append(f"Descripción de {len(self.descripcion)} caracteres (máximo {MAX_DESCRIPCION}).")
        if self.portada and self.portada.exists():
            mb = self.portada.stat().st_size / 1e6
            if mb > MAX_PORTADA_MB:
                fallos.append(f"Miniatura de {mb:.1f} MB (máximo {MAX_PORTADA_MB}).")
        if self.privacidad not in ("public", "unlisted", "private"):
            fallos.append(f"Privacidad '{self.privacidad}' no válida.")
        return fallos

    def imprimir(self) -> None:
        print(f"    archivo     {self.archivo.name} "
              f"({self.archivo.stat().st_size / 1e6:.1f} MB)" if self.archivo.exists()
              else f"    archivo     {self.archivo} (NO EXISTE)")
        print(f"    título      {self.titulo}")
        print(f"    privacidad  {self.privacidad}")
        print(f"    categoría   {self.categoria}")
        print(f"    tags        {', '.join(self.tags[:8])}"
              f"{f' (+{len(self.tags) - 8})' if len(self.tags) > 8 else ''}")
        print(f"    miniatura   {self.portada.name if self.portada else '— ninguna'}")
        primera = self.descripcion.split("\n")[0]
        print(f"    descripción {primera[:70]}…  ({len(self.descripcion)} car.)")


# ── Construcción de los metadatos ─────────────────────────────────────────
def _descripcion(guion: dict, nicho: Nicho) -> str:
    partes = []
    hook = next(
        (s["texto"] for s in guion.get("secciones", []) if s["nombre"] == "hook"), ""
    )
    if hook:
        partes.append(hook)
    partes.append("")
    partes.append(f"Más contenido de {nicho.titulo} cada semana. Suscríbete.")
    partes.append("")
    etiquetas = " ".join(
        "#" + t.replace(" ", "") for t in nicho.datos.get("youtube", {}).get("tags", [])[:6]
    )
    if etiquetas:
        partes.append(etiquetas)
    return "\n".join(partes)[:MAX_DESCRIPCION]


def preparar(nicho: Nicho, guion_id: str, tipo: str = "short") -> Publicacion:
    """Arma la publicación sin contactar con YouTube. Siempre seguro de llamar."""
    ruta_guion = nicho.dir_guiones / f"{guion_id}.json"
    guion = json.loads(ruta_guion.read_text(encoding="utf-8")) if ruta_guion.exists() else {}

    carpeta = "shorts" if tipo == "short" else "largos"
    archivo = nicho.dir_salida / carpeta / f"{guion_id}.mp4"

    portada = nicho.dir_salida / "portadas" / f"{guion_id}_a.jpg"
    yt = nicho.datos.get("youtube", {})

    titulo = guion.get("titulo", guion_id)
    if tipo == "short" and "#shorts" not in titulo.lower():
        titulo = f"{titulo} #shorts"[:MAX_TITULO]

    return Publicacion(
        archivo=archivo,
        titulo=titulo[:MAX_TITULO],
        descripcion=_descripcion(guion, nicho),
        tags=guion.get("tags") or yt.get("tags", []),
        categoria=str(yt.get("categoria", "22")),
        privacidad=yt.get("privacidad", "private"),
        portada=portada if portada.exists() else None,
    )


# ── Autenticación ─────────────────────────────────────────────────────────
def _servicio(nicho: Nicho):
    """Cliente autenticado de YouTube. Solo se invoca al publicar de verdad."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    ruta_token = nicho.dir / "youtube_token.json"
    ruta_secreto = nicho.dir / "client_secrets.json"

    credenciales = None
    if ruta_token.exists():
        credenciales = Credentials.from_authorized_user_file(str(ruta_token), AMBITOS)

    if not credenciales or not credenciales.valid:
        if credenciales and credenciales.expired and credenciales.refresh_token:
            credenciales.refresh(Request())
        else:
            if not ruta_secreto.exists():
                raise RuntimeError(
                    f"Falta {ruta_secreto}. Sin credenciales no se puede publicar."
                )
            flujo = InstalledAppFlow.from_client_secrets_file(str(ruta_secreto), AMBITOS)
            credenciales = flujo.run_local_server(port=0)
        ruta_token.write_text(credenciales.to_json(), encoding="utf-8")

    return build("youtube", "v3", credentials=credenciales)


# ── Publicación ───────────────────────────────────────────────────────────
def subir(
    nicho_nombre: str,
    guion_id: str,
    tipo: str = "short",
    confirmado: bool = False,
    video_db_id: int | None = None,
) -> str | None:
    """
    Publica un video en YouTube.

    Con `confirmado=False` (el valor por defecto) NO se conecta a YouTube:
    solo muestra lo que enviaría y devuelve None. Es la única forma en que
    este módulo se ejecuta salvo que tú pidas lo contrario explícitamente.
    """
    nicho = Nicho.cargar(nicho_nombre)
    pub = preparar(nicho, guion_id, tipo)

    print(f"\n  📤 Publicación preparada para '{guion_id}' en {nicho.titulo}:")
    pub.imprimir()

    problemas = pub.problemas()
    for p in problemas:
        print(f"     ⚠️  {p}")

    if not confirmado:
        print(
            "\n  🔒 MODO SIMULACIÓN — no se ha subido nada ni se ha contactado "
            "con YouTube.\n"
            f"     Para publicar de verdad:\n"
            f"       python cli.py subir {nicho_nombre} {guion_id} --subir-de-verdad"
        )
        return None

    if problemas:
        raise PublicacionNoConfirmada(
            "No se publica con problemas pendientes:\n  - " + "\n  - ".join(problemas)
        )

    return _publicar(nicho, pub, video_db_id)


def _publicar(nicho: Nicho, pub: Publicacion, video_db_id: int | None) -> str:
    """El envío real. Solo se llega aquí con confirmación explícita."""
    from googleapiclient.http import MediaFileUpload

    servicio = _servicio(nicho)
    cuerpo = {
        "snippet": {
            "title": pub.titulo,
            "description": pub.descripcion,
            "tags": pub.tags,
            "categoryId": pub.categoria,
        },
        "status": {
            "privacyStatus": pub.privacidad,
            "selfDeclaredMadeForKids": False,
        },
    }

    medio = MediaFileUpload(str(pub.archivo), chunksize=8 * 1024 * 1024, resumable=True)
    peticion = servicio.videos().insert(
        part="snippet,status", body=cuerpo, media_body=medio
    )

    print("  ⬆️  Subiendo…")
    respuesta = None
    while respuesta is None:
        estado, respuesta = peticion.next_chunk()
        if estado:
            print(f"     {int(estado.progress() * 100)}%")

    youtube_id = respuesta["id"]
    print(f"  ✅ https://youtu.be/{youtube_id}")

    if pub.portada:
        from googleapiclient.http import MediaFileUpload as Subida

        servicio.thumbnails().set(
            videoId=youtube_id, media_body=Subida(str(pub.portada))
        ).execute()
        print("  🖼️  Miniatura aplicada")

    if video_db_id is not None:
        tracker.marcar_subido(video_db_id, youtube_id)

    return youtube_id


def pendientes(nicho_nombre: str | None = None) -> list:
    """Videos producidos y todavía sin publicar. No contacta con YouTube."""
    return tracker.sin_subir(nicho_nombre)

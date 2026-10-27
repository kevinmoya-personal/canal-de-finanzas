"""
config.py — Configuración global, resolución de binarios y secretos.

Reemplaza al `config.json` de la v1, que mezclaba secretos con parámetros
de producción y se leía con rutas relativas frágiles.

Reglas:
  - Los SECRETOS viven en .env y solo se leen desde aquí.
  - Los parámetros CREATIVOS viven en nichos/<nicho>/nicho.yaml.
  - Las RUTAS se derivan de RAIZ, nunca del directorio de trabajo actual.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

# ── Rutas base ────────────────────────────────────────────────────────────
RAIZ = Path(__file__).resolve().parent.parent

DIR_NICHOS = RAIZ / "nichos"
DIR_ASSETS = RAIZ / "assets_globales"
DIR_CACHE = RAIZ / "cache"
DIR_TRABAJO = RAIZ / "trabajo"

DIR_FUENTES = DIR_ASSETS / "fonts"
DIR_MUSICA = DIR_ASSETS / "musica"
DIR_SFX = DIR_ASSETS / "sfx"
DIR_LUTS = DIR_ASSETS / "luts"
DIR_OVERLAYS = DIR_ASSETS / "overlays"

load_dotenv(RAIZ / ".env")


# ── Secretos ──────────────────────────────────────────────────────────────
def secreto(nombre: str, obligatorio: bool = False) -> str | None:
    """Lee una clave del entorno. Falla temprano y con mensaje claro."""
    valor = os.getenv(nombre, "").strip()
    if not valor:
        if obligatorio:
            raise RuntimeError(
                f"Falta {nombre} en {RAIZ / '.env'}.\n"
                f"Copia .env.example a .env y rellena esa clave."
            )
        return None
    return valor


# ── Binarios de FFmpeg ────────────────────────────────────────────────────
# winget instala en ...\WinGet\Links, que a veces no está en el PATH de la
# sesión actual aunque sí en el del usuario. Buscamos en varios sitios.
_CANDIDATOS_EXTRA = [
    Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links",
    Path("C:/ffmpeg/bin"),
    Path("C:/Program Files/ffmpeg/bin"),
]


def _resolver_binario(nombre: str, var_entorno: str) -> str:
    forzado = os.getenv(var_entorno, "").strip()
    if forzado:
        if not Path(forzado).exists():
            raise RuntimeError(f"{var_entorno}={forzado} no existe.")
        return forzado

    encontrado = shutil.which(nombre)
    if encontrado:
        return encontrado

    for carpeta in _CANDIDATOS_EXTRA:
        ruta = carpeta / f"{nombre}.exe"
        if ruta.exists():
            return str(ruta)

    raise RuntimeError(
        f"No se encontró '{nombre}'. Instálalo con:\n"
        f"    winget install Gyan.FFmpeg\n"
        f"y abre una consola nueva, o define {var_entorno} en .env."
    )


@lru_cache(maxsize=1)
def ffmpeg() -> str:
    return _resolver_binario("ffmpeg", "FFMPEG_BIN")


@lru_cache(maxsize=1)
def ffprobe() -> str:
    return _resolver_binario("ffprobe", "FFPROBE_BIN")


# ── Selección de codificador ──────────────────────────────────────────────
@dataclass(frozen=True)
class Codificador:
    """Parámetros de codificación de video ya resueltos para esta máquina."""

    nombre: str
    args: tuple[str, ...]

    @property
    def usa_gpu(self) -> bool:
        return "nvenc" in self.nombre


# NVENC: `cq` es el equivalente a CRF. 21 es visualmente transparente para
# contenido de redes. La v1 usaba preset=ultrafast de x264, que es el peor
# perfil de calidad disponible — de ahí los bloques en las zonas oscuras.
_NVENC = Codificador(
    nombre="h264_nvenc",
    args=(
        "-c:v", "h264_nvenc",
        "-preset", "p5",        # p1=rápido … p7=lento; p5 es el punto dulce
        "-tune", "hq",
        "-rc", "vbr",
        "-cq", "21",
        "-b:v", "12M",
        "-maxrate", "18M",
        "-bufsize", "24M",
        "-profile:v", "high",
        "-pix_fmt", "yuv420p",
    ),
)

_X264 = Codificador(
    nombre="libx264",
    args=(
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "20",
        "-profile:v", "high",
        "-pix_fmt", "yuv420p",
    ),
)


@lru_cache(maxsize=1)
def _nvenc_disponible() -> bool:
    """Comprueba que NVENC no solo esté compilado, sino que codifique."""
    try:
        salida = subprocess.run(
            [ffmpeg(), "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=30,
        )
        if "h264_nvenc" not in salida.stdout:
            return False
        # Prueba real: si el driver no coopera, esto falla.
        prueba = subprocess.run(
            [
                ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "nullsrc=s=256x256:d=0.1",
                "-c:v", "h264_nvenc", "-f", "null", "-",
            ],
            capture_output=True, text=True, timeout=60,
        )
        return prueba.returncode == 0
    except Exception:
        return False


@lru_cache(maxsize=1)
def codificador() -> Codificador:
    preferencia = os.getenv("ENCODER", "auto").strip().lower()
    if preferencia == "cpu":
        return _X264
    if preferencia == "gpu":
        return _NVENC
    return _NVENC if _nvenc_disponible() else _X264


# ── Utilidades de FFmpeg ──────────────────────────────────────────────────
def duracion(ruta: str | Path) -> float:
    """Duración en segundos de cualquier archivo multimedia."""
    salida = subprocess.run(
        [
            ffprobe(), "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(ruta),
        ],
        capture_output=True, text=True, check=True,
    )
    return float(salida.stdout.strip())


def correr(args: list[str], descripcion: str = "") -> None:
    """Ejecuta FFmpeg y levanta un error legible si falla."""
    proceso = subprocess.run(args, capture_output=True, text=True)
    if proceso.returncode != 0:
        cola = "\n".join(proceso.stderr.strip().splitlines()[-15:])
        raise RuntimeError(
            f"FFmpeg falló{' en ' + descripcion if descripcion else ''}:\n{cola}"
        )


# ── Fuentes del sistema ───────────────────────────────────────────────────
# En el PC se usan las de Windows; en GitHub Actions (Ubuntu) no existen ni se
# pueden redistribuir, así que se cae a su equivalente libre más parecido.
_DIRS_SISTEMA = [Path("C:/Windows/Fonts"), Path("/usr/share/fonts")]
_EQUIVALENTES = {
    "segoeui.ttf": ["NotoSans-Regular.ttf", "DejaVuSans.ttf"],
    "segoeuib.ttf": ["NotoSans-Bold.ttf", "DejaVuSans-Bold.ttf"],
    "segoeuisl.ttf": ["NotoSans-Light.ttf", "NotoSans-Regular.ttf", "DejaVuSans.ttf"],
    "arialbd.ttf": ["LiberationSans-Bold.ttf", "DejaVuSans-Bold.ttf"],
    "ariblk.ttf": ["LiberationSans-Bold.ttf", "DejaVuSans-Bold.ttf"],
    "impact.ttf": ["LiberationSans-Bold.ttf", "DejaVuSans-Bold.ttf"],
}


@lru_cache(maxsize=None)
def ruta_fuente(nombre: str) -> Path | None:
    """Busca una fuente en assets_globales/fonts y luego en las del sistema."""
    for nombre_real in [nombre, *_EQUIVALENTES.get(nombre.lower(), [])]:
        propia = DIR_FUENTES / nombre_real
        if propia.exists():
            return propia
        for carpeta in _DIRS_SISTEMA:
            if not carpeta.exists():
                continue
            directa = carpeta / nombre_real
            if directa.exists():
                return directa
            # En Linux las fuentes viven en subcarpetas (truetype/noto/…).
            encontrada = next(carpeta.rglob(nombre_real), None)
            if encontrada:
                return encontrada
    return None


def asegurar_carpetas() -> None:
    for carpeta in (DIR_CACHE, DIR_TRABAJO, DIR_MUSICA, DIR_SFX,
                    DIR_LUTS, DIR_OVERLAYS, DIR_FUENTES):
        carpeta.mkdir(parents=True, exist_ok=True)


if __name__ == "__main__":
    asegurar_carpetas()
    enc = codificador()
    print(f"Raíz          : {RAIZ}")
    print(f"ffmpeg        : {ffmpeg()}")
    print(f"ffprobe       : {ffprobe()}")
    print(f"Codificador   : {enc.nombre} ({'GPU' if enc.usa_gpu else 'CPU'})")
    print(f"Pexels        : {'configurada' if secreto('PEXELS_API_KEY') else 'FALTA'}")
    print(f"Anthropic     : {'configurada' if secreto('ANTHROPIC_API_KEY') else 'falta (Fase 3)'}")

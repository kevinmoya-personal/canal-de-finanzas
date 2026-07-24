"""
niche.py — Un canal (nicho) como objeto explícito.

Reemplaza al `context.py` de la v1, que usaba una variable global mutable
(`ACTIVE_NICHE`). Eso impedía procesar dos canales a la vez y permitía que
cualquier módulo cambiara el estado de todos los demás.

Aquí el nicho se carga una vez y se pasa como argumento. Sin estado global.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import config


# ── Formato de salida ─────────────────────────────────────────────────────
@dataclass(frozen=True)
class Formato:
    """Especificación de un formato de salida (short vertical, largo horizontal)."""

    nombre: str
    ancho: int
    alto: int
    fps: int
    dur_min: float
    dur_max: float
    toma_min: float      # duración mínima de una toma, en segundos
    toma_max: float      # duración máxima de una toma
    sub_tam: int         # tamaño de fuente de los subtítulos
    sub_pos_y: float     # posición vertical del subtítulo, 0.0=arriba 1.0=abajo

    @property
    def relacion(self) -> str:
        return f"{self.ancho}x{self.alto}"

    @property
    def es_vertical(self) -> bool:
        return self.alto > self.ancho


def _formato_desde_yaml(nombre: str, d: dict[str, Any]) -> Formato:
    toma = d.get("toma_seg", [3.0, 5.0])
    return Formato(
        nombre=nombre,
        ancho=int(d["ancho"]),
        alto=int(d["alto"]),
        fps=int(d.get("fps", 30)),
        dur_min=float(d.get("dur_min", 0)),
        dur_max=float(d.get("dur_max", 10_000)),
        toma_min=float(toma[0]),
        toma_max=float(toma[1]),
        sub_tam=int(d.get("sub_tam", 80)),
        sub_pos_y=float(d.get("sub_pos_y", 0.62)),
    )


# ── Nicho ─────────────────────────────────────────────────────────────────
@dataclass
class Nicho:
    """Un canal: su voz, su look, sus rutas y sus reglas de edición."""

    nombre: str
    datos: dict[str, Any] = field(repr=False)

    # ---- Carga ----
    @classmethod
    def cargar(cls, nombre: str) -> "Nicho":
        ruta = config.DIR_NICHOS / nombre / "nicho.yaml"
        if not ruta.exists():
            disponibles = [
                p.name for p in config.DIR_NICHOS.iterdir()
                if (p / "nicho.yaml").exists()
            ]
            raise FileNotFoundError(
                f"No existe el nicho '{nombre}' ({ruta}).\n"
                f"Nichos disponibles: {', '.join(disponibles) or 'ninguno'}"
            )
        with open(ruta, encoding="utf-8") as f:
            datos = yaml.safe_load(f)
        nicho = cls(nombre=nombre, datos=datos)
        nicho.asegurar_carpetas()
        return nicho

    @staticmethod
    def listar() -> list[str]:
        if not config.DIR_NICHOS.exists():
            return []
        return sorted(
            p.name for p in config.DIR_NICHOS.iterdir()
            if (p / "nicho.yaml").exists()
        )

    # ---- Rutas ----
    @property
    def dir(self) -> Path:
        return config.DIR_NICHOS / self.nombre

    @property
    def dir_guiones(self) -> Path:
        return self.dir / "guiones"

    @property
    def dir_salida(self) -> Path:
        return self.dir / "output"

    def dir_trabajo(self, video_id: str) -> Path:
        """Carpeta de artefactos intermedios de un video (cacheable, reanudable)."""
        d = config.DIR_TRABAJO / self.nombre / video_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def asegurar_carpetas(self) -> None:
        for d in (self.dir_guiones, self.dir_salida,
                  self.dir_salida / "shorts", self.dir_salida / "largos",
                  self.dir_salida / "portadas", self.dir_salida / "revisar"):
            d.mkdir(parents=True, exist_ok=True)

    # ---- Accesos tipados ----
    @property
    def titulo(self) -> str:
        return self.datos.get("titulo", self.nombre.title())

    @property
    def voz_id(self) -> str:
        return self.datos["voz"]["id"]

    @property
    def voz_velocidad(self) -> str:
        return self.datos["voz"].get("velocidad", "+0%")

    @property
    def voz_tono(self) -> str:
        return self.datos["voz"].get("tono", "+0Hz")

    def formato(self, nombre: str) -> Formato:
        d = self.datos["formatos"].get(nombre)
        if d is None:
            raise KeyError(
                f"El nicho '{self.nombre}' no define el formato '{nombre}'. "
                f"Definidos: {list(self.datos['formatos'])}"
            )
        return _formato_desde_yaml(nombre, d)

    @property
    def subtitulos(self) -> dict[str, Any]:
        return self.datos.get("subtitulos", {})

    @property
    def look(self) -> dict[str, Any]:
        """Parámetros de color grade: saturación, viñeta, contraste, grano."""
        return self.datos.get("look", {})

    @property
    def camara(self) -> dict[str, Any]:
        return self.datos.get("camara", {})

    @property
    def transiciones(self) -> dict[str, Any]:
        return self.datos.get("transiciones", {})

    @property
    def audio(self) -> dict[str, Any]:
        return self.datos.get("audio", {})

    @property
    def stock(self) -> dict[str, Any]:
        return self.datos.get("stock", {})

    @property
    def imagen_ia(self) -> dict[str, Any]:
        return self.datos.get("imagen_ia", {})

    def ruta_fuente(self, clave: str = "principal") -> Path | None:
        """Resuelve el archivo .ttf de una fuente declarada en el nicho."""
        nombre = self.subtitulos.get("archivo_fuente")
        if not nombre:
            return None
        ruta = config.DIR_FUENTES / nombre
        return ruta if ruta.exists() else None


if __name__ == "__main__":
    for n in Nicho.listar():
        nicho = Nicho.cargar(n)
        short = nicho.formato("short")
        print(f"{nicho.nombre:14} | {nicho.titulo:22} | voz={nicho.voz_id}")
        print(f"{'':14} | short {short.relacion}@{short.fps} "
              f"| tomas de {short.toma_min}-{short.toma_max}s")

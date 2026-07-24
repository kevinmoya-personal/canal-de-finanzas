"""
tracker.py — Registro de todo lo producido, en SQLite.

La v1 llevaba la cuenta en `control.xlsx` con pandas/openpyxl. Eso falla de
dos formas concretas:
  · si tienes el Excel abierto mientras corre el script, la escritura peta
  · si dos procesos escriben a la vez, el archivo se corrompe

SQLite maneja ambos casos y sigue siendo un único archivo que puedes copiar.
El Excel no desaparece: se exporta cuando lo pides, pero deja de ser la
fuente de verdad y pasa a ser una vista.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import config

RUTA_DB = config.RAIZ / "estudio.db"

ESQUEMA = """
CREATE TABLE IF NOT EXISTS videos (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    nicho        TEXT    NOT NULL,
    guion_id     TEXT    NOT NULL,
    tipo         TEXT    NOT NULL,          -- short | largo
    titulo       TEXT    NOT NULL,
    archivo      TEXT    NOT NULL UNIQUE,
    portada      TEXT,
    duracion     REAL,
    tam_mb       REAL,
    ancho        INTEGER,
    alto         INTEGER,
    tomas        INTEGER,
    creado_en    TEXT    NOT NULL,
    -- Estado de publicación. 'local' significa producido pero NO subido.
    estado       TEXT    NOT NULL DEFAULT 'local',
    youtube_id   TEXT,
    subido_en    TEXT,
    notas        TEXT
);

CREATE INDEX IF NOT EXISTS idx_nicho_estado ON videos(nicho, estado);
CREATE INDEX IF NOT EXISTS idx_guion        ON videos(guion_id);
"""


@dataclass
class Video:
    id: int
    nicho: str
    guion_id: str
    tipo: str
    titulo: str
    archivo: str
    portada: str | None
    duracion: float | None
    tam_mb: float | None
    creado_en: str
    estado: str
    youtube_id: str | None

    @property
    def existe(self) -> bool:
        return Path(self.archivo).exists()


@contextmanager
def _conexion():
    con = sqlite3.connect(RUTA_DB, timeout=15)
    con.row_factory = sqlite3.Row
    try:
        # WAL permite leer mientras otro proceso escribe — justamente lo que
        # el Excel de la v1 no soportaba.
        con.execute("PRAGMA journal_mode=WAL")
        yield con
        con.commit()
    finally:
        con.close()


def init() -> None:
    with _conexion() as con:
        con.executescript(ESQUEMA)


# ── Escritura ─────────────────────────────────────────────────────────────
def registrar(
    nicho: str,
    guion_id: str,
    tipo: str,
    titulo: str,
    archivo: Path,
    duracion: float | None = None,
    tomas: int | None = None,
    ancho: int | None = None,
    alto: int | None = None,
    portada: Path | None = None,
) -> int:
    """
    Anota un video recién renderizado. Devuelve su id.

    Re-producir el mismo archivo actualiza la fila en vez de duplicarla, pero
    NUNCA pisa el estado de publicación: si ya estaba subido, sigue subido.
    """
    init()
    tam = archivo.stat().st_size / 1e6 if archivo.exists() else None
    ahora = datetime.now().isoformat(timespec="seconds")

    with _conexion() as con:
        cur = con.execute(
            """
            INSERT INTO videos
                (nicho, guion_id, tipo, titulo, archivo, portada,
                 duracion, tam_mb, ancho, alto, tomas, creado_en)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(archivo) DO UPDATE SET
                titulo   = excluded.titulo,
                portada  = COALESCE(excluded.portada, videos.portada),
                duracion = excluded.duracion,
                tam_mb   = excluded.tam_mb,
                tomas    = excluded.tomas,
                creado_en= excluded.creado_en
            """,
            (
                nicho, guion_id, tipo, titulo, str(archivo),
                str(portada) if portada else None,
                duracion, tam, ancho, alto, tomas, ahora,
            ),
        )
        if cur.lastrowid:
            return cur.lastrowid
        fila = con.execute(
            "SELECT id FROM videos WHERE archivo = ?", (str(archivo),)
        ).fetchone()
        return fila["id"]


def marcar_subido(video_id: int, youtube_id: str) -> None:
    with _conexion() as con:
        con.execute(
            "UPDATE videos SET estado='subido', youtube_id=?, subido_en=? WHERE id=?",
            (youtube_id, datetime.now().isoformat(timespec="seconds"), video_id),
        )


def anotar(video_id: int, nota: str) -> None:
    with _conexion() as con:
        con.execute("UPDATE videos SET notas=? WHERE id=?", (nota, video_id))


# ── Lectura ───────────────────────────────────────────────────────────────
def _a_video(f: sqlite3.Row) -> Video:
    return Video(
        id=f["id"], nicho=f["nicho"], guion_id=f["guion_id"], tipo=f["tipo"],
        titulo=f["titulo"], archivo=f["archivo"], portada=f["portada"],
        duracion=f["duracion"], tam_mb=f["tam_mb"], creado_en=f["creado_en"],
        estado=f["estado"], youtube_id=f["youtube_id"],
    )


def listar(nicho: str | None = None, estado: str | None = None) -> list[Video]:
    init()
    consulta = "SELECT * FROM videos WHERE 1=1"
    params: list = []
    if nicho:
        consulta += " AND nicho = ?"
        params.append(nicho)
    if estado:
        consulta += " AND estado = ?"
        params.append(estado)
    consulta += " ORDER BY creado_en DESC"

    with _conexion() as con:
        return [_a_video(f) for f in con.execute(consulta, params)]


def sin_subir(nicho: str | None = None) -> list[Video]:
    """Videos producidos que todavía no se han publicado."""
    return [v for v in listar(nicho, estado="local") if v.existe]


def resumen() -> str:
    init()
    with _conexion() as con:
        filas = con.execute(
            """
            SELECT nicho, tipo, estado, COUNT(*) n,
                   ROUND(SUM(duracion)/60.0, 1) minutos,
                   ROUND(SUM(tam_mb), 1) mb
            FROM videos GROUP BY nicho, tipo, estado ORDER BY nicho, tipo
            """
        ).fetchall()

    if not filas:
        return "  (todavía no hay nada registrado)"

    lineas = [
        f"  {'nicho':<12} {'tipo':<6} {'estado':<8} {'n':>3} {'min':>6} {'MB':>7}",
        f"  {'-' * 47}",
    ]
    for f in filas:
        lineas.append(
            f"  {f['nicho']:<12} {f['tipo']:<6} {f['estado']:<8} "
            f"{f['n']:>3} {f['minutos'] or 0:>6} {f['mb'] or 0:>7}"
        )
    return "\n".join(lineas)


def exportar_excel(destino: Path | None = None) -> Path:
    """Vista en Excel. La base de datos sigue siendo la fuente de verdad."""
    from openpyxl import Workbook
    from openpyxl.styles import Font

    destino = destino or config.RAIZ / "control.xlsx"
    init()
    with _conexion() as con:
        filas = con.execute("SELECT * FROM videos ORDER BY creado_en DESC").fetchall()

    libro = Workbook()
    hoja = libro.active
    hoja.title = "Videos"

    if filas:
        cabeceras = list(filas[0].keys())
        hoja.append(cabeceras)
        for celda in hoja[1]:
            celda.font = Font(bold=True)
        for f in filas:
            hoja.append([f[c] for c in cabeceras])
        for i, c in enumerate(cabeceras, 1):
            ancho = max(len(c), *(len(str(f[c] or "")) for f in filas))
            hoja.column_dimensions[hoja.cell(1, i).column_letter].width = min(ancho + 2, 60)

    libro.save(destino)
    return destino

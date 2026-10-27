"""
diario.py — Producción y programación desatendida de un día de contenido.

    python cli.py diario finanzas            # prepara y programa MAÑANA
    python cli.py diario finanzas --simular  # todo menos subir

Qué hace, para la fecha objetivo (mañana por defecto):
  1. Mira el calendario del nicho (fase, cuántos shorts, si toca largo).
  2. Shorts: usa primero los ya producidos que nadie ha programado; si
     faltan, escribe guiones nuevos desde el banco de temas y los produce.
  3. Largo: si toca ese día, escribe y produce uno nuevo.
  4. Publicaciones de imagen para Facebook.
  5. Lo programa todo en YouTube (publishAt) y en Facebook
     (scheduled_publish_time) a las horas del calendario.

Es idempotente: cada envío queda anotado en `calendario.jsonl` del nicho
(fecha, hueco, destino). Si se relanza, solo hace lo que falta. Así un
fallo de red a las 3 a. m. se arregla relanzando, sin duplicar nada.

Publicar exige `publicacion_automatica: true` en el nicho. Las
credenciales que falten (token de YouTube, token de Facebook) no rompen la
noche: ese destino se salta con aviso y el resto sigue.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import guionista, pipeline, publicaciones, tracker
from .niche import Nicho

DIAS = ["lun", "mar", "mie", "jue", "vie", "sab", "dom"]


# ── Banco de temas ────────────────────────────────────────────────────────
def _ruta_banco(nicho: Nicho) -> Path:
    return nicho.dir / "temas.json"


def _titulos_usados(nicho: Nicho) -> list[str]:
    usados = []
    for ruta in nicho.dir_guiones.glob("*.json"):
        try:
            g = json.loads(ruta.read_text(encoding="utf-8"))
            usados.append(g.get("tema") or g.get("titulo", ""))
        except Exception:
            continue
    return [u for u in usados if u]


def _rellenar_banco(nicho: Nicho, formato: str, cuantos: int = 15) -> list[str]:
    esquema = {
        "type": "object",
        "properties": {"temas": {"type": "array", "items": {"type": "string"}}},
        "required": ["temas"],
    }
    usados = _titulos_usados(nicho)
    tipo = ("shorts de 30 a 75 segundos: una sola idea concreta y sorprendente"
            if formato == "short" else
            "videos largos de 6 a 10 minutos: un tema con profundidad, que se pueda "
            "explicar paso a paso con ejemplos y cálculos sencillos")
    sistema = (
        f"Eres el editor de contenidos de \"{nicho.titulo}\", un canal de finanzas "
        f"personales explicadas con datos reales para público colombiano y "
        f"latinoamericano. Propón {cuantos} temas nuevos para {tipo}.\n"
        "Cada tema es una frase de 8 a 18 palabras que describe el ángulo, no un "
        "título de YouTube. Variedad: ahorro, deudas, crédito, inflación, "
        "inversión básica, presupuesto, sueldo y prestaciones, vivienda, "
        "psicología del dinero, errores comunes. Nada de productos o bancos "
        "concretos ni promesas de riqueza.\n"
        + ("Temas ya cubiertos, no los repitas:\n" + "\n".join(f"- {u}" for u in usados[-80:])
           if usados else "")
    )
    datos, _ = guionista._generar_gemini(sistema, f"Propón {cuantos} temas.", esquema)
    return [t.strip() for t in datos["temas"] if t.strip()]


def siguiente_tema(nicho: Nicho, formato: str) -> str:
    ruta = _ruta_banco(nicho)
    banco = json.loads(ruta.read_text(encoding="utf-8")) if ruta.exists() else {}
    cola = banco.get(formato, [])
    if not cola:
        print(f"  🗂️  Banco de temas '{formato}' vacío: pidiendo temas nuevos…")
        cola = _rellenar_banco(nicho, formato)
    tema = cola.pop(0)
    banco[formato] = cola
    ruta.write_text(json.dumps(banco, ensure_ascii=False, indent=2), encoding="utf-8")
    return tema


# ── Calendario ────────────────────────────────────────────────────────────
@dataclass
class PlanDia:
    fecha: date
    shorts: int
    largo: bool
    posts: int
    horas_shorts: list[str]
    hora_largo: str
    horas_posts: list[str]


def plan(nicho: Nicho, fecha: date) -> PlanDia:
    cal = nicho.datos.get("calendario", {})
    inicio = cal.get("inicio") or fecha
    if isinstance(inicio, str):
        inicio = date.fromisoformat(inicio)
    dia_n = (fecha - inicio).days + 1

    fase = cal.get("fases", [{}])[-1]
    for f in cal.get("fases", []):
        if f.get("hasta_dia") is None or dia_n <= f["hasta_dia"]:
            fase = f
            break

    horas_shorts = cal.get("horas_shorts", ["12:00"])
    shorts = min(int(fase.get("shorts", 1)), len(horas_shorts))
    return PlanDia(
        fecha=fecha,
        shorts=shorts if dia_n >= 1 else 0,
        largo=dia_n >= 1 and DIAS[fecha.weekday()] in fase.get("largos_dias", []),
        posts=int(cal.get("posts_facebook", 0)) if dia_n >= 1 else 0,
        horas_shorts=horas_shorts[:shorts],
        hora_largo=cal.get("hora_largo", "18:00"),
        horas_posts=cal.get("horas_posts", [])[: int(cal.get("posts_facebook", 0))],
    )


def _momento(fecha: date, hora: str, zona: ZoneInfo) -> datetime:
    h, m = (int(x) for x in hora.split(":"))
    return datetime.combine(fecha, time(h, m), tzinfo=zona)


# ── Registro (idempotencia) ───────────────────────────────────────────────
class Registro:
    def __init__(self, nicho: Nicho, solo_memoria: bool = False):
        # En simulación se anota en memoria: si se escribiera en disco, la
        # noche real creería que esos huecos ya están asignados.
        self.solo_memoria = solo_memoria
        self.ruta = nicho.dir / "calendario.jsonl"
        self.filas = []
        if self.ruta.exists():
            self.filas = [json.loads(l) for l in
                          self.ruta.read_text(encoding="utf-8").splitlines() if l.strip()]

    def hecho(self, fecha: date, hueco: str, destino: str) -> bool:
        return any(f["fecha"] == fecha.isoformat() and f["hueco"] == hueco
                   and f["destino"] == destino for f in self.filas)

    def asignado(self, fecha: date, hueco: str) -> str | None:
        """Qué guion se le dio a este hueco (aunque no se haya subido aún)."""
        for f in self.filas:
            if f["fecha"] == fecha.isoformat() and f["hueco"] == hueco and f.get("guion_id"):
                return f["guion_id"]
        return None

    def ids_usados(self) -> set[str]:
        return {f["guion_id"] for f in self.filas if f.get("guion_id")}

    def anotar(self, **fila) -> None:
        fila["anotado_en"] = datetime.now().isoformat(timespec="seconds")
        self.filas.append(fila)
        if self.solo_memoria:
            return
        with open(self.ruta, "a", encoding="utf-8") as f:
            f.write(json.dumps(fila, ensure_ascii=False) + "\n")


# ── Producción ────────────────────────────────────────────────────────────
def _short_disponible(nicho: Nicho, registro: Registro) -> str | None:
    """El short producido más antiguo que nadie ha asignado todavía."""
    usados = registro.ids_usados()
    candidatos = [v for v in reversed(tracker.sin_subir(nicho.nombre))
                  if v.tipo == "short" and v.guion_id not in usados]
    return candidatos[0].guion_id if candidatos else None


def _producir_nuevo(nicho: Nicho, formato: str) -> str | None:
    tema = siguiente_tema(nicho, formato)
    try:
        guion = guionista.escribir(nicho, tema, formato)
        if not pipeline.producir(nicho.nombre, guion["id"], formato):
            return None
        return guion["id"]
    except Exception as e:
        print(f"  ❌ No se pudo producir '{tema}': {e}")
        return None


# ── Publicación ───────────────────────────────────────────────────────────
def _destinos(nicho: Nicho, simular: bool) -> dict[str, str | None]:
    """Qué destinos se pueden usar y, si no, por qué."""
    from . import config

    if simular:
        return {"youtube": "simulación", "facebook": "simulación"}
    if not nicho.datos.get("publicacion_automatica"):
        motivo = "publicacion_automatica está en false"
        return {"youtube": motivo, "facebook": motivo}
    estado = {"youtube": None, "facebook": None}
    if not (nicho.dir / "youtube_token.json").exists():
        estado["youtube"] = f"falta autorizar YouTube (python cli.py autorizar {nicho.nombre})"
    if not nicho.datos.get("facebook", {}).get("activo"):
        estado["facebook"] = "facebook.activo está en false"
    elif not (config.secreto("FACEBOOK_PAGE_ID") and config.secreto("FACEBOOK_PAGE_TOKEN")):
        estado["facebook"] = "faltan FACEBOOK_PAGE_ID / FACEBOOK_PAGE_TOKEN en .env"
    return estado


def _publicar_video(nicho, registro, destinos, fecha, hueco, guion_id, tipo, cuando):
    from . import facebook, subidor

    for destino in ("youtube", "facebook"):
        if registro.hecho(fecha, hueco, destino):
            print(f"  ✔️  {hueco} → {destino}: ya programado antes")
            continue
        if destinos[destino]:
            print(f"  ⏭️  {hueco} → {destino}: {destinos[destino]}")
            continue
        try:
            if destino == "youtube":
                db_id = next((v.id for v in tracker.listar(nicho.nombre)
                              if v.guion_id == guion_id and v.tipo == tipo), None)
                ident = subidor.subir(nicho.nombre, guion_id, tipo, confirmado=True,
                                      video_db_id=db_id, publicar_en=cuando)
            else:
                ident = facebook.subir(nicho.nombre, guion_id, tipo, confirmado=True,
                                       publicar_en=cuando)
            if ident:
                registro.anotar(fecha=fecha.isoformat(), hueco=hueco, destino=destino,
                                guion_id=guion_id, id=ident, hora=cuando.isoformat())
        except Exception as e:
            print(f"  ❌ {hueco} → {destino}: {e}")


def ejecutar(nicho_nombre: str, fecha: date | None = None, simular: bool = False) -> int:
    nicho = Nicho.cargar(nicho_nombre)
    zona = ZoneInfo(nicho.datos.get("calendario", {}).get("zona", "America/Bogota"))
    fecha = fecha or (datetime.now(zona).date() + timedelta(days=1))
    p = plan(nicho, fecha)
    registro = Registro(nicho, solo_memoria=simular)
    destinos = _destinos(nicho, simular)

    print(f"\n{'═' * 66}\n📅  {nicho.titulo} · {fecha:%A %Y-%m-%d}")
    print(f"    {p.shorts} shorts · {'1 largo' if p.largo else 'sin largo'} · "
          f"{p.posts} publicaciones de imagen")
    for d, motivo in destinos.items():
        print(f"    {d:9} {'✅ publica' if not motivo else '⏭️  ' + motivo}")
    print(f"{'═' * 66}")

    fallos = 0

    # Shorts
    for i, hora in enumerate(p.horas_shorts, 1):
        hueco = f"short_{i}"
        guion_id = registro.asignado(fecha, hueco) or _short_disponible(nicho, registro)
        if not guion_id:
            print(f"\n▸ {hueco}: no hay shorts en reserva, produciendo uno nuevo…")
            guion_id = _producir_nuevo(nicho, "short")
        if not guion_id:
            fallos += 1
            continue
        if not registro.asignado(fecha, hueco):
            registro.anotar(fecha=fecha.isoformat(), hueco=hueco, destino="asignado",
                            guion_id=guion_id, hora=hora)
        print(f"\n▸ {hueco} ({hora}) → {guion_id}")
        _publicar_video(nicho, registro, destinos, fecha, hueco, guion_id, "short",
                        _momento(fecha, hora, zona))

    # Largo
    if p.largo:
        hueco = "largo"
        guion_id = registro.asignado(fecha, hueco)
        if not guion_id:
            print("\n▸ largo: produciendo…")
            guion_id = _producir_nuevo(nicho, "largo")
            if guion_id:
                registro.anotar(fecha=fecha.isoformat(), hueco=hueco, destino="asignado",
                                guion_id=guion_id, hora=p.hora_largo)
        if guion_id:
            print(f"\n▸ largo ({p.hora_largo}) → {guion_id}")
            _publicar_video(nicho, registro, destinos, fecha, hueco, guion_id, "largo",
                            _momento(fecha, p.hora_largo, zona))
        else:
            fallos += 1

    # Publicaciones de imagen (solo Facebook)
    pendientes = [(i, h) for i, h in enumerate(p.horas_posts, 1)
                  if not registro.hecho(fecha, f"post_{i}", "facebook")]
    if pendientes:
        from . import facebook

        print(f"\n▸ {len(pendientes)} publicaciones de imagen")
        try:
            hechas = publicaciones.producir(nicho, len(pendientes), prefijo=fecha.isoformat())
        except Exception as e:
            print(f"  ❌ No se pudieron generar: {e}")
            hechas, fallos = [], fallos + 1
        for (i, hora), pub in zip(pendientes, hechas):
            hueco = f"post_{i}"
            if destinos["facebook"]:
                print(f"  ⏭️  {hueco} → facebook: {destinos['facebook']}")
                continue
            try:
                envio = facebook.preparar_imagen(nicho, pub.imagen, pub.texto_completo)
                envio.publicar_en = _momento(fecha, hora, zona)
                ident = facebook.enviar(envio, nicho, confirmado=True)
                if ident:
                    registro.anotar(fecha=fecha.isoformat(), hueco=hueco, destino="facebook",
                                    id=ident, imagen=str(pub.imagen), hora=hora)
            except Exception as e:
                print(f"  ❌ {hueco} → facebook: {e}")
                fallos += 1

    print(f"\n{'═' * 66}\n{'✅' if not fallos else '⚠️ '} Día {fecha} preparado"
          f"{f' con {fallos} fallo(s)' if fallos else ''}.\n{'═' * 66}")
    return fallos

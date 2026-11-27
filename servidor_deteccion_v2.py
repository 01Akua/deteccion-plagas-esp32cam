"""Servidor de deteccion de actividad de plagas - v2.

Cambios respecto a la v1 (Paquete_Sustentacion_Plagas/03_Codigo/servidor_deteccion.py):
  1. Un sustractor de fondo (MOG2) POR ZONA, en vez de uno global compartido.
  2. Rafaga: /upload acepta varios archivos 'image'. Los primeros entrenan el fondo
     y solo el ULTIMO se evalua (el firmware puede mandar varias fotos por disparo del PIR).
  3. Calentamiento: no se evalua hasta que la zona haya visto MIN_FRAMES_FONDO (5) frames.
     Rafaga minima por disparo = 6 fotos (5 entrenan, la ultima se evalua).
  4. Validacion de entradas: nombre de zona saneado (antes iba directo al nombre del
     archivo del snapshot) y limite de tamano de la peticion.
  5. Prototipo de fase 3: GET / (panel web en vivo: fotos recibidas, detecciones, historial y
     eventos; se alimenta de GET /api/estado y GET /frames/<id>/<n>.jpg), GET /events, GET /health.

Alcance (igual que la v1): detecta actividad compatible con plaga por TAMANO del
objeto en movimiento. NO identifica especie. No envia alertas locales ni remotas.
"""
import argparse
import csv
import glob
import os
import re
import threading
import time
from collections import deque
from datetime import datetime

import cv2
import numpy as np
from flask import Flask, Response, abort, jsonify, request, send_from_directory

try:
    import serial   # pyserial: solo hace falta para la camara por cable USB
except ImportError:
    serial = None

AREA_MINIMA_PX = 150
AREA_MAXIMA_PX = 8000
# Medido con escenas sinteticas (evaluar_deteccion.py): con <=4 frames de fondo MOG2 clasifica
# el objeto oscuro como 'sombra' y no lo detecta; con >=5 frames lo detecta siempre.
MIN_FRAMES_FONDO = 5
MAX_UPLOAD_BYTES = 8 * 1024 * 1024
CELDAS_MOVIMIENTO_DEFECTO = 25   # sensibilidad inicial del nodo (celdas de 80x60 que deben cambiar)
MAX_RAFAGAS_MEMORIA = 12   # rafagas recientes que el panel puede mostrar
ZONA_VALIDA = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
CAMPOS_CSV = ["timestamp", "zona", "area_px", "objetos", "snapshot"]


class DetectorZona:
    """Estado de deteccion de una sola zona (su propio modelo de fondo)."""

    def __init__(self, area_min, area_max):
        self.area_min = area_min
        self.area_max = area_max
        self.sustractor = cv2.createBackgroundSubtractorMOG2(
            history=300, varThreshold=40, detectShadows=True
        )
        self.frames_vistos = 0
        self.lock = threading.Lock()

    def entrenar(self, frame):
        self.sustractor.apply(frame)
        self.frames_vistos += 1

    def evaluar(self, frame):
        """Devuelve (frame_anotado, eventos). Cada evento: {'area_px', 'bbox'}."""
        mascara = self.sustractor.apply(frame)
        self.frames_vistos += 1
        _, mascara = cv2.threshold(mascara, 200, 255, cv2.THRESH_BINARY)
        mascara = cv2.erode(mascara, None, iterations=1)
        mascara = cv2.dilate(mascara, None, iterations=2)
        contornos, _ = cv2.findContours(
            mascara, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        anotado = frame.copy()
        eventos = []
        for c in contornos:
            area = cv2.contourArea(c)
            if self.area_min <= area <= self.area_max:
                x, y, w, h = cv2.boundingRect(c)
                cv2.rectangle(anotado, (x, y), (x + w, y + h), (0, 0, 255), 2)
                cv2.putText(
                    anotado, "Posible actividad", (x, max(y - 10, 0)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2,
                )
                eventos.append({"area_px": int(area), "bbox": [x, y, w, h]})
        return anotado, eventos


def buscar_puerto_usb():
    """Puerto serie de la placa conectada por cable (macOS), o None."""
    puertos = sorted(glob.glob("/dev/cu.usbmodem*") + glob.glob("/dev/cu.usbserial*"))
    return puertos[0] if puertos else None


def crear_app(directorio_logs="logs", area_min=AREA_MINIMA_PX, area_max=AREA_MAXIMA_PX,
              min_frames_fondo=MIN_FRAMES_FONDO, guardar_rafagas=None, puerto_serie=None):
    dir_snapshots = os.path.join(directorio_logs, "snapshots")
    dir_fotos_usb = os.path.join(directorio_logs, "fotos_usb")
    archivo_csv = os.path.join(directorio_logs, "eventos.csv")
    os.makedirs(dir_snapshots, exist_ok=True)
    if not os.path.exists(archivo_csv):
        with open(archivo_csv, "w", newline="") as f:
            csv.writer(f).writerow(CAMPOS_CSV)

    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
    detectores = {}
    historial = deque(maxlen=MAX_RAFAGAS_MEMORIA)   # ultimas rafagas con sus fotos (para el panel)
    totales = {"rafagas": 0, "fotos": 0, "alertas": 0}
    estado_ultima = {"t": None}
    lock_hist = threading.Lock()
    # Control del nodo: el panel deja ordenes y el nodo las recoge al consultar GET /cmd
    control = {"disparar": False, "auto": False, "celdas": CELDAS_MOVIMIENTO_DEFECTO}   # auto apagado: solo dispara con el boton del panel
    nodo = {"t": None, "mov": 0, "anillo": 0, "zona": None}
    lock_ctl = threading.Lock()
    lock_global = threading.Lock()
    lock_csv = threading.Lock()

    def detector_de(zona):
        with lock_global:
            if zona not in detectores:
                detectores[zona] = DetectorZona(area_min, area_max)
            return detectores[zona]

    def registrar(zona, eventos, anotado):
        ahora = datetime.now()
        marca = ahora.strftime("%Y-%m-%d_%H-%M-%S-%f")[:-3]
        nombre = f"{marca}_{zona}.jpg"
        cv2.imwrite(os.path.join(dir_snapshots, nombre), anotado)
        area_max_ev = max(e["area_px"] for e in eventos)
        with lock_csv, open(archivo_csv, "a", newline="") as f:
            csv.writer(f).writerow(
                [ahora.strftime("%Y-%m-%d %H:%M:%S"), zona, area_max_ev, len(eventos), nombre]
            )
        print(f"[ALERTA] {marca} | zona={zona} | area={area_max_ev}px | objetos={len(eventos)}")
        return nombre

    def leer_eventos(limite=20):
        with lock_csv, open(archivo_csv, newline="") as f:
            filas = list(csv.DictReader(f))
        return filas[-limite:][::-1]

    def guardar_en_historial(zona, frames, crudos, estado, eventos, snapshot, origen=""):
        alto, ancho = frames[-1].shape[:2]
        with lock_hist:
            totales["rafagas"] += 1
            totales["fotos"] += len(crudos)
            totales["alertas"] += 1 if eventos else 0
            estado_ultima["t"] = time.time()
            historial.append({"id": totales["rafagas"], "zona": zona, "estado": estado,
                              "hora": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                              "alerta": bool(eventos), "eventos": eventos, "snapshot": snapshot,
                              "origen": origen, "n_fotos": len(crudos), "ancho": ancho, "alto": alto, "_fotos": crudos})

    lock_usb = threading.Lock()

    def foto_por_usb(timeout=15):
        """Pide UNA foto a la placa por el cable (orden FOTO) y devuelve los bytes del JPEG.
        Firmware: firmware/prueba_camara_usb. La placa nunca toma fotos por su cuenta."""
        if serial is None:
            raise RuntimeError("falta instalar pyserial (pip install pyserial)")
        puerto = puerto_serie or buscar_puerto_usb()
        if not puerto:
            raise RuntimeError("no hay ninguna placa conectada por USB")
        with serial.Serial(puerto, 115200, timeout=1) as s:
            s.reset_input_buffer()
            s.write(b"FOTO\n")
            fin = time.time() + timeout
            while time.time() < fin:
                linea = s.readline().decode("utf-8", "replace").strip()
                if "camara lista" in linea:
                    s.write(b"FOTO\n")       # la placa se reinicio al abrir el puerto: repetir la orden
                elif linea.startswith("FOTO_ERROR"):
                    raise RuntimeError(linea)
                elif linea.startswith("FOTO_INICIO"):
                    n = int(linea.split()[1])
                    datos = s.read(n)
                    if len(datos) != n or datos[:2] != b"\xff\xd8":
                        raise RuntimeError(f"foto incompleta ({len(datos)}/{n} bytes)")
                    return datos
        raise RuntimeError("la placa no respondio (revise el cable y que tenga el firmware de prueba USB)")

    @app.route("/api/usb/foto", methods=["POST"])
    def api_usb_foto():
        with lock_usb:
            try:
                crudo = foto_por_usb()
            except Exception as e:
                return jsonify({"ok": False, "error": str(e)}), 503
        frame = cv2.imdecode(np.frombuffer(crudo, np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            return jsonify({"ok": False, "error": "la imagen recibida no es valida"}), 502
        os.makedirs(dir_fotos_usb, exist_ok=True)
        with open(os.path.join(dir_fotos_usb, datetime.now().strftime("%Y%m%d_%H%M%S") + ".jpg"), "wb") as f:
            f.write(crudo)
        guardar_en_historial("Cable-USB", [frame], [crudo], "foto_usb", [], None, "cable USB")
        return jsonify({"ok": True, "bytes": len(crudo)})

    @app.route("/health")
    def health():
        return jsonify({"estado": "ok", "zonas": sorted(detectores.keys())})

    @app.route("/upload", methods=["POST"])
    def upload():
        zona = request.form.get("zone", "Zona-1")
        origen = request.form.get("origen", "desconocido")[:20]
        if not ZONA_VALIDA.match(zona):
            return jsonify({"error": "zona invalida (use letras, numeros, - o _; max 32)"}), 400
        archivos = request.files.getlist("image")
        if not archivos:
            return jsonify({"error": "falta el campo 'image'"}), 400

        frames, crudos = [], []
        for a in archivos:
            crudo = a.read()
            datos = np.frombuffer(crudo, np.uint8)
            frame = cv2.imdecode(datos, cv2.IMREAD_COLOR)
            if frame is None:
                return jsonify({"error": "no se pudo decodificar la imagen"}), 400
            frames.append(frame)
            crudos.append(crudo)
            if guardar_rafagas:  # opcional: conservar las fotos recibidas (evidencia/calibracion)
                os.makedirs(guardar_rafagas, exist_ok=True)
                marca = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                with open(os.path.join(guardar_rafagas, f"{marca}_{zona}_{len(frames)}.jpg"), "wb") as f:
                    f.write(crudo)

        det = detector_de(zona)
        with det.lock:
            for f in frames[:-1]:
                det.entrenar(f)
            if det.frames_vistos < min_frames_fondo:
                det.entrenar(frames[-1])
                guardar_en_historial(zona, frames, crudos, "calentando", [], None, origen)
                return jsonify({"alerta": False, "eventos": [], "estado": "calentando",
                                "frames_vistos": det.frames_vistos})
            anotado, eventos = det.evaluar(frames[-1])

        snapshot = registrar(zona, eventos, anotado) if eventos else None
        guardar_en_historial(zona, frames, crudos, "evaluado", eventos, snapshot, origen)
        return jsonify({"alerta": bool(eventos), "eventos": eventos, "snapshot": snapshot,
                        "estado": "evaluado"})

    @app.route("/events")
    def events():
        return jsonify(leer_eventos(int(request.args.get("n", 20))))

    @app.route("/snapshots/<path:nombre>")
    def snapshots(nombre):
        return send_from_directory(os.path.abspath(dir_snapshots), nombre)

    @app.route("/cmd")
    def cmd():
        """El nodo consulta aqui (cada ~2 s): recibe ordenes/ajustes y reporta su estado."""
        with lock_ctl:
            nodo.update(t=time.time(), zona=request.args.get("zone"),
                        mov=request.args.get("mov", 0, type=int), anillo=request.args.get("anillo", 0, type=int))
            orden = control["disparar"]
            control["disparar"] = False          # la orden se entrega una sola vez
            return jsonify({"disparar": orden, "auto": control["auto"], "celdas": control["celdas"]})

    @app.route("/api/disparar", methods=["POST"])
    def api_disparar():
        with lock_ctl:
            control["disparar"] = True
        return jsonify({"ok": True})

    @app.route("/api/config", methods=["POST"])
    def api_config():
        datos = request.get_json(silent=True) or {}
        with lock_ctl:
            if "auto" in datos:
                control["auto"] = bool(datos["auto"])
            if "celdas" in datos:
                control["celdas"] = max(1, min(2000, int(datos["celdas"])))
            return jsonify({"auto": control["auto"], "celdas": control["celdas"]})

    @app.route("/api/estado")
    def api_estado():
        with lock_hist:
            rafagas = [{k: v for k, v in r.items() if k != "_fotos"} for r in reversed(historial)]
            desde = None if estado_ultima["t"] is None else round(time.time() - estado_ultima["t"], 1)
            tot = dict(totales)
        with lock_ctl:
            desde_nodo = None if nodo["t"] is None else round(time.time() - nodo["t"], 1)
            info_nodo = {"segundos_desde_consulta": desde_nodo, "mov": nodo["mov"], "anillo": nodo["anillo"],
                         "disparo_pendiente": control["disparar"], "auto": control["auto"], "celdas": control["celdas"]}
        with lock_global:
            zonas = {z: {"frames_vistos": d.frames_vistos, "listo": d.frames_vistos >= min_frames_fondo}
                     for z, d in detectores.items()}
        puerto_usb = puerto_serie or buscar_puerto_usb()
        return jsonify({"nodo": info_nodo, "totales": tot, "segundos_desde_ultima": desde, "zonas": zonas, "rafagas": rafagas,
                        "usb": {"disponible": serial is not None and bool(puerto_usb), "puerto": puerto_usb},
                        "eventos": leer_eventos(15),
                        "parametros": {"area_min": area_min, "area_max": area_max,
                                       "min_frames_fondo": min_frames_fondo}})

    @app.route("/frames/<int:id_rafaga>/<int:n>.jpg")
    def frame(id_rafaga, n):
        with lock_hist:
            for r in historial:
                if r["id"] == id_rafaga and 0 <= n < len(r["_fotos"]):
                    return Response(r["_fotos"][n], mimetype="image/jpeg")
        abort(404)

    @app.route("/")
    def panel():
        return send_from_directory(os.path.dirname(os.path.abspath(__file__)), "panel.html")

    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--puerto", type=int, default=5000)
    parser.add_argument("--logs", default="logs")
    parser.add_argument("--guardar-rafagas", default=None, help="carpeta donde guardar las fotos recibidas")
    parser.add_argument("--puerto-serie", default=None, help="puerto de la camara por cable USB (por defecto se detecta solo)")
    args = parser.parse_args()
    print(f"Servidor v2 escuchando en http://0.0.0.0:{args.puerto}/  (POST /upload)")
    crear_app(args.logs, guardar_rafagas=args.guardar_rafagas, puerto_serie=args.puerto_serie).run(
        host="0.0.0.0", port=args.puerto, debug=False)

"""Generador de escenas sinteticas (piso de cocina visto desde arriba, 640x480).

Se usan SOLO porque aun no hay imagenes reales de la ESP32-CAM. Simulan:
  - fondo con baldosas y ruido de sensor distinto por zona,
  - compresion JPEG como la que envia la camara,
  - objetos pequenos oscuros (plaga), motas, objetos grandes y cambios de luz.
"""
import cv2
import numpy as np

ANCHO, ALTO = 640, 480


def fondo_base(semilla_zona):
    """Fondo fijo de una zona: baldosas con tono y textura propios."""
    rng = np.random.default_rng(semilla_zona)
    tono = int(rng.integers(110, 190))
    base = np.full((ALTO, ANCHO), tono, np.float32)
    paso = int(rng.integers(70, 110))
    for x in range(0, ANCHO, paso):
        base[:, x:x + 3] -= 28
    for y in range(0, ALTO, paso):
        base[y:y + 3, :] -= 28
    textura = cv2.GaussianBlur(rng.normal(0, 6, (ALTO, ANCHO)).astype(np.float32), (0, 0), 3)
    return base + textura


def frame_vacio(fondo, rng, brillo=0.0):
    """Un frame del fondo con ruido de sensor y leve cambio de brillo."""
    img = fondo + brillo + rng.normal(0, 3, fondo.shape)
    img = np.clip(img, 0, 255).astype(np.uint8)
    return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)


def dibujar_elipse(img, centro, ejes, color):
    cv2.ellipse(img, centro, ejes, float(np.random.default_rng(centro[0]).integers(0, 180)),
                0, 360, color, -1)


def agregar_objeto(img, rng, area_objetivo, oscuro=True):
    """Dibuja una elipse de area aproximada `area_objetivo` en posicion aleatoria."""
    relacion = rng.uniform(1.4, 2.6)
    b = max(1, int(round(np.sqrt(area_objetivo / (np.pi * relacion)))))
    a = max(1, int(round(b * relacion)))
    margen = a + 5
    cx = int(rng.integers(margen, ANCHO - margen))
    cy = int(rng.integers(margen, ALTO - margen))
    color = (35, 35, 35) if oscuro else (225, 225, 225)
    dibujar_elipse(img, (cx, cy), (a, b), color)
    return img


def a_jpeg(img, calidad=80):
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, calidad])
    assert ok
    return buf.tobytes()


# tipo -> (es_positivo, descripcion)
TIPOS = {
    "vacia": (False, "sin objeto"),
    "mota": (False, "mota/ruido menor al area minima"),
    "plaga": (True, "objeto del tamano de una plaga"),
    "grande": (False, "objeto mayor al area maxima (persona, carrito)"),
    "luz": (False, "cambio brusco de iluminacion"),
}
PESOS = {"vacia": 0.25, "mota": 0.15, "plaga": 0.35, "grande": 0.10, "luz": 0.15}


def generar_disparo(tipo, fondo, rng):
    """Imagen (BGR) del instante del disparo del PIR para un tipo de escena."""
    if tipo == "luz":
        img = frame_vacio(fondo, rng, brillo=float(rng.choice([-1, 1]) * rng.uniform(28, 45)))
    else:
        img = frame_vacio(fondo, rng)
    if tipo == "mota":
        agregar_objeto(img, rng, float(rng.uniform(25, 100)))
    elif tipo == "plaga":
        agregar_objeto(img, rng, float(rng.uniform(250, 3000)), oscuro=bool(rng.random() < 0.8))
    elif tipo == "grande":
        agregar_objeto(img, rng, float(rng.uniform(14000, 40000)))
    return img

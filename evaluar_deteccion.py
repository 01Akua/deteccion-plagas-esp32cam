"""Evaluacion con escenas SINTETICAS: servidor v1 (original) vs v2.

Uso:  python evaluar_deteccion.py        (genera resultados/ con metricas, figuras y logs)

Todas las imagenes son sinteticas (ver escenas.py). Los numeros miden si la LOGICA de
deteccion funciona; NO prueban el desempeno con la camara real ni con plagas reales.
"""
import importlib.util
import json
import os
import shutil
import tempfile
import time
from io import BytesIO

import cv2
import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

import escenas  # noqa: E402
from servidor_deteccion_v2 import crear_app  # noqa: E402

AQUI = os.path.dirname(os.path.abspath(__file__))
SALIDA = os.path.join(AQUI, "resultados")
RUTA_V1 = os.path.join(AQUI, "..", "Paquete_Sustentacion_Plagas", "03_Codigo", "servidor_deteccion.py")
TRIGGERS_POR_ZONA = 150
SEMILLA = 2026
AZUL, GRIS, ROJO = "#1f5fa8", "#8a8f98", "#c0392b"


def plan_escenas(n_zonas):
    """Mismo plan de escenas para todas las configuraciones (semilla fija)."""
    rng = np.random.default_rng(SEMILLA)
    tipos, pesos = list(escenas.PESOS), list(escenas.PESOS.values())
    return [(i % n_zonas, str(rng.choice(tipos, p=pesos))) for i in range(TRIGGERS_POR_ZONA * n_zonas)]


def metricas(resultados):
    tp = sum(1 for t, a in resultados if escenas.TIPOS[t][0] and a)
    fn = sum(1 for t, a in resultados if escenas.TIPOS[t][0] and not a)
    fp = sum(1 for t, a in resultados if not escenas.TIPOS[t][0] and a)
    tn = sum(1 for t, a in resultados if not escenas.TIPOS[t][0] and not a)
    div = lambda a, b: round(a / b, 3) if b else None  # noqa: E731
    por_tipo = {}
    for tipo in escenas.TIPOS:
        r = [a for t, a in resultados if t == tipo]
        por_tipo[tipo] = {"n": len(r), "alertas": sum(r)}
    return {"TP": tp, "FP": fp, "FN": fn, "TN": tn, "precision": div(tp, tp + fp),
            "recall": div(tp, tp + fn), "tasa_falsa_alarma": div(fp, fp + tn),
            "exactitud": div(tp + tn, tp + fp + fn + tn), "por_tipo": por_tipo}


def cargar_v1():
    """Importa el servidor ORIGINAL sin modificarlo (crea su carpeta logs en un tmp)."""
    tmp = tempfile.mkdtemp()
    cwd = os.getcwd()
    os.chdir(tmp)
    try:
        spec = importlib.util.spec_from_file_location("servidor_v1", os.path.abspath(RUTA_V1))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        os.chdir(cwd)
    return mod


def correr_v1(n_zonas):
    """v1: un MOG2 global, UNA foto por disparo (como hace el firmware actual)."""
    mod = cargar_v1()
    rng = np.random.default_rng(SEMILLA + 1)
    fondos = [escenas.fondo_base(100 + z) for z in range(n_zonas)]
    res, t_ms = [], []
    for zona, tipo in plan_escenas(n_zonas):
        img = escenas.generar_disparo(tipo, fondos[zona], rng)
        frame = cv2.imdecode(np.frombuffer(escenas.a_jpeg(img), np.uint8), cv2.IMREAD_COLOR)
        t0 = time.perf_counter()
        _, alerta, _ = mod.procesar_frame(frame, f"Zona-{zona + 1}")
        t_ms.append((time.perf_counter() - t0) * 1000)
        res.append((tipo, alerta))
    return res, float(np.mean(t_ms))


def correr_v2(n_zonas, dir_logs):
    """v2: MOG2 por zona; cada disparo manda una rafaga de 6 fotos (5 de fondo + 1)."""
    app = crear_app(dir_logs)
    app.testing = True
    cli = app.test_client()
    rng = np.random.default_rng(SEMILLA + 1)
    fondos = [escenas.fondo_base(100 + z) for z in range(n_zonas)]
    res, t_ms, ejemplos = [], [], {}
    for zona, tipo in plan_escenas(n_zonas):
        previos = [escenas.frame_vacio(fondos[zona], rng) for _ in range(5)]
        disparo = escenas.generar_disparo(tipo, fondos[zona], rng)
        datos = {"zone": f"Zona-{zona + 1}",
                 "image": [(BytesIO(escenas.a_jpeg(f)), "f.jpg") for f in previos + [disparo]]}
        t0 = time.perf_counter()
        r = cli.post("/upload", data=datos, content_type="multipart/form-data").get_json()
        t_ms.append((time.perf_counter() - t0) * 1000)
        res.append((tipo, r["alerta"]))
        if tipo not in ejemplos:
            ejemplos[tipo] = (disparo, r)
    return res, float(np.mean(t_ms)), ejemplos


def experimento_calentamiento():
    """Tasa de deteccion de un objeto tipo plaga segun cuantos frames de fondo se vieron."""
    xs, ys = list(range(1, 11)), []
    for n_prev in xs:
        ok = 0
        for s in range(40):
            rng = np.random.default_rng(s)
            fondo = escenas.fondo_base(s % 5)
            sub = cv2.createBackgroundSubtractorMOG2(history=300, varThreshold=40, detectShadows=True)
            for _ in range(n_prev):
                sub.apply(escenas.frame_vacio(fondo, rng))
            img = escenas.frame_vacio(fondo, rng)
            escenas.agregar_objeto(img, rng, 900, oscuro=(s % 5 != 0))
            ok += int((sub.apply(img) == 255).sum() >= 150)
        ys.append(ok / 40)
    return xs, ys


def figura_comparacion(m):
    nombres = ["v1 · 1 zona\n(1 foto/disparo)", "v1 · 2 zonas\n(1 foto/disparo)", "v2 · 2 zonas\n(rafaga de 6)"]
    claves = [("recall", "Recall\n(plagas detectadas)"), ("precision", "Precision\n(alertas correctas)"),
              ("tasa_falsa_alarma", "Tasa de falsa alarma\n(menor es mejor)")]
    fig, ejes = plt.subplots(1, 3, figsize=(11, 3.8), sharey=True)
    for ax, (k, titulo) in zip(ejes, claves):
        vals = [m[c][k] or 0 for c in ("v1_1zona", "v1_2zonas", "v2_2zonas")]
        barras = ax.bar(nombres, vals, color=[GRIS, GRIS, AZUL])
        ax.set_title(titulo, fontsize=10)
        ax.set_ylim(0, 1.05)
        ax.tick_params(axis="x", labelsize=8)
        for b, v in zip(barras, vals):
            ax.text(b.get_x() + b.get_width() / 2, v + 0.02, f"{v:.2f}", ha="center", fontsize=9)
        ax.spines[["top", "right"]].set_visible(False)
    fig.suptitle("Escenas sinteticas: 150 disparos (1 zona) y 300 disparos (2 zonas)", fontsize=11)
    fig.tight_layout()
    fig.savefig(os.path.join(SALIDA, "fig1_comparacion.png"), dpi=150)
    plt.close(fig)


def figura_calentamiento(xs, ys):
    fig, ax = plt.subplots(figsize=(6, 3.6))
    ax.plot(xs, [y * 100 for y in ys], "o-", color=AZUL)
    ax.axvline(5, color=ROJO, ls="--", lw=1)
    ax.text(5.15, 8, "minimo elegido: 5 frames", color=ROJO, fontsize=9)
    ax.set_xlabel("Frames de fondo vistos antes del disparo")
    ax.set_ylabel("Deteccion del objeto (%)")
    ax.set_xticks(xs)
    ax.set_ylim(-3, 105)
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_title("Por que la rafaga debe ser de al menos 6 fotos", fontsize=10)
    fig.tight_layout()
    fig.savefig(os.path.join(SALIDA, "fig3_calentamiento.png"), dpi=150)
    plt.close(fig)


def figura_ejemplos(ejemplos):
    orden = ["plaga", "mota", "grande", "luz", "vacia"]
    fig, ejes = plt.subplots(1, 5, figsize=(15, 3.6))
    for ax, tipo in zip(ejes, orden):
        img, r = ejemplos[tipo]
        vista = img.copy()
        for e in r["eventos"]:
            x, y, w, h = e["bbox"]
            cv2.rectangle(vista, (x, y), (x + w, y + h), (0, 0, 255), 3)
        ax.imshow(cv2.cvtColor(vista, cv2.COLOR_BGR2RGB))
        res = "ALERTA" if r["alerta"] else "sin alerta"
        ok = r["alerta"] == escenas.TIPOS[tipo][0]
        ax.set_title(f"{tipo}: {res}\n{escenas.TIPOS[tipo][1]}", fontsize=8,
                     color="black" if ok else ROJO)
        ax.axis("off")
    fig.suptitle("Ejemplos (sinteticos) - recuadro rojo = 'posible actividad'", fontsize=11)
    fig.tight_layout()
    fig.savefig(os.path.join(SALIDA, "fig2_ejemplos.png"), dpi=150)
    plt.close(fig)


def main():
    if os.path.isdir(SALIDA):
        shutil.rmtree(SALIDA)
    os.makedirs(SALIDA)
    r1a, t1a = correr_v1(1)
    r1b, t1b = correr_v1(2)
    r2, t2, ejemplos = correr_v2(2, os.path.join(SALIDA, "logs_v2"))
    xs, ys = experimento_calentamiento()
    m = {"v1_1zona": metricas(r1a), "v1_2zonas": metricas(r1b), "v2_2zonas": metricas(r2),
         "ms_por_disparo": {"v1": round(t1b, 1), "v2_rafaga_6_fotos": round(t2, 1)},
         "calentamiento": dict(zip([str(x) for x in xs], ys)), "semilla": SEMILLA,
         "disparos_por_config": TRIGGERS_POR_ZONA * 2}
    with open(os.path.join(SALIDA, "metricas.json"), "w") as f:
        json.dump(m, f, indent=2, ensure_ascii=False)
    figura_comparacion(m)
    figura_calentamiento(xs, ys)
    figura_ejemplos(ejemplos)
    for k in ("v1_1zona", "v1_2zonas", "v2_2zonas"):
        x = m[k]
        print(f"{k:10s} TP={x['TP']:3d} FP={x['FP']:3d} FN={x['FN']:3d} TN={x['TN']:3d} "
              f"recall={x['recall']} precision={x['precision']} falsa_alarma={x['tasa_falsa_alarma']}")
    print("ms/disparo:", m["ms_por_disparo"])
    print("calentamiento:", m["calentamiento"])


if __name__ == "__main__":
    main()

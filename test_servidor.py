"""Pruebas automaticas del servidor v2.  Ejecutar:  python -m pytest -v test_servidor.py"""
import csv
import os

import numpy as np
import pytest

import escenas
from servidor_deteccion_v2 import crear_app


@pytest.fixture
def cliente(tmp_path):
    app = crear_app(str(tmp_path / "logs"))
    app.testing = True
    return app.test_client(), tmp_path / "logs"


def _subir(cli, imgs, zona="Zona-1"):
    from io import BytesIO
    datos = {"zone": zona, "image": [(BytesIO(escenas.a_jpeg(i)), "f.jpg") for i in imgs]}
    return cli.post("/upload", data=datos, content_type="multipart/form-data")


def _calentar(cli, fondo, rng, zona="Zona-1", n=5):
    return _subir(cli, [escenas.frame_vacio(fondo, rng) for _ in range(n)], zona)


@pytest.fixture
def fondo():
    return escenas.fondo_base(7)


@pytest.fixture
def rng():
    return np.random.default_rng(0)


def test_health(cliente):
    cli, _ = cliente
    r = cli.get("/health")
    assert r.status_code == 200 and r.get_json()["estado"] == "ok"


def test_sin_imagen_da_400(cliente):
    cli, _ = cliente
    assert cli.post("/upload", data={"zone": "Z1"}).status_code == 400


def test_imagen_corrupta_da_400(cliente):
    from io import BytesIO
    cli, _ = cliente
    r = cli.post("/upload", data={"zone": "Z1", "image": (BytesIO(b"no soy jpeg"), "x.jpg")},
                 content_type="multipart/form-data")
    assert r.status_code == 400


@pytest.mark.parametrize("zona", ["../../etc", "a b", "x" * 33, "zona/1", ""])
def test_zona_invalida_se_rechaza(cliente, fondo, rng, zona):
    cli, _ = cliente
    assert _subir(cli, [escenas.frame_vacio(fondo, rng)], zona).status_code == 400


def test_primer_disparo_calienta_y_no_alerta(cliente, fondo, rng):
    cli, _ = cliente
    r = _subir(cli, [escenas.frame_vacio(fondo, rng)]).get_json()
    assert r["estado"] == "calentando" and r["alerta"] is False


def test_detecta_objeto_tamano_plaga_y_registra(cliente, fondo, rng):
    cli, logs = cliente
    _calentar(cli, fondo, rng)
    img = escenas.frame_vacio(fondo, rng)
    escenas.agregar_objeto(img, rng, 900)
    r = _subir(cli, [img]).get_json()
    assert r["alerta"] is True and 150 <= r["eventos"][0]["area_px"] <= 8000
    filas = list(csv.DictReader(open(logs / "eventos.csv")))
    assert len(filas) == 1 and os.path.exists(logs / "snapshots" / filas[0]["snapshot"])


def test_ignora_mota_menor_al_area_minima(cliente, fondo, rng):
    cli, _ = cliente
    _calentar(cli, fondo, rng)
    img = escenas.frame_vacio(fondo, rng)
    escenas.agregar_objeto(img, rng, 40)
    assert _subir(cli, [img]).get_json()["alerta"] is False


def test_ignora_objeto_mayor_al_area_maxima(cliente, fondo, rng):
    cli, _ = cliente
    _calentar(cli, fondo, rng)
    img = escenas.frame_vacio(fondo, rng)
    escenas.agregar_objeto(img, rng, 25000)
    assert _subir(cli, [img]).get_json()["alerta"] is False


def test_cambio_brusco_de_luz_no_alerta(cliente, fondo, rng):
    cli, _ = cliente
    _calentar(cli, fondo, rng)
    assert _subir(cli, [escenas.frame_vacio(fondo, rng, brillo=40)]).get_json()["alerta"] is False


def test_rafaga_entrena_con_los_primeros_y_evalua_el_ultimo(cliente, fondo, rng):
    cli, _ = cliente
    vacios = [escenas.frame_vacio(fondo, rng) for _ in range(5)]
    con_objeto = escenas.frame_vacio(fondo, rng)
    escenas.agregar_objeto(con_objeto, rng, 900)
    r = _subir(cli, vacios + [con_objeto]).get_json()   # rafaga en UNA sola peticion
    assert r["estado"] == "evaluado" and r["alerta"] is True


def test_zonas_son_independientes(cliente, rng):
    """Con un MOG2 global (v1), alternar zonas con fondos distintos contamina el modelo."""
    cli, _ = cliente
    f1, f2 = escenas.fondo_base(1), escenas.fondo_base(2)
    for _ in range(6):
        _calentar(cli, f1, rng, "Zona-A", 1)
        _calentar(cli, f2, rng, "Zona-B", 1)
    img = escenas.frame_vacio(f1, rng)
    escenas.agregar_objeto(img, rng, 900)
    assert _subir(cli, [img], "Zona-A").get_json()["alerta"] is True


def test_events_y_panel(cliente, fondo, rng):
    cli, _ = cliente
    _calentar(cli, fondo, rng)
    img = escenas.frame_vacio(fondo, rng)
    escenas.agregar_objeto(img, rng, 900)
    _subir(cli, [img])
    assert len(cli.get("/events").get_json()) == 1
    assert "Panel" in cli.get("/").get_data(as_text=True) or "panel" in cli.get("/").get_data(as_text=True)


def test_api_estado_y_frames_para_el_panel(cliente, fondo, rng):
    cli, _ = cliente
    vacio = cli.get("/api/estado").get_json()
    assert vacio["segundos_desde_ultima"] is None and vacio["rafagas"] == []
    _calentar(cli, fondo, rng)
    img = escenas.frame_vacio(fondo, rng)
    escenas.agregar_objeto(img, rng, 900)
    _subir(cli, [img])
    est = cli.get("/api/estado").get_json()
    assert est["totales"]["rafagas"] == 2 and est["totales"]["alertas"] == 1
    ultima = est["rafagas"][0]                      # la mas reciente primero
    assert ultima["alerta"] is True and ultima["n_fotos"] == 1 and "_fotos" not in ultima
    assert est["zonas"]["Zona-1"]["listo"] is True
    r = cli.get(f"/frames/{ultima['id']}/0.jpg")
    assert r.status_code == 200 and r.mimetype == "image/jpeg"
    assert cli.get("/frames/999/0.jpg").status_code == 404


def test_control_remoto_disparar_y_config(cliente):
    cli, _ = cliente
    assert cli.get("/cmd?zone=Zona-1&mov=3&anillo=5").get_json()["disparar"] is False
    assert cli.post("/api/disparar").get_json()["ok"] is True
    assert cli.get("/api/estado").get_json()["nodo"]["disparo_pendiente"] is True
    assert cli.get("/cmd").get_json()["disparar"] is True      # el nodo recoge la orden
    assert cli.get("/cmd").get_json()["disparar"] is False     # ...y solo una vez
    r = cli.post("/api/config", json={"auto": False, "celdas": 40}).get_json()
    assert r == {"auto": False, "celdas": 40}
    assert cli.get("/cmd").get_json()["auto"] is False
    assert cli.get("/api/estado").get_json()["nodo"]["mov"] == 0


def test_origen_queda_en_el_historial(cliente, fondo, rng):
    from io import BytesIO
    cli, _ = cliente
    datos = {"zone": "Zona-1", "origen": "movimiento",
             "image": [(BytesIO(escenas.a_jpeg(escenas.frame_vacio(fondo, rng))), "f.jpg")]}
    cli.post("/upload", data=datos, content_type="multipart/form-data")
    assert cli.get("/api/estado").get_json()["rafagas"][0]["origen"] == "movimiento"


def test_snapshot_no_permite_salir_del_directorio(cliente):
    cli, _ = cliente
    assert cli.get("/snapshots/../eventos.csv").status_code in (400, 404)

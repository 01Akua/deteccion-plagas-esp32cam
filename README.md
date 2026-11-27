# Detección de actividad de plagas con ESP32-S3-CAM

Proyecto de Sistemas Digitales y Ensambladores. Un nodo con cámara (GOOUUU ESP32-S3-CAM) manda ráfagas de fotos por WiFi a un servidor en Python que detecta actividad compatible con plagas por el tamaño del objeto en movimiento y la muestra en un panel web.

## Qué hace y qué no
- Detecta movimiento de objetos con un área entre 150 y 8000 px (sustractor de fondo MOG2 por zona).
- **No** identifica la especie, **no** envía alertas locales ni remotas.
- Las cifras de `resultados/` salen de **escenas sintéticas**; hay una sola prueba real de extremo a extremo (ver `evidencia_camara_real/registro_prueba.txt`), con falsos positivos.
- El sensor PIR solo se simuló en Wokwi.

## Estructura
| Ruta | Contenido |
|---|---|
| `servidor_deteccion_v2.py` | Servidor Flask + OpenCV (API y panel) |
| `panel.html` | Panel web: ráfagas, detecciones, control del nodo |
| `firmware/nodo_plagas/nodo_plagas.ino` | Firmware de la cámara (Arduino, ESP32-S3) |
| `firmware/nodo_plagas/configurar_nodo.py` | Guarda WiFi y servidor en la placa por USB |
| `escenas.py`, `evaluar_deteccion.py` | Escenas sintéticas y evaluación v1 vs v2 |
| `test_servidor.py` | Pruebas automáticas del servidor |
| `wokwi/` | Simulación del circuito con PIR |
| `resultados/` | Métricas y figuras de la evaluación |
| `informe/` | Informe en LaTeX y PDF |

## Cómo correrlo
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python servidor_deteccion_v2.py --puerto 5050
```
Abrir http://localhost:5050/. Pruebas: `python -m pytest -v test_servidor.py`.

## Cámara
1. Instalar el firmware `nodo_plagas.ino` (Arduino IDE o `arduino-cli`, placa ESP32S3 con PSRAM).
2. Configurar la red por USB: `python firmware/nodo_plagas/configurar_nodo.py` (pide la clave sin mostrarla; queda en la memoria flash de la placa, no en el código).
3. La placa consulta al servidor cada 2 s y envía 6 fotos por disparo.

El **disparo automático por movimiento viene apagado**: la cámara solo toma fotos con el botón "Tomar foto ahora" del panel, el botón BOOT de la placa o el comando serie `FOTO`. Se puede activar desde el panel.

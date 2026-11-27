"""Configura el nodo por USB sin escribir credenciales en el codigo.

Detecta la red WiFi a la que esta conectado el computador, pide la clave por consola
(no se muestra ni se guarda en disco) y envia WiFi + nombre del servidor (mDNS) a la
placa, que los guarda en su memoria flash.   Uso:  python configurar_nodo.py [puerto]
"""
import getpass, glob, subprocess, sys, time
import serial


def red_actual():
    out = subprocess.run(["networksetup", "-getairportnetwork", "en0"], capture_output=True, text=True).stdout
    return out.split(": ", 1)[1].strip() if ": " in out else ""


def host_local():
    return subprocess.run(["scutil", "--get", "LocalHostName"], capture_output=True, text=True).stdout.strip() + ".local"


puerto = sys.argv[1] if len(sys.argv) > 1 else (glob.glob("/dev/cu.usbmodem*") or glob.glob("/dev/cu.usbserial*"))[0]
ssid = (red_actual() or input("Nombre de la red WiFi (2.4 GHz): ")).strip()
print(f"Red: {ssid}   Servidor: {host_local()}   Puerto: {puerto}")
clave = getpass.getpass("Clave del WiFi (no se muestra): ")
s = serial.Serial(); s.port = puerto; s.baudrate = 115200; s.timeout = 1; s.dtr = False; s.rts = False; s.open()
time.sleep(4)            # abrir el puerto reinicia la placa: esperar a que arranque
s.reset_input_buffer()
s.write(f"CONFIG {ssid}|{clave}|{host_local()}\n".encode())
t = time.time()
while time.time() - t < 30:
    l = s.readline().decode(errors="replace").rstrip()
    if l: print(l.replace(clave, "****") if clave else l)
    if l.startswith("[WiFi]") and ("IP " in l or "fallo" in l): break

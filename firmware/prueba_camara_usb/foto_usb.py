"""Toma UNA foto por cable USB cada vez que se pulsa Enter (no toma fotos solas).

Uso:  python foto_usb.py [puerto]     (por defecto busca /dev/cu.usbmodem* o /dev/cu.usbserial*)
Las fotos se guardan en la carpeta fotos_usb/ y se abren al recibirlas.
"""
import glob, os, subprocess, sys, time
import serial

puerto = sys.argv[1] if len(sys.argv) > 1 else next(iter(glob.glob("/dev/cu.usbmodem*") + glob.glob("/dev/cu.usbserial*")), None)
if not puerto:
    sys.exit("No encuentro la placa. Conectala por USB o pasa el puerto como argumento.")
os.makedirs("fotos_usb", exist_ok=True)
s = serial.Serial(puerto, 115200, timeout=1)
print(f"Conectado a {puerto}. Esperando a la camara...")
fin = time.time() + 6
while time.time() < fin:                 # al abrir el puerto la placa se reinicia
    l = s.readline().decode("utf-8", "replace").strip()
    if l:
        print("placa:", l)
    if "camara lista" in l or "FALLO" in l:
        break

def leer_foto():
    s.reset_input_buffer()
    s.write(b"FOTO\n")
    t0 = time.time()
    while time.time() - t0 < 10:
        l = s.readline().decode("utf-8", "replace").strip()
        if l.startswith("FOTO_ERROR"):
            return print("placa:", l)
        if l.startswith("FOTO_INICIO"):
            n = int(l.split()[1])
            datos = s.read(n)
            if len(datos) != n or datos[:2] != b"\xff\xd8":
                return print(f"Foto incompleta ({len(datos)}/{n} bytes)")
            ruta = os.path.join("fotos_usb", time.strftime("foto_%Y%m%d_%H%M%S.jpg"))
            open(ruta, "wb").write(datos)
            print(f"Foto guardada: {ruta} ({n} bytes)")
            return subprocess.run(["open", ruta])
    print("La placa no respondio.")

print("Pulsa Enter para tomar una foto (q + Enter para salir).")
while input().strip().lower() != "q":
    leer_foto()

// Nodo de deteccion de plagas - GOOUUU ESP32-S3-CAM v1.5 (camara OV3660).
//
// Funcionamiento:
//  1. Cada MUESTREO_MS toma una foto, la reduce a una rejilla de 80x60 celdas en gris y la compara
//     con un fondo que se actualiza lentamente. Si cambian suficientes celdas -> hay movimiento.
//  2. Guarda las ultimas 5 fotos (anillo) como fondo. Al detectar movimiento (o por orden manual)
//     envia 6 fotos: las 5 anteriores al movimiento + la del movimiento (el servidor entrena con
//     las 5 primeras y evalua la ultima).
//  3. Cada 2 s consulta al servidor (GET /cmd): recibe la orden "tomar foto" del panel y los ajustes
//     (disparo automatico, sensibilidad) y le reporta cuanto movimiento ve.
// Credenciales y servidor NO estan en el codigo: se guardan en flash con configurar_nodo.py.
#include <WiFi.h>
#include <HTTPClient.h>
#include <ESPmDNS.h>
#include <Preferences.h>
#include "esp_camera.h"
#include "img_converters.h"

// ---- Configuracion ----
const uint16_t SERVIDOR_PORT = 5050;
const char*   ZONA            = "Zona-1";
const int     FOTOS_FONDO     = 5;              // + 1 foto del evento = 6 (minimo del servidor v2)
const int     INTERVALO_FOTOS_MS = 200;         // solo para la rafaga rapida de respaldo
const int     PIN_BOTON       = 0;              // BOOT
const unsigned long MUESTREO_MS = 500;          // cada cuanto mira si hay movimiento
const unsigned long SONDEO_MS   = 2000;         // cada cuanto consulta al servidor
const unsigned long ENFRIAMIENTO_MS = 8000;     // pausa tras un disparo
const int     GRID_W = 80, GRID_H = 60;         // rejilla de comparacion
const int     UMBRAL_PIXEL = 28;                // cambio minimo de gris para contar una celda
const size_t  CAP_FOTO = 90 * 1024;             // tamano maximo de cada foto guardada

// WiFi y servidor se guardan en flash (NVS)
Preferences prefs;
String wifiSsid, wifiClave, servidorHost;       // host: "mi-mac.local" (mDNS) o una IP
IPAddress ipServidor; bool ipValida = false;

// ---- Pines de camara (GOOUUU ESP32-S3-CAM, mismo mapa que ESP32-S3-EYE) ----
#define PWDN_GPIO_NUM  -1
#define RESET_GPIO_NUM -1
#define XCLK_GPIO_NUM  15
#define SIOD_GPIO_NUM  4
#define SIOC_GPIO_NUM  5
#define Y9_GPIO_NUM    16
#define Y8_GPIO_NUM    17
#define Y7_GPIO_NUM    18
#define Y6_GPIO_NUM    12
#define Y5_GPIO_NUM    10
#define Y4_GPIO_NUM    8
#define Y3_GPIO_NUM    9
#define Y2_GPIO_NUM    11
#define VSYNC_GPIO_NUM 6
#define HREF_GPIO_NUM  7
#define PCLK_GPIO_NUM  13

// ---- Estado ----
bool camaraOk = false;
bool autoActivo = true;                         // ajustable desde el panel
int  celdasMin = 25;                            // sensibilidad, ajustable desde el panel
uint8_t* rgb = nullptr;                         // buffer de decodificacion (PSRAM)
uint8_t fondo[GRID_H][GRID_W];
bool fondoListo = false;
int  ultimoMov = 0;
unsigned long ultimoMuestreo = 0, ultimoSondeo = 0, hastaCuando = 0;

struct Foto { uint8_t* buf; size_t len; };
Foto anillo[FOTOS_FONDO];                       // ultimas fotos sin movimiento
Foto actual;                                    // foto del evento
int anilloN = 0, anilloIdx = 0;

// ================= Camara =================
bool iniciarCamara() {
  camera_config_t c = {};
  c.ledc_channel = LEDC_CHANNEL_0;
  c.ledc_timer = LEDC_TIMER_0;
  c.pin_d0 = Y2_GPIO_NUM; c.pin_d1 = Y3_GPIO_NUM; c.pin_d2 = Y4_GPIO_NUM; c.pin_d3 = Y5_GPIO_NUM;
  c.pin_d4 = Y6_GPIO_NUM; c.pin_d5 = Y7_GPIO_NUM; c.pin_d6 = Y8_GPIO_NUM; c.pin_d7 = Y9_GPIO_NUM;
  c.pin_xclk = XCLK_GPIO_NUM; c.pin_pclk = PCLK_GPIO_NUM;
  c.pin_vsync = VSYNC_GPIO_NUM; c.pin_href = HREF_GPIO_NUM;
  c.pin_sccb_sda = SIOD_GPIO_NUM; c.pin_sccb_scl = SIOC_GPIO_NUM;
  c.pin_pwdn = PWDN_GPIO_NUM; c.pin_reset = RESET_GPIO_NUM;
  c.xclk_freq_hz = 20000000;
  c.pixel_format = PIXFORMAT_JPEG;
  c.frame_size = FRAMESIZE_VGA;      // 640x480, como las escenas de la evaluacion
  c.jpeg_quality = 12;
  c.fb_count = 2;
  c.fb_location = CAMERA_FB_IN_PSRAM;
  c.grab_mode = CAMERA_GRAB_LATEST;
  esp_err_t err = esp_camera_init(&c);
  if (err != ESP_OK) { Serial.printf("[CAM] error de inicio 0x%x\n", err); return false; }
  sensor_t* sen = esp_camera_sensor_get();
  if (sen && sen->id.PID == OV3660_PID) {   // el OV3660 entrega la imagen invertida
    sen->set_vflip(sen, 1);
    sen->set_brightness(sen, 1);
    sen->set_saturation(sen, -2);
  }
  return true;
}

// ================= WiFi y servidor =================
bool conectarWiFi() {
  if (WiFi.status() == WL_CONNECTED) return true;
  if (wifiSsid.length() == 0) { Serial.println("[WiFi] sin configurar: ejecute configurar_nodo.py"); return false; }
  WiFi.mode(WIFI_STA);
  WiFi.begin(wifiSsid.c_str(), wifiClave.c_str());
  Serial.print("[WiFi] conectando");
  for (int i = 0; i < 40 && WiFi.status() != WL_CONNECTED; i++) { delay(500); Serial.print("."); }
  if (WiFi.status() != WL_CONNECTED) { Serial.printf(" fallo (estado %d)\n", (int)WiFi.status()); return false; }
  Serial.printf(" ok, IP %s\n", WiFi.localIP().toString().c_str());
  return true;
}

// Resuelve el servidor por nombre (mDNS) y recuerda la IP; si falla una conexion se vuelve a resolver.
bool obtenerIP(IPAddress& ip) {
  if (ipValida) { ip = ipServidor; return true; }
  String h = servidorHost;
  bool ok = false;
  if (h.endsWith(".local")) {
    h.remove(h.length() - 6);
    MDNS.begin("nodo-plagas");
    ip = MDNS.queryHost(h, 3000);
    ok = (uint32_t)ip != 0;
    if (!ok) Serial.printf("[mDNS] no se encontro %s.local\n", h.c_str());
  } else {
    ok = ip.fromString(h) || WiFi.hostByName(h.c_str(), ip) == 1;
  }
  if (ok) { ipServidor = ip; ipValida = true; Serial.printf("[HTTP] servidor %s -> %s\n", servidorHost.c_str(), ip.toString().c_str()); }
  return ok;
}

// Consulta al servidor: recibe orden de disparo y ajustes; reporta el movimiento visto.
bool sondear() {
  IPAddress ip;
  if (WiFi.status() != WL_CONNECTED || !obtenerIP(ip)) return false;
  HTTPClient http;
  http.setConnectTimeout(1500); http.setTimeout(1500);
  String ruta = String("/cmd?zone=") + ZONA + "&mov=" + ultimoMov + "&anillo=" + anilloN;
  if (!http.begin(ip.toString(), SERVIDOR_PORT, ruta)) return false;
  int codigo = http.GET();
  bool orden = false;
  if (codigo == 200) {
    String r = http.getString();
    orden = r.indexOf("\"disparar\":true") >= 0;
    autoActivo = r.indexOf("\"auto\":true") >= 0;
    int i = r.indexOf("\"celdas\":");
    if (i >= 0) celdasMin = max(1, (int)r.substring(i + 9).toInt());
  } else { ipValida = false; }
  http.end();
  return orden;
}

bool enviarRafaga(uint8_t** buf, size_t* len, int n, const char* origen) {
  IPAddress ip;
  if (!conectarWiFi() || !obtenerIP(ip)) { Serial.println("[HTTP] servidor no encontrado"); return false; }
  WiFiClient cli;
  if (!cli.connect(ip, SERVIDOR_PORT)) { ipValida = false; Serial.println("[HTTP] no conecta al servidor"); return false; }
  const String lim = "----nodoplagas";
  String cabZona = "--" + lim + "\r\nContent-Disposition: form-data; name=\"zone\"\r\n\r\n" + ZONA + "\r\n";
  String cabOri  = "--" + lim + "\r\nContent-Disposition: form-data; name=\"origen\"\r\n\r\n" + origen + "\r\n";
  String cabImg  = "--" + lim + "\r\nContent-Disposition: form-data; name=\"image\"; filename=\"f.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n";
  String cola    = "\r\n";
  String fin     = "--" + lim + "--\r\n";
  size_t total = cabZona.length() + cabOri.length() + fin.length();
  for (int i = 0; i < n; i++) total += cabImg.length() + len[i] + cola.length();

  cli.printf("POST /upload HTTP/1.1\r\nHost: %s:%u\r\nContent-Type: multipart/form-data; boundary=%s\r\nContent-Length: %u\r\nConnection: close\r\n\r\n",
             servidorHost.c_str(), SERVIDOR_PORT, lim.c_str(), (unsigned)total);
  cli.print(cabZona); cli.print(cabOri);
  for (int i = 0; i < n; i++) { cli.print(cabImg); cli.write(buf[i], len[i]); cli.print(cola); }
  cli.print(fin);

  unsigned long t0 = millis();
  while (cli.connected() && !cli.available() && millis() - t0 < 10000) delay(10);
  String resp = cli.readString();
  cli.stop();
  int i = resp.indexOf("\r\n\r\n");
  Serial.printf("[HTTP] %s\n", resp.substring(0, resp.indexOf("\r\n")).c_str());
  Serial.printf("[HTTP] respuesta: %s\n", i >= 0 ? resp.substring(i + 4).c_str() : "(vacia)");
  return resp.startsWith("HTTP/1.1 200");
}

// ================= Fotos, anillo y movimiento =================
bool copiarFoto(Foto& f, camera_fb_t* fb) {
  if (fb->len > CAP_FOTO) return false;
  memcpy(f.buf, fb->buf, fb->len); f.len = fb->len; return true;
}

void guardarEnAnillo(camera_fb_t* fb) {
  if (!copiarFoto(anillo[anilloIdx], fb)) return;
  anilloIdx = (anilloIdx + 1) % FOTOS_FONDO;
  if (anilloN < FOTOS_FONDO) anilloN++;
}

// Reduce la foto a una rejilla GRID_W x GRID_H en gris. Devuelve false si no se pudo decodificar.
bool rejilla(camera_fb_t* fb, uint8_t g[GRID_H][GRID_W]) {
  if (!fmt2rgb888(fb->buf, fb->len, PIXFORMAT_JPEG, rgb)) return false;
  int w = fb->width, h = fb->height, px = w / GRID_W, py = h / GRID_H;
  for (int y = 0; y < GRID_H; y++)
    for (int x = 0; x < GRID_W; x++) {
      const uint8_t* p = rgb + ((y * py + py / 2) * w + (x * px + px / 2)) * 3;
      g[y][x] = (p[0] * 29 + p[1] * 150 + p[2] * 77) >> 8;   // el buffer sale en orden BGR
    }
  return true;
}

// Cuenta las celdas que cambiaron respecto al fondo y actualiza el fondo lentamente.
int compararYActualizar(uint8_t g[GRID_H][GRID_W]) {
  int cambios = 0;
  for (int y = 0; y < GRID_H; y++)
    for (int x = 0; x < GRID_W; x++) {
      int d = (int)g[y][x] - (int)fondo[y][x];
      if (abs(d) > UMBRAL_PIXEL) cambios++;
      fondo[y][x] += d / 8;
    }
  return cambios;
}

void reiniciarDeteccion() { anilloN = 0; anilloIdx = 0; fondoListo = false; ultimoMov = 0; }

// Envia 5 fotos de fondo + la foto del evento. Si aun no hay 5 de fondo, toma una rafaga rapida.
void disparar(const char* origen, camera_fb_t* fbEvento) {
  Serial.printf("[DISPARO] origen=%s\n", origen);
  uint8_t* buf[FOTOS_FONDO + 1]; size_t len[FOTOS_FONDO + 1]; int n = 0;
  bool respaldo = false;
  if (anilloN == FOTOS_FONDO && fbEvento && copiarFoto(actual, fbEvento)) {
    for (int k = 0; k < FOTOS_FONDO; k++) { Foto& f = anillo[(anilloIdx + k) % FOTOS_FONDO]; buf[n] = f.buf; len[n++] = f.len; }
    buf[n] = actual.buf; len[n++] = actual.len;
    Serial.println("[DISPARO] 5 fotos de fondo previas + foto del evento");
  } else {                                      // respaldo: rafaga rapida
    respaldo = true;
    for (int i = 0; i < FOTOS_FONDO + 1; i++) {
      camera_fb_t* fb = esp_camera_fb_get();
      if (!fb) break;
      buf[n] = (uint8_t*)ps_malloc(fb->len);
      if (!buf[n]) { esp_camera_fb_return(fb); break; }
      memcpy(buf[n], fb->buf, fb->len); len[n++] = fb->len;
      esp_camera_fb_return(fb);
      delay(INTERVALO_FOTOS_MS);
    }
    Serial.println("[DISPARO] sin fondo previo: rafaga rapida de respaldo");
  }
  if (fbEvento) esp_camera_fb_return(fbEvento);
  if (n == FOTOS_FONDO + 1) enviarRafaga(buf, len, n, origen);
  else Serial.println("[DISPARO] rafaga incompleta: no se envia");
  if (respaldo) for (int i = 0; i < n; i++) free(buf[i]);
  reiniciarDeteccion();                         // el fondo se vuelve a aprender
  hastaCuando = millis() + ENFRIAMIENTO_MS;
}

void dispararManual(const char* origen) {
  camera_fb_t* fb = esp_camera_fb_get();
  disparar(origen, fb);                         // disparar() devuelve el fb
}

void muestrear() {
  camera_fb_t* fb = esp_camera_fb_get();
  if (!fb) { Serial.println("[CAM] captura fallida"); return; }
  static uint8_t g[GRID_H][GRID_W];
  if (!rejilla(fb, g)) { esp_camera_fb_return(fb); return; }
  if (!fondoListo) {
    memcpy(fondo, g, sizeof(g)); fondoListo = true;
    guardarEnAnillo(fb); esp_camera_fb_return(fb); return;
  }
  int cambios = compararYActualizar(g);
  ultimoMov = cambios;
  bool luzGlobal = cambios > GRID_W * GRID_H * 6 / 10;       // cambio de luz: no es movimiento local
  if (cambios >= celdasMin / 2) Serial.printf("[MOV] celdas cambiadas: %d (umbral %d)%s\n", cambios, celdasMin, luzGlobal ? " [luz global]" : "");
  if (luzGlobal) { esp_camera_fb_return(fb); reiniciarDeteccion(); return; }
  if (autoActivo && millis() > hastaCuando && anilloN == FOTOS_FONDO && cambios >= celdasMin) {
    disparar("movimiento", fb);                 // fb se devuelve dentro
    return;
  }
  guardarEnAnillo(fb);
  esp_camera_fb_return(fb);
}

// ================= Comandos por USB =================
// CONFIG ssid|clave|host   (lo envia configurar_nodo.py) | BORRAR | SCAN | FOTO
void leerComandos() {
  if (!Serial.available()) return;
  String l = Serial.readStringUntil('\n'); l.trim();
  if (l == "BORRAR") { prefs.clear(); wifiSsid = wifiClave = servidorHost = ""; Serial.println("[CONFIG] borrada"); return; }
  if (l == "FOTO") { dispararManual("serie"); return; }
  if (l == "SCAN") {
    WiFi.disconnect(true, false); delay(500); WiFi.mode(WIFI_STA); int n = WiFi.scanNetworks();
    Serial.printf("[SCAN] %d redes\n", n);
    for (int i = 0; i < n; i++) Serial.printf("[SCAN] %s '%s' canal %d senal %d dBm\n", WiFi.BSSIDstr(i).c_str(), WiFi.SSID(i).c_str(), WiFi.channel(i), WiFi.RSSI(i));
    return;
  }
  if (!l.startsWith("CONFIG ")) return;
  l = l.substring(7);
  int a = l.indexOf('|'), b = l.lastIndexOf('|');
  if (a < 0 || b <= a) { Serial.println("[CONFIG] formato invalido"); return; }
  wifiSsid = l.substring(0, a); wifiClave = l.substring(a + 1, b); servidorHost = l.substring(b + 1);
  prefs.putString("ssid", wifiSsid); prefs.putString("clave", wifiClave); prefs.putString("host", servidorHost);
  ipValida = false;
  Serial.printf("[CONFIG] guardada: red '%s', servidor '%s'\n", wifiSsid.c_str(), servidorHost.c_str());
  WiFi.disconnect(); conectarWiFi();
}

void setup() {
  Serial.begin(115200);
  delay(1500);
  Serial.println("\nNodo plagas - GOOUUU ESP32-S3-CAM v1.5");
  pinMode(PIN_BOTON, INPUT_PULLUP);
  prefs.begin("nodo", false);
  wifiSsid = prefs.getString("ssid", ""); wifiClave = prefs.getString("clave", "");
  servidorHost = prefs.getString("host", "");
  Serial.printf("[PSRAM] %s (%u bytes)\n", psramFound() ? "ok" : "NO detectada", (unsigned)ESP.getPsramSize());
  rgb = (uint8_t*)ps_malloc(640 * 480 * 3);
  for (int i = 0; i < FOTOS_FONDO; i++) anillo[i].buf = (uint8_t*)ps_malloc(CAP_FOTO);
  actual.buf = (uint8_t*)ps_malloc(CAP_FOTO);
  if (!rgb || !actual.buf) { Serial.println("[MEM] sin memoria PSRAM"); return; }
  camaraOk = iniciarCamara();
  if (camaraOk) Serial.println("[CAM] camara lista");
  conectarWiFi();
  Serial.println("[NODO] deteccion de movimiento activa; panel: http://<servidor>:5050");
}

void loop() {
  leerComandos();
  if (!camaraOk) { delay(200); return; }
  unsigned long ahora = millis();
  if (ahora - ultimoSondeo >= SONDEO_MS) {
    ultimoSondeo = ahora;
    if (sondear()) { dispararManual("panel"); return; }
  }
  if (digitalRead(PIN_BOTON) == LOW) { dispararManual("boton"); delay(500); return; }
  if (ahora - ultimoMuestreo >= MUESTREO_MS) { ultimoMuestreo = ahora; muestrear(); }
  delay(10);
}

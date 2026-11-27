// Prueba de camara por cable USB (sin WiFi) - GOOUUU ESP32-S3-CAM v1.5 (OV3660).
// La placa NO toma fotos sola: espera la linea "FOTO" por el puerto serie. Al recibirla captura UNA
// foto JPEG y la envia por el mismo cable:  FOTO_INICIO <bytes>\n  <bytes del JPEG>  \nFOTO_FIN\n
// El script foto_usb.py la recibe y la guarda en el computador.
#include "esp_camera.h"

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

bool camaraOk = false;

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
  c.frame_size = FRAMESIZE_VGA;
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

void enviarFoto() {
  for (int i = 0; i < 2; i++) {             // descarta 2 cuadros: el sensor ajusta la exposicion
    camera_fb_t* d = esp_camera_fb_get();
    if (d) esp_camera_fb_return(d);
  }
  camera_fb_t* fb = esp_camera_fb_get();
  if (!fb) { Serial.println("FOTO_ERROR sin imagen"); return; }
  Serial.printf("FOTO_INICIO %u\n", (unsigned)fb->len);
  const size_t TROZO = 512;
  for (size_t i = 0; i < fb->len; i += TROZO) {
    Serial.write(fb->buf + i, min(TROZO, fb->len - i));
    Serial.flush();
  }
  Serial.print("\nFOTO_FIN\n");
  esp_camera_fb_return(fb);
}

void setup() {
  Serial.begin(115200);
  delay(1500);
  Serial.println("\nPrueba de camara por USB");
  camaraOk = iniciarCamara();
  Serial.println(camaraOk ? "[CAM] camara lista. Escribe FOTO para tomar una foto." : "[CAM] FALLO la camara");
}

void loop() {
  if (Serial.available()) {
    String l = Serial.readStringUntil('\n');
    l.trim();
    if (l == "FOTO") { if (camaraOk) enviarFoto(); else Serial.println("FOTO_ERROR camara no iniciada"); }
  }
  delay(10);
}

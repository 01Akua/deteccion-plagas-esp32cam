// Simulacion en Wokwi (ESP32 DevKit + PIR) de la LOGICA DE DISPARO del nodo.
// No simula la camara: cada "captura" solo se cuenta e imprime por el monitor serie.
// Refleja el firmware real: PIR en GPIO 13, y la rafaga de 6 fotos por disparo
// que exige el servidor v2 (5 de fondo + 1 a evaluar).

#define PIN_PIR 13
#define PIN_LED 2             // LED = "captura en curso"
#define FOTOS_POR_RAFAGA 6    // minimo que necesita el servidor v2
#define INTERVALO_FOTOS_MS 200
#define ENFRIAMIENTO_MS 5000  // evita disparos repetidos mientras el PIR sigue en alto

unsigned long ultimoDisparo = 0;
int disparos = 0;
bool pirAnterior = false;

void capturarRafaga() {
  digitalWrite(PIN_LED, HIGH);
  for (int i = 1; i <= FOTOS_POR_RAFAGA; i++) {
    Serial.printf("  foto %d/%d capturada (simulada)\n", i, FOTOS_POR_RAFAGA);
    delay(INTERVALO_FOTOS_MS);
  }
  Serial.println("  -> rafaga lista para enviar por HTTP POST a /upload");
  digitalWrite(PIN_LED, LOW);
}

void setup() {
  Serial.begin(115200);
  pinMode(PIN_PIR, INPUT);
  pinMode(PIN_LED, OUTPUT);
  Serial.println("Nodo listo. Esperando movimiento del PIR...");
}

void loop() {
  bool pir = digitalRead(PIN_PIR) == HIGH;
  bool flancoSubida = pir && !pirAnterior;
  pirAnterior = pir;

  if (flancoSubida && (millis() - ultimoDisparo > ENFRIAMIENTO_MS || disparos == 0)) {
    ultimoDisparo = millis();
    disparos++;
    Serial.printf("[PIR] movimiento detectado (disparo #%d)\n", disparos);
    capturarRafaga();
  } else if (flancoSubida) {
    Serial.println("[PIR] movimiento ignorado (enfriamiento)");
  }
  delay(20);
}

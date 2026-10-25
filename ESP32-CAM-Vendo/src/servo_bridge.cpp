#include "servo_bridge.h"
#include "config.h"
#include "camera_detect.h"
#include "tflite_bottle.h"

#include <WiFi.h>

static bool s_triggerPending = false;
static String s_line;

static void replyPong() {
  const int cam = cameraReady() ? 1 : 0;
  const int wifi = (WiFi.status() == WL_CONNECTED) ? 1 : 0;
  const int tf = tfliteBottleReady() ? 1 : 0;
  Serial2.printf("PONG CAM=%d WIFI=%d TF=%d\n", cam, wifi, tf);
  Serial.printf("Uno PING → PONG CAM=%d WIFI=%d TF=%d\n", cam, wifi, tf);
}

static void handleUnoLine(const String &raw) {
  String cmd = raw;
  cmd.trim();
  cmd.toUpperCase();
  if (cmd.length() == 0) {
    return;
  }
  if (cmd == "PING") {
    replyPong();
    return;
  }
  if (cmd == "TRIGGER") {
    s_triggerPending = true;
    Serial.println(F("Uno TRIGGER (Flying Fish)"));
    return;
  }
  Serial.print(F("Uno: "));
  Serial.println(cmd);
}

void servoBridgeBegin() {
  if (!SERVO_BRIDGE_ENABLE) {
    Serial.println(F("Servo bridge disabled"));
    return;
  }
  Serial2.begin(SERVO_UART_BAUD, SERIAL_8N1, SERVO_UART_RX_PIN, SERVO_UART_TX_PIN);
  Serial.printf("Uno UART2 %u baud TX=%d RX=%d\n", SERVO_UART_BAUD, SERVO_UART_TX_PIN,
                SERVO_UART_RX_PIN);
  delay(50);
  servoIdle();
}

void servoBridgeLoop() {
  if (!SERVO_BRIDGE_ENABLE) {
    return;
  }
  while (Serial2.available()) {
    char c = (char)Serial2.read();
    if (c == '\n' || c == '\r') {
      if (s_line.length()) {
        handleUnoLine(s_line);
        s_line = "";
      }
    } else if (s_line.length() < 48) {
      s_line += c;
    } else {
      s_line = "";
    }
  }
}

bool servoConsumeTrigger() {
  if (!s_triggerPending) {
    return false;
  }
  s_triggerPending = false;
  return true;
}

void servoSend(const char *cmd) {
  if (!SERVO_BRIDGE_ENABLE || !cmd) {
    return;
  }
  Serial2.print(cmd);
  Serial2.print('\n');
  Serial.printf("Servo CMD → %s\n", cmd);
}

void servoCancelPending() { s_triggerPending = false; }

void servoOpenEntry() {
  // No entry gate on Uno — keep command for older logs / no-op
}
void servoCloseEntry() {}
void servoAccept() { servoSend("ACCEPT"); }
void servoAcceptKeepOpen() { servoSend("ACCEPT"); }
void servoReject() { servoSend("REJECT"); }
void servoIdle() { servoSend("IDLE"); }

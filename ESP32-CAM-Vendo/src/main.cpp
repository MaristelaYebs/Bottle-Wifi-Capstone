#include <Arduino.h>
#include <WiFi.h>
#include <cstring>

#include "config.h"
#include "camera_detect.h"
#include "session.h"
#include "servo_bridge.h"
#include "web_api.h"
#include "tflite_bottle.h"

static String g_usbLine;

static void printCamStatus() {
  Serial.println(F("--- STATUS ---"));
  Serial.printf("camera=%d  tflite=%d  uart=%d\n", cameraReady() ? 1 : 0,
                tfliteBottleReady() ? 1 : 0, SERVO_BRIDGE_ENABLE ? 1 : 0);
  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("wifi=1  ip=%s\n", WiFi.localIP().toString().c_str());
  } else {
    Serial.println("wifi=0");
  }
  Serial.println(F("USB: PING STATUS HELP"));
}

static void handleCamUsb(String cmd) {
  cmd.trim();
  cmd.toUpperCase();
  if (cmd.length() == 0) {
    return;
  }
  if (cmd == "PING") {
    servoSend("PING");
    Serial.println(F("→ Uno PING (expect Uno: OK)"));
    return;
  }
  if (cmd == "STATUS") {
    printCamStatus();
    return;
  }
  if (cmd == "HELP") {
    Serial.println(F("USB commands:"));
    Serial.println(F("  PING    check if Arduino Uno replies"));
    Serial.println(F("  STATUS  camera / WiFi / TFLite / UART"));
    Serial.println(F("  HELP    this list"));
    return;
  }
}

static void pollUsb() {
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      if (g_usbLine.length()) {
        handleCamUsb(g_usbLine);
        g_usbLine = "";
      }
    } else if (g_usbLine.length() < 48) {
      g_usbLine += c;
    } else {
      g_usbLine = "";
    }
  }
}

static void connectWifi() {
  WiFi.mode(WIFI_STA);
  if (USE_STATIC_IP) {
    if (!WiFi.config(LOCAL_IP, GATEWAY, SUBNET, DNS1)) {
      Serial.println("Static IP failed");
    }
  }
  Serial.printf("Connecting to %s", WIFI_SSID);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  uint32_t start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < 20000) {
    delay(400);
    Serial.print(".");
  }
  Serial.println();
  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("WiFi OK %s\n", WiFi.localIP().toString().c_str());
  } else {
    Serial.println("WiFi FAILED — retrying in loop");
  }
}

static void handleIdentifyResult(const DetectResult &r) {
  webSetDetectLabel(r.label);
  webSetDetectStats(r.motion, r.area, r.aspect, r.streak);
  Serial.printf("IDENTIFY %-12s score=%.2f motion=%d area=%d ar=%.2f\n", r.label, r.score,
                r.motion, r.area, r.aspect);

  const bool sessionOpen = webDetectEnabled();

  if (r.detected) {
    servoAccept();
    if (sessionOpen) {
      bool ok = sessionCreditBottle();
      TopUpSession *s = sessionActive();
      Serial.printf("BOTTLE credited=%d count=%d/%d score=%.2f\n", ok,
                    s ? s->bottleCount : 0, s ? s->maxBottles : 0, r.score);
    } else {
      Serial.println("BOTTLE accepted (no session credit)");
    }
  } else {
    servoReject();
    Serial.println("REJECT not a PET bottle");
  }
}

#ifndef PIO_UNIT_TESTING
void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\n=== ESP32-CAM Bottle WiFi Vendo ===");
  Serial.println("Flying Fish on Arduino Uno triggers capture + PET identify");
  Serial.printf("Minutes/bottle=%d DEMO=%d DETECT_TEST=%d\n", MINUTES_PER_BOTTLE, DEMO_MODE,
                DETECT_TEST_MODE);
  Serial.println("USB: PING / STATUS / HELP");
  if (DETECT_TEST_MODE) {
    Serial.println("Detect TEST MODE: Serial shows every item status (no /topUp needed)");
  }

  // Init camera before WiFi to reduce DMA contention during boot
  if (!cameraInit()) {
    Serial.println("FATAL: camera init failed. Check model/PSRAM/power (use 5V ≥2A).");
  }

  connectWifi();

  servoBridgeBegin();
  webServerBegin();
  detectorReset();
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    connectWifi();
  }

  webServerLoop();
  servoBridgeLoop();
  pollUsb();

  if (webConsumeWaitTimedOut()) {
    servoReject();
  }

  if (servoConsumeTrigger()) {
    webSetDetectLabel("identifying");
    Serial.println("CAPTURE: Flying Fish trigger");
    DetectResult r = detectorIdentifyBottle();
    handleIdentifyResult(r);
  } else {
    static uint32_t s_lastBgMs = 0;
    const uint32_t now = millis();
    if (now - s_lastBgMs >= 400) {
      s_lastBgMs = now;
      detectorLearnBackground();
    } else {
      cameraDrainFrame();
    }
  }

  delay(20);
}
#endif

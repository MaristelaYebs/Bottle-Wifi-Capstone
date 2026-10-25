#pragma once

// ========== WiFi (same LAN as MikroTik BottleWiFi hotspot) ==========
// Must match mikrotik-hap-lite: SSID BottleWiFi on bridge 10.10.10.0/24
static const char *WIFI_SSID = "BottleWiFi";
static const char *WIFI_PASSWORD = "";  // open AP (no WPA) — match MikroTik security-profile=default

// Prefer static IP so hotspot config.js (vendoIp) can reach here
static const bool USE_STATIC_IP = true;
static const IPAddress LOCAL_IP(10, 10, 10, 252);
static const IPAddress GATEWAY(10, 10, 10, 1);
static const IPAddress SUBNET(255, 255, 255, 0);
static const IPAddress DNS1(10, 10, 10, 1);

// ========== Vendo credit rules ==========
static const int MINUTES_PER_BOTTLE = 15;   // SET YOUR TIME HERE
static const int MAX_BOTTLES_PER_SESSION = 5;
static const uint32_t WAIT_TIME_MS = 90000; // portal wait window
// Auto-release a stuck INSERT session so the next phone is not told "Bottle Chute is busy"
static const uint32_t SESSION_STALE_MS = 180000;

// Demo: skip MikroTik API (still generates vouchers / Done succeeds)
// Set true to test portal Done without a working RouterOS API login
static const bool DEMO_MODE = false;

// ========== MikroTik RouterOS API ==========
// Matches bottle-vendo-hap-lite.rsc (API :8728, user vendo)
static const char *MT_HOST = "10.10.10.1";
static const uint16_t MT_PORT = 8728;
static const char *MT_USER = "vendo";  // must match RouterOS /user vendo name
static const char *MT_PASSWORD = "Welcome01";  // must match RouterOS /user vendo password
static const char *MT_HOTSPOT_PROFILE = "default";
// false = hotspot user password is the voucher. Must match portal loginOption:
//   false ↔ loginOption = 1 in captive-portal/assets/js/config.js
//   true  ↔ loginOption = 0 (empty password). Empty HTTP-CHAP often fails on RouterOS.
static const bool MT_USERNAME_ONLY = false;

// After upload, open http://10.10.10.252/mikrotikTest
// If detail says "connect failed", on MikroTik Terminal:
//   /import file-name=esp32cam-static-bypass.rsc
// or:
//   /ip hotspot ip-binding add address=10.10.10.252 to-address=10.10.10.252 type=bypassed
//   /ip service enable api

// ========== Camera / chute detector ==========
// AI-Thinker ESP32-CAM
#define CAMERA_MODEL_AI_THINKER

// true = identify + move servos on Flying Fish trigger even without /topUp
// false = still identify on trigger; WiFi credit only during an open portal session
static const bool DETECT_TEST_MODE = false;

// TensorFlow Lite Micro bottle classifier (96×96 int8). Train with tools/train_bottle_model.py
static const bool USE_TFLITE = true;
static const float TFLITE_BOTTLE_THRESHOLD = 0.91f;
// Only used by fallback shape rules if TFLite is off / not ready
static const int TFLITE_MOTION_GATE = 700;

// Full-frame motion gate + fallback shape rules (used if TFLite off / not ready)
static const int MIN_MOTION_PIXELS = 700;
static const int CLOSEUP_MOTION_PCT = 25;
static const int MIN_BLOB_AREA = 200;
static const float MIN_ASPECT = 1.2f;
static const float MAX_ASPECT = 6.0f;
static const int MOTION_THRESHOLD = 28;
// Wait after Arduino TRIGGER so the bottle is in the camera view
static const uint32_t CAPTURE_DELAY_MS = 400;
static const int IDENTIFY_FRAMES = 5;
static const uint32_t IDENTIFY_FRAME_GAP_MS = 50;
// Unused by trigger path; kept for detectorProcessGray / tests
static const uint32_t DETECT_ARM_DELAY_MS = 2500;
static const int CONFIRM_FRAMES = 5;
static const uint32_t COOLDOWN_MS = 2000;
static const uint32_t DETECT_LOG_MS = 200;

// Web server
static const uint16_t HTTP_PORT = 80;

// ========== Arduino Uno bridge (UART2) ==========
// CAM TX GPIO14 → Uno pin 8 (SoftwareSerial RX)
// CAM RX GPIO15 ← Uno pin 7 (SoftwareSerial TX) via 5V→3.3V divider
// Uno watches MH-Sensor Flying Fish, then sends TRIGGER; CAM replies ACCEPT/REJECT
static const bool SERVO_BRIDGE_ENABLE = true;
static const int SERVO_UART_TX_PIN = 14;
static const int SERVO_UART_RX_PIN = 15;
static const uint32_t SERVO_UART_BAUD = 9600;

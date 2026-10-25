#include <Arduino.h>

#include "config.h"
#include "servos.h"

// =====================================================================
// Bottle Wi-Fi Vendo — Arduino Uno controller
//
//   IDLE --IR blocked+debounced--> OBJECT_DETECTED --send-->  WAITING_FOR_AI
//   WAITING_FOR_AI --rx ACCEPT--> ACCEPTING   --seq done--> COOLDOWN --> IDLE
//   WAITING_FOR_AI --rx REJECT--> REJECTING   --seq done--> COOLDOWN --> IDLE
//   WAITING_FOR_AI --timeout---> REJECTING (fail-safe)
//   any state --rx RESET-------> IDLE
//
// Serial protocol (USB @ 115200, LF-terminated ASCII):
//   Arduino -> Pi : READY | OBJECT_DETECTED | SERVO_COMPLETE | ERROR:<msg>
//                   PONG | ACK:ACCEPT | ACK:REJECT | ACK:RESET
//   Pi -> Arduino : PING | ACCEPT | REJECT | RESET
// Lines beginning with '#' are human-readable debug the Pi ignores.
// =====================================================================

enum class State : uint8_t {
  Idle = 0,
  ObjectDetected,
  WaitingForAi,
  Accepting,
  Rejecting,
  Cooldown,
  Error,
};

static State    g_state = State::Idle;
static String   g_line;
static uint32_t g_aiDeadlineMs = 0;
static uint32_t g_cooldownUntilMs = 0;

// IR debounce / re-arm state
static bool     g_fishLatched = false;
static uint32_t g_blockedSinceMs = 0;
static uint32_t g_rearmAfterMs = 0;

// ---------------- protocol emit helpers ----------------
static void emit(const __FlashStringHelper *msg) { Serial.println(msg); }
static void emitError(const __FlashStringHelper *msg) {
  Serial.print(F("ERROR:"));
  Serial.println(msg);
}
static void comment(const __FlashStringHelper *msg) {
  Serial.print(F("# "));
  Serial.println(msg);
}

// ---------------- IR sensor ----------------
static bool fishBlocked() {
  int v = digitalRead(PIN_FLYING_FISH);
  return FLYING_FISH_ACTIVE_LOW ? (v == LOW) : (v == HIGH);
}

// Returns true once when a debounced, re-armed object insertion is confirmed.
static bool fishTriggered() {
  if ((int32_t)(millis() - g_rearmAfterMs) < 0) {
    return false;
  }
  const uint32_t now = millis();
  if (!fishBlocked()) {
    g_fishLatched = false;
    g_blockedSinceMs = 0;
    return false;
  }
  if (!g_fishLatched) {
    g_fishLatched = true;
    g_blockedSinceMs = now;
    return false;
  }
  if (now - g_blockedSinceMs < FISH_HOLD_MS) {
    return false;
  }
  g_fishLatched = false;
  g_rearmAfterMs = now + FISH_REARM_MS;
  return true;
}

static void goIdle() {
  servosForceIdle();
  g_state = State::Idle;
  g_fishLatched = false;
  g_blockedSinceMs = 0;
}

// ---------------- command handling (Pi -> Arduino) ----------------
static void printStatus() {
  comment(F("STATUS"));
  Serial.print(F("# state="));
  Serial.println((int)g_state);
  Serial.print(F("# ir_raw="));
  Serial.print(digitalRead(PIN_FLYING_FISH));
  Serial.print(F(" blocked="));
  Serial.println(fishBlocked() ? 1 : 0);
  Serial.print(F("# servos_busy="));
  Serial.println(servosBusy() ? 1 : 0);
}

static void handleCommand(String cmd) {
  cmd.trim();
  cmd.toUpperCase();
  if (cmd.length() == 0 || cmd[0] == '#') {
    return;
  }

  if (cmd == "PING") {
    emit(F("PONG"));
    return;
  }

  if (cmd == "RESET") {
    Serial.println(F("ACK:RESET"));
    goIdle();
    return;
  }

  if (cmd == "ACCEPT") {
    if (g_state == State::WaitingForAi) {
      Serial.println(F("ACK:ACCEPT"));
      servosStartAccept();
      g_state = State::Accepting;
    } else {
      emitError(F("STATE"));
    }
    return;
  }

  if (cmd == "REJECT") {
    if (g_state == State::WaitingForAi) {
      Serial.println(F("ACK:REJECT"));
      servosStartReject();
      g_state = State::Rejecting;
    } else {
      emitError(F("STATE"));
    }
    return;
  }

  // ---- debug-only helpers over the same USB serial ----
  if (DEBUG_COMMANDS && (cmd == "TRIGGER" || cmd == "FISH")) {
    if (g_state == State::Idle) {
      g_rearmAfterMs = millis() + FISH_REARM_MS;
      g_state = State::ObjectDetected;
    } else {
      comment(F("not idle"));
    }
    return;
  }
  if (DEBUG_COMMANDS && cmd == "STATUS") {
    printStatus();
    return;
  }
  // Bench test: run a servo sequence from ANY state, bypassing the handshake.
  if (DEBUG_COMMANDS && (cmd == "TESTACCEPT" || cmd == "TESTREJECT")) {
    if (servosBusy()) {
      comment(F("servos busy"));
      return;
    }
    if (cmd == "TESTACCEPT") {
      comment(F("test accept"));
      servosStartAccept();
      g_state = State::Accepting;
    } else {
      comment(F("test reject"));
      servosStartReject();
      g_state = State::Rejecting;
    }
    return;
  }

  // Unknown line: ignore (keeps the protocol robust to noise).
  if (DEBUG_COMMANDS) {
    Serial.print(F("# unknown: "));
    Serial.println(cmd);
  }
}

static void pollLine() {
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      if (g_line.length()) {
        handleCommand(g_line);
        g_line = "";
      }
    } else if (g_line.length() < SERIAL_LINE_MAX) {
      g_line += c;
    } else {
      g_line = "";  // overflow -> drop line
    }
  }
}

// ---------------- setup / loop ----------------
void setup() {
  Serial.begin(PI_BAUD);
  pinMode(PIN_FLYING_FISH, INPUT_PULLUP);
  servosBegin();
  delay(200);
  comment(F("Bottle Vendo Uno controller"));
  emit(F("READY"));
}

void loop() {
  servosLoop();
  pollLine();

  switch (g_state) {
    case State::Idle:
      if (fishTriggered()) {
        g_state = State::ObjectDetected;
      }
      break;

    case State::ObjectDetected:
      emit(F("OBJECT_DETECTED"));
      g_aiDeadlineMs = millis() + AI_RESPONSE_TIMEOUT_MS;
      g_state = State::WaitingForAi;
      break;

    case State::WaitingForAi:
      if ((int32_t)(millis() - g_aiDeadlineMs) >= 0) {
        emitError(F("AI_TIMEOUT"));  // fail safe: reject so the chute cannot jam
        servosStartReject();
        g_state = State::Rejecting;
      }
      break;

    case State::Accepting:
    case State::Rejecting:
      if (servosConsumeComplete()) {
        emit(F("SERVO_COMPLETE"));
        g_cooldownUntilMs = millis() + COOLDOWN_MS;
        g_state = State::Cooldown;
      }
      break;

    case State::Cooldown:
      // Wait out the cooldown AND for the beam to clear before re-arming.
      if ((int32_t)(millis() - g_cooldownUntilMs) >= 0 && !fishBlocked()) {
        goIdle();
      }
      break;

    case State::Error:
      // Recoverable only via RESET.
      break;
  }
}

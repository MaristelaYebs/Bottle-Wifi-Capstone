#include "servos.h"
#include "config.h"

#include <Servo.h>

static Servo acceptServo;
static Servo rejectServo;

enum class Seq : uint8_t { None = 0, Accept, Reject };

static Seq      g_seq = Seq::None;
static uint32_t g_holdUntilMs = 0;   // when to return servo to idle
static uint32_t g_beepUntilMs = 0;   // when to silence the buzzer
static bool     g_beeping = false;
static bool     g_justCompleted = false;

static void indicatorsOff() {
  digitalWrite(PIN_LED_GREEN, LOW);
  digitalWrite(PIN_LED_RED, LOW);
  noTone(PIN_BUZZER);
  digitalWrite(PIN_BUZZER, LOW);
  g_beeping = false;
}

static void beginBeep(uint32_t durationMs) {
  tone(PIN_BUZZER, BUZZER_HZ);
  g_beeping = true;
  g_beepUntilMs = millis() + durationMs;
}

void servosBegin() {
  pinMode(PIN_LED_GREEN, OUTPUT);
  pinMode(PIN_LED_RED, OUTPUT);
  pinMode(PIN_BUZZER, OUTPUT);
  indicatorsOff();
  acceptServo.attach(PIN_ACCEPT);
  rejectServo.attach(PIN_REJECT);
  servosForceIdle();
}

void servosStartAccept() {
  rejectServo.write(REJECT_IDLE);
  acceptServo.write(ACCEPT_DUMP);
  digitalWrite(PIN_LED_RED, LOW);
  digitalWrite(PIN_LED_GREEN, HIGH);
  beginBeep(ACCEPT_BEEP_MS);
  g_seq = Seq::Accept;
  g_holdUntilMs = millis() + SERVO_HOLD_MS;
}

void servosStartReject() {
  acceptServo.write(ACCEPT_IDLE);
  rejectServo.write(REJECT_DUMP);
  digitalWrite(PIN_LED_GREEN, LOW);
  digitalWrite(PIN_LED_RED, HIGH);
  beginBeep(REJECT_BEEP_MS);
  g_seq = Seq::Reject;
  g_holdUntilMs = millis() + SERVO_HOLD_MS;
}

void servosLoop() {
  // Silence the beep independently of the servo hold.
  if (g_beeping && (int32_t)(millis() - g_beepUntilMs) >= 0) {
    noTone(PIN_BUZZER);
    digitalWrite(PIN_BUZZER, LOW);
    g_beeping = false;
  }

  if (g_seq == Seq::None) {
    return;
  }
  if ((int32_t)(millis() - g_holdUntilMs) < 0) {
    return;  // still holding at dump angle
  }

  // Sequence complete: return servo to idle, clear indicators, flag done.
  if (g_seq == Seq::Accept) {
    acceptServo.write(ACCEPT_IDLE);
  } else {
    rejectServo.write(REJECT_IDLE);
  }
  indicatorsOff();
  g_seq = Seq::None;
  g_justCompleted = true;
}

bool servosBusy() { return g_seq != Seq::None; }

bool servosConsumeComplete() {
  if (!g_justCompleted) {
    return false;
  }
  g_justCompleted = false;
  return true;
}

void servosForceIdle() {
  g_seq = Seq::None;
  g_justCompleted = false;
  acceptServo.write(ACCEPT_IDLE);
  rejectServo.write(REJECT_IDLE);
  indicatorsOff();
}

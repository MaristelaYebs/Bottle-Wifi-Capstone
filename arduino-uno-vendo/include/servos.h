#pragma once

#include <Arduino.h>

// Non-blocking servo + LED + buzzer control for the accept/reject sequences.
// A sequence: move dump servo, turn on its LED, beep (short=accept, long=reject),
// hold at dump angle SERVO_HOLD_MS, return to idle, LED off, then report done.
// Call servosLoop() every iteration of loop(); poll servosConsumeComplete()
// to learn when a sequence has just finished (to emit SERVO_COMPLETE).

void servosBegin();

// Start an accept sequence (green LED, short beep, accept servo dump/return).
void servosStartAccept();

// Start a reject sequence (red LED, long beep, reject servo dump/return).
void servosStartReject();

// Drive timing/return-to-idle. Non-blocking; call frequently.
void servosLoop();

// True while a sequence is running (servo not yet returned to idle).
bool servosBusy();

// Returns true exactly once, right after a sequence finishes.
bool servosConsumeComplete();

// Immediately return everything to idle (servos idle, LEDs off, buzzer off).
// Used by RESET and on boot.
void servosForceIdle();

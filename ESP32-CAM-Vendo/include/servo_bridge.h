#pragma once

#include <Arduino.h>

void servoBridgeBegin();
void servoBridgeLoop();

/** Fire-and-forget command to Arduino Uno (newline appended). */
void servoSend(const char *cmd);

/** True once when Uno reports Flying Fish passage (TRIGGER). */
bool servoConsumeTrigger();

void servoOpenEntry();
void servoCloseEntry();
void servoAccept();
/** Same as ACCEPT — Uno has no entry gate. */
void servoAcceptKeepOpen();
void servoReject();
void servoIdle();
void servoCancelPending();

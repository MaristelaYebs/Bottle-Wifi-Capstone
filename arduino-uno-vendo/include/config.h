#pragma once

// =====================================================================
// Bottle Wi-Fi Vendo — Arduino Uno firmware configuration
// =====================================================================
// The Arduino is the hardware controller. It senses the inserted object,
// tells the Orange Pi Zero 3 over USB serial, then runs the accept/reject
// servo+LED+buzzer sequence the Pi commands. The Arduino NEVER classifies
// and NEVER touches the network.
//
// Link to Orange Pi: the primary hardware Serial (USB) @ 115200 8N1.
// (The old SoftwareSerial link to the ESP32-CAM has been removed; pins
//  D7/D8 are now free.)
// Protocol + state machine: docs/architecture/02-low-level-architecture.md §5
// =====================================================================

// ---------- Serial link to Orange Pi ----------
static const uint32_t PI_BAUD = 115200;   // must match config.yaml serial.baudrate
static const uint8_t  SERIAL_LINE_MAX = 48;

// ---------- MH-Sensor "Flying Fish" IR sensor (object trigger) ----------
// The rig uses an MH obstacle/reflective IR sensor (single unit), not a true
// two-part break-beam. Both present the same digital "object present" line, so
// the firmware is identical; only mounting differs.
//
// Digital OUT polarity differs between modules. If TRIGGER fires while the
// chute is EMPTY, flip FLYING_FISH_ACTIVE_LOW. Use the "STATUS" debug command
// to read the raw pin. (NOTE: repo README and the previous config disagreed on
// polarity — verify on your bench and set this correctly.)
static const int  PIN_FLYING_FISH = 2;
static const bool FLYING_FISH_ACTIVE_LOW = false;   // false = blocked reads HIGH
static const uint32_t FISH_HOLD_MS  = 80;    // must stay blocked this long (debounce)
static const uint32_t FISH_REARM_MS = 800;   // ignore re-trigger this long after firing

// ---------- Servos (PWM signal only; power from EXTERNAL 5-6V supply) ----------
static const int PIN_ACCEPT = 9;
static const int PIN_REJECT = 10;

// Do NOT assume 0/180 are safe mechanical limits — use tuned idle/dump angles.
static const int ACCEPT_IDLE = 5;
static const int ACCEPT_DUMP = 175;
static const int REJECT_IDLE = 5;
static const int REJECT_DUMP = 175;

// Servo stays at the dump angle this long before returning to idle.
static const uint32_t SERVO_HOLD_MS = 1800;

// ---------- Indicators ----------
// Green LED = accept, Red LED = reject. Buzzer beeps briefly at the start of a
// sequence (LED stays on for the whole sequence). Accept = short, reject = long.
// LED: pin -> 220R -> LED anode, cathode -> GND
// Active buzzer: pin -> buzzer +, buzzer - -> GND
static const int PIN_LED_GREEN = 4;
static const int PIN_LED_RED   = 5;
static const int PIN_BUZZER    = 6;
static const uint16_t BUZZER_HZ = 1000;
static const uint32_t ACCEPT_BEEP_MS = 150;    // short beep
static const uint32_t REJECT_BEEP_MS = 1000;   // long beep

// ---------- State machine timing ----------
// After OBJECT_DETECTED, wait this long for the Pi to reply ACCEPT/REJECT.
// If it never does, fail SAFE by rejecting so the chute cannot jam.
static const uint32_t AI_RESPONSE_TIMEOUT_MS = 8000;
// Ignore the IR sensor this long after a sequence completes (and until the
// beam is clear) so one object cannot trigger twice.
static const uint32_t COOLDOWN_MS = 2000;

// ---------- Debug ----------
// When true, accept human test commands over the same USB serial:
//   TRIGGER    -> simulate an object insertion (no IR needed)
//   STATUS     -> print state + raw IR pin as "# ..." comment lines
//   TESTACCEPT -> run the accept servo sequence from any state
//   TESTREJECT -> run the reject servo sequence from any state
// The Pi ignores lines it does not recognise (esp. those starting with '#').
static const bool DEBUG_COMMANDS = true;

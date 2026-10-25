# Arduino Uno — Bottle Wi-Fi Vendo controller

Hardware controller for the AI Bottle Wi-Fi Vendo. The Uno senses the inserted
object, tells the **Orange Pi Zero 3** over USB serial, then runs the
accept/reject **servo + LED + buzzer** sequence the Pi commands. The Uno does
**not** classify bottles and does **not** touch the network.

> Replaces the old ESP32-CAM setup: the Pi (YOLO + NCNN) is now the brain and
> connects to the Uno's **primary USB serial**. The former SoftwareSerial link
> to the ESP32-CAM is removed, so **pins D7/D8 are now free**.

```
Object in chute
  → MH "Flying Fish" IR sensor (blocked, debounced)
    → USB serial: OBJECT_DETECTED  → Orange Pi (YOLO/NCNN decides)
      → Pi replies ACCEPT → green LED + short beep + accept servo dump
      → Pi replies REJECT → red LED + long beep + reject servo dump
        → SERVO_COMPLETE → cooldown → idle
```

## Serial protocol (USB @ 115200, 8N1, LF-terminated)

| Direction | Message | Meaning |
|-----------|---------|---------|
| Uno → Pi | `READY` | Booted, idle |
| Uno → Pi | `OBJECT_DETECTED` | Debounced object present |
| Uno → Pi | `SERVO_COMPLETE` | Accept/reject sequence finished |
| Uno → Pi | `ERROR:<msg>` | Fault (e.g. `ERROR:AI_TIMEOUT`, `ERROR:STATE`) |
| Uno → Pi | `PONG` / `ACK:ACCEPT` / `ACK:REJECT` / `ACK:RESET` | Acknowledgements |
| Pi → Uno | `PING` | Liveness check |
| Pi → Uno | `ACCEPT` / `REJECT` | Run sequence (only valid while waiting for AI) |
| Pi → Uno | `RESET` | Force back to idle (any state) |

Lines starting with `#` are human-readable debug the Pi ignores. If the Pi never
answers within `AI_RESPONSE_TIMEOUT_MS` (8 s), the Uno **fails safe and rejects**
so the chute cannot jam.

### State machine
```
IDLE --IR blocked+debounced--> OBJECT_DETECTED --send--> WAITING_FOR_AI
WAITING_FOR_AI --ACCEPT--> ACCEPTING --done--> COOLDOWN --> IDLE
WAITING_FOR_AI --REJECT--> REJECTING --done--> COOLDOWN --> IDLE
WAITING_FOR_AI --timeout-> REJECTING (fail-safe)
any --RESET--> IDLE
```
Guards: IR debounce, detection lock (IR ignored unless IDLE), cooldown + beam-clear
before re-arming, AI-response watchdog.

## Pins (unchanged from the previous rig)

| Function | Uno pin | Notes |
|----------|---------|--------|
| Flying Fish IR OUT | D2 | `INPUT_PULLUP`. See polarity note below. |
| Accept servo signal | D9 | idle 5° → dump 175° |
| Reject servo signal | D10 | idle 5° → dump 175° |
| Green LED (accept) | D4 | pin → 220Ω → LED → GND |
| Red LED (reject) | D5 | pin → 220Ω → LED → GND |
| Buzzer | D6 | short beep (150 ms) accept, long beep (1000 ms) reject |
| USB serial to Orange Pi | USB | 115200 (the Pi is on `/dev/ttyACM*`) |
| ~~SoftSerial to CAM~~ | ~~D7/D8~~ | **removed — now free** |

**⚠ IR polarity to verify:** the previous repo's README said the sensor is *LOW
when blocked* but `config.h` had `FLYING_FISH_ACTIVE_LOW = false` (blocked =
HIGH). These disagree. The firmware keeps the last-committed value
(`false`). **Test on your bench:** upload, open the monitor, type `STATUS`, and
watch `ir_raw`/`blocked` with and without an object. If `TRIGGER` fires on an
empty chute, set `FLYING_FISH_ACTIVE_LOW = true` in `include/config.h`.

## Power (required)

Servos: **external 5–6 V ≥ 2 A** supply, common ground with the Uno. Do **not**
power the servos from the Uno 5V pin (brownouts corrupt the Pi/Uno).

```
External 5-6V ──┬── Accept servo V+
                └── Reject servo V+
GND ── Uno GND ── Servo GND ── (external supply GND)   (all common)
Uno D9/D10 ── servo signal only
```

## Flash & bench test

1. `pio run -t upload`, then `pio device monitor -b 115200`.
2. You should see `READY`.
3. Type `PING` → `PONG`. Type `TRIGGER` → `OBJECT_DETECTED`; then `ACCEPT` or
   `REJECT` to drive the servos; you'll get `SERVO_COMPLETE`.
4. Type `STATUS` to inspect state and the raw IR pin.

Tune angles/timings in [`include/config.h`](include/config.h). Arduino IDE
users: copy `include/config.h`, `include/servos.h`, `src/servos.cpp`,
`src/main.cpp` into a sketch folder (Servo is built-in).

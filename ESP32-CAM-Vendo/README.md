# ESP32-CAM Bottle WiFi Vendo

ESP32-CAM (Arduino framework) + **Arduino Uno** for the chute sensors and servos.

```
Phone → MikroTik Hotspot → ESP32-CAM :80 (JuanFi APIs + camera)
Arduino Uno: Flying Fish → TRIGGER → CAM identify → ACCEPT / REJECT servos
```

## Features

- JuanFi-compatible: `/topUp`, `/checkCoin`, `/cancelTopUp`, `/useVoucher`, `/getRates`
- MH-Sensor Flying Fish on the Uno starts a capture when something passes the tube
- ESP32-CAM identifies PET (TFLite, with motion/shape fallback)
- PET found → accept servo **180°**; not PET → reject servo **180°**
- UART to **Arduino Uno** (`../arduino-uno-vendo/`)
- Preview: `http://ESP_IP/preview`
- Simulate credit (no bottle): `http://ESP_IP/simulateBottle`

## Hardware

| Item | Notes |
|------|--------|
| ESP32-CAM | AI-Thinker (PSRAM recommended) — camera + WiFi + vendo API |
| Arduino Uno | Flying Fish + accept/reject servos — see `../arduino-uno-vendo/` |
| MH-Sensor Flying Fish | IR obstacle on the tube; OUT → Uno D2 |
| 2× 180° servos | Accept / reject (no entry gate) |
| USB-TTL programmer | 3.3V, for flashing CAM (IO0→GND to enter flash mode) |
| 5V power | Stable 5V/2A+ for camera; **separate 5V ≥2A** for servos |
| Level divider | **Required:** Uno TX (5V) → CAM RX (3.3V) |
| MikroTik hAP lite | See `../mikrotik-hap-lite/` |

## Flow

1. User starts a portal session (`/topUp`) — WiFi credit is armed.
2. Bottle passes the Flying Fish → Uno sends `TRIGGER` over UART.
3. CAM waits briefly, captures a few frames, classifies PET vs not.
4. `ACCEPT` or `REJECT` is sent back; Uno rotates that servo 180° and returns to idle.
5. PET during a session credits minutes; invalid items are rejected.

## Configure

Edit `include/config.h`:

```cpp
WIFI_SSID / WIFI_PASSWORD
LOCAL_IP = 10.10.10.252
MINUTES_PER_BOTTLE = 15
MT_HOST / MT_USER / MT_PASSWORD
DEMO_MODE = true   // first test without MikroTik
SERVO_BRIDGE_ENABLE = true
```

## Build & flash

See **[SETUP.md](SETUP.md)**. Flash the Uno from **[../arduino-uno-vendo/](../arduino-uno-vendo/)** as well.

## Hotspot portal

In `hotspot/assets/js/config.js`:

```js
vendoIp: "10.10.10.252"; // ESP32-CAM IP
```

Add that IP to MikroTik hotspot **walled garden**. Full router steps: **[../mikrotik-hap-lite/](../mikrotik-hap-lite/)**.


## Training the Bottle Detection Model

The PlatformIO build does not automatically retrain the bottle detection model. Train the model separately whenever new images are added.

### Add Training Images

Place images in the appropriate folders:

- `tools/dataset/bottle/` for valid bottles
- `tools/dataset/empty/` for empty or background images
- `tools/dataset/reject/` for non-bottle items

Images placed directly inside `tools/` are not used for training.

### Train and Convert the Model

Install the training dependencies, then run:

```bash
pip install -r requirements-train.txt
python train_bottle_model.py --data tools/dataset
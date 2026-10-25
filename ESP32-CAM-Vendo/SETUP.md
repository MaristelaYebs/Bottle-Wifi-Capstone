# Setup Guide — ESP32-CAM Bottle Vendo (Arduino)

This replaces the Orange Pi Zero 3 path. Use **ESP32-CAM** programmed with the **Arduino** framework.

---

## 1. What you need

| Item | Purpose |
|------|---------|
| ESP32-CAM (AI-Thinker) | Camera + WiFi + vendo API |
| Arduino Uno | Flying Fish + 2 servos (see `../arduino-uno-vendo/`) |
| MH-Sensor Flying Fish | IR beam / obstacle on the drop tube |
| 2× 180° servos | Accept / reject flaps |
| USB-TTL (CP2102 / CH340) | Flash CAM firmware (3.3V logic) |
| Jumper wires | Programming + UART CAM↔Uno |
| 1kΩ + 2kΩ resistors | 5V→3.3V divider on Uno TX → CAM RX |
| 5V 2A power | Run camera stably (do not power from weak USB alone) |
| 5V ≥2A servo PSU | Separate supply; common GND with CAM and Uno |
| MikroTik hAP lite | Hotspot + vouchers — see `../mikrotik-hap-lite/` |
| PC with Arduino IDE **or** PlatformIO | Build/upload |

**Not needed:** Orange Pi, ESP32-D, desktop Linux, YOLO PC GPU.

---

## 2. Wire for programming

| USB-TTL | ESP32-CAM |
|---------|-----------|
| 5V | 5V |
| GND | GND |
| RX | U0T (TX) |
| TX | U0R (RX) |

**Flash mode:** connect **IO0 → GND**, then power on / press reset.  
After upload, **remove IO0→GND** and press reset to run.

---

## 3A. Flash with Arduino IDE (beginner)

1. Install [Arduino IDE 2.x](https://www.arduino.cc/en/software)
2. **File → Preferences → Additional Board Manager URLs**, add:
   ```
   https://espressif.github.io/arduino-esp32/package_esp32_index.json
   ```
3. **Boards Manager** → install **esp32** by Espressif
4. Install library: **ArduinoJson** (by Benoit Blanchon) via Library Manager
5. Copy this folder structure into a sketch folder, **or** use PlatformIO (3B) which is easier for multi-file projects

### Easiest for multi-file project: use PlatformIO (recommended)

If you are new, install **VS Code + PlatformIO** extension, open folder:

`Vendo Wifi/esp32-cam-vendo`

Then:

- Edit `include/config.h`
- Click **Upload**

### Arduino IDE single-project tip

PlatformIO project layout (`src/`, `include/`) is not native Arduino IDE. Prefer PlatformIO for this repo.  
(You can still use Arduino IDE Serial Monitor at **115200** after PlatformIO upload.)

---

## 3B. Flash with PlatformIO

1. Install VS Code → extension **PlatformIO IDE**
2. Open folder `esp32-cam-vendo`
3. Edit `include/config.h` (WiFi, IP, minutes, MikroTik)
4. Connect USB-TTL, put board in flash mode (IO0→GND)
5. Terminal:
   ```bash
   pio run -t upload
   pio device monitor -b 115200
   ```
6. Remove IO0 jumper, reset board
7. Serial should show: `WiFi OK 10.10.10.252` and `HTTP server on :80`

---

## 4. First config (`include/config.h`)

```cpp
static const char *WIFI_SSID = "YourWiFi";
static const char *WIFI_PASSWORD = "YourPassword";

static const IPAddress LOCAL_IP(10, 10, 10, 252);  // ESP IP
static const IPAddress GATEWAY(10, 10, 10, 1);     // MikroTik

static const int MINUTES_PER_BOTTLE = 15;           // SET TIME HERE

static const bool DEMO_MODE = true;                 // true until MikroTik API works

static const char *MT_HOST = "10.10.10.1";
static const char *MT_USER = "admin";
static const char *MT_PASSWORD = "your-password";
```

Re-upload after every config change.

---

## 5. Test without MikroTik

1. Set `DEMO_MODE = true`, upload
2. On PC browser:
   - http://10.10.10.252/health
   - http://10.10.10.252/preview  (live chute view + ROI)
3. Simulate a bottle credit:
   - Start session from portal **or** use curl:
   ```bash
   curl -X POST http://10.10.10.252/topUp -d "mac=AA:BB:CC:DD:EE:FF"
   curl -X POST http://10.10.10.252/simulateBottle
   curl -X POST http://10.10.10.252/checkCoin -d "voucher=XXXX"
   ```

---

## 6. Mount the camera (chute)

1. Fix ESP32-CAM so it looks into the **bottle drop chute**
2. Open `/preview` — white rectangle is the ROI
3. Tune in `config.h` if needed (QQVGA 160×120 coordinates):

```cpp
ROI_X, ROI_Y, ROI_W, ROI_H
MIN_BLOB_AREA / MAX_BLOB_AREA
MIN_ASPECT / MAX_ASPECT
```

4. Drop a PET water bottle (250ml–1L) so it passes the Flying Fish; Serial Monitor should print `CAPTURE: Flying Fish trigger` then `IDENTIFY bottle` / `BOTTLE credited=1`

---

## 7. UART to Arduino Uno (Flying Fish + servos)

Wire after both firmwares are flashed. **Uno TX is 5V — use a divider into the CAM RX.**

| ESP32-CAM | Arduino Uno |
|-----------|-------------|
| GPIO14 (TX) | D8 (SoftwareSerial RX) |
| GPIO15 (RX) | D7 via 1kΩ / 2kΩ divider |
| GND | GND |

```
Uno D7 ── 1kΩ ──┬── CAM GPIO15
                2kΩ
                GND
```

| Uno | Device |
|-----|--------|
| D2 | Flying Fish digital OUT |
| D9 | Accept servo signal |
| D10 | Reject servo signal |
| 5V / GND | Flying Fish VCC / GND |
| External 5V | Both servo V+ (common GND) |

In `include/config.h`:

```cpp
static const bool SERVO_BRIDGE_ENABLE = true;
static const int SERVO_UART_TX_PIN = 14;
static const int SERVO_UART_RX_PIN = 15;
static const uint32_t SERVO_UART_BAUD = 9600;
```

**Sequence:** `/topUp` arms credit → Flying Fish → Uno `TRIGGER` → CAM capture + PET identify → `ACCEPT` (180°) or `REJECT` (180°) → cancel/timeout → `REJECT`.

Flash and bench-test the Uno first (`../arduino-uno-vendo/README.md`). USB on the Uno: type `FISH`, `ACCEPT`, `REJECT`. Set `SERVO_BRIDGE_ENABLE = false` if testing CAM without the Uno.

---

## 8. MikroTik (hAP lite v7)

Prefer the pack in **`../mikrotik-hap-lite/`**. Short version:

1. Enable **IP → Services → api** (port 8728)
2. Set real password in `config.h`, set `DEMO_MODE = false`, re-upload
3. Upload updated `hotspot/` to MikroTik
4. In `hotspot/assets/js/config.js`:
   ```js
   var vendorIpAddress = "10.10.10.252";
   // multiVendoAddresses[0].vendoIp = "10.10.10.252"
   ```
5. **Walled Garden**: allow hotspot clients to reach `10.10.10.252`

---

## 9. End-to-end

1. Phone joins MikroTik Wi‑Fi  
2. Tap **INSERT BOTTLE** (session waiting for credit)  
3. Drop PET in the tube — Flying Fish fires, CAM identifies, accept servo 180°  
4. Portal shows 1 bottle + time  
5. **Done** → voucher login  
   Non-PET / failed identify → reject servo 180°  

---

## 10. ESP32-CAM vs Orange Pi

| | ESP32-CAM | OPI Zero 3 |
|--|-----------|------------|
| Cost / power | Low | Higher |
| OS install | None (Arduino firmware) | Linux + packages |
| Detection | Motion + shape (OpenCV-style) | YOLO + OpenCV |
| Accuracy | Good in fixed chute | Higher if tuned |
| Best for | Capstone vendo, simple chute | Heavier AI |

Detection is **not YOLO** on ESP32-CAM (not enough RAM). It uses camera grayscale + ROI motion/aspect — works well for a **fixed chute**.

---

## 11. Troubleshooting

| Problem | Fix |
|---------|-----|
| Camera init failed | Use 5V/2A power; check AI-Thinker pins; board with PSRAM |
| Brownout / reboot | Better power supply; shorter 5V cable |
| Won't flash | IO0→GND, correct TX/RX swap, 115200 |
| WiFi fail | Check SSID/password; 2.4GHz only |
| Portal can't reach ESP | Walled garden + correct `vendoIp` / `vendorIpAddress` |
| No bottle credit | Tune ROI/area on `/preview`; watch Serial |
| MikroTik fail | `DEMO_MODE` first; then API user/password/port 8728 |
| Servos silent | Common GND; external 5V PSU; check UART TX/RX; Uno Serial shows `CAM:` / `→ CAM TRIGGER` |
| Servo wrong direction | Swap idle/dump angles in `arduino-uno-vendo/include/config.h` |
| No capture on drop | Flying Fish OUT on D2; flip `FLYING_FISH_ACTIVE_LOW` if the module is inverted |
| CAM RX damaged / garbage UART | Missing 5V→3.3V divider on Uno D7 → GPIO15 |

---

## Checklist

- [ ] Arduino Uno uploaded; USB `FISH` → CAM Serial `Uno TRIGGER`  
- [ ] ESP32-CAM uploaded; Serial shows WiFi IP + `Uno UART2`  
- [ ] UART CAM↔Uno wired with 5V divider; `ACCEPT` on Uno USB moves accept servo  
- [ ] `/preview` shows chute  
- [ ] Flying Fish pass → capture → PET accept 180° / fail reject 180°  
- [ ] `MINUTES_PER_BOTTLE` set  
- [ ] Hotspot `vendoIp` = ESP IP  
- [ ] Walled garden allows ESP IP  
- [ ] DEMO test OK → then `DEMO_MODE = false`  
- [ ] Real PET during `/topUp` → credit → voucher → login  

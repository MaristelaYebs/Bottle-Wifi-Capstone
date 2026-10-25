#include "camera_detect.h"
#include "config.h"
#include "tflite_bottle.h"

#include "esp_camera.h"
#include "img_converters.h"
#include <cstring>

static camera_fb_t *s_heldFb = nullptr;
static int s_hitStreak = 0;
static bool s_armed = false;
static bool s_waitingClear = false;
static int s_clearStreak = 0;
static uint32_t s_lastCreditMs = 0;
static uint32_t s_armAfterMs = 0; // 0 = settled; else wait until millis()
static uint8_t *s_bg = nullptr;
static int s_bgW = 0;
static int s_bgH = 0;
static bool s_bgReady = false;
static bool s_cameraOk = false;

#if defined(CAMERA_MODEL_AI_THINKER)
#define PWDN_GPIO_NUM 32
#define RESET_GPIO_NUM -1
#define XCLK_GPIO_NUM 0
#define SIOD_GPIO_NUM 26
#define SIOC_GPIO_NUM 27
#define Y9_GPIO_NUM 35
#define Y8_GPIO_NUM 34
#define Y7_GPIO_NUM 39
#define Y6_GPIO_NUM 36
#define Y5_GPIO_NUM 21
#define Y4_GPIO_NUM 19
#define Y3_GPIO_NUM 18
#define Y2_GPIO_NUM 5
#define VSYNC_GPIO_NUM 25
#define HREF_GPIO_NUM 23
#define PCLK_GPIO_NUM 22
#endif

bool cameraInit() {
  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_sccb_sda = SIOD_GPIO_NUM;
  config.pin_sccb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  // 10 MHz is more stable with WiFi; 20 MHz often causes cam_hal: EV-VSYNC-OVF
  config.xclk_freq_hz = 10000000;
  config.pixel_format = PIXFORMAT_GRAYSCALE;
  config.frame_size = FRAMESIZE_QQVGA; // 160x120
  config.jpeg_quality = 12;
  config.fb_count = 2;
  config.grab_mode = CAMERA_GRAB_LATEST;
#if defined(BOARD_HAS_PSRAM)
  config.fb_location = CAMERA_FB_IN_PSRAM;
#else
  config.fb_location = CAMERA_FB_IN_DRAM;
#endif

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    s_cameraOk = false;
    Serial.printf("Camera init failed: 0x%x\n", err);
    return false;
  }

  sensor_t *s = esp_camera_sensor_get();
  if (s) {
    s->set_brightness(s, 0);
    s->set_contrast(s, 1);
    s->set_whitebal(s, 1);
    s->set_gain_ctrl(s, 1);
    s->set_exposure_ctrl(s, 1);
    s->set_hmirror(s, 0);
    s->set_vflip(s, 0);
  }

  Serial.println("Camera OK (QQVGA grayscale, 10MHz xclk)");
  if (USE_TFLITE) {
    if (!tfliteBottleBegin()) {
      Serial.println("TFLite init failed — falling back to motion detect");
    }
  }
  s_cameraOk = true;
  return true;
}

bool cameraReady() { return s_cameraOk; }

void cameraDrainFrame() {
  cameraReleaseFrame();
  camera_fb_t *fb = esp_camera_fb_get();
  if (fb) {
    esp_camera_fb_return(fb);
  }
}

void cameraDeinit() {
  cameraReleaseFrame();
  if (s_bg) {
    free(s_bg);
    s_bg = nullptr;
  }
  s_cameraOk = false;
  esp_camera_deinit();
}

bool cameraCaptureGray(uint8_t **outBuf, int *outW, int *outH) {
  cameraReleaseFrame();
  camera_fb_t *fb = esp_camera_fb_get();
  if (!fb || fb->format != PIXFORMAT_GRAYSCALE) {
    if (fb) esp_camera_fb_return(fb);
    return false;
  }
  s_heldFb = fb;
  *outBuf = fb->buf;
  *outW = fb->width;
  *outH = fb->height;
  return true;
}

void cameraReleaseFrame() {
  if (s_heldFb) {
    esp_camera_fb_return(s_heldFb);
    s_heldFb = nullptr;
  }
}

camera_fb_t *cameraCaptureJpeg() {
  return nullptr;
}

bool cameraSnapshotJpeg(uint8_t **jpg, size_t *len) {
  uint8_t *gray = nullptr;
  int w = 0, h = 0;
  if (!cameraCaptureGray(&gray, &w, &h) || !s_heldFb) {
    return false;
  }
  bool ok = fmt2jpg(gray, w * h, w, h, PIXFORMAT_GRAYSCALE, 80, jpg, len);
  cameraReleaseFrame();
  return ok;
}

static void ensureBg(int w, int h) {
  if (s_bg && s_bgW == w && s_bgH == h) return;
  if (s_bg) free(s_bg);
  s_bgW = w;
  s_bgH = h;
  s_bg = (uint8_t *)malloc(w * h);
  if (s_bg) memset(s_bg, 0, w * h);
  s_bgReady = false;
}

void detectorReset() {
  s_hitStreak = 0;
  s_armed = false;
  s_waitingClear = false;
  s_clearStreak = 0;
  s_lastCreditMs = 0;
  s_armAfterMs = 0;
  s_bgReady = false;
  if (s_bg && s_bgW && s_bgH) {
    memset(s_bg, 0, s_bgW * s_bgH);
  }
}

void detectorLearnBackground() {
  uint8_t *gray = nullptr;
  int w = 0, h = 0;
  if (!cameraCaptureGray(&gray, &w, &h) || !gray) {
    return;
  }
  ensureBg(w, h);
  if (s_bg) {
    const int frameArea = w * h;
    if (!s_bgReady) {
      memcpy(s_bg, gray, frameArea);
      s_bgReady = true;
    } else {
      for (int i = 0; i < frameArea; i++) {
        s_bg[i] = (uint8_t)((s_bg[i] * 7 + gray[i]) / 8);
      }
    }
  }
  cameraReleaseFrame();
}

static bool fallbackLooksLikeBottle(const uint8_t *gray, int w, int h, DetectResult *r) {
  if (!s_bg || !s_bgReady || !gray) {
    return false;
  }
  int minX = w, minY = h, maxX = -1, maxY = -1;
  int motionCount = 0;
  for (int y = 0; y < h; y++) {
    for (int x = 0; x < w; x++) {
      int idx = y * w + x;
      int diff = abs((int)gray[idx] - (int)s_bg[idx]);
      if (diff >= MOTION_THRESHOLD) {
        motionCount++;
        if (x < minX) minX = x;
        if (y < minY) minY = y;
        if (x > maxX) maxX = x;
        if (y > maxY) maxY = y;
      }
    }
  }
  r->motion = motionCount;
  if (maxX < minX || maxY < minY || motionCount < MIN_MOTION_PIXELS) {
    return false;
  }
  int bw = (maxX - minX + 1);
  int bh = (maxY - minY + 1);
  r->area = bw * bh;
  r->aspect = (float)max(bw, bh) / (float)max(1, min(bw, bh));
  return r->area >= MIN_BLOB_AREA && r->aspect >= MIN_ASPECT && r->aspect <= MAX_ASPECT;
}

DetectResult detectorIdentifyBottle() {
  DetectResult r;
  r.label = "identifying";

  delay(CAPTURE_DELAY_MS);
  cameraDrainFrame();

  bool hit = false;
  int hits = 0;
  int scored = 0;
  float bestScore = -1;
  int bestMotion = 0;
  int bestArea = 0;
  float bestAspect = 0;

  for (int i = 0; i < IDENTIFY_FRAMES; i++) {
    uint8_t *gray = nullptr;
    int w = 0, h = 0;
    if (!cameraCaptureGray(&gray, &w, &h) || !gray) {
      delay(IDENTIFY_FRAME_GAP_MS);
      continue;
    }

    DetectResult frame;
    bool frameHit = false;
    if (tfliteBottleReady()) {
      float score = tfliteBottleScore(gray, w, h);
      frame.score = score;
      frameHit = (score >= TFLITE_BOTTLE_THRESHOLD);
      fallbackLooksLikeBottle(gray, w, h, &frame);
      frame.score = score;
    } else {
      frameHit = fallbackLooksLikeBottle(gray, w, h, &frame);
      frame.score = frameHit ? 1.0f : 0.0f;
    }

    scored++;
    if (frameHit) {
      hits++;
    }
    if (frame.score > bestScore) {
      bestScore = frame.score;
    }
    if (frame.motion > bestMotion) {
      bestMotion = frame.motion;
    }
    if (frame.area > bestArea) {
      bestArea = frame.area;
      bestAspect = frame.aspect;
    }

    cameraReleaseFrame();
    delay(IDENTIFY_FRAME_GAP_MS);
  }

  // Need at least two agreeing frames so a glare flash is not an accept
    hit = (hits >= 2) ||
      (scored == 1 && hits == 1 && bestScore >= TFLITE_BOTTLE_THRESHOLD);
  r.detected = hit;
  r.score = bestScore;
  r.motion = bestMotion;
  r.area = bestArea;
  r.aspect = bestAspect;
  r.label = hit ? "bottle" : "not-bottle";
  return r;
}

DetectResult detectorProcessGray(const uint8_t *gray, int w, int h) {
  DetectResult r;
  ensureBg(w, h);
  if (!s_bg || !gray) {
    r.label = "no-buf";
    return r;
  }

  const int frameArea = w * h;

  if (!s_bgReady) {
    memcpy(s_bg, gray, frameArea);
    s_bgReady = true;
  }

  int minX = w, minY = h, maxX = -1, maxY = -1;
  int motionCount = 0;
  for (int y = 0; y < h; y++) {
    for (int x = 0; x < w; x++) {
      int idx = y * w + x;
      int diff = abs((int)gray[idx] - (int)s_bg[idx]);
      if (diff >= MOTION_THRESHOLD) {
        motionCount++;
        if (x < minX) minX = x;
        if (y < minY) minY = y;
        if (x > maxX) maxX = x;
        if (y > maxY) maxY = y;
      }
    }
  }
  r.motion = motionCount;

  // Adapt background only when view is mostly empty — otherwise bottle blends into bg
  const int adaptGate = max(40, MIN_MOTION_PIXELS / 4);
  const bool settling =
      (s_armAfterMs != 0 && (int32_t)(millis() - s_armAfterMs) < 0);
  // During settle (entry gate moving), always refresh background so servo motion
  // is not treated as a bottle once armed.
  if (settling || motionCount < adaptGate) {
    for (int i = 0; i < frameArea; i++) {
      s_bg[i] = settling ? gray[i]
                         : (uint8_t)((s_bg[i] * 7 + gray[i]) / 8);
    }
  }

  bool hit = false;
  if (maxX >= minX && maxY >= minY && motionCount >= MIN_MOTION_PIXELS) {
    int bw = (maxX - minX + 1);
    int bh = (maxY - minY + 1);
    int area = bw * bh;
    float aspect = (float)max(bw, bh) / (float)max(1, min(bw, bh));
    r.area = area;
    r.aspect = aspect;
  }

  // Prefer TFLite when ready: strong motion gate + ML score
  if (tfliteBottleReady() && motionCount >= TFLITE_MOTION_GATE) {
    float score = tfliteBottleScore(gray, w, h);
    r.score = score;
    hit = (score >= TFLITE_BOTTLE_THRESHOLD);
  } else if (!tfliteBottleReady() && maxX >= minX && maxY >= minY &&
             motionCount >= MIN_MOTION_PIXELS) {
    // Fallback shape rules if TFLite unavailable (no pure "close-up motion" credit)
    bool shaped = r.area >= MIN_BLOB_AREA && r.aspect >= MIN_ASPECT &&
                  r.aspect <= MAX_ASPECT;
    hit = shaped;
  }
  r.detected = hit;

  uint32_t now = millis();

  // Arm only after settle window; snapshot a clean background at that moment
  if (s_armAfterMs != 0) {
    if ((int32_t)(now - s_armAfterMs) < 0) {
      r.streak = 0;
      r.label = "settling";
      r.detected = false;
      return r;
    }
    memcpy(s_bg, gray, frameArea);
    s_bgReady = true;
    s_armAfterMs = 0;
    s_armed = true;
    s_hitStreak = 0;
    s_waitingClear = false;
    s_clearStreak = 0;
  }

  if (s_waitingClear) {
    if (!hit) {
      if (++s_clearStreak >= 3) {
        s_waitingClear = false;
        s_armed = true;
        s_hitStreak = 0;
        s_clearStreak = 0;
      }
    } else {
      s_clearStreak = 0;
    }
    r.streak = s_hitStreak;
    r.label = "waiting-clear";
    return r;
  }

  if (now - s_lastCreditMs < COOLDOWN_MS) {
    r.streak = s_hitStreak;
    r.label = "cooldown";
    return r;
  }
  if (!s_armed) {
    r.streak = s_hitStreak;
    r.label = "disarmed";
    return r;
  }

  if (hit) s_hitStreak++;
  else s_hitStreak = 0;
  r.streak = s_hitStreak;

  if (s_hitStreak >= CONFIRM_FRAMES) {
    r.credited = true;
    r.label = "credited";
    s_lastCreditMs = now;
    s_hitStreak = 0;
    s_armed = false;
    s_waitingClear = true;
    s_clearStreak = 0;
  } else if (hit) {
    r.label = "tracking";
  } else if (motionCount >= adaptGate) {
    r.label = "motion";
  } else {
    r.label = "idle";
  }
  return r;
}

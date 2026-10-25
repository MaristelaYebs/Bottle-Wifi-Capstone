#pragma once

#include <Arduino.h>

struct DetectResult {
  bool detected = false;
  bool credited = false;
  int area = 0;
  int motion = 0;
  int streak = 0;
  float aspect = 0;
  float score = -1; // TFLite bottle probability [0..1], or -1 if unused
  const char *label = "idle";
};

bool cameraInit();
bool cameraReady();
void cameraDeinit();
bool cameraCaptureGray(uint8_t **outBuf, int *outW, int *outH);
void cameraReleaseFrame();
void cameraDrainFrame(); // discard one frame (prevents EV-VSYNC-OVF when idle)

bool cameraSnapshotJpeg(uint8_t **jpg, size_t *len);

void detectorReset();
/** Blend current empty-chute frame into the background (call while idle). */
void detectorLearnBackground();
/**
 * After Arduino Flying Fish TRIGGER: wait, capture IDENTIFY_FRAMES, classify PET.
 * detected=true means accept; false means reject.
 */
DetectResult detectorIdentifyBottle();
DetectResult detectorProcessGray(const uint8_t *gray, int w, int h);
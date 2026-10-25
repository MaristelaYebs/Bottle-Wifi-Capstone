#pragma once

#include <Arduino.h>

/** Init TFLite interpreter. Returns false if model/arena failed. */
bool tfliteBottleBegin();

/** True when USE_TFLITE + model ready and interpreter OK. */
bool tfliteBottleReady();

/**
 * Classify grayscale frame as bottle probability [0..1].
 * Downsamples w×h → 96×96. Returns -1 on failure.
 */
float tfliteBottleScore(const uint8_t *gray, int w, int h);

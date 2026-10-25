#include "tflite_bottle.h"
#include "config.h"

#if defined(BOTTLE_MODEL_READY) && (BOTTLE_MODEL_READY == 1)

#include "bottle_model_data.h"

#include <TensorFlowLite_ESP32.h>
#include "tensorflow/lite/micro/all_ops_resolver.h"
#include "tensorflow/lite/micro/micro_error_reporter.h"
#include "tensorflow/lite/micro/micro_interpreter.h"
#include "tensorflow/lite/schema/schema_generated.h"

namespace {

constexpr int kArenaSize = 70 * 1024;
tflite::MicroErrorReporter s_errorReporter;
const tflite::Model *s_model = nullptr;
tflite::MicroInterpreter *s_interpreter = nullptr;
TfLiteTensor *s_input = nullptr;
TfLiteTensor *s_output = nullptr;
uint8_t *s_arena = nullptr;
bool s_ok = false;

void downsampleGray(const uint8_t *src, int sw, int sh, int8_t *dst, int dw, int dh,
                    float scale, int zero_point) {
  for (int y = 0; y < dh; y++) {
    int sy = (y * sh) / dh;
    for (int x = 0; x < dw; x++) {
      int sx = (x * sw) / dw;
      float px = (float)src[sy * sw + sx];
      int q = (int)lroundf(px / scale) + zero_point;
      if (q < -128) q = -128;
      if (q > 127) q = 127;
      dst[y * dw + x] = (int8_t)q;
    }
  }
}

} // namespace

bool tfliteBottleBegin() {
  if (!USE_TFLITE) {
    Serial.println("TFLite disabled in config.h");
    return false;
  }
  if (s_ok) return true;

#if defined(BOARD_HAS_PSRAM)
  s_arena = (uint8_t *)ps_malloc(kArenaSize);
#else
  static uint8_t s_arenaStatic[kArenaSize];
  s_arena = s_arenaStatic;
#endif
  if (!s_arena) {
    Serial.println("TFLite: arena alloc failed");
    return false;
  }

  s_model = tflite::GetModel(g_bottle_model);
  if (s_model->version() != TFLITE_SCHEMA_VERSION) {
    Serial.printf("TFLite: schema mismatch\n");
    return false;
  }

  static tflite::AllOpsResolver resolver;
  static tflite::MicroInterpreter interpreter(s_model, resolver, s_arena, kArenaSize,
                                              &s_errorReporter);
  s_interpreter = &interpreter;

  if (s_interpreter->AllocateTensors() != kTfLiteOk) {
    Serial.println("TFLite: AllocateTensors failed");
    return false;
  }

  s_input = s_interpreter->input(0);
  s_output = s_interpreter->output(0);
  if (!s_input || !s_output) {
    Serial.println("TFLite: missing tensors");
    return false;
  }

  Serial.printf("TFLite OK in=%dx%dx%d arena=%d model=%u\n", s_input->dims->data[1],
                s_input->dims->data[2], s_input->dims->data[3], kArenaSize,
                (unsigned)g_bottle_model_len);
  s_ok = true;
  return true;
}

bool tfliteBottleReady() { return s_ok; }

float tfliteBottleScore(const uint8_t *gray, int w, int h) {
  if (!s_ok || !gray || w < 8 || h < 8) return -1.0f;

  const int dw = s_input->dims->data[1];
  const int dh = s_input->dims->data[2];
  if (s_input->type != kTfLiteInt8) {
    return -1.0f;
  }

  float in_scale = s_input->params.scale;
  int in_zp = s_input->params.zero_point;
  if (in_scale <= 0) in_scale = 1.0f;

  downsampleGray(gray, w, h, s_input->data.int8, dw, dh, in_scale, in_zp);

  if (s_interpreter->Invoke() != kTfLiteOk) {
    return -1.0f;
  }

  if (s_output->type == kTfLiteInt8) {
    float out_scale = s_output->params.scale;
    int out_zp = s_output->params.zero_point;
    float v = (s_output->data.int8[0] - out_zp) * out_scale;
    if (v < 0) v = 0;
    if (v > 1) v = 1;
    return v;
  }
  if (s_output->type == kTfLiteFloat32) {
    float v = s_output->data.f[0];
    if (v < 0) v = 0;
    if (v > 1) v = 1;
    return v;
  }
  return -1.0f;
}

#else

bool tfliteBottleBegin() {
  Serial.println("TFLite: no bottle_model_data.h — run tools/train_bottle_model.py");
  return false;
}
bool tfliteBottleReady() { return false; }
float tfliteBottleScore(const uint8_t *, int, int) { return -1.0f; }

#endif

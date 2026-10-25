#include "session.h"
#include "config.h"

#include <cstring>

static TopUpSession g_session;

void sessionGenVoucher(char *out, size_t len) {
  static const char *alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
  if (len < 9) {
    if (len) out[0] = 0;
    return;
  }
  for (int i = 0; i < 8; i++) {
    out[i] = alphabet[esp_random() % 32];
  }
  out[8] = 0;
}

static void copyField(char *dst, size_t dstLen, const char *src) {
  if (!dst || dstLen == 0) {
    return;
  }
  if (!src) {
    dst[0] = 0;
    return;
  }
  strncpy(dst, src, dstLen - 1);
  dst[dstLen - 1] = 0;
}

bool sessionMacMatches(const TopUpSession *s, const char *mac) {
  if (!s || !mac || !mac[0] || !s->mac[0]) {
    return false;
  }
  char a[13] = {0};
  char b[13] = {0};
  int ia = 0, ib = 0;
  for (const char *p = s->mac; *p && ia < 12; p++) {
    char c = *p;
    if (c >= 'a' && c <= 'f') {
      c = static_cast<char>(c - 32);
    }
    if ((c >= '0' && c <= '9') || (c >= 'A' && c <= 'F')) {
      a[ia++] = c;
    }
  }
  for (const char *p = mac; *p && ib < 12; p++) {
    char c = *p;
    if (c >= 'a' && c <= 'f') {
      c = static_cast<char>(c - 32);
    }
    if ((c >= '0' && c <= '9') || (c >= 'A' && c <= 'F')) {
      b[ib++] = c;
    }
  }
  return ia == 12 && ib == 12 && strcmp(a, b) == 0;
}

void sessionReapStale() {
  if (!g_session.active || g_session.cancelled || g_session.finalized) {
    return;
  }
  const uint32_t age = millis() - g_session.startedMs;
  if (g_session.bottleCount == 0 && sessionExpired(&g_session)) {
    Serial.println("Session released (wait timeout, no bottles)");
    g_session.active = false;
    return;
  }
  if (age >= SESSION_STALE_MS) {
    Serial.printf("Session released (stale %lums, bottles=%d)\n",
                  static_cast<unsigned long>(age), g_session.bottleCount);
    g_session.active = false;
    g_session.cancelled = true;
  }
}

bool sessionIsBusy() {
  sessionReapStale();
  if (!g_session.active || g_session.cancelled || g_session.finalized) {
    return false;
  }
  if (sessionExpired(&g_session) && g_session.bottleCount == 0) {
    return false;
  }
  if (sessionExpired(&g_session) && g_session.bottleCount > 0) {
    // still busy until useVoucher/cancel (unless stale reap already cleared it)
    return true;
  }
  return g_session.active;
}

void sessionBegin(const char *voucherIn, const char *mac, bool extendTime, const char *ip) {
  memset(&g_session, 0, sizeof(g_session));
  if (voucherIn && voucherIn[0]) {
    copyField(g_session.voucher, sizeof(g_session.voucher), voucherIn);
  } else {
    sessionGenVoucher(g_session.voucher, sizeof(g_session.voucher));
  }
  copyField(g_session.mac, sizeof(g_session.mac), mac);
  copyField(g_session.ip, sizeof(g_session.ip), ip);
  g_session.active = true;
  g_session.extendTime = extendTime;
  g_session.startedMs = millis();
  g_session.waitTimeMs = WAIT_TIME_MS;
  g_session.minutesPerBottle = MINUTES_PER_BOTTLE;
  g_session.maxBottles = MAX_BOTTLES_PER_SESSION;
}

TopUpSession *sessionGet(const char *voucher) {
  if (!voucher || !g_session.active) {
    return nullptr;
  }
  if (strcmp(g_session.voucher, voucher) != 0) {
    return nullptr;
  }
  return &g_session;
}

TopUpSession *sessionGetOrActive(const char *voucher) {
  TopUpSession *s = sessionGet(voucher);
  if (s) {
    return s;
  }
  if (g_session.active && !g_session.cancelled && !g_session.finalized) {
    return &g_session;
  }
  return nullptr;
}

TopUpSession *sessionActive() {
  return g_session.active ? &g_session : nullptr;
}

bool sessionCreditBottle() {
  if (!g_session.active || g_session.cancelled || g_session.finalized) {
    return false;
  }
  if (sessionExpired(&g_session) && g_session.bottleCount == 0) {
    return false;
  }
  if (g_session.bottleCount >= g_session.maxBottles) {
    return false;
  }
  g_session.bottleCount++;
  g_session.lastNewBottles = 1;
  g_session.timeAddedSeconds = g_session.bottleCount * g_session.minutesPerBottle * 60;
  g_session.startedMs = millis(); // refresh wait window
  return true;
}

void sessionCancel(const char *voucher) {
  TopUpSession *s = sessionGet(voucher);
  if (!s) {
    if (g_session.active) {
      g_session.cancelled = true;
      g_session.active = false;
    }
    return;
  }
  s->cancelled = true;
  s->active = false;
}

void sessionForceClear() {
  g_session.active = false;
  g_session.cancelled = true;
  g_session.finalized = false;
  g_session.bottleCount = 0;
  g_session.lastNewBottles = 0;
}

void sessionFinalize(const char *voucher) {
  TopUpSession *s = sessionGetOrActive(voucher);
  if (!s) {
    return;
  }
  s->finalized = true;
  s->active = false;
}

uint32_t sessionRemainMs(const TopUpSession *s) {
  if (!s) return 0;
  uint32_t elapsed = millis() - s->startedMs;
  if (elapsed >= s->waitTimeMs) return 0;
  return s->waitTimeMs - elapsed;
}

bool sessionExpired(const TopUpSession *s) {
  return sessionRemainMs(s) == 0;
}

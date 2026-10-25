#pragma once

#include <Arduino.h>

struct TopUpSession {
  char voucher[32] = {0};
  char mac[24] = {0};
  char ip[16] = {0};
  bool active = false;
  bool cancelled = false;
  bool finalized = false;
  bool extendTime = false;
  uint32_t startedMs = 0;
  uint32_t waitTimeMs = 90000;
  int minutesPerBottle = 15;
  int maxBottles = 5;
  int bottleCount = 0;
  int timeAddedSeconds = 0;
  int lastNewBottles = 0;
};

void sessionBegin(const char *voucherIn, const char *mac, bool extendTime,
                  const char *ip = nullptr);
bool sessionIsBusy();
/** Drop idle/stuck sessions so a new phone can INSERT BOTTLE. */
void sessionReapStale();
TopUpSession *sessionGet(const char *voucher);
/** Active session, or the only chute session if voucher mismatches. */
TopUpSession *sessionGetOrActive(const char *voucher);
TopUpSession *sessionActive();
bool sessionMacMatches(const TopUpSession *s, const char *mac);
bool sessionCreditBottle();
void sessionCancel(const char *voucher);
void sessionForceClear();
void sessionFinalize(const char *voucher);
uint32_t sessionRemainMs(const TopUpSession *s);
bool sessionExpired(const TopUpSession *s);
void sessionGenVoucher(char *out, size_t len);

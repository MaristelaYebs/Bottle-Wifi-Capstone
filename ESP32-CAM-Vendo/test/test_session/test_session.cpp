#include <Arduino.h>
#include <cstring>
#include <unity.h>

#include "config.h"
#include "session.h"

void setUp(void) {
  // Reset any leftover session between tests
  sessionCancel("");
}

void tearDown(void) {}

void test_session_begin_sets_active(void) {
  sessionBegin("ABCD1234", "AA:BB:CC:DD:EE:FF", false);

  TEST_ASSERT_TRUE(sessionIsBusy());
  TopUpSession *s = sessionGet("ABCD1234");
  TEST_ASSERT_NOT_NULL(s);
  TEST_ASSERT_EQUAL_STRING("ABCD1234", s->voucher);
  TEST_ASSERT_EQUAL_STRING("AA:BB:CC:DD:EE:FF", s->mac);
  TEST_ASSERT_FALSE(s->extendTime);
  TEST_ASSERT_EQUAL_INT(0, s->bottleCount);
  TEST_ASSERT_EQUAL_INT(MINUTES_PER_BOTTLE, s->minutesPerBottle);
  TEST_ASSERT_EQUAL_INT(MAX_BOTTLES_PER_SESSION, s->maxBottles);
}

void test_session_get_rejects_wrong_voucher(void) {
  sessionBegin("ABCD1234", "AA:BB:CC:DD:EE:FF", false);
  TEST_ASSERT_NULL(sessionGet("WRONG001"));
}

void test_session_credit_bottle_updates_time(void) {
  sessionBegin("ABCD1234", "AA:BB:CC:DD:EE:FF", false);

  TEST_ASSERT_TRUE(sessionCreditBottle());
  TopUpSession *s = sessionGet("ABCD1234");
  TEST_ASSERT_NOT_NULL(s);
  TEST_ASSERT_EQUAL_INT(1, s->bottleCount);
  TEST_ASSERT_EQUAL_INT(MINUTES_PER_BOTTLE * 60, s->timeAddedSeconds);

  TEST_ASSERT_TRUE(sessionCreditBottle());
  TEST_ASSERT_EQUAL_INT(2, s->bottleCount);
  TEST_ASSERT_EQUAL_INT(2 * MINUTES_PER_BOTTLE * 60, s->timeAddedSeconds);
}

void test_session_credit_respects_max_bottles(void) {
  sessionBegin("ABCD1234", "AA:BB:CC:DD:EE:FF", false);

  for (int i = 0; i < MAX_BOTTLES_PER_SESSION; i++) {
    TEST_ASSERT_TRUE(sessionCreditBottle());
  }
  TEST_ASSERT_FALSE(sessionCreditBottle());

  TopUpSession *s = sessionGet("ABCD1234");
  TEST_ASSERT_NOT_NULL(s);
  TEST_ASSERT_EQUAL_INT(MAX_BOTTLES_PER_SESSION, s->bottleCount);
}

void test_session_cancel_clears_busy(void) {
  sessionBegin("ABCD1234", "AA:BB:CC:DD:EE:FF", false);
  TEST_ASSERT_TRUE(sessionIsBusy());

  sessionCancel("ABCD1234");
  TEST_ASSERT_FALSE(sessionIsBusy());
  TEST_ASSERT_NULL(sessionGet("ABCD1234"));
}

void test_session_finalize_clears_busy(void) {
  sessionBegin("ABCD1234", "AA:BB:CC:DD:EE:FF", false);
  TEST_ASSERT_TRUE(sessionCreditBottle());

  sessionFinalize("ABCD1234");
  TEST_ASSERT_FALSE(sessionIsBusy());
  TEST_ASSERT_NULL(sessionGet("ABCD1234"));
}

void test_session_force_clear_clears_busy(void) {
  sessionBegin("ABCD1234", "AA:BB:CC:DD:EE:FF", false);
  TEST_ASSERT_TRUE(sessionCreditBottle());
  TEST_ASSERT_TRUE(sessionIsBusy());
  sessionForceClear();
  TEST_ASSERT_FALSE(sessionIsBusy());
  TEST_ASSERT_NULL(sessionActive());
}

void test_session_get_or_active_allows_mismatch(void) {
  sessionBegin("ABCD1234", "AA:BB:CC:DD:EE:FF", false);
  TEST_ASSERT_NOT_NULL(sessionGetOrActive("WRONG001"));
  TEST_ASSERT_EQUAL_STRING("ABCD1234", sessionGetOrActive("WRONG001")->voucher);
}

void test_session_mac_matches_ignores_separators(void) {
  sessionBegin("ABCD1234", "AA:BB:CC:DD:EE:FF", false);
  TopUpSession *s = sessionActive();
  TEST_ASSERT_NOT_NULL(s);
  TEST_ASSERT_TRUE(sessionMacMatches(s, "aabbccddeeff"));
  TEST_ASSERT_TRUE(sessionMacMatches(s, "AA-BB-CC-DD-EE-FF"));
  TEST_ASSERT_FALSE(sessionMacMatches(s, "00:11:22:33:44:55"));
}

void test_session_gen_voucher_length(void) {
  char code[16];
  sessionGenVoucher(code, sizeof(code));
  TEST_ASSERT_EQUAL_UINT(8, strlen(code));
}

void test_session_remain_ms_positive_when_fresh(void) {
  sessionBegin("ABCD1234", "AA:BB:CC:DD:EE:FF", false);
  TopUpSession *s = sessionGet("ABCD1234");
  TEST_ASSERT_NOT_NULL(s);
  TEST_ASSERT_FALSE(sessionExpired(s));
  TEST_ASSERT_TRUE(sessionRemainMs(s) > 0);
  TEST_ASSERT_TRUE(sessionRemainMs(s) <= WAIT_TIME_MS);
}

void setup() {
  delay(2000);
  UNITY_BEGIN();
  RUN_TEST(test_session_begin_sets_active);
  RUN_TEST(test_session_get_rejects_wrong_voucher);
  RUN_TEST(test_session_credit_bottle_updates_time);
  RUN_TEST(test_session_credit_respects_max_bottles);
  RUN_TEST(test_session_cancel_clears_busy);
  RUN_TEST(test_session_finalize_clears_busy);
  RUN_TEST(test_session_force_clear_clears_busy);
  RUN_TEST(test_session_get_or_active_allows_mismatch);
  RUN_TEST(test_session_mac_matches_ignores_separators);
  RUN_TEST(test_session_gen_voucher_length);
  RUN_TEST(test_session_remain_ms_positive_when_fresh);
  UNITY_END();
}

void loop() {}

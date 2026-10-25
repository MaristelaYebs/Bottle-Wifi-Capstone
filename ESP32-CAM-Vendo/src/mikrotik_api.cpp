#include "mikrotik_api.h"
#include "config.h"

#include <WiFi.h>

static void setReason(String *reasonOut, const char *msg) {
  if (reasonOut) {
    *reasonOut = msg;
  }
}

static bool apiReadWord(WiFiClient &c, String &out, uint32_t timeoutMs = 4000) {
  out = "";
  uint32_t start = millis();
  while (c.connected() && (millis() - start) < timeoutMs) {
    if (!c.available()) {
      delay(1);
      continue;
    }
    int len = 0;
    int b = c.read();
    if (b < 0) return false;
    if (b == 0) {
      return true; // end-of-sentence empty word
    }
    if ((b & 0x80) == 0x00) {
      len = b;
    } else if ((b & 0xC0) == 0x80) {
      int b2 = c.read();
      if (b2 < 0) return false;
      len = ((b & 0x3F) << 8) + b2;
    } else if ((b & 0xE0) == 0xC0) {
      int b2 = c.read();
      int b3 = c.read();
      if (b2 < 0 || b3 < 0) return false;
      len = ((b & 0x1F) << 16) + (b2 << 8) + b3;
    } else {
      return false;
    }
    for (int i = 0; i < len; i++) {
      while (!c.available() && (millis() - start) < timeoutMs) delay(1);
      int ch = c.read();
      if (ch < 0) return false;
      out += (char)ch;
    }
    return true;
  }
  return false;
}

static void apiWriteWord(WiFiClient &c, const String &word) {
  int len = word.length();
  if (len < 0x80) {
    c.write((uint8_t)len);
  } else if (len < 0x4000) {
    c.write((uint8_t)((len >> 8) | 0x80));
    c.write((uint8_t)(len & 0xFF));
  } else {
    c.write((uint8_t)((len >> 16) | 0xC0));
    c.write((uint8_t)((len >> 8) & 0xFF));
    c.write((uint8_t)(len & 0xFF));
  }
  c.print(word);
}

static void apiEndSentence(WiFiClient &c) { c.write((uint8_t)0); }

static String formatUptime(int seconds) {
  seconds = max(0, seconds);
  int h = seconds / 3600;
  int m = (seconds % 3600) / 60;
  int s = seconds % 60;
  char buf[32];
  if (h > 0) snprintf(buf, sizeof(buf), "%dh%dm%ds", h, m, s);
  else if (m > 0) snprintf(buf, sizeof(buf), "%dm%ds", m, s);
  else snprintf(buf, sizeof(buf), "%ds", s);
  return String(buf);
}

/** Parse RouterOS uptime like 1d2h3m4s / 15m / 900s / 00:15:00. */
static int parseUptimeSeconds(const String &s) {
  if (s.length() == 0) return 0;

  // RouterOS sometimes returns HH:MM:SS or MM:SS
  if (s.indexOf(':') >= 0) {
    int parts[3] = {0, 0, 0};
    int n = 0;
    int val = 0;
    for (unsigned i = 0; i < s.length() && n < 3; i++) {
      char c = s[i];
      if (c >= '0' && c <= '9') {
        val = val * 10 + (c - '0');
      } else if (c == ':') {
        parts[n++] = val;
        val = 0;
      }
    }
    parts[n++] = val;
    if (n == 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
    if (n == 2) return parts[0] * 60 + parts[1];
    return parts[0];
  }

  int total = 0;
  int val = 0;
  bool sawDigit = false;
  for (unsigned i = 0; i < s.length(); i++) {
    char c = s[i];
    if (c >= '0' && c <= '9') {
      val = val * 10 + (c - '0');
      sawDigit = true;
    } else if (c == 'w' || c == 'W') {
      total += val * 7 * 86400;
      val = 0;
      sawDigit = false;
    } else if (c == 'd' || c == 'D') {
      total += val * 86400;
      val = 0;
      sawDigit = false;
    } else if (c == 'h' || c == 'H') {
      total += val * 3600;
      val = 0;
      sawDigit = false;
    } else if (c == 'm' || c == 'M') {
      total += val * 60;
      val = 0;
      sawDigit = false;
    } else if (c == 's' || c == 'S') {
      total += val;
      val = 0;
      sawDigit = false;
    }
  }
  if (sawDigit && total == 0) {
    total = val; // bare number → seconds
  }
  return total;
}

static bool drainUntilDone(WiFiClient &c, bool &trap, String *trapMessage = nullptr) {
  trap = false;
  String word;
  uint32_t start = millis();
  while (c.connected() && millis() - start < 5000) {
    if (!apiReadWord(c, word)) break;
    if (word.length() == 0) {
      // end of sentence — keep reading until !done if not seen
      continue;
    }
    if (word == "!trap" || word == "!fatal") {
      trap = true;
    } else if (trap && trapMessage && word.startsWith("=message=")) {
      *trapMessage = word.substring(9);
    }
    if (word == "!done") return !trap;
  }
  return false;
}

static bool apiLogin(WiFiClient &c, String *reasonOut) {
  apiWriteWord(c, "/login");
  apiWriteWord(c, String("=name=") + MT_USER);
  apiWriteWord(c, String("=password=") + MT_PASSWORD);
  apiEndSentence(c);

  bool trap = false;
  String trapMsg;
  if (!drainUntilDone(c, trap, &trapMsg)) {
    String msg = "login failed";
    if (trapMsg.length()) {
      msg += ": ";
      msg += trapMsg;
    } else {
      msg += " (check admin password / API user)";
    }
    setReason(reasonOut, msg.c_str());
    Serial.printf("MikroTik %s\n", msg.c_str());
    return false;
  }
  return true;
}

static bool findUserIdAndLimit(WiFiClient &c, const char *voucher, String &idOut,
                               int &limitSecOut, int *usedSecOut = nullptr) {
  idOut = "";
  limitSecOut = 0;
  if (usedSecOut) {
    *usedSecOut = 0;
  }
  apiWriteWord(c, "/ip/hotspot/user/print");
  apiWriteWord(c, String("?name=") + voucher);
  apiWriteWord(c, "=.proplist=.id,limit-uptime,uptime");
  apiEndSentence(c);

  String word;
  bool trap = false;
  uint32_t start = millis();
  while (c.connected() && millis() - start < 4000) {
    if (!apiReadWord(c, word)) break;
    if (word == "!trap" || word == "!fatal") trap = true;
    if (word.startsWith("=.id=")) {
      idOut = word.substring(5);
    } else if (word.startsWith("=limit-uptime=")) {
      limitSecOut = parseUptimeSeconds(word.substring(14));
    } else if (usedSecOut && word.startsWith("=uptime=")) {
      *usedSecOut = parseUptimeSeconds(word.substring(8));
    }
    if (word == "!done") break;
  }
  return !trap && idOut.length() > 0;
}

static bool setUserUptime(WiFiClient &c, const String &id, const String &uptime,
                          const String &password, bool setPassword, String *reasonOut) {
  apiWriteWord(c, "/ip/hotspot/user/set");
  apiWriteWord(c, String("=.id=") + id);
  apiWriteWord(c, String("=limit-uptime=") + uptime);
  if (setPassword) {
    apiWriteWord(c, String("=password=") + password);
  }
  apiEndSentence(c);
  bool trap = false;
  String msg;
  bool ok = drainUntilDone(c, trap, &msg);
  if (!ok) {
    String detail = "set user failed";
    if (msg.length()) {
      detail += ": ";
      detail += msg;
    }
    setReason(reasonOut, detail.c_str());
    Serial.println(detail);
  }
  return ok;
}

static bool connectApi(WiFiClient &c, String *reasonOut) {
  c.setTimeout(5);
  if (!c.connect(MT_HOST, MT_PORT)) {
    char buf[128];
    snprintf(buf, sizeof(buf),
             "connect failed %s:%u (bypass ESP in hotspot ip-binding?)", MT_HOST,
             MT_PORT);
    setReason(reasonOut, buf);
    Serial.println(buf);
    return false;
  }
  return true;
}

bool mikrotikTestConnection(String *reasonOut) {
  if (DEMO_MODE) {
    setReason(reasonOut, "demo_mode=true (API skipped)");
    return true;
  }
  WiFiClient client;
  if (!connectApi(client, reasonOut)) {
    return false;
  }
  bool ok = apiLogin(client, reasonOut);
  client.stop();
  if (ok) {
    setReason(reasonOut, "login OK");
    Serial.println("MikroTik test: login OK");
  }
  return ok;
}

static int stackedLimitSec(int oldLimitSec, int usedSec, int addUptimeSeconds,
                           bool keepSessionOnline) {
  const int add = max(0, addUptimeSeconds);
  if (keepSessionOnline) {
    // Session uptime keeps counting, so grow the original limit.
    if (oldLimitSec <= 0) {
      return add;
    }
    return oldLimitSec + add;
  }
  int remaining = oldLimitSec <= 0 ? 0 : max(0, oldLimitSec - usedSec);
  return remaining + add;
}

bool mikrotikCreateOrExtendUser(const char *voucher, int addUptimeSeconds,
                                String *reasonOut, int *totalLimitSecOut,
                                bool keepSessionOnline, bool allowCreate) {
  if (totalLimitSecOut) {
    *totalLimitSecOut = max(0, addUptimeSeconds);
  }
  if (DEMO_MODE) {
    Serial.printf("[DEMO] voucher=%s add=%ds keep=%d\n", voucher, addUptimeSeconds,
                  keepSessionOnline ? 1 : 0);
    setReason(reasonOut, "demo_mode");
    return true;
  }

  WiFiClient client;
  if (!connectApi(client, reasonOut)) {
    return false;
  }
  if (!apiLogin(client, reasonOut)) {
    client.stop();
    return false;
  }

  // Must match hotspot loginOption: 0 = empty password, 1 = password=voucher
  String password = MT_USERNAME_ONLY ? String("") : String(voucher);

  // Prefer extend path when user already exists (previous remaining + new credit)
  String existingId;
  int oldLimitSec = 0;
  int usedSec = 0;
  if (findUserIdAndLimit(client, voucher, existingId, oldLimitSec, &usedSec)) {
    int remaining = oldLimitSec <= 0 ? 0 : max(0, oldLimitSec - usedSec);
    int totalSec = stackedLimitSec(oldLimitSec, usedSec, addUptimeSeconds,
                                   keepSessionOnline);
    String uptime = formatUptime(totalSec);
    bool ok = setUserUptime(client, existingId, uptime, password, false, reasonOut);
    client.stop();
    if (ok) {
      if (totalLimitSecOut) {
        *totalLimitSecOut = totalSec;
      }
      setReason(reasonOut, "extended");
    }
    Serial.printf(
        "MikroTik voucher %s EXTEND used=%ds remain=%ds +%ds keep=%d => total=%ds %s (%s)\n",
        voucher, usedSec, remaining, addUptimeSeconds, keepSessionOnline ? 1 : 0,
        totalSec, ok ? "OK" : "FAIL", uptime.c_str());
    return ok;
  }

  if (!allowCreate) {
    client.stop();
    setReason(reasonOut, "user not found");
    Serial.printf("MikroTik voucher %s missing — will not create on extend\n", voucher);
    return false;
  }

  String uptime = formatUptime(addUptimeSeconds);
  apiWriteWord(client, "/ip/hotspot/user/add");
  apiWriteWord(client, String("=name=") + voucher);
  apiWriteWord(client, String("=password=") + password);
  apiWriteWord(client, String("=profile=") + MT_HOTSPOT_PROFILE);
  apiWriteWord(client, String("=limit-uptime=") + uptime);
  apiEndSentence(client);

  bool trap = false;
  String trapMsg;
  bool ok = drainUntilDone(client, trap, &trapMsg);
  if (!ok) {
    Serial.printf("MikroTik add failed (%s) — retry find/set\n",
                  trapMsg.length() ? trapMsg.c_str() : "unknown");
    String id;
    int lim = 0;
    int used = 0;
    if (findUserIdAndLimit(client, voucher, id, lim, &used)) {
      int totalSec = stackedLimitSec(lim, used, addUptimeSeconds, keepSessionOnline);
      uptime = formatUptime(totalSec);
      ok = setUserUptime(client, id, uptime, password, true, reasonOut);
      if (ok && totalLimitSecOut) {
        *totalLimitSecOut = totalSec;
      }
    } else {
      String detail = "add failed";
      if (trapMsg.length()) {
        detail += ": ";
        detail += trapMsg;
      } else {
        detail += " (profile/user rights?)";
      }
      setReason(reasonOut, detail.c_str());
      Serial.println(detail);
    }
  } else {
    setReason(reasonOut, "created");
    if (totalLimitSecOut) {
      *totalLimitSecOut = max(0, addUptimeSeconds);
    }
  }

  client.stop();
  Serial.printf("MikroTik voucher %s => %s (%s)\n", voucher, ok ? "OK" : "FAIL",
                uptime.c_str());
  return ok;
}

static String normalizeMac(const char *mac) {
  String hex;
  if (!mac) {
    return hex;
  }
  for (const char *p = mac; *p; p++) {
    char c = *p;
    if (c >= 'a' && c <= 'f') {
      c = static_cast<char>(c - 32);
    }
    if ((c >= '0' && c <= '9') || (c >= 'A' && c <= 'F')) {
      hex += c;
    }
  }
  if (hex.length() != 12) {
    return String("");
  }
  String fmt;
  for (int i = 0; i < 12; i += 2) {
    if (i) {
      fmt += ':';
    }
    fmt += hex[i];
    fmt += hex[i + 1];
  }
  return fmt;
}

bool mikrotikFindActiveUserByMac(const char *mac, String *userOut) {
  if (userOut) {
    *userOut = "";
  }
  if (DEMO_MODE) {
    return false;
  }
  String macFmt = normalizeMac(mac);
  if (macFmt.length() == 0) {
    return false;
  }

  WiFiClient client;
  String reason;
  if (!connectApi(client, &reason)) {
    return false;
  }
  if (!apiLogin(client, &reason)) {
    client.stop();
    return false;
  }

  apiWriteWord(client, "/ip/hotspot/active/print");
  apiWriteWord(client, String("?mac-address=") + macFmt);
  apiWriteWord(client, "=.proplist=user");
  apiEndSentence(client);

  String word;
  String user;
  uint32_t start = millis();
  while (client.connected() && millis() - start < 4000) {
    if (!apiReadWord(client, word)) {
      break;
    }
    if (word.startsWith("=user=")) {
      user = word.substring(6);
    }
    if (word == "!done") {
      break;
    }
  }
  client.stop();
  if (user.length() == 0) {
    return false;
  }
  if (userOut) {
    *userOut = user;
  }
  Serial.printf("MikroTik active user for %s = %s\n", macFmt.c_str(), user.c_str());
  return true;
}

bool mikrotikFindActiveUserByIp(const char *ip, String *userOut) {
  if (userOut) {
    *userOut = "";
  }
  if (DEMO_MODE || !ip || !ip[0]) {
    return false;
  }
  WiFiClient client;
  String reason;
  if (!connectApi(client, &reason) || !apiLogin(client, &reason)) {
    client.stop();
    return false;
  }
  apiWriteWord(client, "/ip/hotspot/active/print");
  apiWriteWord(client, String("?address=") + ip);
  apiWriteWord(client, "=.proplist=user");
  apiEndSentence(client);
  String word;
  String user;
  uint32_t start = millis();
  while (client.connected() && millis() - start < 4000) {
    if (!apiReadWord(client, word)) {
      break;
    }
    if (word.startsWith("=user=")) {
      user = word.substring(6);
    }
    if (word == "!done") {
      break;
    }
  }
  client.stop();
  if (user.length() == 0) {
    return false;
  }
  if (userOut) {
    *userOut = user;
  }
  Serial.printf("MikroTik active user for ip %s = %s\n", ip, user.c_str());
  return true;
}

int mikrotikRemainingSeconds(const char *voucher) {
  if (!voucher || !voucher[0] || DEMO_MODE) {
    return 0;
  }
  WiFiClient client;
  String reason;
  if (!connectApi(client, &reason) || !apiLogin(client, &reason)) {
    client.stop();
    return 0;
  }
  String id;
  int limitSec = 0;
  int usedSec = 0;
  bool ok = findUserIdAndLimit(client, voucher, id, limitSec, &usedSec);
  client.stop();
  if (!ok || limitSec <= 0) {
    return 0;
  }
  return max(0, limitSec - usedSec);
}

static int collectReplyIds(WiFiClient &c, String *ids, int maxIds) {
  String word;
  int n = 0;
  uint32_t start = millis();
  while (c.connected() && millis() - start < 4000) {
    if (!apiReadWord(c, word)) {
      break;
    }
    if (word.startsWith("=.id=") && n < maxIds) {
      ids[n++] = word.substring(5);
    }
    if (word == "!done") {
      break;
    }
  }
  return n;
}

static void printActiveIds(WiFiClient &c, const String &query, String *ids, int *n, int maxIds) {
  apiWriteWord(c, "/ip/hotspot/active/print");
  apiWriteWord(c, query);
  apiWriteWord(c, "=.proplist=.id");
  apiEndSentence(c);
  int got = collectReplyIds(c, ids + *n, maxIds - *n);
  *n += got;
}

bool mikrotikKickHotspot(const char *voucher, const char *mac, String *reasonOut) {
  if (DEMO_MODE) {
    setReason(reasonOut, "demo_mode");
    return true;
  }

  WiFiClient client;
  if (!connectApi(client, reasonOut)) {
    return false;
  }
  if (!apiLogin(client, reasonOut)) {
    client.stop();
    return false;
  }

  String ids[8];
  int n = 0;
  if (voucher && voucher[0]) {
    printActiveIds(client, String("?user=") + voucher, ids, &n, 8);
  }
  String macFmt = normalizeMac(mac);
  if (macFmt.length() && n < 8) {
    printActiveIds(client, String("?mac-address=") + macFmt, ids, &n, 8);
  }

  int removed = 0;
  for (int i = 0; i < n; i++) {
    bool dup = false;
    for (int j = 0; j < i; j++) {
      if (ids[i] == ids[j]) {
        dup = true;
        break;
      }
    }
    if (dup || ids[i].length() == 0) {
      continue;
    }
    apiWriteWord(client, "/ip/hotspot/active/remove");
    apiWriteWord(client, String("=.id=") + ids[i]);
    apiEndSentence(client);
    bool trap = false;
    drainUntilDone(client, trap);
    if (!trap) {
      removed++;
    }
  }

  client.stop();
  Serial.printf("MikroTik kick user=%s mac=%s removed=%d\n", voucher ? voucher : "",
                macFmt.c_str(), removed);
  setReason(reasonOut, removed ? "kicked" : "none");
  return true;
}

bool mikrotikHotspotLoginMac(const char *voucher, const char *mac, const char *ip,
                             String *reasonOut) {
  if (DEMO_MODE) {
    setReason(reasonOut, "demo_mode");
    return true;
  }
  String macFmt = normalizeMac(mac);
  if (!voucher || !voucher[0] || macFmt.length() == 0) {
    setReason(reasonOut, "missing mac/voucher");
    return false;
  }

  String passwords[2];
  passwords[0] = MT_USERNAME_ONLY ? String("") : String(voucher);
  passwords[1] = MT_USERNAME_ONLY ? String(voucher) : String("");

  WiFiClient client;
  if (!connectApi(client, reasonOut)) {
    return false;
  }
  if (!apiLogin(client, reasonOut)) {
    client.stop();
    return false;
  }

  bool trap = false;
  String trapMsg;
  bool ok = false;
  for (int i = 0; i < 2 && !ok; i++) {
    apiWriteWord(client, "/ip/hotspot/active/login");
    apiWriteWord(client, String("=user=") + voucher);
    apiWriteWord(client, String("=password=") + passwords[i]);
    apiWriteWord(client, String("=mac-address=") + macFmt);
    if (ip && ip[0]) {
      apiWriteWord(client, String("=ip=") + ip);
    }
    apiEndSentence(client);
    trap = false;
    trapMsg = "";
    ok = drainUntilDone(client, trap, &trapMsg);
  }
  client.stop();
  if (!ok) {
    String detail = "hotspot login failed";
    if (trapMsg.length()) {
      detail += ": ";
      detail += trapMsg;
    }
    setReason(reasonOut, detail.c_str());
    Serial.println(detail);
    return false;
  }
  setReason(reasonOut, "logged-in");
  Serial.printf("MikroTik MAC login %s user=%s\n", macFmt.c_str(), voucher);
  return true;
}

bool mikrotikAdoptVoucherForMac(const char *voucher, const char *mac, const char *ip,
                                int minSeconds, String *reasonOut) {
  if (!voucher || !voucher[0]) {
    setReason(reasonOut, "no voucher");
    return false;
  }
  if (DEMO_MODE) {
    setReason(reasonOut, "demo_mode");
    return true;
  }

  String activeUser;
  if (!mikrotikFindActiveUserByMac(mac, &activeUser) || !activeUser.length()) {
    mikrotikFindActiveUserByIp(ip, &activeUser);
  }
  if (!activeUser.length()) {
    setReason(reasonOut, "not online");
    return false;
  }
  if (activeUser == voucher) {
    setReason(reasonOut, "already this user");
    return true;
  }

  int leftover = mikrotikRemainingSeconds(activeUser.c_str());
  int grant = leftover;
  if (grant < minSeconds) {
    grant = minSeconds;
  }

  Serial.printf("extend-adopt: %s leftover=%ds -> new voucher %s grant=%ds\n",
                activeUser.c_str(), leftover, voucher, grant);

  String createReason;
  int total = 0;
  if (!mikrotikCreateOrExtendUser(voucher, grant, &createReason, &total)) {
    setReason(reasonOut, createReason.c_str());
    return false;
  }

  String kickReason;
  mikrotikKickHotspot(activeUser.c_str(), mac, &kickReason);
  String loginReason;
  if (!mikrotikHotspotLoginMac(voucher, mac, ip, &loginReason)) {
    setReason(reasonOut, loginReason.c_str());
    Serial.printf("extend-adopt login failed: %s\n", loginReason.c_str());
    return false;
  }
  setReason(reasonOut, "adopted");
  return true;
}

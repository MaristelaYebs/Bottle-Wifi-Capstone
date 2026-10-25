#include "web_api.h"
#include "config.h"
#include "session.h"
#include "camera_detect.h"
#include "mikrotik_api.h"
#include "servo_bridge.h"

#include <ArduinoJson.h>
#include <WiFi.h>

WebServer server(HTTP_PORT);

static bool g_detectEnabled = false;
static bool g_waitTimedOutPending = false;
static String g_lastDetectLabel = "idle";
static int g_lastMotion = 0;
static int g_lastArea = 0;
static float g_lastAspect = 0;
static int g_lastStreak = 0;

static void sendJson(int code, const String &body) {
  server.sendHeader("Access-Control-Allow-Origin", "*");
  server.sendHeader("Access-Control-Allow-Methods", "GET,POST,OPTIONS");
  server.sendHeader("Access-Control-Allow-Headers", "*");
  server.send(code, "application/json", body);
}

static void handleOptions() {
  sendJson(204, "");
}

static String jsonOk(JsonDocument &doc) {
  doc["status"] = "true";
  String out;
  serializeJson(doc, out);
  return out;
}

static String jsonFail(const char *errorCode) {
  JsonDocument doc;
  doc["status"] = "false";
  doc["errorCode"] = errorCode;
  String out;
  serializeJson(doc, out);
  return out;
}

static void handleRoot() {
  JsonDocument doc;
  doc["service"] = "esp32cam-bottle-vendo";
  doc["compatible"] = "juanfi";
  doc["minutes_per_bottle"] = MINUTES_PER_BOTTLE;
  doc["busy"] = sessionIsBusy();
  doc["ip"] = WiFi.localIP().toString();
  doc["detect"] = g_lastDetectLabel;
  sendJson(200, jsonOk(doc));
}

static void handleHealth() {
  bool busy = sessionIsBusy();
  TopUpSession *s = sessionActive();
  JsonDocument doc;
  doc["ok"] = true;
  doc["busy"] = busy;
  doc["minutes_per_bottle"] = MINUTES_PER_BOTTLE;
  doc["demo_mode"] = DEMO_MODE;
  doc["detect_enabled"] = g_detectEnabled || DETECT_TEST_MODE;
  doc["detect_test_mode"] = DETECT_TEST_MODE;
  doc["tflite"] = USE_TFLITE;
  doc["detect"] = g_lastDetectLabel;
  doc["motion"] = g_lastMotion;
  doc["area"] = g_lastArea;
  doc["aspect"] = g_lastAspect;
  doc["streak"] = g_lastStreak;
  doc["servo_bridge"] = SERVO_BRIDGE_ENABLE;
  doc["uno_trigger"] = true;
  doc["ip"] = WiFi.localIP().toString();
  doc["bottles"] = s ? s->bottleCount : 0;
  doc["voucher"] = (s && s->voucher[0]) ? s->voucher : "";
  sendJson(200, jsonOk(doc));
}

static String argCI(const char *name) {
  for (int i = 0; i < server.args(); i++) {
    if (server.argName(i).equalsIgnoreCase(name)) {
      return server.arg(i);
    }
  }
  return String("");
}

static void handleTopUp() {
  if (server.method() == HTTP_OPTIONS) {
    handleOptions();
    return;
  }
  String voucher = argCI("voucher");
  if (!voucher.length()) {
    voucher = argCI("username");
  }
  if (!voucher.length()) {
    voucher = argCI("user");
  }
  if (voucher == "undefined" || voucher == "null") {
    voucher = "";
  }
  String mac = argCI("mac");
  String ip = argCI("ip");
  if (!ip.length()) {
    ip = argCI("ipaddress");
  }
  if (!ip.length()) {
    ip = argCI("ipAddress");
  }
  if (!ip.length()) {
    ip = server.client().remoteIP().toString();
  }
  String extendRaw = argCI("extendTime");
  bool extendTime = (extendRaw == "1" || extendRaw.equalsIgnoreCase("true"));
  String topupType = argCI("topupType");
  if (!topupType.length()) {
    topupType = "INTERNET";
  }
  if (topupType.equalsIgnoreCase("CHARGER")) {
    sendJson(200, jsonFail("coin.slot.notavailable"));
    return;
  }

  sessionReapStale();
  if (sessionIsBusy()) {
    TopUpSession *s = sessionActive();
    const bool sameMac = s && sessionMacMatches(s, mac.c_str());
    const bool sameVoucher =
        s && voucher.length() > 0 && strcmp(s->voucher, voucher.c_str()) == 0;
    if (s && (sameMac || sameVoucher)) {
      // Same phone tapped INSERT again — do not report "Bottle Chute is busy"
      JsonDocument doc;
      doc["voucher"] = s->voucher;
      sendJson(200, jsonOk(doc));
      Serial.printf("topUp reuse voucher=%s\n", s->voucher);
      return;
    }
    sendJson(200, jsonFail("coinslot.busy"));
    return;
  }

  // Extend must keep the username they are already online as. Never mint a
  // new voucher here — that plus a later kick is "Invalid voucher".
  if (extendTime) {
    String activeUser;
    if (mikrotikFindActiveUserByMac(mac.c_str(), &activeUser) && activeUser.length()) {
      voucher = activeUser;
    } else if (mikrotikFindActiveUserByIp(ip.c_str(), &activeUser) &&
               activeUser.length()) {
      voucher = activeUser;
    }
    if (!voucher.length()) {
      sendJson(200, jsonFail("extend.not.online"));
      Serial.println("topUp extend: no live hotspot user for this MAC/IP");
      return;
    }
  }

  sessionBegin(voucher.c_str(), mac.c_str(), extendTime, ip.c_str());
  detectorReset();
  g_detectEnabled = true;
  g_waitTimedOutPending = false;
  servoIdle();

  TopUpSession *s = sessionActive();
  JsonDocument doc;
  doc["voucher"] = s ? s->voucher : "";
  sendJson(200, jsonOk(doc));
  Serial.printf("topUp voucher=%s mac=%s ip=%s extend=%d\n", s ? s->voucher : "?",
                mac.c_str(), ip.c_str(), extendTime ? 1 : 0);
}

static void handleCheckCoin() {
  if (server.method() == HTTP_OPTIONS) {
    handleOptions();
    return;
  }
  String voucher = argCI("voucher");
  TopUpSession *s = sessionGetOrActive(voucher.c_str());
  if (!s) {
    sendJson(200, jsonFail("coin.slot.notavailable"));
    return;
  }
  if (s->cancelled) {
    sendJson(200, jsonFail("coinslot.busy"));
    return;
  }

  if (s->bottleCount > 0 && s->lastNewBottles > 0) {
    int neu = s->lastNewBottles;
    s->lastNewBottles = 0;
    JsonDocument doc;
    doc["totalCoin"] = String(s->bottleCount);
    doc["newCoin"] = String(neu);
    doc["timeAdded"] = String(s->timeAddedSeconds);
    doc["validity"] = String(s->minutesPerBottle * s->bottleCount);
    doc["data"] = "";
    sendJson(200, jsonOk(doc));
    return;
  }

  uint32_t remain = sessionRemainMs(s);
  // Bottles already credited: keep the portal wait alive so it does not
  // auto-cancel before the user taps Done (that made vouchers "not work").
  if (s->bottleCount > 0 && remain == 0) {
    remain = 30000;
  }

  JsonDocument doc;
  doc["status"] = "false";
  doc["errorCode"] = "coin.not.inserted";
  doc["totalCoin"] = String(s->bottleCount);
  doc["timeAdded"] = String(s->timeAddedSeconds);
  doc["remainTime"] = String(remain);
  doc["waitTime"] = String(s->waitTimeMs);
  doc["validity"] = String(s->minutesPerBottle * max(1, s->bottleCount ? s->bottleCount : 1));
  doc["data"] = "";
  String out;
  serializeJson(doc, out);
  sendJson(200, out);

  if (sessionRemainMs(s) == 0 && s->bottleCount == 0) {
    if (g_detectEnabled) {
      g_waitTimedOutPending = true; // reject in main loop
    }
    g_detectEnabled = false;
  }
}

static void handleCancel() {
  if (server.method() == HTTP_OPTIONS) {
    handleOptions();
    return;
  }
  String voucher = argCI("voucher");
  g_detectEnabled = false;
  g_waitTimedOutPending = false;
  sessionCancel(voucher.c_str());
  servoCancelPending();
  servoReject();
  JsonDocument doc;
  sendJson(200, jsonOk(doc));
}

static void handleUseVoucher() {
  if (server.method() == HTTP_OPTIONS) {
    handleOptions();
    return;
  }
  String voucher = argCI("voucher");
  TopUpSession *s = sessionGetOrActive(voucher.c_str());
  if (!s) {
    sendJson(200, jsonFail("coin.slot.notavailable"));
    return;
  }
  if (s->bottleCount <= 0) {
    g_detectEnabled = false;
    sessionCancel(s->voucher);
    servoReject();
    sendJson(200, jsonFail("coin.not.inserted"));
    return;
  }
  g_detectEnabled = false;
  g_waitTimedOutPending = false;
  servoCancelPending();
  int bottles = s->bottleCount;
  int minutes = s->minutesPerBottle;
  int timeAdded = s->timeAddedSeconds;
  bool extendTime = s->extendTime;

  // Extend must keep the username they are already online as (e.g. VXB6TBAQ).
  // Switching to a new code + kick is what dropped them to /login Invalid voucher.
  if (extendTime) {
    String activeUser;
    if (mikrotikFindActiveUserByMac(s->mac, &activeUser) && activeUser.length()) {
      strncpy(s->voucher, activeUser.c_str(), sizeof(s->voucher) - 1);
      s->voucher[sizeof(s->voucher) - 1] = 0;
    } else if (mikrotikFindActiveUserByIp(s->ip, &activeUser) && activeUser.length()) {
      strncpy(s->voucher, activeUser.c_str(), sizeof(s->voucher) - 1);
      s->voucher[sizeof(s->voucher) - 1] = 0;
    } else if (voucher.length() > 0) {
      strncpy(s->voucher, voucher.c_str(), sizeof(s->voucher) - 1);
      s->voucher[sizeof(s->voucher) - 1] = 0;
    }
  } else if (voucher.length() > 0) {
    strncpy(s->voucher, voucher.c_str(), sizeof(s->voucher) - 1);
    s->voucher[sizeof(s->voucher) - 1] = 0;
  }
  String mtReason;
  int totalLimitSec = timeAdded;
  bool ok = mikrotikCreateOrExtendUser(s->voucher, timeAdded, &mtReason, &totalLimitSec,
                                      true, !extendTime);
  if (!ok) {
    Serial.printf("useVoucher: MikroTik failed — %s\n", mtReason.c_str());
    JsonDocument doc;
    doc["status"] = "false";
    doc["errorCode"] = "mikrotik.failed";
    doc["detail"] = mtReason;
    String out;
    serializeJson(doc, out);
    sendJson(200, out);
    return;
  }
  bool loggedIn = false;
  String kickReason;
  mikrotikKickHotspot(s->voucher, s->mac, &kickReason);
  for (int attempt = 0; attempt < 3 && !loggedIn; attempt++) {
    delay(300);
    String loginReason;
    loggedIn = mikrotikHotspotLoginMac(s->voucher, s->mac, s->ip, &loginReason);
    if (!loggedIn) {
      Serial.printf("useVoucher: MAC login try %d — %s\n", attempt + 1,
                    loginReason.c_str());
    }
  }
  if (extendTime) {
    Serial.printf("useVoucher: extend %s added %ds loggedIn=%d\n", s->voucher, timeAdded,
                  loggedIn ? 1 : 0);
  }
  String code = s->voucher;
  sessionFinalize(code.c_str());
  // Portal tempValidity is minutes; for extend this is remaining+new total
  int validityMinutes = (totalLimitSec + 59) / 60;
  if (validityMinutes < minutes * bottles) {
    validityMinutes = minutes * bottles;
  }
  JsonDocument doc;
  doc["validity"] = String(validityMinutes);
  doc["timeAdded"] = String(timeAdded);
  doc["totalSeconds"] = String(totalLimitSec);
  doc["extend"] = extendTime ? "true" : "false";
  doc["voucher"] = code;
  doc["loggedIn"] = loggedIn;
  sendJson(200, jsonOk(doc));
}

static void handleMikrotikTest() {
  String reason;
  bool ok = mikrotikTestConnection(&reason);
  JsonDocument doc;
  doc["status"] = ok ? "true" : "false";
  doc["ok"] = ok;
  doc["host"] = MT_HOST;
  doc["port"] = MT_PORT;
  doc["user"] = MT_USER;
  doc["demo_mode"] = DEMO_MODE;
  doc["detail"] = reason;
  String out;
  serializeJson(doc, out);
  sendJson(200, out);
}

static void handleRates() {
  if (server.method() == HTTP_OPTIONS) {
    handleOptions();
    return;
  }
  char line[80];
  snprintf(line, sizeof(line), "1 bottle (250ml-1L)#0#0#%d#", MINUTES_PER_BOTTLE);
  server.sendHeader("Access-Control-Allow-Origin", "*");
  server.send(200, "text/plain", line);
}

static void handleReleaseChute() {
  g_detectEnabled = false;
  g_waitTimedOutPending = false;
  sessionForceClear();
  servoCancelPending();
  servoIdle();
  JsonDocument doc;
  doc["released"] = true;
  sendJson(200, jsonOk(doc));
  Serial.println("Chute released (busy cleared)");
}

static void handleSimulate() {
  if (!sessionIsBusy()) {
    sendJson(200, jsonFail("coin.slot.notavailable"));
    return;
  }
  bool ok = sessionCreditBottle();
  TopUpSession *s = sessionActive();
  if (ok) {
    if (s && s->bottleCount < s->maxBottles) {
      servoAcceptKeepOpen();
    } else {
      servoAccept();
    }
  }
  JsonDocument doc;
  doc["credited"] = ok ? "true" : "false";
  doc["totalCoin"] = s ? s->bottleCount : 0;
  sendJson(200, jsonOk(doc));
}

static void handleSnapshot() {
  uint8_t *jpg = nullptr;
  size_t len = 0;
  if (!cameraSnapshotJpeg(&jpg, &len) || !jpg) {
    server.send(500, "text/plain", "snapshot failed");
    return;
  }
  server.sendHeader("Access-Control-Allow-Origin", "*");
  server.sendHeader("Cache-Control", "no-store");
  server.send_P(200, "image/jpeg", (const char *)jpg, len);
  free(jpg);
}

static void handlePreviewHtml() {
  String html = F(
      "<!doctype html><html><head><meta name=viewport content='width=device-width,initial-scale=1'>"
      "<title>ESP32-CAM Bottle Vendo</title>"
      "<style>body{font-family:sans-serif;background:#111;color:#eee;text-align:center}"
      "img{max-width:100%;border:2px solid #f80;background:#222}</style></head><body>"
      "<h2>ESP32-CAM Bottle Preview</h2>"
      "<p>Flying Fish triggers capture — PET bottle in the tube is accepted</p>"
      "<p style='opacity:.8'>To teach real PET bottles, on your PC run:<br>"
      "<code>python tools/label_live.py --ip 10.10.10.252</code></p>"
      "<p><a href='/releaseChute' style='color:#f80'>Release busy chute</a> · "
      "<a href='/mikrotikTest' style='color:#8af'>MikroTik API test</a></p>"
      "<img id=i src='/snapshot'><br><pre id=s></pre>"
      "<script>"
      "setInterval(()=>{i.src='/snapshot?'+Date.now()},800);"
      "setInterval(async()=>{const r=await fetch('/health');const j=await r.json();"
      "s.textContent='detect='+j.detect+' motion='+j.motion+' area='+j.area+"
      "' ar='+(j.aspect||0).toFixed?.(2)+' streak='+j.streak+'\\n'+JSON.stringify(j,null,2)},400);"
      "</script></body></html>");
  server.send(200, "text/html", html);
}

void webServerBegin() {
  server.on("/", HTTP_GET, handleRoot);
  server.on("/health", HTTP_GET, handleHealth);
  server.on("/topUp", HTTP_POST, handleTopUp);
  server.on("/topUp", HTTP_OPTIONS, handleOptions);
  server.on("/checkCoin", HTTP_POST, handleCheckCoin);
  server.on("/checkCoin", HTTP_OPTIONS, handleOptions);
  server.on("/cancelTopUp", HTTP_POST, handleCancel);
  server.on("/cancelTopUp", HTTP_OPTIONS, handleOptions);
  server.on("/useVoucher", HTTP_POST, handleUseVoucher);
  server.on("/useVoucher", HTTP_OPTIONS, handleOptions);
  server.on("/getRates", HTTP_GET, handleRates);
  server.on("/getRates", HTTP_OPTIONS, handleOptions);
  server.on("/mikrotikTest", HTTP_GET, handleMikrotikTest);
  server.on("/releaseChute", HTTP_GET, handleReleaseChute);
  server.on("/releaseChute", HTTP_POST, handleReleaseChute);
  server.on("/simulateBottle", HTTP_GET, handleSimulate);
  server.on("/simulateBottle", HTTP_POST, handleSimulate);
  server.on("/snapshot", HTTP_GET, handleSnapshot);
  server.on("/preview", HTTP_GET, handlePreviewHtml);
  server.begin();
  Serial.printf("HTTP server on :%u\n", HTTP_PORT);
}

void webServerLoop() {
  sessionReapStale();
  server.handleClient();
}

bool webDetectEnabled() { return g_detectEnabled; }
void webSetDetectLabel(const char *label) { g_lastDetectLabel = label; }
void webSetDetectStats(int motion, int area, float aspect, int streak) {
  g_lastMotion = motion;
  g_lastArea = area;
  g_lastAspect = aspect;
  g_lastStreak = streak;
}

bool webConsumeWaitTimedOut() {
  if (!g_waitTimedOutPending) {
    return false;
  }
  g_waitTimedOutPending = false;
  return true;
}

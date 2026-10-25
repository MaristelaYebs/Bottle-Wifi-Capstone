#pragma once

#include <WebServer.h>

void webServerBegin();
void webServerLoop();
bool webDetectEnabled();
void webSetDetectLabel(const char *label);
void webSetDetectStats(int motion, int area, float aspect, int streak);
/** True once when wait window expires with detect previously enabled (no bottles). */
bool webConsumeWaitTimedOut();
extern WebServer server;

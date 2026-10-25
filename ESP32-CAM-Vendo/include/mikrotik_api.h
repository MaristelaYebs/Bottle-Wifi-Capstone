#pragma once

#include <Arduino.h>

/**
 * Create hotspot user with limit-uptime = addUptimeSeconds.
 * If the user already exists, stacks new credit onto the previous limit.
 *
 * keepSessionOnline: the phone stays logged in (extend). New limit =
 * existing limit-uptime + add (do not subtract used time — session uptime
 * keeps counting). allowCreate=false refuses to mint a new username.
 */
bool mikrotikCreateOrExtendUser(const char *voucher, int addUptimeSeconds,
                                String *reasonOut = nullptr,
                                int *totalLimitSecOut = nullptr,
                                bool keepSessionOnline = false,
                                bool allowCreate = true);

/** Probe API reachability + login (no user create). Fills reasonOut always. */
bool mikrotikTestConnection(String *reasonOut);

/** Remaining limit-uptime seconds for a hotspot user (0 if missing). */
int mikrotikRemainingSeconds(const char *voucher);

/** Look up the hotspot username currently online for this MAC (extend). */
bool mikrotikFindActiveUserByMac(const char *mac, String *userOut);
bool mikrotikFindActiveUserByIp(const char *ip, String *userOut);

/** Drop /ip/hotspot/active rows for this user and/or MAC (shared-users=1). */
bool mikrotikKickHotspot(const char *voucher, const char *mac, String *reasonOut = nullptr);

/** If this MAC/IP is already online, copy leftover time onto voucher and log them in as it. */
bool mikrotikAdoptVoucherForMac(const char *voucher, const char *mac, const char *ip,
                                int minSeconds, String *reasonOut = nullptr);

/** Log the phone onto hotspot by MAC after the voucher user exists. */
bool mikrotikHotspotLoginMac(const char *voucher, const char *mac, const char *ip,
                             String *reasonOut = nullptr);

"""Portal API Service

JuanFi-compatible HTTP API the existing captive portal calls
(topUp / checkCoin / useVoucher / cancelTopUp / getRates / health / snapshot /
mikrotikTest). Lets the current portal keep working with the Orange Pi standing in
for the ESP32-CAM at the same IP.

Handlers return plain dicts and are unit-tested directly; a tiny WSGI adapter +
waitress serve() run them on :80 on the Pi.

Implemented in Phase 16. Contract: docs/architecture/02-low-level-architecture.md §6
"""

from __future__ import annotations

import json
import logging
from urllib.parse import parse_qs

log = logging.getLogger("portal")


def _ok(**kw) -> dict:
    d = {"status": "true"}
    d.update(kw)
    return d


def _fail(code: str, **kw) -> dict:
    d = {"status": "false", "errorCode": code}
    d.update(kw)
    return d


class PortalApi:
    def __init__(self, cfg, session_mgr, reward_svc, mikrotik, camera=None,
                 health_provider=None, preview=None) -> None:
        self.cfg = cfg
        self.sessions = session_mgr
        self.reward = reward_svc
        self.mikrotik = mikrotik
        self.camera = camera
        self.preview = preview
        self.health_provider = health_provider
        self.minutes = int(cfg.get("reward.minutes_per_bottle", 15))
        self.ip_hint = str(cfg.get("portal_api.static_ip_hint", ""))

    # ----------------- endpoint handlers -----------------
    def root(self) -> dict:
        return _ok(service="orangepi-bottle-vendo", compatible="juanfi",
                   minutes_per_bottle=self.minutes, busy=bool(self.sessions.active()))

    def health(self) -> dict:
        s = self.sessions.active()
        d = _ok(busy=bool(s), minutes_per_bottle=self.minutes, ip=self.ip_hint,
                bottles=(s["BottleCount"] if s else 0),
                voucher=(s["Voucher"] if s else ""))
        if self.health_provider:
            try:
                hr = self.health_provider()
                d.update(ready=hr.is_ready(), camera=hr.camera, serial=hr.serial,
                         model=hr.model, database=hr.database, mikrotik=hr.mikrotik)
            except Exception:  # noqa: BLE001
                pass
        return d

    def top_up(self, voucher, mac, ip, extend_time, topup_type="INTERNET") -> dict:
        if str(topup_type).upper() == "CHARGER":
            return _fail("coin.slot.notavailable")
        self.sessions.reap_stale()
        current = self.sessions.active()
        if current:
            same = self.sessions.mac_matches(current, mac) or \
                (voucher and current["Voucher"] == voucher)
            if same:
                return _ok(voucher=current["Voucher"])
            return _fail("coinslot.busy")

        if extend_time:
            resolved = self.mikrotik.find_active_user_by_mac(mac) or \
                self.mikrotik.find_active_user_by_ip(ip)
            if not resolved:
                return _fail("extend.not.online")
            voucher = resolved
            # Reopening this user's session for a new extend cycle: clear the
            # previous grant so the new Done adds (remaining + new), not the old
            # already-granted total. reward.clear_grant handles a missing repo.
            prior = self.sessions.get_by_voucher(voucher)
            if prior:
                self.reward.clear_grant_for_session(prior["Id"])

        session, err = self.sessions.begin(voucher or "", mac, ip, bool(extend_time))
        if err:
            return _fail(err)
        return _ok(voucher=session["Voucher"])

    def check_coin(self, voucher) -> dict:
        s = self.sessions.get_or_active(voucher)
        if not s:
            return _fail("coin.slot.notavailable")
        if s["State"] == "CANCELLED":
            return _fail("coinslot.busy")
        count = s["BottleCount"]
        max_bottles = s["MaxBottles"]
        full = count >= max_bottles
        # maxReached/maxBottles let the portal show "you've hit the limit; extra
        # bottles are auto-rejected" and enable Done. Sent on every branch.
        cap = {"maxBottles": str(max_bottles),
               "maxReached": "true" if full else "false"}
        new = self.sessions.new_bottles_since_poll(s["Voucher"], count)
        validity = self.minutes * max(1, count)
        if count > 0 and new > 0:
            return _ok(totalCoin=str(count), newCoin=str(new),
                       timeAdded=str(s["TimeAddedSeconds"]), validity=str(validity),
                       data="", **cap)
        remain = self.sessions.remaining_wait_ms(s)
        if count > 0 and remain == 0:
            remain = 30000  # keep the portal waiting until the user taps Done
        return _fail("coin.not.inserted", totalCoin=str(count),
                     timeAdded=str(s["TimeAddedSeconds"]), remainTime=str(remain),
                     waitTime=str(s["WaitTimeMs"]), validity=str(validity), data="", **cap)

    def use_voucher(self, voucher) -> dict:
        s = self.sessions.get_or_active(voucher)
        if not s:
            return _fail("coin.slot.notavailable")
        if s["BottleCount"] <= 0:
            self.sessions.cancel(s["Voucher"])
            return _fail("coin.not.inserted")
        res = self.reward.grant_for_session(s)
        if res.get("ok"):
            return _ok(validity=str(res["validity_minutes"]), timeAdded=str(res["time_added"]),
                       totalSeconds=str(res["total_seconds"]), voucher=s["Voucher"],
                       loggedIn=res["logged_in"], extend="true" if s["ExtendTime"] else "false")
        # MikroTik unavailable: reward is stored PENDING and will be retried, but we
        # cannot grant internet right now — report failure honestly.
        return _fail("mikrotik.failed", detail=res.get("detail", ""), voucher=s["Voucher"])

    def cancel_top_up(self, voucher) -> dict:
        if voucher:
            self.sessions.cancel(voucher)
        return _ok()

    def get_rates(self) -> str:
        return f"1 bottle (250ml-1L)#0#0#{self.minutes}#"

    def mikrotik_test(self) -> dict:
        ok, detail = self.mikrotik.test_connection()
        return {"status": "true" if ok else "false", "ok": ok, "detail": detail}

    def snapshot(self, annotate: bool = False):
        """Raw JPEG, or — with annotate — boxes + confidences + a verdict HUD."""
        if annotate and self.preview:
            jpg, info = self.preview.annotated_jpeg()
            return jpg, info
        return (self.camera.snapshot_jpeg() if self.camera else None), {}

    def detect(self) -> dict:
        """Dry-run the real burst + decision engine. Actuates nothing."""
        if not self.preview:
            return _fail("preview.unavailable")
        return self.preview.dry_run()

    # ----------------- commissioning preview page -----------------
    def preview_page(self) -> str:
        """Self-contained camera-aiming page. No CDN: the Pi has no internet on
        the hotspot LAN and a captive-portal client certainly doesn't."""
        gate = self.cfg.get("detection.confidence", 0.70)
        need = self.cfg.get("detection.required_positive_frames", 3)
        total = self.cfg.get("detection.total_frames", 5)
        return """<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Camera Preview</title><style>
*{box-sizing:border-box}
body{margin:0;background:#11141a;color:#e6e9ef;font:14px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
.wrap{max-width:960px;margin:0 auto;padding:16px}
h1{font-size:17px;margin:0 0 4px}
.sub{color:#8b93a3;font-size:12px;margin-bottom:14px}
.shot{width:100%;border-radius:8px;background:#000;display:block;aspect-ratio:16/9;object-fit:contain}
.bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:12px 0}
button{background:#2a3242;color:#e6e9ef;border:1px solid #3a465c;border-radius:6px;
 padding:9px 14px;font-size:14px;cursor:pointer}
button:hover{background:#343e52}
button.go{background:#1f6f43;border-color:#2b8a55}
button.go:hover{background:#268050}
select{background:#2a3242;color:#e6e9ef;border:1px solid #3a465c;border-radius:6px;padding:9px}
.card{background:#1a1f29;border:1px solid #29313f;border-radius:8px;padding:14px;margin-top:12px}
.verdict{font-size:20px;font-weight:600;margin-bottom:8px}
.ACCEPT{color:#5ddb8b}.REJECT{color:#ff7b7b}.muted{color:#8b93a3}
table{width:100%;border-collapse:collapse;font-size:13px}
td{padding:4px 0;border-bottom:1px solid #242c38}
td:first-child{color:#8b93a3;width:42%}
ul{margin:8px 0 0;padding-left:20px}
code{background:#242c38;padding:1px 5px;border-radius:4px;font-size:12px}
.err{color:#ffb86b}
</style></head><body><div class="wrap">
<h1>Camera preview &mdash; commissioning</h1>
<div class="sub">Accept gate <code>__GATE__</code> &middot; needs <code>__NEED__</code> of
<code>__TOTAL__</code> frames. Nothing here moves the servo or credits a session.</div>

<img id="shot" class="shot" alt="camera">
<div id="err" class="err"></div>

<div class="bar">
  <button id="toggle" class="go">Pause</button>
  <select id="rate">
    <option value="1000">every 1s</option>
    <option value="2000" selected>every 2s</option>
    <option value="5000">every 5s</option>
  </select>
  <button id="test">Run full detection test</button>
</div>

<div class="card">
  <div id="verdict" class="verdict muted">Press <b>Run full detection test</b> to see what a
  bottle dropped right now would get.</div>
  <div id="detail"></div>
</div>
</div><script>
var img=document.getElementById('shot'), err=document.getElementById('err');
var live=true, timer=null;
function tick(){
  if(!live) return;
  var u='/snapshot?annotate=1&t='+Date.now();
  var probe=new Image();
  probe.onload=function(){ img.src=u; err.textContent=''; schedule(); };
  probe.onerror=function(){ err.textContent='snapshot unavailable (camera busy or not open)';
                            schedule(); };
  probe.src=u;
}
function schedule(){ clearTimeout(timer);
  timer=setTimeout(tick, parseInt(document.getElementById('rate').value,10)); }
document.getElementById('toggle').onclick=function(){
  live=!live; this.textContent=live?'Pause':'Resume';
  this.className=live?'go':''; if(live) tick(); else clearTimeout(timer);
};
document.getElementById('rate').onchange=function(){ if(live){clearTimeout(timer); tick();} };

document.getElementById('test').onclick=function(){
  var v=document.getElementById('verdict'), d=document.getElementById('detail');
  v.className='verdict muted'; v.textContent='capturing __TOTAL__ frames...'; d.innerHTML='';
  fetch('/detect').then(function(r){return r.json();}).then(function(j){
    if(!j.ok){ v.className='verdict err';
      v.textContent=(j.error||'failed')+': '+(j.detail||''); return; }
    var dec=j.decision;
    v.className='verdict '+dec.outcome;
    v.textContent=dec.outcome+' \\u2014 '+dec.logical+' ('+dec.prediction+')';
    var rows=[['confidence',dec.confidence+'  (gate '+j.gate.confidence+')'],
      ['positive frames',dec.positive_frames+' / '+dec.total_frames+
       '  (need '+j.gate.required_positive_frames+')'],
      ['capture',j.timing_ms.capture+' ms'],
      ['inference',j.timing_ms.infer_per_frame+' ms/frame ('+j.timing_ms.infer_total+' ms total)'],
      ['model',j.model],
      ['would save sample',dec.would_collect_sample?'yes':'no']];
    var h='<table>';
    rows.forEach(function(r){ h+='<tr><td>'+r[0]+'</td><td>'+r[1]+'</td></tr>'; });
    h+='</table>';
    if(dec.reasons&&dec.reasons.length){ h+='<ul>';
      dec.reasons.forEach(function(x){ h+='<li>'+x+'</li>'; }); h+='</ul>'; }
    d.innerHTML=h;
  }).catch(function(e){ v.className='verdict err'; v.textContent='request failed: '+e; });
};
tick();
</script></body></html>""".replace("__GATE__", str(gate)) \
            .replace("__NEED__", str(need)).replace("__TOTAL__", str(total))

    # ----------------- WSGI adapter -----------------
    @staticmethod
    def _params(environ) -> dict:
        params: dict[str, str] = {}
        qs = environ.get("QUERY_STRING", "")
        for k, v in parse_qs(qs).items():
            params[k.lower()] = v[-1]
        if environ.get("REQUEST_METHOD") == "POST":
            try:
                size = int(environ.get("CONTENT_LENGTH") or 0)
            except ValueError:
                size = 0
            if size:
                body = environ["wsgi.input"].read(size).decode("utf-8", "replace")
                for k, v in parse_qs(body).items():
                    params[k.lower()] = v[-1]
        return params

    @staticmethod
    def _arg(p, *names):
        for n in names:
            v = p.get(n.lower())
            if v not in (None, "", "undefined", "null"):
                return v
        return ""

    def wsgi_app(self, environ, start_response):
        path = environ.get("PATH_INFO", "/")
        method = environ.get("REQUEST_METHOD", "GET")
        cors = [("Access-Control-Allow-Origin", "*"),
                ("Access-Control-Allow-Methods", "GET,POST,OPTIONS"),
                ("Access-Control-Allow-Headers", "*")]
        if method == "OPTIONS":
            start_response("204 No Content", cors)
            return [b""]

        p = self._params(environ)

        def _json(d, code="200 OK"):
            start_response(code, cors + [("Content-Type", "application/json")])
            return [json.dumps(d).encode("utf-8")]

        try:
            if path == "/" and method == "GET":
                return _json(self.root())
            if path == "/health":
                return _json(self.health())
            if path == "/getRates":
                start_response("200 OK", cors + [("Content-Type", "text/plain")])
                return [self.get_rates().encode("utf-8")]
            if path == "/mikrotikTest":
                return _json(self.mikrotik_test())
            if path == "/snapshot":
                annotate = self._arg(p, "annotate") in ("1", "true", "True")
                jpg, info = self.snapshot(annotate)
                if not jpg:
                    reason = info.get("detail") or info.get("error") or "snapshot failed"
                    code = "409 Conflict" if info.get("error") == "busy" \
                        else "500 Internal Server Error"
                    start_response(code, cors + [("Content-Type", "text/plain")])
                    return [str(reason).encode("utf-8")]
                start_response("200 OK", cors + [("Content-Type", "image/jpeg"),
                                                 ("Cache-Control", "no-store")])
                return [jpg]
            if path == "/detect":
                return _json(self.detect())
            if path == "/preview":
                start_response("200 OK", cors + [("Content-Type", "text/html; charset=utf-8"),
                                                 ("Cache-Control", "no-store")])
                return [self.preview_page().encode("utf-8")]
            if path == "/topUp":
                return _json(self.top_up(
                    self._arg(p, "voucher", "username", "user"),
                    self._arg(p, "mac"),
                    self._arg(p, "ip", "ipaddress", "ipAddress") or environ.get("REMOTE_ADDR", ""),
                    self._arg(p, "extendTime") in ("1", "true", "True"),
                    self._arg(p, "topupType") or "INTERNET"))
            if path == "/checkCoin":
                return _json(self.check_coin(self._arg(p, "voucher")))
            if path == "/useVoucher":
                return _json(self.use_voucher(self._arg(p, "voucher")))
            if path == "/cancelTopUp":
                return _json(self.cancel_top_up(self._arg(p, "voucher")))
        except Exception as exc:  # noqa: BLE001
            log.exception("portal handler error")
            return _json(_fail("server.error", detail=str(exc)), "200 OK")

        start_response("404 Not Found", cors)
        return [b"not found"]

    def serve(self, host=None, port=None) -> None:
        from waitress import serve  # type: ignore

        host = host or str(self.cfg.get("portal_api.host", "0.0.0.0"))
        port = port or int(self.cfg.get("portal_api.port", 80))
        log.info("portal API on %s:%d", host, port)
        serve(self.wsgi_app, host=host, port=port, threads=4, _quiet=True)

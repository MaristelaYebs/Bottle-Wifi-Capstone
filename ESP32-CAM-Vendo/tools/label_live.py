#!/usr/bin/env python3
"""
Label chute photos while you test, then train on this PC (ESP32 cannot train).

  python tools/label_live.py --ip 10.10.10.252

Open http://127.0.0.1:8765
  B / click PET bottle  — hold a real accepted bottle in the camera view
  E / click Empty       — empty chute
  N / click Reject      — can, paper, hand, crushed junk
  Train when you have ~25+ PET and ~25+ empty (reject shots help a lot)

Then re-upload firmware so the new model is on the CAM.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "tools" / "dataset"
LABELS = ("bottle", "empty", "reject")

ESP_IP = "10.10.10.252"
train_lock = threading.Lock()
train_state = {"running": False, "log": "", "ok": None}


def dataset_dir(label: str) -> Path:
    d = DATA / label
    d.mkdir(parents=True, exist_ok=True)
    return d


def count_images(label: str) -> int:
    d = DATA / label
    if not d.exists():
        return 0
    return sum(1 for p in d.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png"})


def counts() -> dict[str, int]:
    return {k: count_images(k) for k in LABELS}


def fetch_snapshot() -> bytes:
    url = f"http://{ESP_IP}/snapshot?{int(time.time() * 1000)}"
    with urllib.request.urlopen(url, timeout=8) as r:
        return r.read()


def save_one(label: str) -> Path:
    if label not in LABELS:
        raise ValueError(label)
    blob = fetch_snapshot()
    path = dataset_dir(label) / f"{label}_{int(time.time() * 1000)}.jpg"
    path.write_bytes(blob)
    return path


def run_train() -> None:
    with train_lock:
        if train_state["running"]:
            return
        train_state.update(running=True, log="Training started…\n", ok=None)

    cmd = [
        sys.executable,
        str(ROOT / "tools" / "train_bottle_model.py"),
        "--data",
        str(DATA),
        "--no-synth",
    ]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=600,
        )
        log = (proc.stdout or "") + (proc.stderr or "")
        ok = proc.returncode == 0
        if ok:
            log += (
                "\nModel written. Flash the CAM so it uses the new weights:\n"
                "  pio run -t upload\n"
            )
    except Exception as e:  # noqa: BLE001 — show any trainer failure in the UI
        log = f"Train failed: {e}\n"
        ok = False

    with train_lock:
        train_state.update(running=False, log=log, ok=ok)


PAGE = r"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Teach PET bottle</title>
<style>
  body{font-family:sans-serif;background:#111;color:#eee;max-width:720px;margin:0 auto;padding:16px}
  img{width:100%;max-width:480px;border:2px solid #f80;background:#222;display:block;margin:8px auto}
  button{font-size:16px;padding:10px 14px;margin:4px;border:0;border-radius:8px;cursor:pointer}
  .pet{background:#1b8;color:#111;font-weight:700}
  .empty{background:#456;color:#fff}
  .rej{background:#c44;color:#fff}
  .train{background:#f80;color:#111;font-weight:700}
  pre{background:#1a1a1a;padding:12px;overflow:auto;white-space:pre-wrap}
  .ok{color:#8f8}.bad{color:#f88}
</style>
</head>
<body>
<h2>Teach PET bottle</h2>
<p>Hold a real accepted PET bottle in the chute camera, then click <b>PET bottle</b>.
Also save empty chute and junk you must reject. Training runs on this PC — then re-flash the ESP32.</p>
<img id="cam" alt="chute">
<p id="counts"></p>
<p>
  <button class="pet" onclick="save('bottle',1)">PET bottle (B)</button>
  <button class="pet" onclick="save('bottle',8)">Burst ×8</button>
  <button class="empty" onclick="save('empty',1)">Empty chute (E)</button>
  <button class="rej" onclick="save('reject',1)">Reject / not PET (N)</button>
</p>
<p><button class="train" onclick="train()">Train model now</button></p>
<pre id="log">Keys: B bottle · E empty · N reject</pre>
<script>
const cam=document.getElementById('cam');
const countsEl=document.getElementById('counts');
const logEl=document.getElementById('log');
async function refreshCam(){cam.src='/cam?'+Date.now()}
async function refreshCounts(){
  const j=await (await fetch('/counts')).json();
  countsEl.textContent='Saved  PET='+j.bottle+'  empty='+j.empty+'  reject='+j.reject
    +(j.bottle<25||j.empty<25?'  (need ~25 PET + ~25 empty)':'  (ready to train)');
}
async function save(label,n){
  logEl.textContent='Saving '+n+' × '+label+'…';
  for(let i=0;i<n;i++){
    const r=await fetch('/save?label='+label,{method:'POST'});
    const t=await r.text();
    if(!r.ok){logEl.textContent=t;return}
    await new Promise(x=>setTimeout(x,280));
  }
  await refreshCounts();
  logEl.textContent='Saved '+n+' '+label+' frame(s).';
}
async function train(){
  logEl.textContent='Training… this can take 1–3 minutes. Leave this tab open.';
  const r=await fetch('/train',{method:'POST'});
  logEl.textContent=await r.text();
  logEl.className=r.ok?'ok':'bad';
  await refreshCounts();
}
document.addEventListener('keydown',e=>{
  if(e.target.tagName==='INPUT')return;
  const k=e.key.toLowerCase();
  if(k==='b')save('bottle',1);
  if(k==='e')save('empty',1);
  if(k==='n')save('reject',1);
});
setInterval(refreshCam,700);
refreshCam();refreshCounts();
</script>
</body></html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, code: int, ctype: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send(200, "text/html; charset=utf-8", PAGE.encode())
            return
        if path == "/counts":
            self._send(200, "application/json", json.dumps(counts()).encode())
            return
        if path == "/cam":
            try:
                self._send(200, "image/jpeg", fetch_snapshot())
            except Exception as e:  # noqa: BLE001
                self._send(502, "text/plain; charset=utf-8", str(e).encode())
            return
        self._send(404, "text/plain", b"not found")

    def do_POST(self) -> None:  # noqa: N802
        path, _, query = self.path.partition("?")
        if path == "/save":
            label = "bottle"
            for part in query.split("&"):
                if part.startswith("label="):
                    label = part.split("=", 1)[1]
            try:
                p = save_one(label)
                self._send(200, "text/plain; charset=utf-8", str(p).encode())
            except Exception as e:  # noqa: BLE001
                self._send(500, "text/plain; charset=utf-8", str(e).encode())
            return
        if path == "/train":
            c = counts()
            if c["bottle"] < 15 or (c["empty"] + c["reject"]) < 15:
                msg = (
                    f"Need more photos first. PET={c['bottle']} empty={c['empty']} "
                    f"reject={c['reject']}. Save ~25 PET bottles and ~25 empty/reject."
                )
                self._send(400, "text/plain; charset=utf-8", msg.encode())
                return
            run_train()
            with train_lock:
                body = train_state["log"]
                ok = bool(train_state["ok"])
            self._send(200 if ok else 500, "text/plain; charset=utf-8", body.encode())
            return
        self._send(404, "text/plain", b"not found")


def main() -> None:
    global ESP_IP
    ap = argparse.ArgumentParser()
    ap.add_argument("--ip", default="10.10.10.252", help="ESP32-CAM IP")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    ESP_IP = args.ip
    for lab in LABELS:
        dataset_dir(lab)

    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}"
    print(f"ESP camera: http://{ESP_IP}/snapshot")
    print(f"Label here: {url}")
    print("Show a PET bottle in the chute, click PET bottle. Also save empty + reject.")
    print("After Train, run: pio run -t upload")
    threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()

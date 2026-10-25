# Tests

Two layers:

- **Automated** — pure/logic + integration with injected fakes. No hardware, no
  OpenCV/NCNN/pyserial/librouteros needed. Run them all:

  ```bash
  python tests/run_all.py          # summary of all suites
  # or individually:  python tests/test_reward_flow.py
  # or with pytest:   pytest tests/
  ```

- **Hardware / on-device** — need the real rig (Pi + Arduino + webcam + MikroTik).
  Run on the Pi; see the checklist below.

## Automated suites

| Suite | Phase | Covers |
|---|---|---|
| `test_serial_protocol.py` | 5 | protocol classify, ACK/retry/timeout, PING/PONG, events |
| `test_camera_service.py` | 6 | burst/warmup/release, open failure, partial reads |
| `test_decision_service.py` | 7 | accept/reject/empty/borderline/multi/fail-safe + detector select |
| `test_pipeline_integration.py` | 8/9/11/17 | full loop, RESET recovery, dataset capture, exception fail-safe |
| `test_database.py` | 10 | schema, txn IDs, UNIQUE backstop, events |
| `test_dataset_service.py` | 11 | selective capture, disk guard, sidecar metadata |
| `test_validation.py` | 13 | FAR/FRR math, threshold gate, model registry |
| `test_model_service.py` | 14 | gated deploy, refusals, auto-rollback |
| `test_mikrotik_service.py` | 15 | uptime/MAC helpers, unconfigured safety |
| `test_reward_flow.py` | 16 | sessions, 4 idempotency guarantees, PENDING→retry |
| `test_health_service.py` | 17 | readiness gating, degraded-not-fatal, safe probes |

## Spec §32 scenario coverage

| Scenario | Where |
|---|---|
| Valid plastic bottle → ACCEPT | `test_decision_service` (5/5, 3/5), `test_pipeline_integration` (bottle) |
| Invalid object (can/glass/cup/phone/hand/paper/bag) → REJECT | decision maps `reject_object`→REJECT: `test_decision_service`, `test_pipeline_integration`; real objects: **hardware** + model FAR gate (`test_validation`) |
| Crushed / partial / low+bright lighting bottle | dataset (`dataset/LABELING.md`) + model recall/FAR gate; **hardware** for real accept |
| Empty chute → keep watching, no servo | `test_decision_service` (empty), `test_pipeline_integration` (empty chute, below gate) |
| Multiple objects | `test_decision_service` (multi-object → collect) |
| Same bottle still in view after the flap opens | `test_pipeline_integration` (one bottle = one credit: cooldown + clear frames) |
| Watcher arming rules (no session / cancelled / bottle cap) | `test_pipeline_integration` (disarm, camera+lock released) |
| Camera disconnected | `test_camera_service` (open fail), `test_pipeline_integration` (dead camera), `test_health` |
| Arduino disconnected | `test_serial_protocol` (timeout/retry), `test_pipeline_integration` (RESET recovery), `test_health` |
| MikroTik disconnected / timeout | `test_reward_flow` (down→retry), `test_mikrotik_service`, `test_health` |
| Duplicate transaction / same bottle twice | `test_reward_flow` (no double credit/grant), `test_database` (UNIQUE txn id) |
| Application restart during transaction | PENDING grant + session persisted in SQLite: `test_reward_flow` (down→retry), `test_database`; full restart is **hardware** |
| Invalid model | `test_model_service` (refuse unvalidated/failed/high-FAR, auto-rollback) |
| Database unavailable | `test_health` (db_ok→not ready); guarded writes never raise |

## The four required assertions (spec §32)

1. **Valid bottle → ACCEPT** — `test_pipeline_integration.test_bottle_accepts`
2. **Invalid object → REJECT** — `test_pipeline_integration.test_reject_object_rejects`
3. **No detection → REJECT** — `test_pipeline_integration.test_empty_rejects` / `test_no_frames_rejects`
4. **Same transaction can't grant twice** — `test_reward_flow.test_reward_not_double_granted` / `test_credit_not_double_counted`

## Hardware / on-device checklist (run on the Pi)

- [ ] `python scripts/camera_check.py` — webcam opens MJPEG @ target res, burst timing OK
- [ ] `python scripts/hardware_check.py` — ACCEPT (green+short beep+smooth sweep),
      REJECT (red+long beep+sweep), BUSY guard, RESET recovery
- [ ] **Aim the camera** at `http://<pi>/preview` — the camera is the trigger now;
      `Run full detection test` must read a bottle held in the chute
- [ ] Watch timing: tap INSERT BOTTLE, drop a bottle, confirm the flap moves
      within ~1 s and that one bottle credits exactly once
- [ ] Deploy a real model (`scripts/deploy.py`) and run bottle variants: clear,
      coloured, crushed, dirty, rotated, partially visible, bright + low light
- [ ] Real reject objects: can, glass bottle, cup, phone, hand, paper, plastic bag
- [ ] `GET /mikrotikTest` → login OK; full portal flow: INSERT BOTTLE → drop → Done → internet
- [ ] Duplicate-reward: tap Done twice → time added once
- [ ] MikroTik unplugged at Done → PENDING → plug back in → retry grants once
- [ ] `sudo systemctl restart bottle-vendo` mid-idle and reboot → comes up READY
- [ ] Monitor RAM/CPU-temp under load (`/health`, `journalctl -u bottle-vendo`)

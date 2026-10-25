# Bottle Wi-Fi Vendo: Panel Question and Answer Guide

This guide is based on the implementation in this repository. The primary production path is the Orange Pi Zero 3 application. The ESP32-CAM and Arduino projects are related legacy or hardware components; they should not be described as the same runtime.

## 1. Project Overview

### 1. What is the system?
It is an AI-assisted bottle vending system. A customer connects to a MikroTik hotspot, opens the captive portal, inserts a plastic bottle, and receives Wi-Fi access time for an accepted bottle. The system detects the object with a camera, decides whether it is acceptable, controls the chute flap, records the transaction, and grants time through the hotspot.

### 2. What problem does it solve?
It automates the exchange of recyclable plastic bottles for internet access. It reduces manual checking, gives the customer immediate feedback through the chute, and records both accepted and rejected transactions for auditing and future model improvement.

### 3. What is the main contribution of the project?
The main contribution is the integration of computer vision, embedded actuation, captive-portal workflow, persistent transaction records, and MikroTik time granting into one fault-tolerant vending process.

### 4. Who are the users?
The primary user is a customer who wants Wi-Fi access. Operators or developers use the health endpoint, logs, model deployment scripts, database records, and hardware checks to maintain the machine.

### 5. What are the system inputs and outputs?
Inputs are camera frames, the customer session from the portal, and the physical bottle or reject object. Outputs are an ACCEPT or REJECT servo action, a portal credit, a MikroTik hotspot grant, transaction logs, health status, and optional dataset samples.

### 6. What is the scope of the system?
The Orange Pi is the AI and orchestration controller. The Arduino is the servo controller. MikroTik remains the hotspot and network authority. The captive portal remains the customer-facing web interface. The system does not replace the router's DHCP, NAT, firewall, or hotspot configuration.

### 7. What happens when the system starts?
The application loads non-secret YAML configuration and MikroTik secrets from `.env`, opens or creates the SQLite database, connects to the Arduino, starts serial reception, pings the Arduino, loads and warms up the detector, tests MikroTik connectivity, starts the camera watcher, starts the HTTP API, starts health monitoring, and starts the pending-reward retry loop.

### 8. What happens if a component is unavailable at startup?
The application logs the failure and keeps running where possible. Detection cannot become ready without the camera, serial link, model, and database. MikroTik is treated as a degraded dependency for readiness, because accepted rewards can be stored as pending and retried later.

## 2. Architecture and Data Flow

### 9. What are the main hardware components?
The main components are an Orange Pi Zero 3, a USB camera, an Arduino Uno with servo hardware, a MikroTik hAP lite router, and the customer's Wi-Fi device. The captive portal files are served through the hotspot workflow.

### 10. What is the role of the Orange Pi?
It is the central decision-maker. It captures frames, runs YOLO/NCNN inference, applies the decision rules, stores records in SQLite, commands the Arduino, exposes the JuanFi-compatible API, and communicates with MikroTik through RouterOS API.

### 11. What is the role of the Arduino?
The Arduino performs the real-time servo sequence. It receives ACCEPT, REJECT, RESET, and PING commands over USB serial, drives the servo mechanism, returns acknowledgements, and reports SERVO_COMPLETE.

### 12. What is the role of MikroTik?
MikroTik provides the Wi-Fi hotspot and captive portal network. The application only manages hotspot users and active sessions through the RouterOS API. It does not change DHCP, NAT, firewall, or hotspot configuration during normal operation.

### 13. What is the role of the captive portal?
The portal provides the customer interface for starting a bottle session, polling for bottle credits, completing the transaction, logging in, extending time, and showing errors. The Orange Pi preserves the existing JuanFi-style API contract so the portal can continue to work.

### 14. What is the normal end-to-end data flow?
The customer requests `topUp`, which opens one portal session. The watcher arms the camera. The detector analyzes frames. The decision service produces ACCEPT or REJECT. The Pi records a detection transaction, commands the Arduino, waits for acknowledgement and servo completion, and credits the accepted bottle. The portal sees the credit through `checkCoin`. On Done, `useVoucher` grants the calculated time through MikroTik.

### 15. Why is the system event-driven?
Camera scanning, serial reception, health checks, HTTP requests, and reward retries have different timing requirements. Separating them into services and threads prevents a slow router call or serial wait from blocking the camera and portal paths.

### 16. What prevents two operations from controlling the camera at once?
`AppState` provides a single-flight detection lock. The watcher acquires it while scanning, and preview or dry-run detection must not use the camera concurrently. The camera is released when the watcher is disarmed.

## 3. Customer Session and Portal

### 17. How does a customer start a transaction?
The portal calls `topUp` with the customer's voucher or session information, MAC address, IP address, and whether the request is an extension. The session manager creates or reuses a session and returns the voucher used for polling.

### 18. Why is there only one active session?
There is one physical chute, so one open session prevents two phones from claiming the same bottle. A second unrelated customer receives `coinslot.busy`; the same phone or same voucher can safely repeat the request.

### 19. How is a customer identified?
The system uses a generated or supplied voucher together with the client's MAC and IP. MAC matching allows repeated requests from the same phone, while voucher matching supports portal retries.

### 20. How are stale sessions handled?
The session manager marks an OPEN session as EXPIRED when it has not been updated for the configured stale interval. The default is 180 seconds, which prevents a disconnected phone from blocking the chute indefinitely.

### 21. What is the default customer reward?
The configured default is 15 minutes per accepted bottle, with a maximum of 5 bottles per session. These are configuration values and can be changed without changing the core algorithm.

### 22. What happens when the customer reaches the maximum?
The portal reports `maxReached`. The watcher remains able to see an inserted object, but an additional otherwise-acceptable bottle is changed to REJECT so it does not pile up in the chute or receive additional credit.

### 23. What does `checkCoin` do?
It reports the total bottle count, newly detected bottles since the previous poll, time added, remaining wait time, and whether the maximum has been reached. It lets the existing portal update the customer without changing the portal's overall interaction model.

### 24. What does `useVoucher` do?
It verifies that at least one bottle was accepted, computes the reward, writes or reuses a reward record, sets the absolute MikroTik uptime limit, logs the device in when possible, and finalizes the session. If MikroTik is unavailable, it returns a failure to the portal while retaining the reward as PENDING for retry.

### 25. What happens if the customer taps Done twice?
The reward repository has one grant per session. If the grant is already SUCCESS, the existing target limit is returned instead of granting more time. This makes the operation idempotent.

### 26. What happens if the customer cancels?
The portal calls `cancelTopUp`, and the session becomes CANCELLED. The session is no longer an active session for new bottle crediting.

### 27. What does the system return if the user inserts a charger?
The API rejects `CHARGER` top-ups with `coin.slot.notavailable`, because this implementation is for bottle rewards rather than charging services.

## 4. Computer Vision and Decision Logic

### 28. What model is used?
The Orange Pi runtime uses a YOLO-style object detector exported to NCNN. The configured classes are `plastic_bottle` and `reject_object`. Training uses Ultralytics on a PC or GPU machine; the Pi performs inference only.

### 29. Why use NCNN on the Orange Pi?
NCNN is a lightweight inference runtime suitable for ARM edge devices. It avoids installing heavy training dependencies on the 1.5 GB Orange Pi and allows the trained model to run with a controlled number of CPU threads.

### 30. What are the model classes?
Class 0 is `plastic_bottle`, which is potentially acceptable. Class 1 is `reject_object`, which covers objects that must be rejected. An empty chute is not a class; it is represented by no valid detection or by background images during training.

### 31. How is the model trained?
Images are collected for valid bottles, reject objects, and empty/background scenes. Bottle and reject objects are labelled with YOLO bounding boxes. The PC training script trains a YOLO Nano model, the validation script evaluates it, and the export script converts it to NCNN for the Pi.

### 32. What data should the model contain?
It should include clear, coloured, crushed, dirty, labelled, rotated, partially visible, near and far bottles; cans, glass, cups, bags, phones, hands, paper, and other rejects; and empty-chute backgrounds under different lighting and shadows.

### 33. How does the runtime detect a bottle?
While a portal session is OPEN, the watcher keeps the camera open and runs inference frame by frame. Each frame is labelled BOTTLE, REJECT, or EMPTY based on its highest-confidence detection and the confidence gate.

### 34. What confidence threshold is used?
The configured decision confidence is 0.70. The detector's NMS pre-filter is 0.25, while the decision service only accepts a class as valid when its confidence reaches 0.70.

### 35. Why use multiple frames instead of one frame?
A single frame can be blurred, occluded, or affected by lighting. The system uses a sliding window of 5 frames and requires 3 agreeing bottle frames by default. This reduces decisions caused by a transient false detection.

### 36. When is a bottle accepted?
A bottle is accepted when at least 3 of the 5 window frames have a `plastic_bottle` detection at or above the confidence threshold. The reported confidence is the average confidence of the positive bottle frames.

### 37. When is an object rejected?
A `reject_object` with sufficient confidence is rejected. A bottle with insufficient confidence, an empty or uncertain result in a dry-run decision, a detector error, or any unsafe condition is also fail-safe REJECT when a physical decision is made.

### 38. Is an empty chute automatically rejected?
Not during continuous watching. Empty is the idle state, so the watcher keeps looking and does not move the servo. This is different from a completed detection burst, where an unresolved decision is represented as REJECT for safety.

### 39. How does the system prevent one bottle from being counted twice?
After a decision, the watcher requires the chute to remain clear for the configured number of empty frames and observes a cooldown period. The transaction database also records whether a physical detection was already credited.

### 40. How are multiple objects handled?
The decision service detects multiple above-threshold objects in a frame, adds a reason, and marks the sample for collection. The conservative behavior is rejection unless the bottle evidence still satisfies the configured acceptance rule; the event remains auditable.

### 41. What happens when the detector fails to load?
The application logs that the model is not loaded. Inference returns no valid detections, and the decision path fails safe rather than accepting an unverified object. Health reports the model as not ready.

### 42. What happens when the camera fails?
The watcher releases the camera and detection lock, updates camera health, and does not actuate based on a missing frame. Hardware and integration tests cover open failure and partial read failure.

### 43. Why collect rejected or borderline samples?
Rejected, low-confidence, empty, multi-object, and borderline cases are the most useful cases for improving the model. The dataset service saves selected images with JSON metadata while enforcing a minimum free-disk limit.

## 5. Hardware Control and Safety

### 44. What is the serial protocol?
The Pi sends newline-terminated ASCII commands: `PING`, `ACCEPT`, `REJECT`, and `RESET`. The Arduino answers with `PONG`, `ACK:ACCEPT`, `ACK:REJECT`, or `ACK:RESET`, then reports `SERVO_COMPLETE` after the motion.

### 45. How does the Pi know the servo action completed?
It first waits for the command acknowledgement and then waits for `SERVO_COMPLETE` within the configured timeout. A command acknowledgement alone is not considered successful physical actuation.

### 46. What happens when an acknowledgement times out?
The serial service retries according to configuration. If the command or completion still fails, the orchestrator records `actuation_failed` and sends RESET to return the Arduino to a known idle state.

### 47. Why is RESET important?
RESET is recovery from a dropped command, a servo timeout, an interrupted process, or an inconsistent Arduino state. It prevents a failed communication attempt from leaving the chute controller stuck in a waiting or motion state.

### 48. What is the fail-safe direction?
Uncertain recognition is never treated as an accepted bottle. A hardware or model problem prevents credit and causes a reject or no-action path rather than giving free Wi-Fi for an unverified object.

### 49. What are the timing considerations?
The camera uses a continuous scan, the default decision window is 5 frames, the watcher has a 2-second cooldown, the serial completion timeout is 6 seconds, and the portal waits up to 90 seconds for a new bottle by default. Actual performance must still be verified on the installed camera and chute.

### 50. Is there an IR sensor in the current design?
The current Orange Pi architecture says no: the camera itself watches the chute while a portal session is open. However, the Arduino source still contains legacy IR-trigger states and an `OBJECT_DETECTED` event. That source must be treated as a migration boundary and verified against the flashed firmware and wiring before claiming that the physical rig is fully camera-triggered.

### 51. What does the Arduino do with an old OBJECT_DETECTED event?
The current Orange Pi orchestrator ignores it because camera-triggered watching owns the detection decision. If the installed Arduino firmware still requires an IR-triggered WAITING_FOR_AI state before accepting commands, the rig will not match the current Orange Pi design and must be updated or configured consistently.

### 52. How are servos configured?
Servo positions and hold timing are mirrored in the YAML configuration and the Arduino configuration. The Arduino remains responsible for the actual movement, so the physical values must be calibrated on the machine rather than assumed from software defaults.

## 6. MikroTik, Rewards, and Reliability

### 53. How does the system grant Wi-Fi time?
It creates or updates a MikroTik hotspot user and sets an absolute `limit-uptime` target. For a normal session the target is the new bottle time; for an extension it is the remaining time plus the new bottle time.

### 54. Why use an absolute target instead of repeatedly adding time?
An absolute target is retry-safe. If the same reward is retried after a timeout, setting the same target does not add the reward twice.

### 55. What happens if MikroTik is offline at Done?
The reward is stored as PENDING with its target and error information. The retry thread periodically attempts the same absolute target. The customer is not falsely told that internet access was granted while the router is unreachable.

### 56. What happens if the application restarts after detection?
Detection and session records are persisted in SQLite. A pending reward remains available for retry after restart, subject to the deployment and startup process restoring the database and service.

### 57. What are the four idempotency protections?
A physical transaction has a unique transaction ID; a detection can credit a session at most once; a session has one reward grant; and MikroTik receives an absolute target rather than an unbounded additive operation.

### 58. What database is used?
SQLite is used with WAL mode, foreign keys, indexes, and one shared connection protected by a re-entrant lock. It stores detection transactions, portal sessions, reward grants, system events, and model versions.

### 59. What does the database record for a detection?
It records a unique transaction ID, timestamp, session voucher, prediction, confidence, positive and total frame counts, ACCEPT or REJECT result, model version, optional image path, credit status, and error message.

### 60. What is logged for operations and troubleshooting?
The application logs startup, component readiness, decisions, serial failures, actuation failures, reward attempts, health transitions, model actions, and dataset capture. System events are also persisted in SQLite for later auditing.

### 61. What happens when the database is unavailable?
Database health becomes false, the system is not ready, and guarded operations should fail rather than silently claim a successful transaction. Database writes are centralized through repositories and a locked connection.

## 7. Model Governance and Operations

### 62. How is a model promoted to production?
The candidate is trained and validated on the PC. Validation writes `validation.json`. Deployment refuses a candidate without a passing report, checks FAR, recall, and mAP50 thresholds, backs up the current model, promotes the candidate, registers it, and optionally restarts the service.

### 63. What are the model acceptance gates?
The configured gates are false-acceptance rate at most 0.02, recall at least 0.90, and mAP50 at least 0.80. FAR is especially important because a false acceptance can give a reward for an invalid item.

### 64. What happens if a deployed model cannot load?
The post-deployment health check fails and the model service automatically rolls back to the previous production model. The old version is archived according to the model directory workflow.

### 65. Why is training not performed on the Orange Pi?
Training requires substantially more CPU, memory, and storage. The Pi is an edge inference and control device, while the PC or GPU machine performs training, validation, and export.

### 66. How can the model improve over time?
The system captures hard examples with metadata, an operator reviews and labels them, the training set is updated, a new model is trained and validated, and only a passing candidate is deployed. This is a controlled feedback loop rather than automatic unreviewed retraining.

### 67. How does the service start automatically?
The systemd unit starts after network-online, runs under a non-root `orangepi` user, allows low-port binding through `CAP_NET_BIND_SERVICE`, grants access to video and dialout groups, limits memory, restarts on failure, and sends logs to the journal.

### 68. Why is the API bound to port 80?
The existing captive portal expects the vendo device at HTTP port 80. The systemd capability allows the service to bind that port without running the whole application as root.

### 69. What configuration contains secrets?
Non-secret operational settings are in `config/config.yaml`. MikroTik credentials belong in `.env` and should never be committed to source control. The application uses the configured RouterOS host, port, username, password, profile, and authentication mode.

### 70. What are the main security concerns?
The router API credentials must be protected, the RouterOS API should be reachable only from the trusted local network, the HTTP API should not be exposed beyond the intended LAN, input values should be validated, and logs should not expose secrets. The current code disables certificate verification when MikroTik SSL is enabled, so that mode requires a trusted network or a future certificate-validation improvement.

## 8. Testing and Evaluation

### 71. How is the system tested without hardware?
Services accept injected cameras, detectors, serial transports, databases, and configuration. Fake components allow the detection-to-servo pipeline, database, reward flow, health checks, and model deployment gates to run without physical hardware.

### 72. What are the most important automated tests?
The essential assertions are valid bottle to ACCEPT, reject object to REJECT, no detection to no unsafe acceptance, and the same transaction cannot grant twice. Integration tests also cover clear-frame rearming, camera failure, serial recovery, dataset capture, and exceptions.

### 73. What is tested for the serial layer?
Protocol classification, acknowledgements, retries, timeout handling, PING/PONG, asynchronous events, and reconnection behavior are tested.

### 74. What is tested for the vision layer?
The decision service tests valid bottles, reject objects, empty frames, confidence gates, borderline confidence, multiple objects, and fail-safe exception handling. The complete watcher pipeline tests continuous scanning and one-bottle-one-credit behavior.

### 75. What must still be tested physically?
The real rig must verify camera framing and lighting, detection latency, all bottle and reject variants, servo direction and timing, serial wiring, MikroTik login, portal completion, unplugged-router retry, restart recovery, temperature, memory, and disk behavior.

### 76. How should accuracy be measured?
Use a held-out test set and report precision, recall, mAP50, false-acceptance rate, false-rejection rate, and a confusion matrix. Also evaluate the full 3-of-5 decision behavior, because image-level model metrics alone do not fully represent the vending workflow.

### 77. Which metric is most important for this application?
False acceptance rate is critical because accepting a non-qualifying object gives an undeserved reward. Recall also matters because rejecting valid bottles creates a poor customer experience. The configured gates balance both concerns.

### 78. How do you test real-world robustness?
Test lighting changes, motion blur, bottle colour, labels, crushing, rotation, partial visibility, camera angle, occlusion, chute clutter, and reject objects that resemble bottles. Repeat tests across multiple sessions and record every decision.

## 9. Limitations and Difficult Questions

### 79. What is the biggest limitation?
The system's real-world accuracy and timing depend on the installed camera, lighting, chute geometry, model quality, and physical calibration. Automated tests prove software behavior, but they cannot replace on-device validation with real objects.

### 80. Is the production model definitely present?
The code supports NCNN model loading from `models/production`, but deployment readiness must be confirmed on the target Pi. If no valid `.param` and `.bin` pair is installed, the detector will not load and the system will remain not ready.

### 81. Is the documentation fully synchronized with the implementation?
No. The README still contains scaffold-era wording, while the current code implements later phases. The panel answer should rely on the current entrypoint, services, configuration, tests, and deployment scripts, and the README should be updated before final presentation.

### 82. Is the Arduino implementation fully synchronized with the Orange Pi architecture?
Not conclusively. The current Python watcher is camera-triggered, but the Arduino source still includes an IR sensor and an OBJECT_DETECTED handshake. The exact flashed firmware and wiring must be confirmed, or the Arduino firmware must be revised so both sides use the same trigger model.

### 83. What happens if the router is down but the bottle is accepted?
The physical decision and detection transaction can still be recorded, but the reward remains pending. Once connectivity returns, the retry loop applies the stored absolute limit. The operator should communicate that internet granting is delayed rather than pretending it succeeded.

### 84. What happens if power is lost during servo movement?
The application cannot guarantee mechanical state after an abrupt power loss. On restart it sends RESET when the serial link is available. A physical design should also prevent jams and should be checked for safe startup positions.

### 85. Can a customer exploit repeated requests to get free time?
Repeated top-up requests from the same active customer reuse the session, and repeated Done requests reuse the existing successful reward. Unique transaction and grant records prevent normal duplicate crediting. Authentication, API exposure, and router permissions still need to be restricted to the trusted hotspot network.

### 86. Can the model be fooled?
Yes. Computer vision is probabilistic. The system reduces risk with representative training data, confidence thresholds, multi-frame agreement, FAR validation gates, hard-example collection, and conservative rejection. It cannot claim perfect classification.

### 87. Why not use only the old IR trigger?
An IR trigger can detect that something crossed the chute but cannot identify whether it is a valid plastic bottle. Camera-triggered continuous scanning lets the system identify the object while it remains visible and removes dependence on a separate trigger for the active Orange Pi path. The installed hardware must still be made consistent with that design.

### 88. Why not put all logic in the Arduino?
The Arduino is appropriate for deterministic servo control but is constrained for model inference, persistence, HTTP service, and RouterOS integration. Splitting responsibilities keeps real-time actuation simple and gives the Orange Pi enough compute and storage for AI and transaction management.

### 89. Why not grant time immediately after ACCEPT?
The design separates physical acceptance from final internet authorization. The portal's Done step gives the customer control to finish the session, while the reward record preserves retryability and prevents duplicate grants.

### 90. What would you improve next?
First, synchronize the README and Arduino firmware with the camera-triggered architecture. Then complete on-device acceptance testing, add stronger API authentication or network isolation, validate SSL certificates properly when used, add more hardware fault injection, and publish measured latency and model metrics from the real chute.

## 10. Short Defense Summary

### 91. Explain the project in one minute.
The system exchanges accepted plastic bottles for Wi-Fi time. A customer starts a portal session, the Orange Pi watches the chute with a camera, and a YOLO model classifies consecutive frames. Three positive bottle detections in a five-frame window are enough to accept; otherwise the physical result is conservative. The Pi records the event, commands an Arduino servo controller, waits for completion, and credits the session. When the customer finishes, the application grants an absolute uptime limit through MikroTik. SQLite persistence, idempotency rules, health checks, retry handling, model validation, and rollback make the process auditable and resilient.

### 92. What is your strongest technical justification?
The design separates concerns: the Pi handles AI, persistence, networking, and orchestration; the Arduino handles deterministic actuation; and MikroTik remains responsible for hotspot access. The decision is multi-frame and fail-safe, while the reward path is persistent and idempotent.

### 93. What should you honestly say if asked whether it is fully production-ready?
The software architecture and automated tests cover the main workflow, but production readiness still depends on installing and validating the real NCNN model, aligning the flashed Arduino firmware with the camera-triggered design, calibrating the chute, and completing the hardware checklist on the actual Orange Pi, camera, Arduino, servos, and MikroTik setup.

## Useful Evidence Files

- `main.py`: orchestration, startup, detection-to-actuation flow, and shutdown.
- `services/watch_service.py`: continuous camera-triggered scanning and clear-frame protection.
- `services/decision_service.py`: confidence and multi-frame decision rules.
- `services/reward_service.py`: idempotent bottle crediting and reward retry behavior.
- `services/portal_api_service.py`: captive portal API contract.
- `services/mikrotik_service.py`: RouterOS user and active-session operations.
- `repositories/db.py`: SQLite schema, WAL mode, and guarded access.
- `config/config.yaml`: operational defaults and safety thresholds.
- `tests/README.md`: automated and hardware validation checklist.
- `scripts/validate.py`, `scripts/deploy.py`: model validation gates and rollback.
- `arduino-uno-vendo/src/main.cpp`: Arduino state machine and serial protocol.

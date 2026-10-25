# Orange Pi Zero 3 — Bottle Wi-Fi Vendo Application

AI brain of the AI-Based Plastic Bottle Wi-Fi Vendo System. Runs event-driven
YOLO+NCNN bottle detection, decides ACCEPT/REJECT, logs transactions, and grants
Wi-Fi time through the **existing** MikroTik hotspot + captive portal.

os: Orangepizero3_1.0.4_debian_bookworm_server_linux6.1.31
> Full architecture: [`../docs/architecture/`](../docs/architecture/). This repo
> is one of several in the project (Arduino firmware lives in
> `../arduino-uno-vendo`, network in `../mikrotik-hap-lite`, portal in
> `../captive-portal`).

## Role in the system

```
Arduino Uno <--USB serial 115200-- [ Orange Pi Zero 3 ] --RouterOS API--> MikroTik hAP lite
 (servos only)                      camera watches the chute                (existing hotspot)
                                    + YOLO/NCNN + decision
                                    + JuanFi-compatible HTTP API <-- captive portal
```

The Orange Pi is the only decision-maker and the only device that talks to
MikroTik. There is **no IR sensor**: `services/watch_service.py` keeps the
camera open and infers frame by frame while a portal session is open, and the
Arduino only runs the sequence it is told to run. It exposes the same HTTP API the ESP32-CAM did, so the captive portal
keeps working unchanged.

## Layout

| Path | Purpose | Implemented in |
|---|---|---|
| `main.py` | Entrypoint / orchestrator | Phase 5 |
| `core/` | config loader, app state, shared types, logging | Phase 5 |
| `services/` | serial, camera, **chute watcher**, detector, decision, session, portal API, reward, mikrotik, dataset, model, health | Phases 5–17 |
| `repositories/` | SQLite access (detection, reward, session, event, model) | Phase 10+ |
| `config/config.yaml` | non-secret configuration | Phase 3 (done) |
| `.env` (from `.env.example`) | MikroTik secrets | Phase 15 |
| `models/{production,candidate,archive,rollback}/` | versioned NCNN models | Phase 14 |
| `dataset/{incoming,reviewed,train,val,test}/` | training data pipeline | Phase 11–12 |
| `scripts/` | train, validate, export, deploy, rollback | Phase 12–14 |
| `database/` | `bottle_vendo.db` (runtime) | Phase 10 |
| `systemd/bottle-vendo.service` | auto-start unit | Phase 18 |
| `tests/` | unit/integration/hardware tests | Phase 19 |

## Status

Scaffold complete (Phase 3). Modules are stubs that raise `NotImplementedError`
until their phase. Do **not** install training deps (torch/ultralytics) on the Pi.

## Quick start (once implemented)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # fill MikroTik creds (Phase 15)
python main.py
```

WIndows
source .venv/Scripts/activate


Training the Bottle Detection Model Using a Webcam on Windows
1. Overview
The model is trained on this PC using Ultralytics YOLO. The Orange Pi only performs inference using the exported NCNN model.

The workflow is:


The detector uses two classes:

Class ID	Class name	Meaning
0	plastic_bottle	Acceptable plastic bottle
1	reject_object	Any object that must be rejected
An empty chute is not a class. Empty images must be included as background images without labels.

The class definitions are configured in dataset.yaml and described in LABELING.md.

2. PC Requirements
Recommended:

Windows 10 or Windows 11
Python 3.10 or newer
USB webcam or built-in webcam
At least 8 GB RAM
NVIDIA GPU recommended but not required
At least 10 GB free disk space
Internet connection for installing Python packages
Training can run on CPU, but a GPU will be significantly faster.

3. Open PowerShell
Open PowerShell and move into the project directory:


Verify that the files exist:


4. Create a Python Virtual Environment
Create the environment:


Activate it:


If PowerShell blocks activation, run:


Verify Python:


The prompt should show (.venv) after activation.

5. Install Training Dependencies
Install the training dependencies:


The training requirements include:

Ultralytics
PyTorch
Torchvision
OpenCV
PyYAML
NCNN export support
Verify the installation:


Check whether PyTorch detects an NVIDIA GPU:


If CUDA is unavailable, training can still run on the CPU.

## Live webcam collection and CPU training

To populate the YOLO folders without manually labeling every image, install the
training dependencies and run the local collector from this project directory:

```powershell
python -m pip install -r requirements-train.txt
python live_training.py
```

On Windows, the collector prefers a camera whose driver identifies it as an
integrated or built-in laptop webcam and uses DirectShow. To select a specific
camera index instead, run `python live_training.py --device 0` (try `1` if the
built-in camera is listed second). The window shows the webcam view and has four actions: `Bottle (x10) burst
shot`, `Reject (x10) burst shot`, `Empty chute (x10) burst shot`, and `Train
Model`. Each capture writes to `dataset/train`, `dataset/val`, or
`dataset/test` using a 70/20/10 split. Bottle and reject images receive a
full-frame label, so place the object clearly inside the vendo chute. Empty
chute images are saved as background images without labels. Training runs on
the CPU with a small batch by default; use `--epochs` or `--batch` when
starting the script to adjust the training defaults.

6. Test the Webcam
The current scripts/camera_check.py is designed for the webcam attached to the Orange Pi, not for a Windows PC webcam.

For PC collection, first verify that Windows can see the camera:

Open the Windows Camera application.
Confirm that the correct webcam is selected.
Check that the image is clear.
Place the webcam at the same angle and distance that it will have in the vending machine.
Use consistent lighting where possible.
The training images should represent the real operating environment:

Empty chute
Clear bottles
Colored bottles
Crushed bottles
Dirty bottles
Bottles with labels
Bottles at different angles
Partially visible bottles
Bright lighting
Low lighting
Shadows
Reject objects
Hands and other accidental objects
The final Orange Pi camera uses a target size of 1280x720, while inference uses 480x480. The training script also defaults to 480, as configured in scripts/train.py.

7. Collect Webcam Images
The repository currently does not include a Windows webcam collection script. Images may be collected using:

The Windows Camera application
A webcam capture application
A short Python/OpenCV capture utility
Images copied from the Orange Pi dataset/incoming/ folder
Organize the raw images into categories before labeling:


Recommended minimum starting dataset:

Category	Initial target
Valid plastic bottles	500+ images
Reject objects	500+ images
Empty/background images	100+ images
More important than the exact number is variation. Avoid collecting hundreds of nearly identical frames.

Valid bottle images
Collect:

Clear PET bottles
Colored PET bottles
Small and large bottles
Crushed bottles
Bottles with labels
Bottles without labels
Rotated bottles
Bottles at different distances
Partially visible bottles
Bottles in bright and dim lighting
Reject object images
Collect:

Aluminum cans
Glass bottles
Cups
Plastic bags
Phones
Hands
Food containers
Cardboard
Metal objects
Other garbage
Unknown objects
Background images
Capture the chute with:

Nothing present
Shadows
Background clutter
Different lighting
Empty conveyor or drop area
Background images must not contain a bottle or reject object.

8. Label the Images
Use one of these annotation tools:

CVAT
Roboflow
Label Studio
LabelImg
Create exactly these labels:


Do not create an empty class.

For each object, draw a bounding box around the visible object. Do not include excessive empty space around the object.

YOLO labels use this format:


All coordinates are normalized between 0 and 1.

Example:


A labeled image should have this pair:


An empty/background image should have no label file, or an empty label file.

9. Prepare the Dataset Folders
The expected dataset structure is:


Recommended split:

70% training images
20% validation images
10% test images
Keep the test set separate. Do not use test images while adjusting labels or training parameters.

Example:


The image and label must have the same filename stem.

10. Check dataset.yaml
The current configuration is:


This path is specific to the current PC. If the project is moved to another computer, update the path value.

11. Run a Small Training Test
Before starting a long training run, test the complete pipeline with a small number of epochs:


For an NVIDIA GPU:


The results should appear under:


The trained weights will be located at:


12. Start the Full Training Run
The project training script defaults to:

120 epochs
Image size 480
Batch size 16
Early stopping patience of 30
GPU training:


CPU training:


The final weights should be:


The existing yolo26n.pt file may also be passed with --model, but the current training script documentation and default are based on yolov8n.pt. Use a YOLO checkpoint compatible with the installed Ultralytics version.

13. Validate the Model
Run validation against the test set:


The validation script measures:

Precision
Recall
mAP50
False Acceptance Rate
False Rejection Rate
The configured acceptance gates are:


A model should not be deployed if it accepts too many cans, glass bottles, hands, or empty scenes.

Validation output is stored at:


The model must pass validation before deployment.

14. Export the Model for the Orange Pi
Export the trained PyTorch model to NCNN:


The exported files should be placed under:


The image size must remain 480. This matches:


in config.yaml.

15. Copy the Model to the Orange Pi
Copy this directory to the Orange Pi:


The destination on the Orange Pi should be:


Then run the deployment command on the Orange Pi:


Do not deploy a model that has failed validation.

16. Test the Model on Real Hardware
Test the model with the actual vending machine webcam and chute.

Required test cases:

Clear plastic bottle
Colored plastic bottle
Crushed plastic bottle
Dirty bottle
Rotated bottle
Partially visible bottle
Aluminum can
Glass bottle
Cup
Plastic bag
Hand
Phone
Empty chute
Shadows
Low light
Multiple objects
Expected behavior:


The production configuration requires three positive detections out of five frames:


17. Improve the Model Iteratively
After real-world testing:

Save incorrect or uncertain examples.
Add them to the dataset.
Label them correctly.
Rebuild the train, validation, and test splits.
Train a new model version.
Validate the new model.
Export and deploy only if the new model passes the gates.
Pay special attention to false acceptances. For a vending machine, accepting an invalid object or an empty chute is more dangerous than rejecting one valid bottle.

18. Troubleshooting
ultralytics not installed
Activate the virtual environment and reinstall:


CUDA is unavailable
Use CPU mode:


Out-of-memory error
Reduce the batch size:


Dataset YAML not found
Run the command from the Orange Pi project directory:


Images have no detections
Check that:

The label files use the correct class IDs.
Bounding-box values are normalized.
Image and label filenames match.
plastic_bottle is class 0.
reject_object is class 1.
The dataset contains enough variation.
The camera angle resembles the real vending machine.
The model accepts empty scenes
Add more empty/background images and hard-negative images containing:

Shadows
Hands
Reflections
Background clutter
Partial objects
Chute edges
Then retrain and verify that the False Acceptance Rate is below 0.02.

19. Important Project Files
dataset.yaml: dataset location and class names
LABELING.md: labeling rules
train.py: training command
validate.py: test-set validation
export.py: NCNN export
config.yaml: Orange Pi inference configuration
requirements-train.txt: PC-only dependencies
The main gap in the current repository is a dedicated Windows webcam-capture utility. The existing camera capture implementation is intended for the webcam attached to the Orange Pi, while training and validation are intended to run on this PC.

# No Arduino on the desk? Simulate it — then 'b' fakes a bottle verdict:
.venv\Scripts\python.exe scripts\bench_laptop.py --sim-serial --fake-mikrotik

# Prove the credit plumbing without the model (every scanned frame = a bottle):
.venv\Scripts\python.exe scripts\bench_laptop.py --sim-serial --fake-mikrotik --mock-detector

# Different webcam / port:
.venv\Scripts\python.exe scripts\bench_laptop.py --fake-mikrotik --camera 1 --http-port 9000

"""Capture a small YOLO dataset from a webcam and train it on the local PC.

This is a PC-side helper. Each capture button saves ten frames from the same
camera position used by the vendo. Bottle and reject frames receive a
full-frame YOLO box; empty frames are saved as background images without a
label file. Use a fixed camera angle and place the object inside the chute.
"""

from __future__ import annotations

import argparse
import os
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent
SPLITS = ("train", "val", "test")
CLASS_IDS = {"bottle": 0, "reject": 1, "empty": None}
BUILT_IN_CAMERA_NAMES = ("integrated", "built-in", "builtin", "internal", "laptop")
# Ultralytics colours its progress output; the status bar wants plain text.
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _opencv_device_index(index: int) -> int:
    """Convert a backend-prefixed enumeration id (700 = DirectShow) to a capture index."""
    return index % 100 if index >= 100 else index


def _is_built_in(name: str) -> bool:
    return any(marker in name.lower() for marker in BUILT_IN_CAMERA_NAMES)


def list_cameras() -> list[tuple[int, str]]:
    """Return (OpenCV index, name) pairs, one entry per physical device.

    Windows enumerates every device once per backend (700+ DirectShow,
    1400+ Media Foundation); both collapse to the same capture index.
    """
    try:
        from cv2_enumerate_cameras import enumerate_cameras

        cameras = list(enumerate_cameras())
    except Exception:
        return []
    devices: dict[int, str] = {}
    for camera in cameras:
        index = _opencv_device_index(int(camera.index))
        name = str(getattr(camera, "name", "") or "")
        devices.setdefault(index, name or f"camera index {index}")
    return sorted(devices.items())


def camera_opens(index: int) -> bool:
    """Check that a capture index really delivers a frame before the UI starts."""
    try:
        import cv2
    except Exception:
        return False
    backend = cv2.CAP_DSHOW if hasattr(cv2, "CAP_DSHOW") and sys.platform == "win32" else cv2.CAP_ANY
    cap = cv2.VideoCapture(index, backend)
    try:
        if not cap.isOpened():
            return False
        ok, frame = cap.read()
        return bool(ok and frame is not None)
    finally:
        cap.release()


def find_camera(prefer_built_in: bool = False, default_index: int = 0) -> tuple[int, str]:
    """Pick a webcam, preferring the external USB one the vendo will use."""
    cameras = list_cameras()
    ordered = sorted(cameras, key=lambda device: _is_built_in(device[1]) != prefer_built_in)
    for index, name in ordered:
        if camera_opens(index):
            return index, name
    if ordered:
        return ordered[0]
    return default_index, f"camera index {default_index}"


def split_for_sample(sample_number: int) -> str:
    """Return a deterministic 70/20/10 split for a one-based sample number."""
    slot = (sample_number - 1) % 10
    return "train" if slot < 7 else "val" if slot < 9 else "test"


def full_frame_label(class_id: int) -> str:
    """Create a YOLO label for an object assumed to fill the camera view."""
    return f"{class_id} 0.5 0.5 1.0 1.0\n"


class LiveDataset:
    def __init__(self, dataset_root: Path) -> None:
        self.root = dataset_root
        for split in SPLITS:
            (self.root / split / "images").mkdir(parents=True, exist_ok=True)
            (self.root / split / "labels").mkdir(parents=True, exist_ok=True)

    def _next_number(self, category: str) -> int:
        total = 0
        for split in SPLITS:
            image_dir = self.root / split / "images"
            total += len(list(image_dir.glob(f"{category}_*.jpg")))
        return total + 1

    def save_frames(self, category: str, frames: list, jpeg_quality: int = 90) -> int:
        import cv2

        if category not in CLASS_IDS:
            raise ValueError(f"unknown category: {category}")
        saved = 0
        start = self._next_number(category)
        for offset, frame in enumerate(frames):
            number = start + offset
            split = split_for_sample(number)
            stem = f"{category}_{number:05d}"
            image_path = self.root / split / "images" / f"{stem}.jpg"
            label_path = self.root / split / "labels" / f"{stem}.txt"
            ok, encoded = cv2.imencode(
                ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), jpeg_quality]
            )
            if not ok:
                continue
            image_path.write_bytes(encoded.tobytes())
            if CLASS_IDS[category] is None:
                label_path.unlink(missing_ok=True)
            else:
                label_path.write_text(full_frame_label(CLASS_IDS[category]), encoding="utf-8")
            saved += 1
        return saved


class LiveTrainingApp:
    def __init__(self, args: argparse.Namespace, camera: tuple[int, str]) -> None:
        import tkinter as tk

        try:
            import cv2
        except ImportError as exc:
            raise RuntimeError("OpenCV is required. Install requirements-train.txt") from exc

        from core.config import load_config
        from services.camera_service import CameraService

        self.tk = tk
        self.cv2 = cv2
        self.args = args
        self.cfg = load_config(APP_ROOT)
        self.camera = CameraService(self.cfg)
        camera_index, camera_name = camera
        self.camera._device = camera_index
        self.camera._camera_name = camera_name
        self.camera._width = args.width
        self.camera._height = args.height
        self.dataset = LiveDataset(APP_ROOT / "dataset")
        self.root = tk.Tk()
        self.root.title("Bottle Vendo - Live Training")
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.minsize(900, 620)
        self.lock = threading.Lock()
        self.closed = False
        self.busy = False
        self.output_queue: queue.Queue[str] = queue.Queue()
        self.buttons: list = []
        self.preview_label = None
        self.status_var = tk.StringVar(value="Opening camera...")
        self.progress_var = tk.StringVar(value="No images captured yet")
        self._build_ui()
        if not self.camera.open():
            self.status_var.set(
                f"Could not open {camera_name} (index {camera_index}). "
                "Close other apps using it, or run with --list-cameras then --device N."
            )
        else:
            self.status_var.set(f"Using {camera_name}. Put one item inside the vendo chute, then capture.")
        self.root.after(30, self.update_preview)
        self.root.after(100, self.consume_output)

    def _build_ui(self) -> None:
        import tkinter as tk

        self.root.configure(bg="#14211f")
        header = tk.Frame(self.root, bg="#14211f", padx=24, pady=18)
        header.pack(fill="x")
        tk.Label(header, text="LIVE TRAINING", fg="#f3e7c8", bg="#14211f",
                 font=("Segoe UI", 20, "bold")).pack(anchor="w")
        tk.Label(header, text="Capture the chute exactly as the vendo will see it.",
                 fg="#b8c8bf", bg="#14211f", font=("Segoe UI", 10)).pack(anchor="w")

        body = tk.Frame(self.root, bg="#14211f", padx=24)
        body.pack(fill="both", expand=True)
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=0, minsize=250)
        body.grid_rowconfigure(0, weight=1)
        self.preview_label = tk.Label(body, bg="#0b1211", width=800, height=430)
        self.preview_label.grid(row=0, column=0, sticky="nsew", padx=(0, 18))

        controls = tk.Frame(body, bg="#14211f", width=250)
        controls.grid(row=0, column=1, sticky="ns")
        tk.Label(controls, text="COLLECT", fg="#f3e7c8", bg="#14211f",
                 font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(4, 12))
        self._add_button(controls, "Bottle (x10) burst shot", "bottle", "#2d806d")
        self._add_button(controls, "Reject (x10) burst shot", "reject", "#b4654a")
        self._add_button(controls, "Empty chute (x10) burst shot", "empty", "#687b78")
        tk.Frame(controls, height=18, bg="#14211f").pack()
        self.train_button = tk.Button(
            controls, text="Train Model", command=self.train_model, state="normal",
            bg="#e0b35a", fg="#14211f", activebackground="#f0cb77",
            relief="flat", padx=12, pady=12, font=("Segoe UI", 11, "bold"), cursor="hand2"
        )
        self.train_button.pack(fill="x")
        self.buttons.append(self.train_button)
        tk.Label(controls, textvariable=self.progress_var, justify="left", wraplength=230,
                 fg="#b8c8bf", bg="#14211f", font=("Segoe UI", 9)).pack(anchor="w", pady=18)

        footer = tk.Label(self.root, textvariable=self.status_var, anchor="w",
                          fg="#f3e7c8", bg="#0b1211", padx=24, pady=10,
                          font=("Segoe UI", 10))
        footer.pack(fill="x")

    def _add_button(self, parent, text: str, category: str, color: str) -> None:
        import tkinter as tk

        button = tk.Button(parent, text=text, command=lambda: self.capture(category),
                           bg=color, fg="#ffffff", activebackground=color,
                           relief="flat", padx=8, pady=11, font=("Segoe UI", 10, "bold"),
                           cursor="hand2")
        button.pack(fill="x", pady=4)
        self.buttons.append(button)

    def update_preview(self) -> None:
        if self.closed:
            return
        frame = None
        with self.lock:
            if self.camera._cap is not None:
                ok, frame = self.camera._cap.read()
        if frame is not None:
            from PIL import Image, ImageTk

            frame = self.cv2.cvtColor(frame, self.cv2.COLOR_BGR2RGB)
            image = Image.fromarray(frame)
            image.thumbnail((800, 480))
            photo = ImageTk.PhotoImage(image=image)
            self.preview_label.configure(image=photo)
            self.preview_label.image = photo
        self.root.after(30, self.update_preview)

    def capture(self, category: str) -> None:
        if self.busy:
            return
        self.busy = True
        self._set_buttons("disabled")
        self.status_var.set(f"Capturing {category} burst: 0/{self.args.count}")
        threading.Thread(target=self._capture_worker, args=(category,), daemon=True).start()

    def _capture_worker(self, category: str) -> None:
        frames = []
        with self.lock:
            if self.camera._cap is None:
                self.output_queue.put("Camera is not open.")
            else:
                for index in range(self.args.count):
                    ok, frame = self.camera._cap.read()
                    if ok and frame is not None:
                        frames.append(frame)
                    self.output_queue.put(f"Capturing {category} burst: {index + 1}/{self.args.count}")
                    if index + 1 < self.args.count:
                        time.sleep(self.args.gap_ms / 1000)
        if frames:
            saved = self.dataset.save_frames(category, frames, self.args.jpeg_quality)
            self.output_queue.put(f"Saved {saved} {category} image(s) into train/val/test.")
        else:
            self.output_queue.put("No frames captured. Check the webcam.")
        self.output_queue.put("__READY__")

    def train_model(self) -> None:
        if self.busy:
            return
        image_count = sum(len(list((APP_ROOT / "dataset" / split / "images").glob("*.jpg")))
                          for split in SPLITS)
        if image_count == 0:
            self.status_var.set("Capture at least one burst before training.")
            return
        self.busy = True
        self._set_buttons("disabled")
        self.status_var.set("Training on CPU. This can take several minutes...")
        command = [
            sys.executable, str(APP_ROOT / "scripts" / "train.py"),
            "--data", str(APP_ROOT / "dataset" / "dataset.yaml"),
            "--model", self.args.model, "--epochs", str(self.args.epochs),
            "--imgsz", str(self.args.imgsz), "--batch", str(self.args.batch),
            "--device", "cpu", "--name", self.args.name, "--project", str(APP_ROOT / "runs"),
        ]
        threading.Thread(target=self._train_worker, args=(command,), daemon=True).start()

    def _train_worker(self, command: list[str]) -> None:
        # Ultralytics prints non-ASCII progress output; force UTF-8 on both ends
        # so the Windows ANSI codepage cannot break the reader mid-run.
        env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        try:
            process = subprocess.Popen(command, cwd=APP_ROOT, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, text=True, bufsize=1,
                                       encoding="utf-8", errors="replace", env=env)
            assert process.stdout is not None
            for line in process.stdout:
                self.output_queue.put(_ANSI_ESCAPE.sub("", line).rstrip())
            code = process.wait()
            self.output_queue.put("Training finished successfully." if code == 0
                                  else f"Training failed with exit code {code}.")
        except Exception as exc:  # noqa: BLE001
            self.output_queue.put(f"Could not start training: {exc}")
        self.output_queue.put("__READY__")

    def consume_output(self) -> None:
        if self.closed:
            return
        try:
            while True:
                message = self.output_queue.get_nowait()
                if message == "__READY__":
                    self.busy = False
                    self._set_buttons("normal")
                elif message.startswith(("Saved ", "Capturing ")):
                    self.status_var.set(message)
                    self.progress_var.set(message)
                elif message.startswith(("Training ", "Could ")):
                    self.status_var.set(message)
                else:
                    self.progress_var.set(message[-250:])
        except queue.Empty:
            pass
        self.root.after(100, self.consume_output)

    def _set_buttons(self, state: str) -> None:
        for button in self.buttons:
            button.configure(state=state)

    def close(self) -> None:
        self.closed = True
        with self.lock:
            self.camera.release()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect webcam images and train YOLO on CPU")
    parser.add_argument("--device", type=int, default=None,
                        help="OpenCV camera index override, e.g. 0 or 1; default prefers the external USB webcam")
    parser.add_argument("--built-in", action="store_true",
                        help="prefer the laptop's built-in webcam instead of the USB one")
    parser.add_argument("--list-cameras", action="store_true",
                        help="print the detected cameras with their indexes and exit")
    parser.add_argument("--count", type=int, default=10, help="images per burst")
    parser.add_argument("--gap-ms", type=int, default=180, help="delay between burst frames")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--jpeg-quality", type=int, default=90)
    parser.add_argument("--model", default="yolov8n.pt")
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--imgsz", type=int, default=480)
    parser.add_argument("--batch", type=int, default=2)
    parser.add_argument("--name", default="live_bottle")
    return parser.parse_args()


def print_cameras() -> int:
    cameras = list_cameras()
    if not cameras:
        print("No cameras detected. Install cv2-enumerate-cameras or pass --device 0.")
        return 1
    for index, name in cameras:
        kind = "built-in" if _is_built_in(name) else "usb"
        status = "ok" if camera_opens(index) else "busy or unavailable"
        print(f"--device {index}  {name}  [{kind}, {status}]")
    return 0


def resolve_camera(args: argparse.Namespace) -> tuple[int, str]:
    if args.device is not None:
        return args.device, f"camera index {args.device}"
    return find_camera(prefer_built_in=args.built_in)


def main() -> int:
    args = parse_args()
    if args.list_cameras:
        return print_cameras()
    camera_index, camera_name = resolve_camera(args)
    if args.device is None and not args.built_in and _is_built_in(camera_name):
        # Capturing the dataset on the laptop webcam would not match the vendo
        # chute, so stop here rather than quietly collecting the wrong frames.
        print(f"ERROR: no USB camera found, only '{camera_name}'.")
        print("Plug the vendo camera in, then check it with: python live_training.py --list-cameras")
        print("To capture on the built-in webcam anyway, pass --built-in.")
        return 1
    try:
        LiveTrainingApp(args, (camera_index, camera_name)).run()
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
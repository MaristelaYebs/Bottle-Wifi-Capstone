#!/usr/bin/env python3
"""
Train a tiny int8 TFLite model: bottle vs empty (96x96 grayscale).

  python tools/train_bottle_model.py --synthetic
  python tools/train_bottle_model.py --data tools/dataset
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

IMG = 96
ROOT = Path(__file__).resolve().parents[1]
OUT_TFLITE = ROOT / "tools" / "bottle_model.tflite"
OUT_HEADER = ROOT / "include" / "bottle_model_data.h"


def make_model():
    from tensorflow import keras

    inputs = keras.Input(shape=(IMG, IMG, 1))
    x = keras.layers.Rescaling(1.0 / 255.0)(inputs)
    x = keras.layers.Conv2D(8, 3, activation="relu", padding="same")(x)
    x = keras.layers.MaxPooling2D()(x)
    x = keras.layers.Conv2D(16, 3, activation="relu", padding="same")(x)
    x = keras.layers.MaxPooling2D()(x)
    x = keras.layers.Conv2D(32, 3, activation="relu", padding="same")(x)
    x = keras.layers.MaxPooling2D()(x)
    x = keras.layers.GlobalAveragePooling2D()(x)
    x = keras.layers.Dense(32, activation="relu")(x)
    outputs = keras.layers.Dense(1, activation="sigmoid")(x)
    model = keras.Model(inputs, outputs)
    model.compile(optimizer="adam", loss="binary_crossentropy", metrics=["accuracy"])
    return model


def synth_batch(n: int, bottle: bool) -> np.ndarray:
    imgs = np.random.randint(90, 160, size=(n, IMG, IMG, 1), dtype=np.int16)
    if not bottle:
        imgs += np.random.randint(-8, 9, size=imgs.shape)
        return np.clip(imgs, 0, 255).astype(np.float32)

    for i in range(n):
        if np.random.rand() < 0.5:
            bw, bh = np.random.randint(18, 40), np.random.randint(45, 90)
        else:
            bw, bh = np.random.randint(45, 90), np.random.randint(18, 40)
        x0 = np.random.randint(0, max(1, IMG - bw))
        y0 = np.random.randint(0, max(1, IMG - bh))
        shade = np.random.randint(20, 80)
        yy, xx = np.ogrid[:IMG, :IMG]
        cy, cx = y0 + bh / 2.0, x0 + bw / 2.0
        mask = ((yy - cy) / max(1.0, bh / 2.0)) ** 2 + ((xx - cx) / max(1.0, bw / 2.0)) ** 2 <= 1.0
        channel = imgs[i, ..., 0]
        channel[mask] = shade
        channel[y0 : y0 + bh, x0 + bw // 3 : x0 + bw // 3 + 2] = min(255, shade + 70)
    return np.clip(imgs + np.random.randint(-5, 6, size=imgs.shape), 0, 255).astype(np.float32)


def load_folder(folder: Path, label: int):
    try:
        from PIL import Image
    except ImportError as e:
        raise SystemExit("Install Pillow: pip install pillow") from e

    xs, ys, weights = [], [], []
    if not folder.exists():
        return xs, ys, weights
    for p in list(folder.glob("*.jpg")) + list(folder.glob("*.png")) + list(folder.glob("*.jpeg")):
        im = Image.open(p).convert("L").resize((IMG, IMG))
        arr = np.asarray(im, dtype=np.float32)[..., None]
        xs.append(arr)
        ys.append(float(label))
        name = p.name.lower()
        # Real ESP32-CAM /snapshot frames matter more than shop photos
        weights.append(8.0 if name.startswith("snapshot") else 1.0)
    return xs, ys, weights


def augment_one(img: np.ndarray) -> np.ndarray:
    out = img.astype(np.float32).copy()
    if np.random.rand() < 0.5:
        out = np.fliplr(out)
    out = np.clip(out * np.random.uniform(0.7, 1.35) + np.random.uniform(-28, 28), 0, 255)
    dy, dx = np.random.randint(-10, 11), np.random.randint(-10, 11)
    out = np.roll(np.roll(out, dy, 0), dx, 1)
    if np.random.rand() < 0.4:
        out = np.clip(out + np.random.normal(0, 10, out.shape), 0, 255)
    return out.astype(np.float32)


def expand(xs, ys, weights, extra_per: int):
    out_x, out_y = [], []
    for img, lab, w in zip(xs, ys, weights):
        copies = extra_per * int(max(1.0, w))
        out_x.append(img)
        out_y.append(lab)
        for _ in range(copies):
            out_x.append(augment_one(img))
            out_y.append(lab)
    return out_x, out_y


def build_xy(data_dir: Path | None, synth_n: int, force_synth: bool, no_synth: bool):
    parts_x, parts_y = [], []
    n_bottle = 0
    n_neg = 0
    if data_dir and not force_synth:
        for label, name in ((0, "empty"), (0, "reject"), (1, "bottle")):
            xs, ys, weights = load_folder(data_dir / name, label)
            if xs:
                extra = 6 if name == "bottle" else 4
                xs, ys = expand(xs, ys, weights, extra)
                parts_x.append(np.stack(xs, 0))
                parts_y.append(np.array(ys, dtype=np.float32))
                print(f"  {name}: {len(xs)} images after augment")
                if label == 1:
                    n_bottle += len(xs)
                else:
                    n_neg += len(xs)

    real_n = sum(len(p) for p in parts_x)
    need_synth = force_synth or (not no_synth and real_n < 30)
    if no_synth and (n_bottle < 10 or n_neg < 10):
        raise SystemExit(
            f"Not enough real photos (PET={n_bottle} not-PET={n_neg}). "
            "Save more with: python tools/label_live.py"
        )
    if need_synth:
        print(f"Adding {synth_n} synthetic samples...")
        parts_x.append(synth_batch(synth_n // 2, False))
        parts_y.append(np.zeros(synth_n // 2, dtype=np.float32))
        parts_x.append(synth_batch(synth_n // 2, True))
        parts_y.append(np.ones(synth_n // 2, dtype=np.float32))
    elif real_n:
        print(f"Training on {real_n} augmented samples (CAM snapshots weighted higher).")

    x = np.concatenate(parts_x, 0)
    y = np.concatenate(parts_y, 0)
    idx = np.random.permutation(len(x))
    return x[idx], y[idx]


def convert_tflite(model, x_calib: np.ndarray):
    import tensorflow as tf

    def rep():
        for i in range(min(100, len(x_calib))):
            yield [x_calib[i : i + 1].astype(np.float32)]

    converter = tf.lite.TFLiteConverter.from_keras_model(model)
    converter.optimizations = [tf.lite.Optimize.DEFAULT]
    converter.representative_dataset = rep
    converter.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS_INT8]
    converter.inference_input_type = tf.int8
    converter.inference_output_type = tf.int8
    return converter.convert()


def write_header(tflite_bytes: bytes, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("#pragma once\n")
        f.write("// Auto-generated by tools/train_bottle_model.py — do not edit\n")
        f.write(f"#define BOTTLE_MODEL_INPUT {IMG}\n")
        f.write(f"#define BOTTLE_MODEL_LEN {len(tflite_bytes)}\n")
        f.write("#define BOTTLE_MODEL_READY 1\n")
        f.write("alignas(16) const unsigned char g_bottle_model[] = {\n")
        for i in range(0, len(tflite_bytes), 12):
            chunk = tflite_bytes[i : i + 12]
            f.write("  " + ", ".join(f"0x{b:02x}" for b in chunk) + ",\n")
        f.write("};\n")
        f.write("const unsigned int g_bottle_model_len = BOTTLE_MODEL_LEN;\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=ROOT / "tools" / "dataset")
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--no-synth", action="store_true", help="Use only real labeled photos")
    ap.add_argument("--epochs", type=int, default=20)
    ap.add_argument("--synth-n", type=int, default=800)
    args = ap.parse_args()

    import tensorflow as tf

    print("TF", tf.__version__)
    x, y = build_xy(
        None if args.synthetic else args.data,
        args.synth_n,
        args.synthetic,
        args.no_synth,
    )
    print(f"Train samples: {len(x)} bottles={int(y.sum())} empty={int(len(y) - y.sum())}")

    model = make_model()
    n_pos = max(1.0, float(y.sum()))
    n_neg = max(1.0, float(len(y) - y.sum()))
    class_weight = {0: (n_pos + n_neg) / (2.0 * n_neg), 1: (n_pos + n_neg) / (2.0 * n_pos)}
    print(f"class_weight={class_weight}")
    split = max(1, int(len(x) * 0.85))
    model.fit(
        x[:split],
        y[:split],
        validation_data=(x[split:], y[split:]) if split < len(x) else None,
        epochs=args.epochs,
        batch_size=32,
        class_weight=class_weight,
        verbose=1,
    )

    tflite_model = convert_tflite(model, x[:200])
    OUT_TFLITE.parent.mkdir(parents=True, exist_ok=True)
    OUT_TFLITE.write_bytes(tflite_model)
    write_header(tflite_model, OUT_HEADER)
    print(f"Wrote {OUT_TFLITE} ({len(tflite_model)} bytes)")
    print(f"Wrote {OUT_HEADER}")
    print("Set USE_TFLITE=true in include/config.h and rebuild.")


if __name__ == "__main__":
    main()

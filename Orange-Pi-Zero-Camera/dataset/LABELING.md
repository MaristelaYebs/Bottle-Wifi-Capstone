# Dataset & Labeling Guide

How to build the training dataset for the Bottle Wi-Fi Vendo detector.
Done on the PC (not the Pi). Two classes; EMPTY is taught with negatives.

## Classes

| id | name | meaning | app outcome |
|----|------|---------|-------------|
| 0 | `plastic_bottle` | acceptable PET bottle | BOTTLE → ACCEPT |
| 1 | `reject_object` | any non-PET-bottle object | REJECT |

`EMPTY` (nothing valid) is **not** a labelled class — it is learned from
background/negative images.

## Folder layout (already scaffolded)

```
dataset/
├── incoming/     # auto-collected from the Pi (review these)
├── reviewed/     # your staging area after review
├── train/{images,labels}/
├── val/{images,labels}/
└── test/{images,labels}/
```

## Label format (YOLO)

One `.txt` per image, same basename, in the sibling `labels/` folder. Each line:

```
<class_id> <x_center> <y_center> <width> <height>
```
All values normalised 0–1. A background/negative image has **no** label file (or
an empty one). Recommended tools: Roboflow, LabelImg, CVAT, `label-studio`.

## What to collect (from the project brief §12)

**plastic_bottle (class 0):** clear + coloured, small + large, crushed, with/without
labels, partially visible, rotated, dirty, bright + low light, varied backgrounds.

**reject_object (class 1):** can, glass bottle, cup, paper, plastic bag, phone,
hand, food container, cardboard, metal, other garbage, unknown objects.

**negatives / background (no labels):** empty chute, empty scene, different
lighting, shadows, background clutter. Include a healthy share (~10–20%) so the
model reliably outputs EMPTY when nothing valid is present — this is what keeps
False Acceptance low.

## Splits

Aim ~70% train / ~20% val / ~10% test, stratified across classes and conditions.
Keep the **test** set untouched for honest metrics (Phase 13 validation).

## Workflow

1. Pull `dataset/incoming/` (+ sidecar JSON) from the Pi.
2. Review, correct/label, and move into `train|val|test/{images,labels}/`.
3. `python scripts/train.py`  →  `python scripts/validate.py`  →  `python scripts/export.py`.
4. Copy `models/candidate/<version>/` to the Pi, then `scripts/deploy.py` (Phase 14).

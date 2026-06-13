# Southern Sea Otter Detector

A YOLOv8-based object detection model fine-tuned to identify Southern Sea Otters in images and live video streams.

## Overview

This project fine-tunes a YOLOv8 model on a custom sea otter dataset sourced from Roboflow. Once trained, the model can detect otters in still images or run in real time against a live YouTube stream.

## Scripts

| Script | Description |
|---|---|
| `train.py` | Fine-tunes the base YOLOv8 model on the sea otter dataset |
| `yolo-test.py` | Runs detection on a single image and displays results |
| `yt-test.py` | Pulls a live YouTube stream and runs real-time otter detection |

## Setup

**1. Clone the repo**
```bash
git clone <your-repo-url>
cd otter-detector
```

**2. Create a virtual environment and install dependencies**

*macOS / Linux:*
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r preliminary-model/requirements.txt
```

*Windows (PowerShell):*
```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r preliminary-model\requirements.txt
```

> **Windows note:** If you get an execution policy error running the activate script, run `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser` once, then retry.

**3. Get the dataset**

Download the sea otter dataset from Roboflow and place it in the `dataset/` folder:
- [sea-otters-dslyz-kperf on Roboflow Universe](https://universe.roboflow.com/desiree-torres/sea-otters-dslyz-kperf)
- Export in **YOLOv8 format**

**4. Update paths in `dataset/data.yaml`**

The `train`, `val`, and `test` paths in `data.yaml` use absolute paths — update them to match your local directory.

- *macOS / Linux:* use forward slashes, e.g. `/Users/you/otter-detector/preliminary-model/dataset/...`
- *Windows:* use forward slashes or double backslashes, e.g. `C:/Users/you/otter-detector/preliminary-model/dataset/...`

**5. Get the base model**

Download `yolo26n.pt` (the base YOLOv8 nano model) and place it in the project root, or update `train.py` and `yolo-test.py` to point to `yolov8n.pt` from the ultralytics defaults.

## Training

Run from inside `preliminary-model/`:
```bash
python train.py
```

Trains for 100 epochs at 640px image size. The best weights are saved to `runs/detect/train/weights/best.pt`.

## Running Detection

**On a single image:**
```bash
python yolo-test.py
```
Update the image path inside `yolo-test.py` to point to your image. Use a path format that matches your OS (forward slashes on macOS/Linux; forward slashes or `C:\...` on Windows).

**On a live YouTube stream:**
```bash
python yt-test.py
```
Update the YouTube URL inside `yt-test.py` to your target stream. Press `q` to quit.

> **Windows:** OpenCV's `imshow` window may not render on headless or WSL environments — run natively in PowerShell or Command Prompt for display to work.

## Dataset

- **Source:** [Roboflow Universe](https://universe.roboflow.com/desiree-torres/sea-otters-dslyz-kperf)
- **Images:** 347 annotated images
- **Class:** `Southern Sea Otter` (1 class)
- **Format:** YOLOv8
- **License:** [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)

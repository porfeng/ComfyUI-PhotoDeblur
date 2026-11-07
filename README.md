# ComfyUI-PhotoDeblur

**English** | [简体中文](README_中文.md)

Native **HI-Diff** and **MISCFilter** photo deblurring nodes for ComfyUI. Supports original-resolution tiled processing, overlap blending, optional TTA, and official model downloads.

## Installation

Clone this repository into `ComfyUI/custom_nodes`:

```sh
git clone https://github.com/porfeng/ComfyUI-PhotoDeblur.git
```

Run these commands with **ComfyUI's Python**, from the node folder:

```sh
python -m pip install -r requirements.txt
python -m pip install -r requirements-download.txt
```

For **MISCFilter on NVIDIA / CUDA 12**, also install:

```sh
python -m pip install -r requirements-misc-cu12.txt
```

Optional tone matching needs OpenCV. If `cv2` is missing:

```sh
python -m pip install -r requirements-tone.txt
```

For the Windows portable edition, replace `python` with its `python_embeded/python.exe`. For Desktop, use its environment's Python. Restart ComfyUI after installing.

## Quick start

1. Drag a workflow from [example_workflows](example_workflows) onto the canvas. [ComfyUI-PhotoDeblur.json](example_workflows/ComfyUI-PhotoDeblur.json) includes Chinese and English notes.
2. Select your own image in **Load Image**.
3. Match the downloader's model and variant to the loader, then run.

```text
Download Deblur Models.ready → Model Loader.download_ready
Load Image → Deblur Image (Tiled).image
Model Loader → Deblur Image (Tiled).deblur_model
Deblur Image (Tiled) IMAGE → Save Image
```

The restored IMAGE output connects to Save Image. If weights are already installed, the download connection is optional. To use HI-Diff, connect its loader instead of MISCFilter and select HI-Diff in the downloader.

## Nodes

| Node | Purpose |
| --- | --- |
| Download Deblur Models | Download and verify official weights |
| HI-Diff Model Loader | Select HI-Diff weights and sampling steps |
| MISCFilter Model Loader | Select MISCFilter weights |
| Deblur Image (Tiled) | Restore the image with tiling, blending and optional TTA |
| Match Input Tone (Optional) | Reduce broad color shifts using the original image |

Search for `Photo Deblur` on the canvas to find these nodes.

## Parameters

| Parameter | Suggested starting value / meaning |
| --- | --- |
| variant | `RealBlur_J`; also compare `RealBlur_R` and `GoPro` |
| tile_size | `768`; try `512` when VRAM is limited |
| overlap | `96` at tile 768, or `64` at tile 512; must be smaller than the tile |
| blend | `comfy_linear`; alternatives: `cosine`, `gaussian`, `average` |
| tta | Off initially; averages eight flipped/rotated predictions, roughly 8× model computation |
| seed | `100`; controls HI-Diff's random prior |
| steps | HI-Diff only: `0` selects 16 for J/R and 8 for GoPro |
| repair_invalid | Off normally; on backs up mismatched files before replacing them |
| sigma | Tone matching only: `16`; `0` bypasses correction |

MISCFilter uses a direct network forward pass and has no sampling steps. Larger tiles, more steps and TTA are comparison options; choose by the result on your photo.

## Model weights

Both models offer **RealBlur_J**, **RealBlur_R** and **GoPro**, corresponding to different training datasets/variants. R is a checkpoint variant, not a camera RAW input mode.

The download node uses the authors' Google Drive archives with SHA256 verification. Verified local files are reused. Weights are stored in ComfyUI's configured model directory:

```text
models/
├── HI-Diff/
│   ├── HI_Diff_RealBlur_J/  # net_g.pth, net_le_dm.pth, net_d.pth
│   ├── HI_Diff_RealBlur_R/  # same three filenames
│   └── HI_Diff_GoPro/       # same three filenames
└── MISCFilter/
    ├── RealBlur_J.pth
    ├── RealBlur_R.pth
    └── GoPro.pth
```

## Precision and color

Inference and blending use FP32 RGB/RGBA tensors at the original image size. Use display-referred sRGB images; develop RAW files before loading. The nodes output values in 0–1.

16-bit TIFF/PNG precision depends on the image loader. Standard Save Image writes 8-bit PNG; for 16-bit output, use **Save Image (Advanced) → PNG → 16-bit → sRGB** when available. A blank TIFF thumbnail can be checked with Preview Image.

Tone matching is optional post-processing added by this project. It subtracts the Gaussian-blurred difference between the restored and original images to constrain broad color changes.

## Setup help

- **Missing CuPy:** install `requirements-misc-cu12.txt` in ComfyUI's Python.
- **CUDA / NVRTC error:** check the matching CuPy package and local CUDA Toolkit / `CUDA_PATH`.
- **Missing weights:** use the download node or the directory structure above.
- **Out of memory:** try tile 512, overlap 64 and TTA off.

## Credits and license

- [HI-Diff](https://github.com/zhengchen1999/HI-Diff), NeurIPS 2023 — Apache-2.0.
- [MISCFilter](https://github.com/ChengxuLiu/MISCFilter), CVPR 2024 — MIT.

Node integration code: [MIT](LICENSE). Upstream notices and port changes: [THIRD_PARTY.md](THIRD_PARTY.md). Model weights are downloaded separately from upstream sources.

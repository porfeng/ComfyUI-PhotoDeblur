# ComfyUI-PhotoDeblur

[English](README.md) | **简体中文**

ComfyUI 原生 **HI-Diff** 和 **MISCFilter** 照片去模糊节点，支持原尺寸分块处理、重叠融合、可选 TTA 和官方模型下载。

## 安装

在 `ComfyUI/custom_nodes` 中克隆仓库：

```sh
git clone https://github.com/porfeng/ComfyUI-PhotoDeblur.git
```

进入节点目录，用 **ComfyUI 实际使用的 Python** 安装：

```sh
python -m pip install -r requirements.txt
python -m pip install -r requirements-download.txt
```

使用 **MISCFilter（NVIDIA / CUDA 12）** 还需安装：

```sh
python -m pip install -r requirements-misc-cu12.txt
```

可选色调修正需要 OpenCV。如果提示缺少 `cv2`，再安装：

```sh
python -m pip install -r requirements-tone.txt
```

Windows 便携版将 `python` 换成自带的 `python_embeded/python.exe` 路径；Desktop 版使用其虚拟环境中的 Python。安装后重启 ComfyUI。

## 快速使用

1. 将 [example_workflows](example_workflows) 中的工作流拖进画布。[ComfyUI-PhotoDeblur.json](example_workflows/ComfyUI-PhotoDeblur.json) 附有中英文说明。
2. 在「加载图像」中选择自己的图片。
3. 下载器和加载器选择相同的模型、权重版本，然后运行。

```text
Download Deblur Models.ready → 模型加载器.download_ready
加载图像 → Deblur Image (Tiled).image
模型加载器 → Deblur Image (Tiled).deblur_model
Deblur Image (Tiled) 的图像输出 → 保存图像
```

权重已在本地时，可以不接下载节点。切换 HI-Diff 时，连接 HI-Diff 加载器，并将下载器的 model 改为 HI-Diff。

## 节点

| 节点 | 用途 |
| --- | --- |
| Download Deblur Models | 下载、校验官方权重 |
| HI-Diff Model Loader | 选择 HI-Diff 权重和采样步数 |
| MISCFilter Model Loader | 选择 MISCFilter 权重 |
| Deblur Image (Tiled) | 去模糊、分块融合、可选 TTA |
| Match Input Tone (Optional) | 以原图为参照，减轻大范围色调偏差 |

画布上搜索 `Photo Deblur` 即可找到这些节点。

## 参数

| 参数 | 起始建议与含义 |
| --- | --- |
| variant | 先用 `RealBlur_J`，再对比 `RealBlur_R`、`GoPro` |
| tile_size | `768`；显存不足改 `512` |
| overlap | 768 分块配 `96`，512 分块配 `64`；必须小于分块尺寸 |
| blend | 默认 `comfy_linear`；另有 `cosine`、`gaussian`、`average` |
| tta | 先关闭；开启后进行 8 种翻转、旋转推理并平均，模型计算量约 8 倍 |
| seed | `100`；控制 HI-Diff 的随机先验 |
| steps | 仅 HI-Diff 使用；`0` 自动选 J/R 的 16 步、GoPro 的 8 步 |
| repair_invalid | 通常关闭；开启后先备份校验不匹配的文件，再下载替换 |
| sigma | 仅色调修正使用；建议 `16`，`0` 为关闭 |

MISCFilter 一次网络前向即可恢复，没有采样步数。分块大小、步数和 TTA 按实际照片效果选择。

## 权重

两个模型均提供 **RealBlur_J、RealBlur_R、GoPro**，代表不同训练数据集或训练版本。R 是权重版本，不是直接读取相机 RAW 的模式。

下载节点使用作者提供的 Google Drive 资源，并校验 SHA256；已有且校验通过的文件直接复用。权重保存到 ComfyUI 配置的 models 目录：

```text
models/
├── HI-Diff/
│   ├── HI_Diff_RealBlur_J/  # net_g.pth、net_le_dm.pth、net_d.pth
│   ├── HI_Diff_RealBlur_R/  # 同样三个文件名
│   └── HI_Diff_GoPro/       # 同样三个文件名
└── MISCFilter/
    ├── RealBlur_J.pth
    ├── RealBlur_R.pth
    └── GoPro.pth
```

## 精度与颜色

模型推理和融合使用 FP32，接收 RGB/RGBA 图像，保持原始尺寸。输入使用普通显示用 sRGB 图像；RAW 先在照片软件中显影。节点输出范围为 0～1。

16 位 TIFF/PNG 的读取精度取决于加载图像节点。普通「保存图像」输出 8 位 PNG；需要 16 位时，可使用支持该功能的「保存图像（高级）→ PNG → 16-bit → sRGB」。TIFF 缩略图空白时，可以连接「预览图像」检查。

色调修正是本项目增加的可选后处理：从恢复图中减去经过高斯模糊的「恢复图与原图之差」，约束大范围颜色变化。

## 常见问题

- **缺少 CuPy：**在 ComfyUI 的 Python 中安装 `requirements-misc-cu12.txt`。
- **CUDA / NVRTC 报错：**检查 CuPy 与 CUDA 版本是否匹配，以及 CUDA Toolkit、`CUDA_PATH`。
- **缺少权重：**使用下载节点，或按上面的目录放置文件。
- **显存不足：**尝试 512 分块、64 重叠、关闭 TTA。

## 致谢与许可

- [HI-Diff](https://github.com/zhengchen1999/HI-Diff)，NeurIPS 2023，Apache-2.0。
- [MISCFilter](https://github.com/ChengxuLiu/MISCFilter)，CVPR 2024，MIT。

节点集成代码采用 [MIT](LICENSE) 许可。上游许可与移植修改见 [THIRD_PARTY.md](THIRD_PARTY.md)。模型权重通过上游来源单独下载。

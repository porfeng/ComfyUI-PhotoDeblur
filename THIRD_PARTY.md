# Credits and upstream licenses

- **HI-Diff** — Zheng Chen et al., NeurIPS 2023. [Source](https://github.com/zhengchen1999/HI-Diff), [Apache-2.0](licenses/HI-Diff-APACHE-2.0.txt).
- **MISCFilter** — Chengxu Liu et al., CVPR 2024. [Source](https://github.com/ChengxuLiu/MISCFilter), [MIT](licenses/MISCFilter-MIT.txt).

Original source revisions and hashes are recorded in `vendor_manifest.json`. Vendored code retains its upstream license. New node integration code is provided under [MIT](LICENSE).

## Port changes

- Private relative imports and removal of BasicSR registry dependencies.
- HI-Diff GoPro sampling extracted into an inference-only module.
- MISCFilter CuPy launcher updated to RawKernel with a PyTorch ExternalStream; CUDA arithmetic retained.
- ComfyUI ModelPatcher integration, tiled inference, optional D4 TTA and tone matching.

Model weights are downloaded from the authors' official sources and are not included in this repository. See the upstream projects for checkpoint terms.

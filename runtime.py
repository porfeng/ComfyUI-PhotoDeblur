"""Private, inference-only adapters. No BasicSR, subprocess, or sys.path edits."""
from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
import os
import torch

_dll_handles = []


def _state(checkpoint, key):
    state = checkpoint.get(key, checkpoint)
    return {k.removeprefix('module.'): v for k, v in state.items()}


class HIImageModel(torch.nn.Module):
    def __init__(self, variant, checkpoints, steps=0):
        super().__init__()
        if variant not in ('RealBlur_J', 'RealBlur_R', 'GoPro'):
            raise ValueError(f'Unsupported HI-Diff variant: {variant}')
        from .vendor.hi.Transformer_arch import Transformer
        from .vendor.hi.latent_encoder_arch import latent_encoder_lrelu, latent_encoder_gelu
        is_gopro = variant == 'GoPro'
        self.steps = steps or (8 if is_gopro else 16)
        self.net_g = Transformer(
            inp_channels=3, out_channels=3, dim=48, num_blocks=[3, 5, 6, 6],
            num_refinement_blocks=4, heads=[1, 2, 4, 8],
            ffn_expansion_factor=2.66 if is_gopro else 2,
            bias=False, LayerNorm_type='WithBias', dual_pixel_task=False,
            embed_dim=64, group=4)
        encoder = latent_encoder_gelu if is_gopro else latent_encoder_lrelu
        condition = encoder(in_chans=3, embed_dim=64, block_num=6 if is_gopro else 5,
                            group=4, stage=2, patch_expansion=0.5, channel_expansion=4)
        if is_gopro:
            from .vendor.hi.denoising_arch import denoising
            from .vendor.hi.gopro_prior import GoProPrior
            denoise = denoising(in_channel=256, out_channel=256, inner_channel=512,
                                block_num=4, group=4, patch_expansion=0.5, channel_expansion=2)
        else:
            from .vendor.hi.denoising_simple_arch import simple_denoise
            from .vendor.hi.ddpm import DDPM
            denoise = simple_denoise(n_feats=64, n_denoise_res=4)
        self.net_g.load_state_dict(_state(checkpoints['g'], 'params'), strict=True)
        condition.load_state_dict(_state(checkpoints['le_dm'], 'params'), strict=True)
        denoise.load_state_dict(_state(checkpoints['d'], 'params'), strict=True)
        if is_gopro:
            self.prior = GoProPrior(condition, denoise, self.steps)
        else:
            self.prior = DDPM(denoise=denoise, condition=condition, n_feats=64, group=4,
                              linear_start=0.1, linear_end=0.99, timesteps=self.steps)
        self.requires_grad_(False).eval()

    def forward(self, image):
        return self.net_g(image, self.prior(image))


def configure_misc_kernel():
    """Use current CuPy's launcher; retain upstream CUDA arithmetic verbatim."""
    cuda_path = os.environ.get('CUDA_PATH')
    if cuda_path and hasattr(os, 'add_dll_directory'):
        for suffix in ['bin', 'bin/x64']:
            directory = Path(cuda_path) / suffix
            if directory.is_dir():
                _dll_handles.append(os.add_dll_directory(str(directory)))
    try:
        import cupy
        import numpy as np
        from .vendor.misc import MISCKernel_cuda as kernels
    except ImportError as exc:
        raise RuntimeError(
            'MISCFilter needs CuPy in the SAME Python as ComfyUI. '
            'For CUDA 12 install requirements-misc-cu12.txt; see README_中文.md. '
            'HI-Diff does not need CuPy.') from exc

    @cupy.memoize(for_each_device=True)
    def launch(name, source):
        kernel = cupy.RawKernel(source, name, options=('--std=c++11',))

        def call(*, grid, block, args, stream):
            packed = (np.int32(args[0]), *(np.uint64(ptr) for ptr in args[1:]))
            kernel(grid, block, packed, stream=cupy.cuda.ExternalStream(stream.ptr))
        return call

    kernels.cupy_launch = launch


class MISCImageModel(torch.nn.Module):
    def __init__(self, checkpoint):
        super().__init__()
        configure_misc_kernel()
        from .vendor.misc.MISCFilterNet import MISCKernelNet
        self.net = MISCKernelNet(inference=False)
        self.net.load_state_dict(_state(checkpoint, 'state_dict'), strict=True)
        self.requires_grad_(False).eval()

    def forward(self, image):
        predictions, _ = self.net(image)
        return predictions[0]


def pad_multiple8(image):
    """OpenCV BORDER_REFLECT_101, including images smaller than the padding."""
    h, w = image.shape[-2:]
    target_h, target_w = max(8, (h + 7) // 8 * 8), max(8, (w + 7) // 8 * 8)
    for axis, length, target in [(-2, h, target_h), (-1, w, target_w)]:
        if length == target:
            continue
        indices = torch.arange(target, device=image.device)
        if length == 1:
            indices.zero_()
        else:
            period = 2 * (length - 1)
            indices = indices.remainder(period)
            indices = torch.minimum(indices, period - indices)
        image = image.index_select(axis, indices)
    return image.contiguous()


def transform(image, mode, inverse=False):
    rotation = mode // 2
    flipped = mode % 2
    if inverse:
        image = torch.rot90(image, -rotation, (-2, -1))
        return image.flip(-2) if flipped else image
    if flipped:
        image = image.flip(-2)
    return torch.rot90(image, rotation, (-2, -1))


def predict(model, image, seed, tta, device, interrupt=lambda: None, progress=lambda: None):
    """One tile at a time, float32 throughout; isolate HI-Diff's random state."""
    image = image.to(device=device, dtype=torch.float32)
    accum = torch.zeros_like(image, device='cpu')
    index = device.index if device.index is not None else torch.cuda.current_device() if device.type == 'cuda' else None
    devices = [index] if device.type == 'cuda' else []
    for mode in range(8 if tta else 1):
        interrupt()
        aug = transform(image, mode).contiguous()
        h, w = aug.shape[-2:]
        context = torch.cuda.device(device) if device.type == 'cuda' else nullcontext()
        with context, torch.random.fork_rng(devices=devices), torch.autocast(device.type, enabled=False):
            local_seed = (int(seed) + mode * 1000003) % (2**63 - 1)
            torch.random.default_generator.manual_seed(local_seed)
            if device.type == 'cuda':
                torch.cuda.default_generators[index].manual_seed(local_seed)
            output = model(pad_multiple8(aug))[..., :h, :w].clamp(0, 1)
        if not torch.isfinite(output).all().item():
            raise RuntimeError('Non-finite model output; try a smaller tile. No image returned.')
        accum.add_(transform(output, mode, inverse=True).to('cpu', torch.float32))
        del aug, output
        progress()
    return accum / (8 if tta else 1)

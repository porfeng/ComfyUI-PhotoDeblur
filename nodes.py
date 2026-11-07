from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path

import torch
import folder_paths
import comfy.model_management as mm
import comfy.model_patcher as mp
import comfy.utils as cu

from .runtime import HIImageModel, MISCImageModel, predict
from .tiling import BLENDS, starts, weighted_tiles
from .downloads import ensure_weights

VARIANTS = ['RealBlur_J', 'RealBlur_R', 'GoPro']

# Keep model files in ComfyUI's configured models directory, separate from code.
for key, directory in [('photo_deblur_hi', 'HI-Diff'), ('photo_deblur_misc', 'MISCFilter')]:
    folder_paths.add_model_folder_path(key, str(Path(folder_paths.models_dir) / directory))


def resolve_hi(variant):
    relative = f'HI_Diff_{variant}'
    for root in folder_paths.get_folder_paths('photo_deblur_hi'):
        paths = {name: Path(root) / relative / f'net_{name}.pth' for name in ['g', 'le_dm', 'd']}
        if all(p.is_file() for p in paths.values()):
            return paths
    raise FileNotFoundError(
        f'HI-Diff: missing {relative}/net_g.pth, net_le_dm.pth, net_d.pth. '
        f'Put the complete folder in ComfyUI/models/HI-Diff/. '
        f'See README_中文.md.')


def resolve_misc(variant):
    for root in folder_paths.get_folder_paths('photo_deblur_misc'):
        path = Path(root) / f'{variant}.pth'
        if path.is_file():
            return path
    raise FileNotFoundError(f'MISCFilter: missing {variant}.pth in ComfyUI/models/MISCFilter/. '
                            'See README_中文.md.')


def fingerprint(paths):
    return tuple((str(p.resolve()), p.stat().st_size, p.stat().st_mtime_ns) for p in paths)


def ready_files(ready, family, variant):
    if ready['family'] != family or ready['variant'] != variant:
        raise ValueError(f'Downloader selected {ready["family"]}/{ready["variant"]}, '
                         f'but this loader selected {family}/{variant}. '
                         'Set both nodes to the same model/variant, or disconnect download_ready '
                         'to load weights already present in the model directory.')
    return ready['files']


class PhotoDeblurDownloader:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {
            'model': (['HI-Diff', 'MISCFilter'], {'default': 'HI-Diff'}),
            'variant': (VARIANTS, {'default': 'RealBlur_J'}),
            'repair_invalid': ('BOOLEAN', {'default': False,
                'tooltip': 'Back up mismatched existing files before replacing them with verified official weights.'}),
        }}

    RETURN_TYPES = ('PHOTO_DEBLUR_READY', 'STRING', 'STRING')
    RETURN_NAMES = ('ready', 'model_dir', 'status')
    FUNCTION = 'download'
    CATEGORY = 'Photo Deblur/download'
    OUTPUT_NODE = True

    @classmethod
    def IS_CHANGED(cls, model, variant, repair_invalid):
        # Recheck missing/changed local files even on a repeated queue operation.
        return float('nan')

    def download(self, model, variant, repair_invalid):
        pbar = cu.ProgressBar(1000)
        phases = {'checking': (0, 100), 'downloading': (100, 700),
                  'extracting': (800, 190), 'complete': (1000, 0)}

        def progress(phase, done, total):
            start, span = phases[phase]
            pbar.update_absolute(start + int(span * min(done / max(total, 1), 1)), 1000)

        result = ensure_weights(model, variant, folder_paths.models_dir, repair_invalid,
                                progress=progress, interrupt=mm.throw_exception_if_processing_interrupted)
        return {'ui': {'text': [result['status'], result['model_dir']]},
                'result': (result, result['model_dir'], result['status'])}


@dataclass
class DeblurModel:
    patcher: object
    family: str
    variant: str


def wrap(model, family, variant):
    # Classic ModelPatcher supports ordinary torch modules and avoids dynamic
    # casting of MISC's custom float32-only CUDA kernel.
    patcher = mp.ModelPatcher(model.eval(), load_device=mm.get_torch_device(),
                             offload_device=torch.device('cpu'))
    return DeblurModel(patcher, family, variant)


class PhotoDeblurHILoader:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {
            'variant': (VARIANTS, {'default': 'RealBlur_J'}),
            'steps': ('INT', {'default': 0, 'min': 0, 'max': 64,
                              'tooltip': '0 = official default: RealBlur-J/R 16, GoPro 8. More is not necessarily better.'}),
        }, 'optional': {'download_ready': ('PHOTO_DEBLUR_READY',)}}

    RETURN_TYPES = ('PHOTO_DEBLUR_MODEL',)
    RETURN_NAMES = ('deblur_model',)
    FUNCTION = 'load'
    CATEGORY = 'Photo Deblur/loaders'

    @classmethod
    def IS_CHANGED(cls, variant, steps, download_ready=None):
        try:
            return fingerprint(resolve_hi(variant).values())
        except FileNotFoundError:
            return 'missing'

    def load(self, variant, steps, download_ready=None):
        if download_ready is None:
            paths = resolve_hi(variant)
        else:
            known = ready_files(download_ready, 'HI-Diff', variant)
            paths = {name: Path(known[f'net_{name}.pth']) for name in ['g', 'le_dm', 'd']}
        checkpoints = {k: cu.load_torch_file(str(path), safe_load=True) for k, path in paths.items()}
        with torch.random.fork_rng(devices=[]):
            model = HIImageModel(variant, checkpoints, steps)
        return (wrap(model, 'HI-Diff', variant),)


class PhotoDeblurMISCLoader:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'variant': (VARIANTS, {'default': 'RealBlur_J'})},
                'optional': {'download_ready': ('PHOTO_DEBLUR_READY',)}}

    RETURN_TYPES = ('PHOTO_DEBLUR_MODEL',)
    RETURN_NAMES = ('deblur_model',)
    FUNCTION = 'load'
    CATEGORY = 'Photo Deblur/loaders'

    @classmethod
    def IS_CHANGED(cls, variant, download_ready=None):
        try:
            return fingerprint([resolve_misc(variant)])
        except FileNotFoundError:
            return 'missing'

    def load(self, variant, download_ready=None):
        if mm.get_torch_device().type != 'cuda':
            raise RuntimeError('This MISCFilter adapter requires an NVIDIA CUDA GPU. HI-Diff also supports CPU.')
        path = (resolve_misc(variant) if download_ready is None
                else ready_files(download_ready, 'MISCFilter', variant)[f'{variant}.pth'])
        checkpoint = cu.load_torch_file(str(path), safe_load=True)
        with torch.random.fork_rng(devices=[]):
            model = MISCImageModel(checkpoint)
        return (wrap(model, 'MISCFilter', variant),)


class PhotoDeblurTiled:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {
            'image': ('IMAGE',),
            'deblur_model': ('PHOTO_DEBLUR_MODEL',),
            'tile_size': ('INT', {'default': 768, 'min': 64, 'max': 4096, 'step': 8}),
            'overlap': ('INT', {'default': 96, 'min': 0, 'max': 2048, 'step': 8}),
            'blend': (BLENDS, {'default': 'comfy_linear'}),
            'tta': ('BOOLEAN', {'default': False, 'tooltip': '8 transforms, about 8x model work; can soften some details.'}),
            'seed': ('INT', {'default': 100, 'min': 0, 'max': 0xffffffffffffffff,
                             'control_after_generate': False}),
        }}

    RETURN_TYPES = ('IMAGE',)
    FUNCTION = 'restore'
    CATEGORY = 'Photo Deblur'

    def restore(self, image, deblur_model, tile_size, overlap, blend, tta, seed):
        if image.ndim != 4 or image.shape[-1] not in (3, 4) or min(image.shape[:3]) < 1:
            raise ValueError('Expected a nonempty ComfyUI RGB/RGBA IMAGE [batch,height,width,channels].')
        if tile_size < 64 or tile_size % 8 or not 0 <= overlap < tile_size or blend not in BLENDS:
            raise ValueError('tile_size must be a multiple of 8 >=64; overlap must be smaller than tile_size.')
        if not torch.isfinite(image).all().item():
            raise ValueError('Input contains non-finite pixels.')
        device = deblur_model.patcher.load_device
        if deblur_model.family == 'MISCFilter' and device.type != 'cuda':
            raise RuntimeError('MISCFilter requires CUDA.')
        # Keep the original and full-size accumulators on CPU. Only a patch is
        # transferred to GPU, unlike moving the entire photo before tiling.
        original = image.detach().to('cpu', torch.float32)
        alpha = original[..., 3:4] if original.shape[-1] == 4 else None
        samples = original[..., :3].movedim(-1, 1).contiguous()
        h, w = samples.shape[-2:]
        if blend == 'comfy_linear':
            tiles = cu.get_tiled_scale_steps(w, h, tile_x=tile_size, tile_y=tile_size, overlap=overlap)
        else:
            tiles = len(starts(w, tile_size, overlap)) * len(starts(h, tile_size, overlap))
        pbar = cu.ProgressBar(samples.shape[0] * tiles * (8 if tta else 1))
        counter = 0

        def function(patch):
            nonlocal counter
            result = predict(deblur_model.patcher.model, patch, seed + counter, tta, device,
                             interrupt=mm.throw_exception_if_processing_interrupted,
                             progress=lambda: pbar.update(1))
            counter += 1
            return result

        # Conservative empirical activation estimate; weights are counted by
        # Comfy itself. Never permit low-VRAM layer casting for the CUDA kernel.
        side = min(tile_size, max(h, w))
        factor = 8500 if deblur_model.family == 'HI-Diff' else 4500
        memory_required = side * side * factor + 256 * 1024**2
        mm.throw_exception_if_processing_interrupted()
        logging.info('PhotoDeblur: %s/%s, %dx%d, tile %d overlap %d, %s, TTA=%s',
                     deblur_model.family, deblur_model.variant, w, h, tile_size, overlap, blend, tta)
        mm.load_models_gpu([deblur_model.patcher], memory_required=memory_required, force_full_load=True)
        with torch.inference_mode():
            try:
                if blend == 'comfy_linear':
                    result = cu.tiled_scale(samples, function, tile_x=tile_size, tile_y=tile_size,
                                            overlap=overlap, upscale_amount=1, output_device='cpu')
                else:
                    result = weighted_tiles(samples, function, tile_size, overlap, blend,
                                            mm.throw_exception_if_processing_interrupted)
            except torch.cuda.OutOfMemoryError as exc:
                raise RuntimeError('PhotoDeblur ran out of GPU memory. Reduce tile_size to 512; '
                                   'the node keeps your chosen settings and does not silently shrink tiles.') from exc
        result = result.movedim(1, -1).clamp(0, 1)
        if alpha is not None:
            result = torch.cat([result, alpha], dim=-1)
        return (result,)


class PhotoDeblurToneMatch:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'original': ('IMAGE',), 'restored': ('IMAGE',),
                             'sigma': ('FLOAT', {'default': 16.0, 'min': 0.0, 'max': 128.0, 'step': 1.0})}}

    RETURN_TYPES = ('IMAGE',)
    FUNCTION = 'match'
    CATEGORY = 'Photo Deblur/optional'

    def match(self, original, restored, sigma):
        try:
            import cv2
        except ImportError as exc:
            raise RuntimeError('Optional tone matching needs OpenCV. Install requirements-tone.txt '
                               'into ComfyUI Python, or bypass this node.') from exc
        import numpy as np
        if original.shape != restored.shape:
            raise ValueError('Original and restored images must have identical dimensions.')
        output = restored.detach().to('cpu', torch.float32).clone()
        if sigma == 0:
            return (output,)
        reference = original.detach().to('cpu', torch.float32)
        for b in range(output.shape[0]):
            mm.throw_exception_if_processing_interrupted()
            residual = (output[b, ..., :3] - reference[b, ..., :3]).numpy()
            broad = cv2.GaussianBlur(residual, (0, 0), float(sigma), borderType=cv2.BORDER_REFLECT_101)
            output[b, ..., :3].sub_(torch.from_numpy(np.ascontiguousarray(broad)))
        return (output.clamp_(0, 1),)


NODE_CLASS_MAPPINGS = {
    'PhotoDeblurDownloader': PhotoDeblurDownloader,
    'PhotoDeblurHILoader': PhotoDeblurHILoader,
    'PhotoDeblurMISCLoader': PhotoDeblurMISCLoader,
    'PhotoDeblurTiled': PhotoDeblurTiled,
    'PhotoDeblurToneMatch': PhotoDeblurToneMatch,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    'PhotoDeblurDownloader': 'Download Deblur Models · Photo Deblur',
    'PhotoDeblurHILoader': 'HI-Diff Model Loader · Photo Deblur',
    'PhotoDeblurMISCLoader': 'MISCFilter Model Loader · Photo Deblur',
    'PhotoDeblurTiled': 'Deblur Image (Tiled) · Photo Deblur',
    'PhotoDeblurToneMatch': 'Match Input Tone (Optional) · Photo Deblur',
}

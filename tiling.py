"""Additional CPU blending windows; default tiling uses comfy.utils directly."""
import math
import torch

BLENDS = ['comfy_linear', 'cosine', 'gaussian', 'average']


def starts(length, tile, overlap):
    if length <= tile:
        return [0]
    positions = list(range(0, length - tile + 1, tile - overlap))
    if positions[-1] != length - tile:
        positions.append(length - tile)
    return positions


def axis_weight(positions, index, length, tile, method):
    pos = positions[index]
    size = min(tile, length - pos)
    weight = torch.ones(size, dtype=torch.float32)
    if method == 'average':
        return weight
    for edge, overlap in [('left', positions[index - 1] + tile - pos if index else 0),
                           ('right', pos + size - positions[index + 1] if index + 1 < len(positions) else 0)]:
        if overlap <= 0:
            continue
        t = torch.arange(1, overlap + 1, dtype=torch.float32) / (overlap + 1)
        if method == 'cosine':
            ramp = 0.5 - 0.5 * torch.cos(math.pi * t)
        elif method == 'gaussian':
            ramp = torch.exp(-0.5 * ((1 - t) / 0.35) ** 2)
        else:
            raise ValueError(f'Unknown blend method: {method}')
        if edge == 'left':
            weight[:overlap] *= ramp
        else:
            weight[-overlap:] *= ramp.flip(0)
    return weight


def weighted_tiles(image, function, tile, overlap, method, interrupt):
    batch, channels, height, width = image.shape
    xs, ys = starts(width, tile, overlap), starts(height, tile, overlap)
    output = torch.empty_like(image, dtype=torch.float32, device='cpu')
    for b in range(batch):
        accum = torch.zeros((1, channels, height, width), dtype=torch.float32)
        den = torch.zeros((1, 1, height, width), dtype=torch.float32)
        for iy, y in enumerate(ys):
            wy = axis_weight(ys, iy, height, tile, method)
            for ix, x in enumerate(xs):
                interrupt()
                wx = axis_weight(xs, ix, width, tile, method)
                patch = image[b:b+1, :, y:y+tile, x:x+tile]
                result = function(patch)
                weight = wy[:, None] * wx[None, :]
                h, w = result.shape[-2:]
                accum[..., y:y+h, x:x+w].add_(result * weight)
                den[..., y:y+h, x:x+w].add_(weight)
        output[b:b+1] = accum / den
    return output

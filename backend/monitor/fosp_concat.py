"""E3 inference from the project's FoSp-Temporal training implementation (0b20b39)."""
import torch
from torch import nn
from torch.nn import functional as F


class Arbitration(nn.Module):
    """Checkpoint-compatible head. E3 leaves arbitration inactive in its forward pass."""

    def __init__(self):
        super().__init__()
        self.net = nn.Sequential(
            nn.Conv2d(6, 32, 3, padding=1), nn.ReLU(inplace=True),
            nn.Conv2d(32, 32, 3, padding=1), nn.ReLU(inplace=True),
            nn.Conv2d(32, 2, 1),
        )


class FoSpConcat(nn.Module):
    def __init__(self, segmentor):
        super().__init__()
        self.seg = segmentor
        self.head = segmentor.decode_head
        stem = next(module for module in segmentor.backbone.modules()
                    if isinstance(module, nn.Conv2d) and module.in_channels == 3)
        self.extra_stem = nn.Conv2d(
            3, stem.out_channels, stem.kernel_size, stem.stride, stem.padding, stem.dilation, bias=False,
        )
        self.arbitration = nn.ModuleList(Arbitration() for _ in self.head.in_channels)
        self.register_buffer('mean', torch.tensor([123.675, 116.28, 103.53]).view(1, 3, 1, 1), persistent=False)
        self.register_buffer('std', torch.tensor([58.395, 57.12, 57.375]).view(1, 3, 1, 1), persistent=False)
        self._reference = None
        stem.register_forward_hook(self._add_reference)

    def _add_reference(self, _module, _inputs, output):
        if self._reference is not None:
            return output + self.extra_stem(self._reference)
        return output

    def forward(self, image, reference=None):
        head = self.head
        self._reference = reference
        try:
            features = head._transform_inputs(self.seg.extract_feat(image))
        finally:
            self._reference = None
        focus, *_ = head.BCG(features)
        mask = ((1 - focus) >= head.focus_threshold).float()
        # Training uses a zero MAT latent and denormalizes the current image to BGR.
        mat_image = (image * self.std + self.mean)[:, [2, 1, 0]] / 127.5 - 1.0
        z = image.new_zeros((image.shape[0], head.G.z_dim))
        label = image.new_zeros((image.shape[0], head.G.c_dim))
        inpaint = head.generate_images(head.G, mat_image, mask, z, label, 'none')
        original = head.generate_images(head.G, mat_image, torch.ones_like(mask), z, label, 'none')

        combined = None
        for index, (feature, hidden, visible) in enumerate(zip(reversed(features), inpaint, original)):
            foreground = head.foreground_channel_convs[index]((hidden - visible).abs() * 10)
            foreground = F.interpolate(foreground, size=feature.shape[2:], mode=head.interpolate_mode,
                                       align_corners=head.align_corners)
            parts = [head.add_query(feature, focus), foreground]
            if combined is not None:
                parts.append(F.interpolate(combined, size=feature.shape[2:], mode=head.interpolate_mode,
                                           align_corners=head.align_corners))
            combined = head.fusion_foreground_inputs_conv[index](torch.cat(parts, dim=1))
        return head.cls_fusion_seg(head.fusion_final_conv(combined))

"""
csrnet/models.py
================
CSRNet — "Dilated Convolutional Neural Networks for Understanding the Highly
Congested Scenes" (Li, Zhang, Chen — CVPR 2018).

Kept in the same style as dm_count/models.py so it can be dropped into the
existing loading / checkpoint code with minimal changes:

  * `csrnet(pretrained=True)` returns a ready-to-train nn.Module
  * forward(x) -> single-channel density map at 1/8 input resolution
    (no upsampling inside the network — CSRNet predicts a coarser map than
    DM-Count on purpose; the fusion layer resizes both maps to a common
    resolution before combining them, see fusion/models.py)

Output convention: same as dm_count — the raw density map `mu`. Total count
for an image = mu.sum(). We also return an L1-normalized copy `mu_normed`
purely so the two models present an identical (mu, mu_normed) tuple to the
rest of the codebase (infer.py already does `out[0] if isinstance(out, (tuple,
list)) else out`).
"""

import torch
import torch.nn as nn
import torch.utils.model_zoo as model_zoo

__all__ = ["CSRNet", "csrnet"]

model_urls = {
    "vgg16": "https://download.pytorch.org/models/vgg16-397923af.pth",
}

# Front-end: first 10 conv layers of VGG16 (through conv4_3), stride 8
frontend_cfg = [64, 64, "M", 128, 128, "M", 256, 256, 256, "M", 512, 512, 512]

# Back-end: dilated convolutions (dilation=2) that keep spatial resolution
backend_cfg = [512, 512, 512, 256, 128, 64]


def make_layers(cfg, in_channels=3, batch_norm=False, dilation=False):
    d_rate = 2 if dilation else 1
    layers = []
    for v in cfg:
        if v == "M":
            layers += [nn.MaxPool2d(kernel_size=2, stride=2)]
        else:
            conv2d = nn.Conv2d(in_channels, v, kernel_size=3, padding=d_rate, dilation=d_rate)
            if batch_norm:
                layers += [conv2d, nn.BatchNorm2d(v), nn.ReLU(inplace=True)]
            else:
                layers += [conv2d, nn.ReLU(inplace=True)]
            in_channels = v
    return nn.Sequential(*layers)


class CSRNet(nn.Module):
    def __init__(self, load_vgg_weights=True):
        super(CSRNet, self).__init__()
        self.frontend = make_layers(frontend_cfg)
        self.backend = make_layers(backend_cfg, in_channels=512, dilation=True)
        self.output_layer = nn.Conv2d(64, 1, kernel_size=1)

        if load_vgg_weights:
            self._init_frontend_from_vgg16()
        else:
            self._init_weights()

    def _init_frontend_from_vgg16(self):
        """Copy the first 10 conv layers of a torchvision-style VGG16 into
        the frontend, then random-init the dilated backend."""
        try:
            vgg16_sd = model_zoo.load_url(model_urls["vgg16"])
            vgg16_features_sd = {
                k.replace("features.", ""): v
                for k, v in vgg16_sd.items()
                if k.startswith("features.")
            }
            own_sd = self.frontend.state_dict()
            matched = {k: v for k, v in vgg16_features_sd.items() if k in own_sd and v.shape == own_sd[k].shape}
            own_sd.update(matched)
            self.frontend.load_state_dict(own_sd)
            print(f"[CSRNet] Initialized frontend from ImageNet VGG16 ({len(matched)}/{len(own_sd)} tensors matched).")
        except Exception as e:
            print(f"[CSRNet] WARNING: could not download VGG16 weights ({e}); using random init.")
        self._init_weights(backend_only=True)

    def _init_weights(self, backend_only=False):
        modules = [self.backend, self.output_layer] if backend_only else self.modules()
        for m in (modules if backend_only else self.modules()):
            layer = m if backend_only else m
            if isinstance(layer, nn.Conv2d):
                nn.init.normal_(layer.weight, std=0.01)
                if layer.bias is not None:
                    nn.init.constant_(layer.bias, 0)
            elif isinstance(layer, nn.BatchNorm2d):
                nn.init.constant_(layer.weight, 1)
                nn.init.constant_(layer.bias, 0)

    def forward(self, x):
        x = self.frontend(x)
        x = self.backend(x)
        mu = self.output_layer(x)
        mu = torch.relu(mu)  # densities are non-negative
        B = mu.size(0)
        mu_sum = mu.view(B, -1).sum(1).view(B, 1, 1, 1)
        mu_normed = mu / (mu_sum + 1e-6)
        return mu, mu_normed


def csrnet(pretrained=True):
    """Build a CSRNet. `pretrained` here means "initialize the VGG16
    frontend from ImageNet weights" (matches the `vgg19(pretrained=...)`
    signature used by dm_count so both models load the same way)."""
    return CSRNet(load_vgg_weights=pretrained)

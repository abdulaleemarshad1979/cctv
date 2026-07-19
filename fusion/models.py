"""
fusion/models.py
================
Fusion layer that combines DM-Count (VGG19, dm_count/models.py) and CSRNet
(VGG16 + dilated convs, csrnet/models.py) into a single crowd-density
estimator.

Why fuse these two specifically
--------------------------------
DM-Count (trained with Optimal-Transport loss) and CSRNet (trained with
pixel-wise MSE against a Gaussian-smoothed density map) make *different*
systematic errors:

  * CSRNet's dilated back-end keeps a large receptive field at 1/8 resolution
    and tends to do well on very dense, fairly uniform crowds (its native
    territory — ShanghaiTech Part A), but it can over-smooth sparse scenes
    and is sensitive to perspective/scale changes because it has no explicit
    multi-scale mechanism.
  * DM-Count's OT loss avoids the Gaussian-kernel assumption entirely, which
    generally makes it more robust on sparse/medium-density scenes and
    across the wide scale range you get from a drone (near vs. far crowd),
    but it can be noisier on extremely dense, occluded crowds than CSRNet.

Because the two networks are complementary rather than redundant, a learned
per-pixel fusion tends to beat either model alone — this is the standard
motivation for model-fusion / ensembling in the crowd-counting literature
(e.g. multi-branch fusion architectures such as MCNN/CP-CNN family, and later
context/attention-fusion counting networks). Here we implement the simplest
version of that idea: a small **gated fusion layer**.

Design
------
1. Run both backbones independently -> two single-channel density maps.
   With this repo's DM-Count (4 max-pools, then one internal 2x upsample)
   and CSRNet (3 max-pools, no upsample), both land at the same 1/8 input
   resolution, so no resize is actually needed here — but we still resize
   defensively (see step 2) so the fusion head keeps working if either
   backbone is later swapped for a differently-strided variant.

2. Resize both to a common resolution with *mass-preserving* interpolation
   (bilinear + rescale by the area ratio) so neither model's total count is
   silently inflated/deflated by the resize. This is a no-op whenever the
   two maps already match, which is the normal case here.

3. Feed `[dm_map, csr_map, |dm_map - csr_map|]` through a tiny 3-layer conv
   "fusion head" that outputs a per-pixel gate g in [0, 1]:

       fused = g * dm_map + (1 - g) * csr_map

   This is the actual "fusion layer" — everything else is just the two
   existing pretrained backbones. It is intentionally small (~5k params) so
   it can be trained in minutes on a handful of density-map-labelled images,
   A learned gate is used only when its checkpoint exists. Otherwise inference
   uses the configured conservative static blend of the two trained backbones.

4. `mu_sum` (total head-count) is `fused.sum()`. We keep the OT-style
   normalized copy `mu_normed` too, purely so this module presents the same
   `(mu, mu_normed)` interface as dm_count and csrnet (infer.py already
   unwraps `out[0] if isinstance(out, (tuple, list)) else out`).

Backbones are frozen by default (`freeze_backbones=True`) — only the fusion
head is trainable, which is what fusion/train_fusion.py trains.
"""

import torch
import torch.nn as nn
import torch.nn.functional as F

from dm_count.models import vgg19
from csrnet.models import csrnet


def mass_preserving_resize(dmap: torch.Tensor, size) -> torch.Tensor:
    """Bilinear-resize a density map to `size` (H, W) while preserving its
    total sum (== predicted count), since plain interpolation preserves
    per-pixel magnitude, not the integral."""
    if dmap.shape[-2:] == tuple(size):
        return dmap
    old_area = dmap.shape[-2] * dmap.shape[-1]
    new_area = size[0] * size[1]
    out = F.interpolate(dmap, size=size, mode="bilinear", align_corners=False)
    return out * (old_area / float(new_area))


class FusionHead(nn.Module):
    """Tiny gated-fusion layer: 3 input channels (dm_map, csr_map,
    |dm_map - csr_map|) -> 1 spatial gate in [0, 1]."""

    def __init__(self, hidden=16):
        super().__init__()
        self.net = nn.Sequential(
            nn.Conv2d(3, hidden, kernel_size=3, padding=1),
            nn.ReLU(inplace=True),
            nn.Conv2d(hidden, hidden, kernel_size=3, padding=1),
            nn.ReLU(inplace=True),
            nn.Conv2d(hidden, 1, kernel_size=1),
        )
        # Zero-init the last layer -> gate starts at sigmoid(0) = 0.5,
        # i.e. an untrained fusion head is a safe 50/50 average.
        nn.init.zeros_(self.net[-1].weight)
        nn.init.zeros_(self.net[-1].bias)

    def forward(self, dm_map, csr_map):
        gate_logits = self.net(torch.cat([dm_map, csr_map, (dm_map - csr_map).abs()], dim=1))
        gate = torch.sigmoid(gate_logits)
        fused = gate * dm_map + (1.0 - gate) * csr_map
        return fused, gate


class FusionCountingModel(nn.Module):
    """Wraps DM-Count + CSRNet with a learned gated-fusion layer."""

    def __init__(self, dm_pretrained=False, csr_pretrained=False,
                 freeze_backbones=True, fusion_hidden=16,
                 dm_weight=0.5, csr_weight=0.5):
        super().__init__()
        self.dm_model = vgg19(pretrained=dm_pretrained)
        self.csr_model = csrnet(pretrained=csr_pretrained)
        self.fusion_head = FusionHead(hidden=fusion_hidden)

        # Static fallback weights, used only if you want a fixed weighted
        # average instead of the learned gate (see `mode="static"` below).
        weight_total = max(float(dm_weight) + float(csr_weight), 1e-6)
        self.dm_weight = float(dm_weight) / weight_total
        self.csr_weight = float(csr_weight) / weight_total

        if freeze_backbones:
            self.set_backbones_trainable(False)

    def set_backbones_trainable(self, trainable: bool):
        for p in self.dm_model.parameters():
            p.requires_grad = trainable
        for p in self.csr_model.parameters():
            p.requires_grad = trainable

    # ponytail: default to static average mode as requested by user
    def forward(self, x, mode="static"):
        """
        mode:
          "static" (default)   — fixed weighted average (self.dm_weight / csr_weight)
          "learned"            — use the trained/trainable gated FusionHead
          "dm_only" / "csr_only" — bypass fusion, for A/B debugging
        """
        dm_map, _ = self.dm_model(x)
        csr_map, _ = self.csr_model(x)

        if mode == "dm_only":
            fused = dm_map
        elif mode == "csr_only":
            fused = mass_preserving_resize(csr_map, dm_map.shape[-2:])
        else:
            # Align resolutions (CSRNet is coarser: no internal upsampling)
            csr_map_aligned = mass_preserving_resize(csr_map, dm_map.shape[-2:])
            if mode == "static":
                fused = self.dm_weight * dm_map + self.csr_weight * csr_map_aligned
            else:  # "learned"
                fused, _gate = self.fusion_head(dm_map, csr_map_aligned)

        B = fused.size(0)
        mu_sum = fused.view(B, -1).sum(1).view(B, 1, 1, 1)
        mu_normed = fused / (mu_sum + 1e-6)
        return fused, mu_normed

    # Convenience for infer.py / other callers that just want a scalar count
    # ponytail: default to static average mode as requested by user
    @torch.no_grad()
    def predict_count(self, x, mode="static"):
        fused, _ = self.forward(x, mode=mode)
        return fused.view(fused.size(0), -1).sum(1)

    # ── checkpoint helpers ────────────────────────────────────────────
    def load_backbone_weights(self, dm_count_path=None, csrnet_path=None, device="cpu"):
        if dm_count_path:
            sd = _clean_state_dict(torch.load(dm_count_path, map_location=device))
            try:
                self.dm_model.load_state_dict(sd, strict=True)
            except RuntimeError as exc:
                raise RuntimeError(f"DM-Count checkpoint is incompatible: {exc}") from exc
            print(f"[Fusion] Loaded DM-Count weights from {dm_count_path}")
        if csrnet_path:
            sd = _clean_state_dict(torch.load(csrnet_path, map_location=device))
            try:
                self.csr_model.load_state_dict(sd, strict=True)
            except RuntimeError as exc:
                raise RuntimeError(f"CSRNet checkpoint is incompatible: {exc}") from exc
            print(f"[Fusion] Loaded CSRNet weights from {csrnet_path}")

    def load_fusion_head(self, path, device="cpu"):
        sd = torch.load(path, map_location=device)
        if isinstance(sd, dict) and "fusion_head" in sd:
            sd = sd["fusion_head"]
        self.fusion_head.load_state_dict(sd)
        print(f"[Fusion] Loaded trained fusion head from {path}")

    def save_fusion_head(self, path):
        torch.save({"fusion_head": self.fusion_head.state_dict()}, path)


def _clean_state_dict(ckpt):
    if isinstance(ckpt, dict):
        for k in ("state_dict", "model_state_dict", "model", "ema"):
            if k in ckpt and isinstance(ckpt[k], dict):
                ckpt = ckpt[k]
                break
    return {
        k.replace("module.", "").replace("model.", ""): v
        for k, v in ckpt.items()
        if isinstance(v, torch.Tensor)
    }


def build_fusion_model(config, device):
    """Convenience factory used by infer.py."""
    import os

    csrnet_path = getattr(config, "CSRNET_WEIGHTS_PATH", None)
    has_csrnet_ckpt = bool(csrnet_path) and os.path.exists(csrnet_path)
    if not has_csrnet_ckpt:
        print(f"[Fusion] No trained CSRNet checkpoint found at "
              f"{csrnet_path!r} — CSRNet branch will run with ImageNet-only "
              f"frontend weights (untrained backend). Train/point to a real "
              f"checkpoint for accurate counts; see csrnet/models.py.")

    model = FusionCountingModel(
        dm_pretrained=False,
        csr_pretrained=not has_csrnet_ckpt,  # fall back to ImageNet init if no checkpoint
        freeze_backbones=True,
        dm_weight=getattr(config, "FUSION_DM_WEIGHT", 0.5),
        csr_weight=getattr(config, "FUSION_CSR_WEIGHT", 0.5),
    )
    model.load_backbone_weights(
        dm_count_path=getattr(config, "WEIGHTS_PATH", None),
        csrnet_path=csrnet_path if has_csrnet_ckpt else None,
        device=device,
    )
    fusion_head_path = getattr(config, "FUSION_HEAD_WEIGHTS_PATH", None)
    if fusion_head_path:
        import os
        if os.path.exists(fusion_head_path):
            model.load_fusion_head(fusion_head_path, device=device)
        else:
            print(f"[Fusion] No trained fusion head at {fusion_head_path} — "
                  f"using configured static trained-backbone blend "
                  f"({model.dm_weight:.2f} DM / {model.csr_weight:.2f} CSR).")
    model.to(device).eval()
    return model

# CSRNet + DM-Count Fusion Layer

This adds a second crowd-counting backbone (**CSRNet**) alongside the
existing **DM-Count** (VGG19) model, and a small learned **fusion layer**
that combines their two density maps into one.

## New files
```
csrnet/models.py          CSRNet (VGG16 frontend + dilated-conv backend)
fusion/models.py          FusionCountingModel + gated FusionHead
fusion/train_fusion.py    Trains only the fusion head (backbones frozen)
```

## How the fusion works
1. Both backbones run on the same preprocessed frame and each output a
   single-channel density map.
2. Maps are aligned to a common resolution with a mass-preserving resize
   (in this repo's config they already match, so this is usually a no-op).
3. A tiny 3-layer conv head looks at `[dm_map, csr_map, |dm_map - csr_map|]`
   and predicts a per-pixel gate `g ∈ [0, 1]`:
   `fused = g * dm_map + (1 - g) * csr_map`.
4. Total count = `fused.sum()`.

Production inference never uses a random CSRNet branch. With the installed
trained CSRNet checkpoint and no site-trained fusion head, the app uses a
conservative normalized static blend: **80% DM-Count + 20% CSRNet**. When a
compatible `fusion_head.pth` is installed, the adapter automatically switches
to the learned spatial gate.

## Turning it on
```powershell
$env:DRONE_MODEL = "fusion"
$env:CSRNET_WEIGHTS_PATH = "csrnet/pretrained_models/csrnet_shtechA.pth"
python infer.py
```
`config.py` defaults to `DRONE_MODEL=fusion`. If the trained CSRNet checkpoint
is missing or incompatible, `infer.py` explicitly falls back to trained
DM-Count rather than silently using random weights. `FUSION_DM_WEIGHT` and
`FUSION_CSR_WEIGHT` control the static blend; both are normalized before use.

## Training just the fusion head
```bash
python -m fusion.train_fusion \
    --data-root /path/to/dataset \
    --dm-weights dm_count/pretrained_models/model_nwpu.pth \
    --csrnet-weights csrnet/pretrained_models/csrnet_shtechA.pth \
    --out fusion/pretrained_models/fusion_head.pth
```
Dataset layout: `images/*.jpg` + `ground_truth/*.h5` (h5py dataset key
`"density"`), the same format most CSRNet/DM-Count training pipelines
already produce.

## Is DM-Count the best choice, or is something else better?
Short answer: DM-Count is a solid, still-competitive choice, but it isn't
the current state of the art, and CSRNet on its own is meaningfully older
and weaker on sparse/varied-scale scenes (exactly what a drone sees). Rough
picture, ordered roughly oldest/weakest → newest/strongest, benchmarked on
the standard ShanghaiTech Part A test set (lower MAE is better):

| Model | ShanghaiTech A MAE (~) | Notes |
|---|---|---|
| MCNN (2016) | ~110 | Multi-column CNN, the original modern baseline |
| CSRNet (2018) | ~68 | What you already had; strong on dense uniform crowds, weaker across large scale variation |
| CAN (2019) | ~62 | Adds explicit multi-scale context |
| **DM-Count (2020)** | **~59** | Optimal-Transport loss, no Gaussian-kernel assumption — good generalization, what's currently in this repo |
| Bayesian Loss / BL (2019) | ~62 | Probability-based loss, similar era/strength to DM-Count |
| P2PNet (2021) | ~52 | Point-to-point matching instead of density maps — anchor-free, strong accuracy |
| CLTR (2022) | ~57 | Transformer-based, point regression |
| STEERER / GauNet / CrowdCLIP-era models (2023+) | ~50 and below | Current SOTA, but heavier and less battle-tested for real-time drone/edge deployment |

Practical takeaway for this project specifically (real-time RTSP/drone
inference on CPU or a single GPU):
- **DM-Count stays a good primary model** — it already beats CSRNet and is
  lighter than most 2022+ transformer-based counters.
- **CSRNet is a reasonable fusion partner, not a replacement** — it's
  complementary (different loss, different receptive-field behavior on
  dense crowds), which is exactly why fusing the two, rather than swapping
  one for the other, tends to help.
- If you want a single-model upgrade later instead of/in addition to
  fusion, **P2PNet** is the best accuracy/complexity tradeoff of the
  post-DM-Count models for a project like this — it drops density maps
  entirely (predicts head points directly), which also sidesteps the
  Gaussian-kernel/resize mass-preservation issues this fusion layer has to
  handle by hand.

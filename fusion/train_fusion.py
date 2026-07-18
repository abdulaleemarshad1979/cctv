"""
fusion/train_fusion.py
=======================
Trains ONLY the small gated FusionHead (dm_count/models.py's vgg19 and
csrnet/models.py's CSRNet stay frozen with their existing pretrained
weights). This is deliberately cheap: ~5k trainable parameters, so a few
hundred labelled images is enough to noticeably beat a plain 50/50 average.

Expected dataset layout (matches the common ShanghaiTech-style preprocessing
used by most CSRNet/DM-Count repos):

    data_root/
      images/
        IMG_1.jpg
        IMG_2.jpg
        ...
      ground_truth/
        IMG_1.h5      # h5py file with dataset 'density' (H x W float32 map)
        IMG_2.h5
        ...

If you only have point annotations (.mat with head coordinates), generate
the .h5 Gaussian density maps first — this is the same preprocessing step
DM-Count / CSRNet training already requires, so if you've trained either
model on your own data you likely already have a script for this
(e.g. `create_density_maps.py` in most CSRNet repos generates exactly this).

Usage:
    python -m fusion.train_fusion \
        --data-root /path/to/dataset \
        --epochs 15 --lr 1e-4 \
        --dm-weights dm_count/pretrained_models/model_nwpu.pth \
        --csrnet-weights csrnet/pretrained_models/csrnet_shtechA.pth \
        --out fusion/pretrained_models/fusion_head.pth
"""

import argparse
import glob
import os

import h5py
import numpy as np
import torch
import torch.nn as nn
from PIL import Image
from torch.utils.data import DataLoader, Dataset
from torchvision import transforms

from fusion.models import FusionCountingModel

_TFM = transforms.Compose([
    transforms.ToTensor(),
    transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
])


class DensityMapDataset(Dataset):
    def __init__(self, data_root, image_size=(768, 1024)):
        self.image_paths = sorted(glob.glob(os.path.join(data_root, "images", "*.jpg")) +
                                   glob.glob(os.path.join(data_root, "images", "*.png")))
        self.gt_dir = os.path.join(data_root, "ground_truth")
        self.image_size = image_size  # (H, W), must be divisible by 32
        if not self.image_paths:
            raise FileNotFoundError(f"No images found under {data_root}/images")

    def __len__(self):
        return len(self.image_paths)

    def _gt_path(self, img_path):
        stem = os.path.splitext(os.path.basename(img_path))[0]
        return os.path.join(self.gt_dir, stem + ".h5")

    def __getitem__(self, idx):
        img_path = self.image_paths[idx]
        img = Image.open(img_path).convert("RGB").resize((self.image_size[1], self.image_size[0]))

        with h5py.File(self._gt_path(img_path), "r") as f:
            density = np.asarray(f["density"], dtype=np.float32)
        gt_count = float(density.sum())  # preserve count through the resize below

        density_img = Image.fromarray(density).resize((self.image_size[1], self.image_size[0]), Image.BILINEAR)
        density_resized = np.asarray(density_img, dtype=np.float32)
        cur_sum = density_resized.sum()
        if cur_sum > 1e-6:
            density_resized *= gt_count / cur_sum  # keep total count exact after resize

        x = _TFM(img)
        y = torch.from_numpy(density_resized).unsqueeze(0)  # (1, H, W)
        return x, y, gt_count


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", required=True)
    ap.add_argument("--dm-weights", required=True)
    ap.add_argument("--csrnet-weights", required=True)
    ap.add_argument("--out", default="fusion/pretrained_models/fusion_head.pth")
    ap.add_argument("--epochs", type=int, default=15)
    ap.add_argument("--batch-size", type=int, default=4)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--count-loss-weight", type=float, default=0.1,
                     help="extra weight on |pred_count - gt_count| on top of pixel MSE")
    args = ap.parse_args()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    model = FusionCountingModel(dm_pretrained=False, csr_pretrained=False, freeze_backbones=True)
    model.load_backbone_weights(dm_count_path=args.dm_weights, csrnet_path=args.csrnet_weights, device=device)
    model.to(device)

    dataset = DensityMapDataset(args.data_root)
    loader = DataLoader(dataset, batch_size=args.batch_size, shuffle=True, num_workers=2)

    optimizer = torch.optim.Adam(model.fusion_head.parameters(), lr=args.lr)
    mse = nn.MSELoss()

    os.makedirs(os.path.dirname(args.out), exist_ok=True)

    for epoch in range(1, args.epochs + 1):
        model.fusion_head.train()
        running_loss = 0.0
        for x, y, gt_count in loader:
            x, y, gt_count = x.to(device), y.to(device), gt_count.to(device)

            fused, _ = model(x, mode="learned")
            fused = torch.nn.functional.interpolate(fused, size=y.shape[-2:], mode="bilinear", align_corners=False)
            # interpolate changes the sum, so rescale back to match `fused`'s own predicted count
            pred_count = fused.view(fused.size(0), -1).sum(1)

            pixel_loss = mse(fused, y)
            count_loss = torch.mean(torch.abs(pred_count - gt_count)) / max(1.0, y.numel() / y.size(0))
            loss = pixel_loss + args.count_loss_weight * count_loss

            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            running_loss += loss.item() * x.size(0)

        avg_loss = running_loss / len(dataset)
        print(f"[Fusion Train] epoch {epoch}/{args.epochs}  loss={avg_loss:.6f}")
        model.save_fusion_head(args.out)

    print(f"[Fusion Train] Done. Fusion head saved to {args.out}")


if __name__ == "__main__":
    main()

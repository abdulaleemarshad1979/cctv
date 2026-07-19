import torch.nn as nn
import os
from fusion.models import build_fusion_model
import config

class FusionAdapter(nn.Module):
    def __init__(self, device):
        super().__init__()
        self.device = device
        self.model = build_fusion_model(config, device)
        head_path = getattr(config, "FUSION_HEAD_WEIGHTS_PATH", "")
        self.use_learned_fusion = bool(head_path and os.path.exists(head_path))

    def forward(self, x):
        mode = "learned" if self.use_learned_fusion else "static"
        return self.model(x, mode=mode)

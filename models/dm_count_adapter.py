import os
import torch
import torch.nn as nn
from dm_count.models import vgg19
import config

class DMCountAdapter(nn.Module):
    def __init__(self, device):
        super().__init__()
        self.device = device
        # Build model without PyTorch's default pretrained weights loader
        self.model = vgg19(pretrained=False)
        
        # Load local trained checkpoint
        ckpt_path = config.WEIGHTS_PATH
        if not os.path.exists(ckpt_path):
            raise FileNotFoundError(f"DM-Count weights not found at {ckpt_path}")
            
        ckpt = torch.load(ckpt_path, map_location=device)
        if isinstance(ckpt, dict):
            for k in ("state_dict", "model_state_dict", "model", "ema"):
                if k in ckpt and isinstance(ckpt[k], dict):
                    ckpt = ckpt[k]
                    break
        sd = {k.replace("module.", "").replace("model.", ""): v
              for k, v in ckpt.items() if isinstance(v, torch.Tensor)}
        
        try:
            self.model.load_state_dict(sd, strict=True)
        except RuntimeError:
            self.model.load_state_dict(sd, strict=False)
            
        self.model.to(device)
        self.model.eval()

    def forward(self, x):
        return self.model(x)

import os
import torch
import torch.nn as nn
from csrnet.models import csrnet
import config

class CSRNetAdapter(nn.Module):
    def __init__(self, device):
        super().__init__()
        self.device = device
        
        # Load local trained checkpoint if available
        ckpt_path = config.CSRNET_WEIGHTS_PATH
        has_ckpt = bool(ckpt_path) and os.path.exists(ckpt_path)
        
        self.model = csrnet(pretrained=not has_ckpt)
        
        if has_ckpt:
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

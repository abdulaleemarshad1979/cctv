"""
tools/export_onnx.py — Exports VGG19 DM-Count model to ONNX format with dynamic shape support
"""
import os
import sys
import torch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from dm_count.models import vgg19

def main():
    print("[ONNX-EXPORT] Starting ONNX export...")
    device = torch.device("cpu")
    
    if not os.path.exists(config.WEIGHTS_PATH):
        print(f"[ONNX-EXPORT] Error: Model checkpoint weights not found at: {config.WEIGHTS_PATH}")
        sys.exit(1)
        
    print(f"[ONNX-EXPORT] Loading checkpoint from: {config.WEIGHTS_PATH}")
    ckpt = torch.load(config.WEIGHTS_PATH, map_location=device)
    if isinstance(ckpt, dict):
        for k in ("state_dict", "model_state_dict", "model", "ema"):
            if k in ckpt and isinstance(ckpt[k], dict):
                ckpt = ckpt[k]
                break
    sd = {k.replace("module.", "").replace("model.", ""): v
          for k, v in ckpt.items() if isinstance(v, torch.Tensor)}
          
    model = vgg19()
    try:
        model.load_state_dict(sd, strict=True)
    except RuntimeError:
        model.load_state_dict(sd, strict=False)
    model.eval()
    
    # We resolve config.INFER_HEIGHT & INFER_WIDTH dynamically (forcing fallback CPU size for dummy trace)
    h, w = config.INFER_HEIGHT, config.INFER_WIDTH
    print(f"[ONNX-EXPORT] Tracing with dummy shape: 1x3x{h}x{w}")
    dummy_input = torch.randn(1, 3, h, w)
    
    onnx_path = config.WEIGHTS_PATH.replace(".pth", ".onnx")
    
    # Exporting
    torch.onnx.export(
        model,
        dummy_input,
        onnx_path,
        export_params=True,
        opset_version=12,
        do_constant_folding=True,
        input_names=['input'],
        output_names=['output_mu', 'output_mu_normed'],
        dynamic_axes={
            'input': {0: 'batch_size', 2: 'height', 3: 'width'},
            'output_mu': {0: 'batch_size', 2: 'height', 3: 'width'},
            'output_mu_normed': {0: 'batch_size', 2: 'height', 3: 'width'}
        }
    )
    print(f"[ONNX-EXPORT] ONNX model exported successfully to: {onnx_path}")

if __name__ == "__main__":
    main()

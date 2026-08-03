# Model Weights Pre-Installation Guide

The AI crowd density estimation model depends on pretrained DM-Count VGG19 weights.

## Required File Location
```text
cctv/
└── dm_count/
    └── pretrained_models/
        └── model_nwpu.pth
```

## Verifying Model File Existence & Size
Before running `install.sh`, verify the file exists:

```bash
ls -lh dm_count/pretrained_models/model_nwpu.pth
```

The file `model_nwpu.pth` should be approximately ~80 MB - 150 MB.

## USB Drive Backup Instructions
If internet access is restricted or GitHub LFS download fails at the deployment site:

1. Copy `model_nwpu.pth` from USB drive:
   ```bash
   mkdir -p dm_count/pretrained_models/
   cp /media/usb/model_nwpu.pth dm_count/pretrained_models/
   ```
2. Verify permissions:
   ```bash
   chmod 644 dm_count/pretrained_models/model_nwpu.pth
   ```

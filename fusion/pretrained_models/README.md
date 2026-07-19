Trained fusion-head checkpoints (output of `fusion/train_fusion.py`) go
here, e.g. `fusion_head.pth`.

Without a compatible trained fusion-head checkpoint, production inference uses
the configured normalized static blend of the two trained backbones (currently
80% DM-Count and 20% CSRNet). It never labels an untrained gate as learned.

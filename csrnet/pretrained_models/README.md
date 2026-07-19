The production file is `csrnet_shtechA.pth`: the official ShanghaiTech Part A
checkpoint converted to a modern tensors-only state dict and verified with a
strict load against `csrnet/models.py:CSRNet`.

Point `CSRNET_WEIGHTS_PATH` (config.py or env var) at it. Without a
checkpoint here, production inference falls back to trained DM-Count. It will
not include an ImageNet-only CSRNet backend in the crowd count. Model binaries
are ignored by Git, so a new installation must restore this file separately
from https://github.com/leeyeehoo/CSRNet-pytorch or use its own trained model.

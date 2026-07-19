from pathlib import Path

import pytest
import torch

import config
from csrnet.models import CSRNet
from fusion.models import mass_preserving_resize


def test_density_resize_preserves_people_count():
    density = torch.rand(2, 1, 17, 29)
    resized = mass_preserving_resize(density, (68, 116))
    torch.testing.assert_close(
        resized.sum(dim=(1, 2, 3)),
        density.sum(dim=(1, 2, 3)),
        rtol=1e-4,
        atol=1e-4,
    )


def test_installed_csrnet_checkpoint_is_safe_and_complete():
    checkpoint_path = Path(config.CSRNET_WEIGHTS_PATH)
    if not checkpoint_path.exists():
        pytest.skip("CSRNet production checkpoint is not installed")

    state_dict = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
    model = CSRNet(load_vgg_weights=False)
    model.load_state_dict(state_dict, strict=True)

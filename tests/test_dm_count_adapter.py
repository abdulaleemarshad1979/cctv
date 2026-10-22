import pytest

torch = pytest.importorskip("torch")

import config
from dm_count.models import vgg19
from models.dm_count_adapter import DMCountAdapter


@pytest.fixture(scope="module")
def checkpoint(tmp_path_factory):
    torch.manual_seed(0)
    model = vgg19(pretrained=False)
    # Positive biases keep the density non-zero so the comparison means something.
    with torch.no_grad():
        for module in model.modules():
            if isinstance(module, torch.nn.Conv2d):
                module.bias.fill_(0.05)
    path = tmp_path_factory.mktemp("weights") / "model.pth"
    torch.save(model.state_dict(), path)
    return path, model.eval()


def test_cpu_graph_is_frozen_and_matches_the_eager_model_on_any_batch_and_size(checkpoint, monkeypatch):
    path, eager = checkpoint
    monkeypatch.setattr(config, "WEIGHTS_PATH", str(path))
    adapter = DMCountAdapter(torch.device("cpu"))

    assert adapter._frozen is not None, "the CPU graph optimisation should be active"
    assert adapter.model is None, "the eager copy of the weights is released"

    for shape in [(1, 3, 96, 160), (2, 3, 64, 96)]:  # other batch and size than the traced one
        x = torch.rand(*shape)
        with torch.inference_mode():
            want_mu, want_normed = eager(x)
            got_mu, got_normed = adapter(x)
        assert got_mu.shape == want_mu.shape
        assert float(want_mu.sum()) > 0
        assert torch.allclose(got_mu, want_mu, rtol=1e-3, atol=1e-5)
        assert torch.allclose(got_normed, want_normed, rtol=1e-3, atol=1e-7)


def test_a_failed_optimisation_falls_back_to_the_eager_model(checkpoint, monkeypatch):
    path, eager = checkpoint
    monkeypatch.setattr(config, "WEIGHTS_PATH", str(path))
    monkeypatch.setattr(torch.jit, "freeze", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no jit here")))
    adapter = DMCountAdapter(torch.device("cpu"))

    assert adapter._frozen is None and adapter.model is not None
    x = torch.rand(1, 3, 216, 384)
    with torch.inference_mode():
        assert torch.allclose(adapter(x)[0], eager(x)[0], rtol=1e-4, atol=1e-6)


def test_missing_weights_raise_a_clear_error(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WEIGHTS_PATH", str(tmp_path / "absent.pth"))
    with pytest.raises(FileNotFoundError, match="DM-Count weights not found"):
        DMCountAdapter(torch.device("cpu"))

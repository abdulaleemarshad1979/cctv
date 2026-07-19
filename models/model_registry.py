from models.dm_count_adapter import DMCountAdapter
from models.experiments.csrnet_adapter import CSRNetAdapter
from models.experiments.fusion_adapter import FusionAdapter

def load_counting_model(model_name: str, device):
    if model_name == "dm_count":
        return DMCountAdapter(device)
    elif model_name == "csrnet":
        return CSRNetAdapter(device)
    elif model_name == "fusion":
        return FusionAdapter(device)
    else:
        raise ValueError(f"Unsupported model: {model_name}")

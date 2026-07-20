def load_counting_model(model_name: str, device):
    if model_name == "dm_count":
        from models.dm_count_adapter import DMCountAdapter
        return DMCountAdapter(device)
    elif model_name == "csrnet":
        from models.experiments.csrnet_adapter import CSRNetAdapter
        return CSRNetAdapter(device)
    elif model_name == "fusion":
        from models.experiments.fusion_adapter import FusionAdapter
        return FusionAdapter(device)
    else:
        raise ValueError(f"Unsupported model: {model_name}")

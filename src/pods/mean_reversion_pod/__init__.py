from .strategy import MeanReversionPod

__all__ = ["MeanReversionPod", "make_mean_reversion_pod"]


def make_mean_reversion_pod(gateway) -> MeanReversionPod:
    return MeanReversionPod(gateway)

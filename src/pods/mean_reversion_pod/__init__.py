from .strategy import MeanReversionPod


def make_mean_reversion_pod(gateway) -> MeanReversionPod:
    return MeanReversionPod(gateway)

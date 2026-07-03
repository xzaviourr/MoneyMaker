from .strategy import MomentumPod

__all__ = ["MomentumPod", "make_momentum_pod"]


def make_momentum_pod(gateway) -> MomentumPod:
    return MomentumPod(gateway)

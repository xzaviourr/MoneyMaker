from .strategy import MomentumPod


def make_momentum_pod(gateway) -> MomentumPod:
    return MomentumPod(gateway)

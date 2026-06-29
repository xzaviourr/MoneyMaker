from .strategy import BreakoutPod


def make_breakout_pod(gateway) -> BreakoutPod:
    return BreakoutPod(gateway)

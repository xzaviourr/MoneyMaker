from .strategy import BreakoutPod

__all__ = ["BreakoutPod", "make_breakout_pod"]


def make_breakout_pod(gateway) -> BreakoutPod:
    return BreakoutPod(gateway)

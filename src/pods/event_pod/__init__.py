from .strategy import EventPod


def make_event_pod(gateway) -> EventPod:
    return EventPod(gateway)

"""Read documented Parse usage without confusing missing billing with zero cost."""
from math import isfinite


def parse_credits(payload):
    if not isinstance(payload, dict): return None
    job = payload.get("job")
    if not isinstance(job, dict): return None
    usage = job.get("usage")
    if not isinstance(usage, dict): return None
    credits = usage.get("credits")
    if type(credits) not in (int, float) or not isfinite(credits) or credits < 0:
        return None
    return credits

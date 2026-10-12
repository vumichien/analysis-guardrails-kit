"""The m-value of a reported claim against a reference set of analysis paths on the same data.

m = 2 x min(share of reference z <= z0, share of reference z >= z0), capped at 1
(Miao, Pritchard & Zou, arXiv:2607.01507). A small m means the reported claim sits in a
tail of what the other defensible analyses of the same data found. The value is relative
to the reference set: here, every specification the six runs on the same file logged.
"""
import math


def m_value(z0, reference):
    ref = [z for z in reference if z is not None and math.isfinite(z)]
    if z0 is None or not math.isfinite(z0) or not ref:
        return None
    le = sum(z <= z0 for z in ref) / len(ref)
    ge = sum(z >= z0 for z in ref) / len(ref)
    return min(1.0, 2 * min(le, ge))

import math
from dataclasses import dataclass

# Readings needed before a sensor's baseline is trusted for z-scores.
WARMUP_SAMPLES = 20
# Steady-state EWMA weight: ~ the last 20 samples dominate, so seasonal drift is tracked.
MIN_ALPHA = 0.05


@dataclass
class RunningStat:
    """Exponentially-weighted mean/variance.

    alpha = max(1/n, MIN_ALPHA): an exact running average during warm-up, then an EWMA
    so the baseline follows slow, legitimate drift (seasons, sensor ageing).
    """

    mean: float = 0.0
    var: float = 0.0
    n: int = 0

    def update(self, x: float) -> None:
        self.n += 1
        alpha = max(1.0 / self.n, MIN_ALPHA)
        diff = x - self.mean
        incr = alpha * diff
        self.mean += incr
        self.var = (1 - alpha) * (self.var + diff * incr)

    @property
    def warm(self) -> bool:
        return self.n >= WARMUP_SAMPLES

    def zscore(self, x: float, min_std: float) -> float | None:
        """None until warm. `min_std` stops a very quiet sensor turning noise into 10σ events."""
        if not self.warm:
            return None
        return (x - self.mean) / max(math.sqrt(self.var), min_std)


SensorStats = dict[str, RunningStat]

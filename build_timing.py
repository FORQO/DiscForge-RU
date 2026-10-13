"""Stage-aware estimates. Times are predictions, never completion signals."""
import time


class BuildEstimate:
    def __init__(self, plans, clock=time.monotonic):
        self.plans = plans
        self.clock = clock
        self.active = None
        self.started = clock()
        self.ratios = {}
        self.measured = False

    def update(self, job, stage, fraction):
        now = self.clock()
        key = (job, stage)
        if key != self.active:
            self.active, self.started = key, now
        fraction = max(0.0, min(1.0, fraction))
        planned = self.plans[job][stage]
        elapsed = now-self.started
        if elapsed >= 2 and fraction > 0.001:
            ratio = elapsed / fraction / planned
            previous = self.ratios.get(stage, ratio)
            self.ratios[stage] = previous*0.7 + ratio*0.3
            self.measured = True
        remaining = planned*(1-fraction)*self.ratios.get(stage, 1)
        stages = list(self.plans[job])
        for later in stages[stages.index(stage)+1:]:
            remaining += self.plans[job][later]*self.ratios.get(later, 1)
        for plan in self.plans[job+1:]:
            remaining += sum(seconds*self.ratios.get(name, 1) for name, seconds in plan.items())
        return max(1, remaining) if self.measured else -1


def stage_plan(duration, stream_bytes, iso, menu, nvenc):
    # Initial I/O estimate 80 MB/s; replace each stage's rate as measurements arrive.
    plan = {'encode': max(2, duration/(3 if nvenc else 0.5)),
            'mux': max(2, stream_bytes/80_000_000), 'menu': 2}
    if iso and menu:
        plan['iso'] = max(3, stream_bytes/80_000_000)
    plan['verify'] = 2
    return plan

"""Persistence gates using EEG elapsed time; overlapping windows are correlated."""
class Decision:
    def __init__(self, config):
        self.config = config
        self.reset()

    def reset(self):
        self.candidate = None
        self.since = None
        self.last_emit = float("-inf")
        self.last_update = None

    def update(self, scores, timestamp, valid=True):
        ordered = sorted(scores.items(), key=lambda pair: pair[1], reverse=True)
        winner, score = ordered[0] if ordered else (None, 0)
        margin = score - ordered[1][1] if len(ordered) > 1 else 0
        if (self.last_update is not None and
                (timestamp <= self.last_update or timestamp-self.last_update > 2*self.config["update_seconds"])):
            self.reset()
        self.last_update = timestamp
        if not valid or score < self.config["min_score"] or margin < self.config["min_margin"]:
            self.candidate, self.since = None, None
            return {"candidate": None, "accepted": None, "emit": False, "score": score, "margin": margin}
        if winner != self.candidate:
            self.candidate, self.since = winner, timestamp
        accepted = winner if timestamp-self.since >= self.config["persistence_seconds"] else None
        emit = accepted is not None and timestamp-self.last_emit >= self.config["repeat_seconds"]
        if emit:
            self.last_emit = timestamp
        return {"candidate": winner, "accepted": accepted, "emit": emit, "score": score, "margin": margin}

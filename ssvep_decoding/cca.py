"""Standard CCA using centered column spaces, with rank-deficient channels handled."""
import numpy as np


def column_basis(values):
    values = np.asarray(values, dtype=float)
    centered = values - values.mean(axis=0, keepdims=True)
    u, singular, _ = np.linalg.svd(centered, full_matrices=False)
    if not len(singular) or singular[0] == 0:
        return u[:, :0]
    rank = int(np.sum(singular > singular[0]*max(centered.shape)*np.finfo(float).eps))
    return u[:, :rank]


class CCA:
    def __init__(self, sample_rate, count, targets, harmonics):
        t = np.arange(count)/sample_rate
        self.count = count
        self.references = {}
        for target_id, frequency in targets.items():
            reference = np.column_stack([
                function(2*np.pi*frequency*h*t)
                for h in range(1, harmonics+1) for function in (np.sin, np.cos)])
            self.references[target_id] = column_basis(reference)

    def scores(self, samples):
        x = np.asarray(samples, dtype=float)
        if x.ndim != 2 or x.shape[0] != self.count or not np.isfinite(x).all():
            raise ValueError("CCA requires a finite samples-by-channels window of the configured length.")
        basis = column_basis(x)
        return {target: float(np.clip(np.linalg.svd(basis.T @ reference, compute_uv=False)[0], 0, 1))
                if basis.shape[1] else 0.0 for target, reference in self.references.items()}

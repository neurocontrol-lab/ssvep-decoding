"""Read a montage snapshot independently of the decoder's channel selection."""
from pathlib import Path

from .report import read_json


DEFAULT_MONTAGE = "occipital-mastoid-v1"
MONTAGE_PROFILE = Path(__file__).parent / "profiles" / "montage_occipital_mastoid_v1.json"


def read_montage(selection, channels):
    if not selection:
        return None
    path = MONTAGE_PROFILE if selection == DEFAULT_MONTAGE else Path(selection)
    montage = read_json(path, None)
    if not isinstance(montage, dict) or not all(
        isinstance(montage.get(key), str) and montage[key].strip()
        for key in ("id", "bioamp_in_minus", "bioamp_ref")
    ):
        raise ValueError(f"Invalid montage profile: {path}")
    mapping = montage.get("channels")
    if not isinstance(mapping, dict) or any(
        not isinstance(mapping.get(pin), str) or not mapping[pin].strip() for pin in channels
    ):
        raise ValueError(f"Montage must identify each selected channel: {path}")
    return montage

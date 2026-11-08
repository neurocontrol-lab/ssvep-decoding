# EEG montages

## Occipital montage with bilateral mastoids

Use `occipital-mastoid-v1` for the confirmed four-channel wiring.

| Connection | Electrode or connection point |
|---|---|
| MG24 D1 (BioAmp output) | O1 |
| MG24 D2 (BioAmp output) | Oz |
| MG24 D3 (BioAmp output) | O2 |
| MG24 D4 (BioAmp output) | POz |
| Shared BioAmp `IN-` | Right mastoid |
| Shared BioAmp `REF` | Left mastoid |
| MG24 D0 | MCU GND for diagnostics, excluded from decoding |

`IN-` is the shared negative signal input. `REF` is a separate BioAmp terminal. D0 is an MCU diagnostic connection.

The launcher saves the selected montage in `montage.json` with the recording. The decoder and derived reports keep a copy of that snapshot. Choose **Unspecified** if the wiring differs and record it in the session notes, or supply a custom montage JSON with `live --montage PATH`. Changing the montage label does not change which channels the decoder reads.

## Historical Round 1

The original winter-cap setup used O1, O2, Oz, and POz with gel, shared `IN-` at Cz, and shared `REF` at the left mastoid. Its D1–D4 order was unrecorded. [Round 1 report](results/2026-10-16-round-01.md)

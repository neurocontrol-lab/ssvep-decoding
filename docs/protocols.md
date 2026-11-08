# Recording protocols

| Intent | When to use it |
|---|---|
| **Diagnostic** | Check electrode contact, acquisition, display timing, or maze integration. |
| **Development** | Compare window lengths, harmonic counts, or decision thresholds. Replays of development recordings belong here. |
| **Evaluation** | Record fresh data after fixing the protocol, decoder settings, and scoring rules. Use separate data for tuning. |

Use **Single target** to check responses at a known stimulus frequency. Use **Four targets** to test gaze selection while the other targets also flicker. Use **Rest only** to measure how often the decoder emits a command without flicker.

Before a run, record the montage, display profile, viewing distance, and any setup changes that could affect the comparison. Use the run label for its intent, for example `contact-check` or `baseline`. Keep the source recording paired with its original stimulus log.

The launcher starts EEG before the first cue and records a five-second tail after the stimulus ends. Summaries label windows using recorded host timing and exclude cue transitions. Replay uses original decoder timestamps matched by sample sequence, with interpolation between nearby original updates when needed. This alignment is approximate. Overlapping windows do not count as independent trials, and host flip timestamps do not establish optical onset.

The [session catalog](results/session-catalog.md) describes the recorded sessions and where to find their outputs.

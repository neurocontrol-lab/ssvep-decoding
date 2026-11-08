# Session catalog

Audit of the local recordings available on **8 November 2026**. Paths below are relative to the ignored `sessions/` directory. Existing names are kept so source references in prior reports continue to resolve.

| Purpose | Use |
|---|---|
| **Diagnostic** | Check acquisition, display timing, contacts, or integration. Use to investigate a problem. |
| **Development** | Compare protocols, explore decoder settings, or estimate rest behavior during tuning. |
| **Evaluation** | Collect fresh data with the protocol, decoder, and scoring rules fixed before recording. Keep these data out of tuning. |

No existing run has a documented, predeclared formal evaluation protocol. The first four-target runs are useful development comparisons, including a repeat under the 4 s / 3 harmonics profile. Their scores have already informed window and gate decisions.

## Diagnostic runs

| Decoder run(s) | Matching stimulus or purpose |
|---|---|
| `20261015_213404_618882`, `20261015_213758_696732` | Replay and synthetic pipeline checks |
| `20261016_012438_332784` | Early live forward check; overlaps `stimulus_20261016_012412_842385` |
| `mastoid_maze_control_01`, `20261108_035127_217792`, `20261108_040453_119926`, `20261108_040626_801836`, `20261108_041029_950537`, `20261108_043640_069330` | Maze integration and movement troubleshooting; no synchronized gaze cues |
| `20261108_040428_232111` | Maze startup check; stream stalled before recording frames |
| `mastoid_psychopy_control_02` + `mastoid_psychopy_control_stim_02` | Compare the EEG response in PsychoPy after low maze scores |
| `mastoid_psychopy_control_03` + `mastoid_psychopy_control_stim_03` | Repeat the PsychoPy check after addressing electrode contacts |

## Development runs

| Decoder run(s) | Matching stimulus or purpose |
|---|---|
| `forward_test_01`, `backward_test_01`, `left_test_01`, `right_test_01`, `left_test_02`, `right_test_02`, `rest_test_01` | Original winter-cap single-target feasibility trials, repeats, and rest; stimulus IDs are in the [Round 1 report](2026-10-16-round-01.md#source-recordings) |
| `mastoid_left_02` + `mastoid_left_stim_02`, `mastoid_right_02` + `mastoid_right_stim_02` | Single-target repeats with the mastoid montage |
| `mastoid_rest_01` | Standalone mastoid rest used in gate comparisons |
| `mastoid_four_target_01` + `mastoid_four_target_stim_01` | First simultaneous four-target development run; used for the window/harmonic sweep and gate calibration |
| `mastoid_four_target_4s3h_01` + `mastoid_four_target_stim_4s3h_01` | Follow-up four-target comparison; used to check the 4-second candidate and the calibration gates |

## Excluded runs

`_excluded/mastoid-round-01/` holds `mastoid_left_01`, `mastoid_right_01`, `mastoid_left_stim_01`, and `mastoid_right_stim_01`. The operator reported excessive viewing distance for these first mastoid trials. Keep them out of decoder tuning and evaluation. The separate mastoid rest recording remains a development baseline.

## Other display logs

The following completed display logs are not linked to a decoder run in this catalog. Treat them as unpaired display checks unless a matching EEG recording is established:

- `stimulus_20261016_012159_121350`
- `stimulus_20261016_014149_917868`
- `stimulus_20261016_014353_254543`
- `stimulus_20261016_014655_106503`
- `stimulus_20261016_015336_477408`
- `stimulus_20261016_020531_290950`

Cancelled before a trial: `stimulus_20261016_012730_126100`, `stimulus_20261016_013834_357583`, `stimulus_20261016_014549_291074`, `stimulus_20261016_015104_714128`, and `stimulus_20261016_021100_587897`. `stimulus_20261016_011947_356182` has no saved report.

## New launcher sessions

Choose **Run purpose** before starting. New sessions go to `sessions/diagnostic/`, `sessions/development/`, or `sessions/evaluation/`. The folder name includes the purpose, test, actual decoder window and harmonic count, timestamp, and optional run label. For example:

```text
sessions/diagnostic/diagnostic_single-right_4s-3h_20261108_104500_000000_contact-check/
```

The purpose and original label are saved with the profiles and summary. An **Evaluation** label records the intended use; it does not certify a completed or valid evaluation. Review the completion status and quality checks before scoring the run. If you use an evaluation recording to change settings, treat it as development data and collect fresh evaluation data.

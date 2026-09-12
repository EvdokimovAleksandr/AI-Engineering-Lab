# Final report

**Report gate:** `ACCEPTED (adjudicated)`

## Narrative (non-authoritative wording)
Decomposed problem into research, analysis, and verification tracks.

## Verified results
- `claim_be4e9303477b` (CALCULATION): Example stress for 0.05 N on 5 µm fiber is on the order of GPa-scale under idealized geometry.
- `claim_e5fde6e5cf41` (CALCULATION): Example stress for 0.05 N on 5 µm fiber is on the order of GPa-scale under idealized geometry.

## Accepted claims
- `claim_be4e9303477b` (CALCULATION): Example stress for 0.05 N on 5 µm fiber is on the order of GPa-scale under idealized geometry.
- `claim_e5fde6e5cf41` (CALCULATION): Example stress for 0.05 N on 5 µm fiber is on the order of GPa-scale under idealized geometry.

## Provenance
- claim_be4e9303477b <- computation=comp_da4a3cc3f444
- claim_e5fde6e5cf41 <- computation=comp_28de5d9ec763

## Disputed claims
- _(none)_

## Rejected claims
- _(none)_

## Verification
- status=`PASS` id=`ver_17963b5972e5`
  - discrepancy: Lab GPa figures lack industrial humidity/strain-rate conditions
  - discrepancy: Mock research source is not a primary measurement

## Red team
- recommended_reject=`False` id=`rt_eeba8ba6938b`
  - summary: Scaling claims remain weakly supported; treat industrial GPa as unproven.
  - [MEDIUM] Extrapolating lab tensile strength to plant-scale fiber without process equivalence.
  - [MEDIUM] Hidden dependency on controlled humidity not stated as assumption.

## Residual risks
- Lab GPa figures lack industrial humidity/strain-rate conditions
- Mock research source is not a primary measurement
- Scaling claims remain weakly supported; treat industrial GPa as unproven.
- [MEDIUM] Extrapolating lab tensile strength to plant-scale fiber without process equivalence.
- [MEDIUM] Hidden dependency on controlled humidity not stated as assumption.

## Open questions / caveats
- Checks passed; claims remain provisional for industrial conditions.
- Adjudication PASS
- non-quantitative claim claim_5d790f64d7aa excluded from accepted results
- non-quantitative claim claim_68100d3e5325 excluded from accepted results
- non-quantitative claim claim_6fd639784eda excluded from accepted results
- non-quantitative claim claim_74898352053d excluded from accepted results
- non-quantitative claim claim_80b01019d826 excluded from accepted results
- non-quantitative claim claim_dc89b0ed2fa1 excluded from accepted results
- non-quantitative claim claim_fd954fa5bff0 excluded from accepted results
- non-quantitative claim claim_5d790f64d7aa excluded from accepted results
- non-quantitative claim claim_68100d3e5325 excluded from accepted results
- non-quantitative claim claim_6fd639784eda excluded from accepted results
- non-quantitative claim claim_74898352053d excluded from accepted results
- non-quantitative claim claim_80b01019d826 excluded from accepted results
- non-quantitative claim claim_dc89b0ed2fa1 excluded from accepted results
- non-quantitative claim claim_fd954fa5bff0 excluded from accepted results

## Evidence references
- adjudication_status: `AdjudicationStatus.PASS`
- decisions: 11

_Generated from SynthesisBundle. Verification/red-team/adjudication are authoritative._
_LLM narrative cannot create or alter accepted quantitative results._

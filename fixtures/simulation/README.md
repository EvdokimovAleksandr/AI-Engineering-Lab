# Uniaxial tension synthetic fixture

Inputs are **benchmark numbers**, not measured spider-silk properties.

- `source = fixture://synthetic`
- `trust = STUB`
- Must not be ingested as `FACT`

Hand check (SI):

```
d = 5 μm
A = π d² / 4
F chosen so F/A = E · (ΔL/L) = 5 GPa · 0.01 = 50 MPa
```

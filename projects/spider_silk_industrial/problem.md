# Industrial reproduction of spider silk

## Problem statement

How can spider silk be reproduced at industrial scale?

This project is a **benchmark scaffold** for the AI Engineering Lab.
The goal of the MVP is to exercise the multi-agent workflow, evidence model,
verification, and red-team loop — **not** to produce a final industrial process design yet.

## Open questions (seed)

1. Which production route is viable: transgenic hosts, synthetic peptide assembly, or hybrid?
2. What mechanical properties must be matched (tensile strength, toughness, elongation)?
3. What are the fundamental bottlenecks in spinning and post-processing?
4. Which assumptions about cost, yield, and purity are currently unsupported?

The V2.5 uniaxial tensile **benchmark** uses `fixtures/simulation/uniaxial_tension.json`
(`fixture://synthetic`, STUB). Those numbers are not measured silk properties.

## Success criteria for a future lab run

- Explicit claims with sources, conditions, and falsifiers
- Independent verification status for critical claims
- Red-team challenges recorded in the decision log
- Clear list of assumptions vs facts

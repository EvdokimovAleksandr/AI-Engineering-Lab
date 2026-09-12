# Expected capabilities — simple_heater

The lab should:

- Route the task to **SIMPLE** workflow (Task Router).
- Avoid launching research / hypothesis / red-team nodes.
- Perform a deterministic calculation (mass, \(c\), \(\Delta T\), time, loss factor).
- Validate units (energy J or Wh → power W/kW).
- Persist the run as an ordinary `RunManifest` under this project namespace only.

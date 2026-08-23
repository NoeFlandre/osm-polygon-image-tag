# Contributing

Thank you for your interest in `osm-polygon-image-tag`. This project is a
single-purpose pipeline for a reproducible OpenStreetMap GeoParquet dataset,
and contributions are welcome in the form of bug reports, focused
improvements, and documentation fixes.

## Quick orientation

- Read [`docs/architecture.md`](docs/architecture.md) for the layered
  structure and dependency rules.
- Read [`docs/data-contract.md`](docs/data-contract.md) for the schema,
  processing contract, and tag-selection contract.
- Read [`docs/development.md`](docs/development.md) for the local quality
  gates and toolchain conventions.

## Working on changes

1. Fork the repository and create a feature branch off `main`.
2. Run `just sync` and `just install-hooks`.
3. Add a failing test that captures the desired behaviour.
4. Implement the minimum change to make the test pass.
5. Run the local quality gates:
   ```bash
   just qa
   ```
   Use `just ci` when you also want the all-files pre-commit checks and strict
   documentation build.
6. Open a pull request against `main`. Use a descriptive title and explain
   the change, the test that proves it, and any caveats. If your change
   touches behaviour covered by [`docs/data-contract.md`](docs/data-contract.md),
   call that out explicitly.

## House rules

- One independently green commit per logical change.
- No generated data, caches, secrets, credentials, PBFs, Parquet files,
  SQLite databases, or Hugging Face receipts in the repository. They are
  produced by running the pipeline against real data.
- Respect the layering rules. The dependency arrow flows downward from
  CLI through `runtime` to `core`. No upward imports.
- Do not lower the coverage threshold. Add focused regression coverage
  instead.

## Reporting issues

Open a GitHub issue at
<https://github.com/NoeFlandre/osm-polygon-image-tag/issues>. Include the
exact command, the exact commit SHA, and the exact path inputs that
reproduce the problem. Do not include real PBF paths, API tokens, or any
other sensitive information.

## Licensing

By contributing, you agree that your contributions will be licensed under
the Apache-2.0 license that covers the project source code. The dataset
itself is independently licensed under the Open Database License (ODbL)
by virtue of being derived from OpenStreetMap data; the generated dataset
card records that attribution.

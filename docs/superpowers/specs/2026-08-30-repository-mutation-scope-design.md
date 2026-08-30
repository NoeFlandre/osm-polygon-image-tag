# Repository-Wide Mutation Scope Design

## Goal

Make the mutation gate exercise every covered production module instead of only
the asset-catalog and Flickr modules, while preserving application behavior,
equivalent-mutation exclusions, and the developer resource bound.

## Current gap

The `[tool.mutmut]` configuration limits `only_mutate` to two source files and
limits `pytest_add_cli_args_test_selection` to their two focused test files.
The gate therefore proves those two boundaries only. The project already has
branch coverage for the complete `src` tree, so the mutation gate can use the
same covered-line restriction across the repository.

## Proposed configuration

Remove both `only_mutate` and `pytest_add_cli_args_test_selection` from
`pyproject.toml`. Mutmut will then discover all covered production mutants
under the existing `source_paths = ["src"]` setting and run the complete
pytest suite with `--no-cov`. Copy the repository contract files and the
mutation runner into the isolated `mutants/` tree with `also_copy`, and keep:

- `mutate_only_covered_lines = true` to avoid mutating unreachable code;
- `on_dependency_change = "rerun"` so changed tests or configuration cannot
  reuse stale verdicts;
- `--max-children 2` in `Justfile` to bound local concurrency;
- the small runner wrapper that keeps native geospatial extensions loaded
  between mutmut's coverage and stats passes;
- the existing `do_not_mutate_patterns` for verified equivalent/no-op cases;
- the existing result parser that rejects every result not marked
  `killed`.

Add a project-foundation test that parses `pyproject.toml` and fails while
either narrowing key exists. This makes the repository-wide scope an explicit
quality contract.

## Survivor workflow

After the expanded campaign, classify every non-killed result:

1. For a real behavior gap, add the smallest test that expresses the surviving
   behavior, run it red, then add the smallest production/test change and run it
   green.
2. For a genuinely equivalent mutation, prove equivalence from the surrounding
   contract and add only its exact stable pattern to
   `do_not_mutate_patterns`, followed by a fresh campaign.
3. Do not suppress a mutation because it is inconvenient, slow, or difficult to
   test.

## Non-goals

- no production behavior, API, CLI, output, dependency, or concurrency changes;
- no weakening of coverage or mutation result parsing;
- no new mutation exclusions without a specific equivalence proof;
- no data-root, PBF, or publication operations.

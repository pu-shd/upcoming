# Developer documentation

For people changing this code. The [project README](../README.md) covers what it produces
and how to use it; these cover how it works and why it is shaped this way.

| | |
|---|---|
| [**architecture.md**](architecture.md) | The pipeline stage by stage, the module map, and the decisions each stage encodes |
| [**failure-modes.md**](failure-modes.md) | What can go wrong, what catches it, and what nothing catches |
| [**testing.md**](testing.md) | What the 891 tests cover, how they are organised, and how to add one |
| [**ci-cd.md**](ci-cd.md) | The four workflows, the cadence, and the publishing model |
| [**roadmap.md**](roadmap.md) | What to build next, ordered by whether anyone else has to act first |

## The one idea

Every design decision here follows from a single premise:

> **Silence must never equate to success.**

A feed that publishes nothing, a scrape that reaches no pages, a filter that matches
nothing, a workflow that stopped running — each produces output indistinguishable from
working correctly. The predecessor shipped four such failures to production, and every
mechanism in this repository exists to make one of them loud.

Read that sentence before changing anything. It explains why config has no defaults where
you would expect them, why gates measure rates rather than counts, and why several
functions refuse rather than guess.

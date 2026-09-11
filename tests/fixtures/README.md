# Test fixtures

Everything here is committed so the suite never touches the network. An autouse fixture in
`tests/conftest.py` makes any real socket connection raise, so a test that reaches out
fails loudly rather than becoming slow and flaky.

## `feeds/<source>/feed.ics`

Real ICS, copied from the predecessor repositories so the two shapes this project must
handle from one codebase are both covered.

| file | from | events | shape |
|---|---|---|---|
| `feeds/orfe/feed.ics` | `pubino/mae-upcoming` `tests/fixtures/orfe_shape.ics` | 14 | `SUMMARY` is the **speaker**; `LOCATION` is `101 - Sherrerd Hall` |
| `feeds/mae/feed.ics` | `pubino/mae-upcoming` `examples/sample_input.example.ics` | 9 | `SUMMARY` is the **title**; `LOCATION` is `Bowen Hall 222` or `Engineering Quad J Wing/J223` |

The ORFE feed carries two events with the same speaker and start time
(`ps_events:11931` and `ps_events:11941`, the second with the location typo
`101 - Sherrerd Hal`). Both must survive: a per-source feed is a faithful representation of
one upstream feed, and de-duplicating is the consumer's business. It is also the case that
proves output ordering needs a tiebreaker.

## `golden/<source>.predecessor.json`

The predecessor's own expected output for those same inputs, used by
`tests/test_differential.py` as an independent check on field-mapping semantics.

**They are a reliable guide to mapping and an unreliable guide to completeness.** Named
`.predecessor.json` rather than `.expected.json` for that reason — they are not this
project's expected output. Four verified caveats:

1. **`orfe.predecessor.json` has 13 records; its feed has 14.** The missing
   `ps_events:11941:delta:0` is present in the ICS and parses fine, so the golden is
   stale.
2. **The test guarding it could not notice.** It iterates the expected records and looks
   each up in produced output, under the comment *"Allow produced to contain additional
   events not yet listed in expected sample."* The MAE equivalent also accepts
   `location.name` and `location.detail` being swapped.
3. **Both were produced by a test-only config.** The tests set
   `represent_newlines_as = "literal_r"`, and since
   `collapse = collapse_whitespace_in_description and rep_mode == "space"`, that turns
   whitespace collapsing off. Production runs `"space"` and collapses. So the goldens
   preserve spacing production would not.
4. **Both are pre-enrichment.** Every ORFE `title` is `""`, which fails this project's
   schema. MAE's TBD event publishes the literal `"TBD"` with `titleSource` and
   `titleIsPlaceholder` absent entirely.

Each intended divergence from these files is asserted by its own named test, with its
reason, in `tests/test_differential.py`.

## `pages/<source>/*.html`

Captured event pages for enrichment tests. Trimmed, and deliberately covering the shapes
that vary *within* one source: MAE's FPO pages carry a speaker field with a bare name while
its seminar pages carry `div.event-subtitle` with name-and-affiliation.

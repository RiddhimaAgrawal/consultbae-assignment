# Stuck Log

This log documents the three hardest engineering problems I encountered while
building the assignment.

I used AI assistance during development, as permitted by the assignment. I
used it primarily as a coding and debugging assistant: to explore possible
approaches, explain unfamiliar behavior, and suggest alternatives. I did not
treat its suggestions as automatically correct. I tested the approaches
against the supplied data, investigated unexpected results, and made the final
implementation and design decisions based on the observed behavior.

For each problem below, I have documented what went wrong, how I investigated
it, what I asked AI, which suggestions I rejected, why I rejected them, and
what I ultimately implemented.
---

## 1. Preventing false positives in entity resolution

### The problem

One of the first challenges was deciding how aggressively to use fuzzy name
matching.

I needed to resolve cases such as `R. Verma` and `Rohit Verma`, but I did not
want the system to merge genuinely different people who happened to have
similar names or the same surname.

My initial approach used Python's `difflib.SequenceMatcher` on full names.
It successfully identified some similar names, but testing it against the
actual dataset exposed false positives such as `Varun Saxena` vs
`Vikram Saxena` and `Isha Chopra` vs `Sneha Chopra`.

The important realization was that a high string-similarity score does not
necessarily mean two people are the same person.

### How I investigated it

I searched for how Python's `SequenceMatcher.ratio()` works and looked into
common problems with fuzzy string matching. I also asked AI to run the
matching logic against the supplied data and expose the individual match
decisions instead of only showing the final merged groups.

This was important because the final output alone hid why some records were
being considered matches.

### What I considered and rejected

One suggested approach was simply to increase the similarity threshold, for
example from 0.80 to 0.90.

I rejected that as the primary solution because it only changes the cutoff.
It does not make the matching logic more meaningful. A different pair of
names
could still cross the new threshold, while legitimate abbreviated names could
be rejected.

### What I implemented

I made the fuzzy tier deliberately narrow.

Instead of treating generic character similarity as sufficient evidence, I
only allow this tier to handle a specific case: a genuine first-name initial
plus matching surname, such as:

`R. Verma` → `Rohit Verma`

The stronger identifiers remain higher priority:

1. exact normalized email
2. exact normalized phone
3. exact name + city without conflicting phone evidence
4. narrowly defined abbreviated-name matching

Same name alone is never enough.

### How I validated it

I reran the pipeline against the supplied records and added a regression test
for the more dangerous merge behavior.

### What I learned

For entity resolution, a more complicated fuzzy algorithm is not necessarily
better. Strong identifiers should drive automatic merges, while weaker signals
should be used conservatively.


## 2. Detecting a transitive false merge

### The problem

I had an explicit rule preventing two records with conflicting phone numbers
from being merged.

This correctly handled the planted `Arjun Mehta` case where two records had
the same name and city but different phone numbers.

However, when I built the complete canonical dataset, I discovered that the two
Arjun Mehta records still ended up in the same canonical group.

This was a more subtle problem than the original pairwise comparison.

### How I investigated it

I inspected the individual match decisions and traced how the records were
being grouped.

I found a third Arjun Mehta record from another source. It had the same name
and city but no phone number.

The situation was effectively:

    Arjun A ─── matches ─── Arjun C
                              │
                              │
                              └── matches ─── Arjun B

while:

    Arjun A ≠ Arjun B
    because their phone numbers conflict.

The pairwise checks were correct, but the grouping mechanism was transitive.
Once A and C were grouped and B and C were grouped, all three became part of
one canonical group.

### What I asked AI

I asked AI to explain why the pairwise conflict check was not preventing the
final group-level merge.

This helped me understand the distinction between checking the two records
being compared and checking the complete groups that would exist after the
merge.

### What I considered and rejected

One possible workaround was to abandon union-find and manually construct the
groups.

I rejected that because the grouping problem is naturally suited to
union-find. The problem was not the data structure itself; the problem was
where the conflict validation was being performed.

### What I changed

Before merging two existing groups, the pipeline now considers the identifiers
already present across both groups.

If combining the groups would introduce two different non-null phone numbers,
the merge is blocked.

I also added a regression test that recreates the transitive scenario so that
future changes to the matching logic cannot silently reintroduce it.

### What I learned

A pairwise matching rule is not necessarily sufficient when matches are
eventually converted into transitive groups.

For entity resolution, I need to reason about both:

- whether two records should match, and
- whether the resulting groups remain internally consistent.

## 3. Handling mixed CTC units

### The problem

The applicant data contained a `Current CTC` field, but the values were not
stored consistently.

Some values were annual INR amounts such as:

`417964`

while others were already represented as LPA:

`4.2`
`11.2`

There was no separate unit column telling me which interpretation to use.

I did not want to silently convert everything using an undocumented assumption.

### How I investigated it

I inspected the distribution of the values and sanity-checked them against
realistic salary ranges for early-career technology roles.

I also asked AI for possible ways to distinguish the units and for the
assumptions behind each approach.

### What I considered

One alternative was to infer the unit from the distribution of values or create
a manual-review category for ambiguous values.

I considered this more flexible, but for this dataset it would have introduced
additional complexity without handling any actual ambiguous records.

### What I implemented

I used a documented magnitude-based rule:

- values >= 1000 are interpreted as annual INR
- those values are converted to LPA by dividing by 100,000
- smaller values are treated as already being in LPA

The important part is that I documented this as a dataset-specific heuristic,
rather than pretending that the source data explicitly provided the unit.

### Why I think this is important

Data cleaning is not only about making values look consistent. When the source
does not contain enough information to know the correct interpretation with
certainty, the assumption should be explicit and reproducible.

If this were production data, I would prefer a source-system fix or an explicit
unit field rather than relying permanently on a magnitude heuristic.
---

## Development environment issues

I also encountered several environment/setup issues while getting the project
running, including Python not being available through the `python` command,
virtual-environment activation in Git Bash, an accidentally initialized Git
repository at the wrong directory level, and an incorrect FastAPI module name.

These were resolved through terminal inspection and documentation/AI
assistance, but I have kept them separate from the three main engineering
challenges because they were environment issues rather than problems in the
solution design.
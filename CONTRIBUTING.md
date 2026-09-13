# Contributing

This is a research prototype maintained by EVEMISS TECHNOLOGY CO., LTD. Contributions are welcome,
with these rules:

1. **Read HANDOFF.md first.** It is the living state of the lab: what is real, what is synthetic, what
   the next steps are.
2. **Invariants are not negotiable** (README → Invariants). A change that rewrites an existing grounding
   projection, lets a worker see the repository, or issues MACR authorities from code will not be merged.
3. **Versions are truth.** Contract text, projector output shape and validator rules are versioned;
   change the text → bump the version; never edit a recorded version in place.
4. **Synthetic is labelled.** Anything produced with the mock backend says SYNTHETIC and is never used
   as evidence about a model.
5. **Validated ≠ published.** Canonical assets, worker outputs and third-party excerpts are not committed.
6. Commit messages say what changed and why; AI-assisted commits carry a `Co-Authored-By` trailer.

Open an issue before a large change so the direction can be checked against the RKE series.

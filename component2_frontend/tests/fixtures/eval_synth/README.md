# Synthetic evaluation app (Phase 4d)

A tiny PHP app written for the tests of `tools/eval/score.py` and `tools/eval/run_eval.py`, with a hand-made gold
standard in the format of `docs/gold-standard-guide.md`. **Not an evaluation app**: no result here is a Phase 5 result.
Some gold rows are deliberately wrong (their notes say so), so that every mapping outcome of `docs/eval-mapping.md`
(correct, abstain, wrong, manual, miss, unsure, not in the mapping) is exercised. `synth.second.csv` is a second
annotator's `if` rows, for the agreement metric.

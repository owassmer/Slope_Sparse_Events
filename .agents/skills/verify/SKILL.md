---
name: verify
description: Run the real product and use it as a user would, and write what you found. The verifier's procedure.
---
The product is running for you, and `drive` is how you use it: the analysis page over each recorded run, and the
`slope` command line. Act as a user with `drive open`, `drive click`, `drive type` and `drive press`; look with
`drive see` (`--all` for the whole page). Use the command line with `drive run -- <arguments>` and the product's HTTP
routes with `drive send`. `drive --help` lists the actions, which are the same for every feature.

What the product runs with answers judgment questions only from saved responses and has no cloud credentials. A user
of this page never re-runs an investigation, asks a model or starts cloud work: a feature that fails for want of a
saved answer or a credential is a BROKEN line, not something to work around.

To check an outcome, reach the feature the way a user would, using `feature-map.md` where it has a line, and do the
thing. Then try the features that share a layer with what changed, two or three of them. Write one line per behaviour
you tried, starting with WORKS or BROKEN, then what you did and what you saw. For each feature you reached, write or
update its line in `feature-map.md`: the feature and the steps you took.
Passing gates say the code is well formed. Only the running product says the work is done.

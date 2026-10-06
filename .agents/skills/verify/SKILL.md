---
name: verify
description: Run the real product and use it as a user would. Use before calling anything done.
---
The product is the analysis page served over recorded runs, and the `slope` command line. `drive` is beside this
file (`.agents/skills/verify/drive`); `drive --help` lists its actions, which are the same for every feature.

Start the product from the checkout with `drive start`. Act as a user with `drive open`, `drive click`,
`drive type` and `drive press`; look with `drive see` (`--all` for the whole page). Use the command line with
`drive run -- <arguments>` and the product's HTTP routes with `drive send`. Stop it with `drive stop`.

What `drive` starts can answer judgment questions only from saved responses and has no cloud credentials. A user
of this page never re-runs an investigation, asks a model or starts cloud work, so neither do you: if a feature
fails for want of a saved answer or a credential, that is a BROKEN line, not something to work around.

To check an outcome, reach the feature the way a user would, using `feature-map.md` where it has a line, and do
the thing. Then try the features that share a layer with what changed, two or three of them. Write one line per
behaviour you tried, starting with WORKS or BROKEN, then what you did and what you saw. For each feature you
reached, write or update its line in `feature-map.md`: the feature, then the steps you took.
Passing gates say the code is well formed. Only the running product says the work is done.

# Findings: `design/002-answer-first.md`

## 1. The guaranteed percentile range is undefined when the changed tail contains the percentile

**Sources:** design lines 248–255, especially `(q - ε)/(1 - ε)` and `q/(1 - ε)` and the promise to show the guaranteed range when spending prevents a re-walk; `app/analysis/core.py:202–208`.

The formula does not define a range for all permitted judgment changes. For P5, let the exactly walked part carry 94% probability at $100 and the stubs carry 6%. The proposed lower quantile level is negative. A continuation putting the stubs at $0 makes the true P5 $0, while every quantile of the walked distribution is $100. Restricting the requested level to the walked distribution's endpoints would therefore still exclude the true percentile. The analogous problem occurs at the upper endpoint when ε exceeds `1 - q`. When an override moves all probability into stubs, ε is 1 and both expressions divide by zero.

These are precisely changes the new fallback must handle: the unseen region becomes important, and the real-judge spending limit can prevent a re-walk. The formula supplies no guaranteed range in that fallback state.

The retained unresolved-mass mechanism bounds how far missing probability can move a percentile's rank; its purpose still holds. It does not bound missing financial values by the observed head's extrema. The existing histogram quantile routine expects valid probability levels, not unknown-tail endpoints, so that retained mechanism does not supply the missing guarantee either.

## 2. Stub probability is not the missing mass of the retained headroom percentile

**Sources:** design lines 56–64, 204–206, 248–251; `app/analysis/page.py:899–920` (`monthly_table`); `app/analysis/core.py:321–333` (headroom counts); `app/analysis/engine.py:385–392` (headroom recorded only on live due dates).

The plan applies its percentile construction using ε equal to the stubs' total path probability. That works for a distribution with one observation per trajectory, but the retained monthly headroom P5 is over **trajectory × due-date** observations. Histories contribute different numbers of observations, and the histogram normalizes their weighted counts. Petition timing and future funding change that denominator.

For example, let the exact head have probability 0.996 and one live due date with headroom $100 before a filing. A stub of probability 0.004 can continue without that filing, retain that first observation, and add 29 live due dates with headroom $0. The low-headroom observations then carry `0.004 × 29 / (0.996 + 0.004 × 30) ≈ 10.4%` of the monthly distribution. Its P4.5, P5 and P5.5 are all $0. Yet the proposed bounds read only quantiles of the head at approximately P4.62 and P5.02, both $100. Here both requested levels are valid: this failure is independent of the endpoint problem above.

The existing due-date restriction measures liquidity when payments actually fall due, rather than on arbitrary calendar days. Its purpose remains applicable. The plan explicitly keeps P5 headroom but does not account for why its record has variable observation counts. Neither the retained half-bin approximation nor an exact total stub probability controls that conditional distribution's missing mass; the promised guarantee and re-walk criterion therefore do not cover this retained page figure.

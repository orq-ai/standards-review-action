# Coding standards

Each numbered line in the form `N. **Title.** Explanation` is one rule. The action asks the model one question per rule for every changed file. Other lines (headings, prose, sub-bullets) are ignored, so you can add context freely.

1. **Distinguish absence from failure.** Do not turn a failed call, parse error, or timeout into an empty result, a default value, or a silent skip. Raise, or return a result that says it failed.
2. **Test the behavior behind the claim.** A change that claims new or fixed behavior comes with a test that fails without it. Mock only boundaries that are impractical to run.
3. **Correct stale claims with the change.** When code changes what a README, docstring, or comment says, update that text in the same change.
4. **Bound automation and privilege.** Scripts, CI jobs, and agents get the least permissions they need, fixed timeouts, and bounded retries.

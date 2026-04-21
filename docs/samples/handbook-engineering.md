# Engineering conventions

The conventions below are not recommendations. They are defaults — do this unless you have
a specific reason not to, and if you do deviate, write down why in the PR description.

## Code review

Every change is reviewed by at least one other engineer before merge. Reviews should focus
on correctness, readability, and whether the change matches the stated intent. Style
nitpicks that a linter could catch are not a good use of review time. If your review would
be improved by knowing something the author didn't put in the description, ask.

Target response time on a review request is one business day. If a review has been sitting
for longer than that without a response, ping the reviewer directly — do not assume they
saw it. Slack is fine; so is pulling them aside in the office if that's an option.

## Testing

Every bug fix ships with a regression test that fails before the fix and passes after. If
the bug cannot be reproduced in a test, fix the test harness before fixing the bug. We have
been burned too many times by "looks right, works locally" fixes for bugs that reappear two
releases later.

Integration tests hit real dependencies, not mocks. If you find yourself wanting to mock
the database, ask first — almost always the answer is "use a real database and clean up
after yourself". Mocks diverge from reality and pass while production breaks.

Unit tests are fine for logic that does not cross a system boundary. Do not write unit
tests that assert on the shape of mocks — those tests pass forever and protect nothing.

## Deployment

Changes ship via a green CI pipeline to a staging environment, bake for at least 30
minutes, and then promote to production via a one-click deploy. The 30-minute bake is not
a formality. It is specifically long enough for our synthetic-traffic monitor to run a
full cycle and surface regressions that unit tests do not catch.

Rollback is a first-class operation. If you are on call and you see a production
regression, the correct first move is to roll back. Investigate afterwards. Heroic
in-flight fixes are how three-minute outages become three-hour outages.

## On-call

On-call rotation is one week, handed off on Wednesdays at 10am. The handoff is a live
conversation, not a wiki update — the outgoing on-call walks the incoming on-call through
anything open. We rotate in pairs for new hires' first three weeks.

Escalation for anything above a SEV-3 goes to the engineering manager in the affected area,
not to a generic alias. If you are on call and you are genuinely stuck, page your manager.
This is what they are there for.

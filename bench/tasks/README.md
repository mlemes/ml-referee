# Agent tasks

Scripted attacks use synthetic tool-call events and direct module calls. They do
not measure agent behavior. Audit flags count as detection, not access prevention;
A5 counts reuse attempts only after legitimate evaluations exhaust the budget.
C2 models literal deny rules for evaluator and holdout paths. A7 records the
remaining risk from commands that evade configured trial patterns.

BAITBENCH tasks and the project pipeline task are pending under
[the evaluation protocol](../../docs/research/evaluation/protocol.md). Release and
license confirmation, task setup, the two-model pilot, variance estimates, and
power analysis remain pending. No agent results are claimed here.

The bare symlink A2 command currently bypasses the guard. Keep this failure visible;
the scripted suite is not evidence that all holdout access is prevented.

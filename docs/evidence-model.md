# Evidence Model

## Purpose

The SRE agent is designed to reason from verifiable operational evidence rather than from unsupported assumptions.

The evidence model separates:

* raw observations,
* normalized evidence,
* incident diagnosis,
* confidence,
* remediation policy,
* verification results.

This separation is important because Kubernetes failures often have symptoms that are easy to observe but causes that remain uncertain.

The system therefore attempts to make a clear distinction between:

```text
what happened
```

and:

```text
why it happened
```

## Evidence Pipeline

Conceptually, the workflow is:

```text
Kubernetes / application signals
        |
        v
raw observations
        |
        v
normalized incident evidence
        |
        v
AI-assisted diagnosis
        |
        v
observed condition
+
possible root cause
+
confidence
        |
        v
deterministic policy
        |
        v
no action / automatic action / human approval
        |
        v
remediation
        |
        v
post-action verification
```

The LLM does not directly observe the Kubernetes cluster.

It reasons over evidence gathered through controlled application logic and read-only Kubernetes access.

## Operational Evidence Sources

The SRE agent can collect evidence from several sources.

### Pod State

Pod and container state provides information such as:

* Pod identity,
* creation time,
* scheduling state,
* readiness,
* restart count,
* current container state,
* previous container termination state,
* exit code,
* termination reason.

Examples of directly observable facts include:

```text
container restart count = 4
last termination reason = OOMKilled
exit code = 137
pod Ready = false
```

These facts can be used as high-confidence evidence.

## Kubernetes Events

Events provide supporting context around workload behavior.

Examples include:

* Pod scheduling,
* container creation,
* container start,
* readiness failures,
* liveness failures,
* restart backoff,
* image pulls,
* rollout activity.

Events are useful for reconstructing the sequence of an incident.

They are treated as evidence rather than as a complete diagnosis.

For example:

```text
BackOff restarting failed container
```

shows repeated container failure, but does not by itself establish why the container failed.

## Container Logs

The SRE agent can inspect application container logs.

Logs may provide:

* application exceptions,
* request failures,
* dependency errors,
* startup failures,
* timing information.

Logs are correlated with Kubernetes state.

The absence of an application exception is also relevant when Kubernetes reports a process-level termination such as `OOMKilled`.

A container may be terminated by the kernel without the application producing a useful memory error in its own logs.

## Deployment and ReplicaSet History

Deployment history provides evidence for determining whether an incident correlates with a recent release.

The system can inspect:

* Deployment revision,
* ReplicaSets,
* container image,
* Pod template configuration,
* resource configuration,
* timing of rollout activity.

This is especially important for rollback decisions.

A failing workload does not automatically imply that rollback is appropriate.

Evidence should support a meaningful difference between the current revision and a previous safe revision.

## Health Probes

Application readiness is independently checked through the bulletin-board readiness endpoint.

The agent records:

* HTTP status,
* success or failure,
* observation timestamp,
* connection errors.

This helps distinguish Kubernetes object state from application-level service availability.

For example, after remediation the agent does not rely only on:

```text
Deployment updated successfully
```

It also verifies that the application becomes reachable and healthy.

## Dependency State

The agent can inspect relevant dependency state when diagnosing application failures.

For the demonstrated application this includes PostgreSQL.

Evidence may include:

* PostgreSQL Pod readiness,
* restart count,
* application readiness behavior,
* application logs indicating database connectivity problems.

This allows the agent to avoid incorrectly attributing every application failure to the application itself.

## Evidence Normalization

Raw evidence from different sources is converted into a consistent incident representation.

The goal is to allow the workflow to reason over a timeline such as:

```text
19:34:30
api container terminated
reason = OOMKilled
exitCode = 137

19:34:31
Kubernetes restarted container

19:34:36
BackOff event observed

19:34:44
readiness probe connection refused

19:34:46
application temporarily Ready
```

A timeline is more useful than treating each individual signal independently.

## Facts Versus Inference

A central design principle is that observable conditions and inferred root causes are stored separately.

For example:

```text
Observed condition:
container_memory_limit_exceeded

Evidence:
OOMKilled
exit code 137
memory limit 192Mi
repeated restarts

Possible causes:
application memory-growth defect
workload-driven memory demand
undersized memory limit
```

The first group is strongly supported by operational evidence.

The second group contains hypotheses.

The system should not collapse those two categories into a single claim.

## Diagnosis Structure

A diagnosis may contain fields such as:

```text
summary
evidence
risk_notes
observed_condition
condition_confidence
likely_root_cause
root_cause_confidence
recommended_human_action
```

These fields intentionally separate confidence in the observable condition from confidence in the cause.

For example:

```text
observed_condition:
container_memory_limit_exceeded

condition_confidence:
0.99

likely_root_cause:
unknown
```

This allows the agent to be confident that a specific operational failure occurred while remaining appropriately uncertain about why it occurred.

## Confidence

Confidence is used as part of the diagnostic output, but confidence alone does not authorize remediation.

A high-confidence diagnosis can still require human approval.

Conversely, deterministic rules can deny action even when the model recommends one.

The architecture therefore treats confidence as evidence for policy evaluation rather than as an authorization mechanism.

## Scenario 1 — Pod Disappearance

### Observation

An application Pod disappears.

Evidence includes:

* old Pod termination,
* new Pod creation,
* scheduling,
* container startup,
* readiness recovery.

### Interpretation

The condition is:

```text
pod_replaced
```

Kubernetes performs the recovery through the Deployment controller.

### Decision

The SRE agent verifies recovery.

No remediation action is required.

The evidence demonstrates that the platform's native self-healing mechanism worked as intended.

## Scenario 2 — Deployment Regression

### Observation

A recently deployed revision begins producing application failures.

Evidence may include:

* recent Deployment change,
* new ReplicaSet,
* failures beginning after rollout,
* readiness degradation,
* application errors,
* previous revision history.

### Interpretation

The workflow evaluates whether there is sufficient correlation between the failure and a recent deployment change.

### Decision

If deterministic rollback conditions are satisfied, the SRE agent may automatically restore the previous safe revision.

The decision does not depend solely on an LLM statement such as:

```text
This looks like a deployment problem.
```

It depends on supporting Deployment and ReplicaSet evidence plus deterministic policy.

## Scenario 3 — Repeated OOMKilled

### Observation

The application repeatedly restarts.

Evidence includes:

```text
last termination reason = OOMKilled
exit code = 137
memory request = 96Mi
memory limit = 192Mi
repeated restart cycles
readiness instability
PostgreSQL healthy
```

Multiple replacement Pods show the same behavior.

### Interpretation

The immediate operational condition is strongly supported:

```text
container_memory_limit_exceeded
```

However, available evidence does not prove why memory consumption reached the limit.

Possible explanations include:

* application memory growth,
* workload demand,
* an undersized configured limit.

### Decision

The system therefore does not claim a confirmed application memory leak.

Automatic remediation is denied.

Human review is required before applying the bounded memory increase.

## Temporal Correlation

Time is an important part of the evidence model.

The system attempts to correlate:

* rollout time,
* Pod creation,
* first failure,
* restart sequence,
* readiness failures,
* remediation time,
* recovery observations.

Temporal correlation can strengthen a hypothesis but does not necessarily prove causality.

For example:

```text
deployment changed
5 seconds later failures began
```

is useful evidence for a deployment regression.

It is stronger than observing a failure without any recent deployment change.

## Negative Evidence

The system can also use evidence that argues against a hypothesis.

Examples include:

```text
PostgreSQL is Ready with zero restarts
```

which weakens the hypothesis of a database outage.

Or:

```text
multiple affected revisions use the same image and resource configuration
```

which weakens the case for rollback.

This is important because reliable diagnosis requires considering both supporting and contradicting evidence.

## Remediation Evidence

When a remediation executes, the action itself becomes part of the incident record.

Examples include:

* requested action,
* policy decision,
* human approval,
* target resource,
* original configuration,
* new configuration,
* Deployment revision after change.

This provides traceability between diagnosis and execution.

## Verification Evidence

A remediation is not considered successful merely because the Kubernetes API accepted a request.

The system records a verification window.

For each observation it may record:

* timestamp,
* application probe result,
* Deployment revision,
* Pod restart counts,
* memory limit,
* replica readiness.

For example, after the Scenario 3 mitigation the workflow observed:

```text
memory limit = 512Mi
replacement Pod Ready
restart count = 0
readiness endpoint = HTTP 200
```

over multiple consecutive checks.

Only after sufficient successful observations was the remediation recorded as succeeded.

## Mitigation Versus Root-Cause Resolution

Post-remediation stability is not automatically treated as proof that the underlying cause is fixed.

This is especially important for resource incidents.

Scenario 3 records:

```text
root_cause_resolved = false
```

even though the application stabilized after the memory limit was increased.

The distinction is:

```text
Mitigation:
service stability improved

Root-cause resolution:
underlying cause identified and eliminated
```

The demonstrated action accomplished the first, not the second.

## Persistence

Evidence and incident history are persisted so that diagnosis is inspectable after the live event.

Stored information includes:

* incident records,
* Pod observations,
* probe history,
* Deployment history,
* Kubernetes Events,
* diagnosis output,
* approvals,
* remediation records,
* verification observations.

This provides a historical operational record rather than relying only on transient console output.

## Evidence Quality Principles

The implementation follows several evidence principles.

### Prefer Direct Observation

Prefer:

```text
termination reason = OOMKilled
```

over:

```text
the application probably ran out of memory
```

### Preserve Uncertainty

If evidence cannot establish a cause, record the cause as uncertain.

### Correlate Multiple Sources

A stronger diagnosis combines:

```text
Pod state
+
Events
+
logs
+
Deployment history
+
health probes
+
dependency state
```

rather than relying on one signal.

### Look for Contradicting Evidence

Evidence that weakens a hypothesis should be retained, not discarded.

### Separate Diagnosis From Authorization

Evidence and reasoning explain what appears to have happened.

Policy and RBAC determine what may be changed.

### Verify Outcomes

A remediation is incomplete until workload behavior is checked after the action.

## Why This Matters

An AI system that only generates plausible operational explanations may be useful as a chatbot, but it is difficult to trust during production incidents.

An SRE workflow becomes more useful when an engineer can inspect:

```text
What did the system observe?

What conclusion did it draw?

How confident was it?

Which evidence supports the conclusion?

What evidence contradicts alternatives?

Why was an action allowed or denied?

What changed?

Did the workload actually recover?
```

The purpose of the evidence model is to make those questions answerable.

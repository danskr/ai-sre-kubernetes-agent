# Safety Model

## Purpose

The SRE agent is designed to assist with Kubernetes incident diagnosis and remediation without giving an LLM unrestricted operational authority.

The safety model uses multiple independent control layers:

1. evidence-grounded diagnosis,
2. deterministic remediation policy,
3. human approval for higher-risk actions,
4. Kubernetes RBAC enforcement,
5. bounded remediation implementations,
6. post-action verification and audit persistence.

The LLM participates in reasoning, but it is not the final authority over Kubernetes changes.

## Core Principle

The system separates:

```text
reasoning
    from
authorization
    from
execution
```

An AI-generated recommendation does not automatically become an infrastructure change.

A remediation can execute only when deterministic application logic and Kubernetes authorization both permit it.

## Layer 1 — Evidence-Grounded Diagnosis

The agent bases diagnoses on observable operational evidence such as:

* Pod state,
* restart counts,
* previous container termination state,
* container logs,
* Kubernetes Events,
* Deployment revisions,
* ReplicaSets,
* resource requests and limits,
* readiness results,
* Services and Endpoints,
* dependency health.

The system attempts to distinguish between an observed condition and an inferred root cause.

For example:

```text
Observed:
container terminated with OOMKilled
exit code 137
memory limit = 192Mi

Inferred:
possible application memory growth
possible workload-driven demand
possible undersized memory limit
```

`OOMKilled` is strong evidence that the configured memory limit was exceeded.

It is not sufficient evidence by itself to conclude that the application contains a memory leak.

This distinction is important because remediation decisions should not depend on unsupported causal claims.

## Layer 2 — Deterministic Policy

The LLM does not decide whether arbitrary Kubernetes changes are allowed.

Deterministic application logic evaluates whether a remediation is permitted.

Policy controls include:

* whether automatic remediation is enabled,
* incident classification,
* confidence thresholds,
* deployment recency,
* rollback eligibility,
* remediation cooldowns,
* allowed human actions,
* bounded resource targets,
* required verification.

For example, a deployment regression may qualify for automatic rollback when evidence correlates the failure with a recent Deployment change.

A repeated resource-exhaustion incident does not qualify for the same automatic policy because increasing resources can mask an application defect or consume additional cluster capacity.

## Layer 3 — Human Approval

Actions with greater uncertainty or operational risk require explicit human approval.

The repeated OOMKilled scenario demonstrates this behavior.

The workflow:

```text
detect repeated OOMKilled
        |
        v
collect evidence
        |
        v
diagnose resource-limit breach
        |
        v
deterministic policy
        |
        v
automatic action denied
        |
        v
LangGraph interrupt
        |
        v
human review
        |
        +---- reject ----> no action
        |
        +---- approve ---> bounded mitigation
```

The human does not provide an arbitrary shell command.

The approved action is selected from a predefined set of allowed operations.

For the demonstrated resource incident, the permitted human action is:

```text
increase_memory_limit
```

## Layer 4 — Bounded Remediation

Remediation implementations are intentionally constrained.

For the demonstrated memory mitigation:

```text
target Deployment: bulletin-board
target container:  api
original limit:    192Mi
permitted target:  512Mi
hard maximum:      512Mi
```

The human approval does not authorize an arbitrary memory value.

The workflow cannot translate approval into:

```text
2Gi
8Gi
unlimited
```

because the target and maximum are defined by deterministic configuration.

The mitigation is also explicitly recorded as temporary operational headroom rather than proof that the root cause has been fixed.

## Layer 5 — Kubernetes RBAC

Kubernetes provides an infrastructure-level authorization boundary independent of the AI workflow.

The SRE agent uses a dedicated ServiceAccount.

Its Role provides read access to:

* Pods,
* Pod logs,
* Events,
* Services,
* Endpoints,
* Deployments,
* ReplicaSets.

Its only Kubernetes write permission is:

```text
PATCH
apps/deployments
resourceName: bulletin-board
```

This means the agent cannot:

* delete Pods,
* create arbitrary workloads,
* patch arbitrary Deployments,
* modify PostgreSQL resources,
* operate on unrelated application namespaces,
* access the user-agent namespace.

Even if application logic attempted an unauthorized Kubernetes action, the Kubernetes API would independently reject it.

## Why Pod Deletion Is Not Allowed

The agent deliberately has no permission to delete Pods.

Pod deletion is often used operationally as a convenient restart mechanism, but granting it would significantly broaden the agent's ability to disrupt workloads.

Scenario 1 demonstrates that Kubernetes already provides native self-healing.

When a Pod disappears, the SRE agent observes the event, verifies that Kubernetes restores the workload, and records the incident without performing remediation.

This avoids duplicating capabilities that Kubernetes already provides.

## Why Rollback Is Conditional

Rollback is not treated as a universal response to application failure.

Before rollback, the workflow evaluates whether evidence supports a recent deployment regression.

Relevant evidence can include:

* Deployment revision,
* ReplicaSet history,
* image changes,
* pod-template changes,
* timing correlation between rollout and failure.

If the affected revisions contain the same image and equivalent configuration, rollback is not considered supported by the available evidence.

This prevents the system from performing a rollback simply because an application is unhealthy.

## Why Resource Increases Require Human Approval

Increasing memory can restore service availability, but it carries different risks from rollback.

A larger limit may:

* hide an application memory-growth defect,
* increase node-level memory pressure,
* delay rather than eliminate another OOM,
* increase infrastructure consumption.

Therefore repeated OOMKilled incidents are classified outside the autonomous rollback policy.

The system may recommend additional headroom, but execution requires explicit human approval.

## LLM Tool Boundary

Read-only operational capabilities may be exposed to AI reasoning.

Infrastructure-changing operations are not implemented as unrestricted generic LLM tools.

Instead, writes are executed through deterministic workflow nodes with predefined behavior.

This prevents a model from freely constructing arbitrary Kubernetes mutations.

Conceptually:

```text
LLM:
"What appears to be happening?"

Policy:
"Is any action permitted?"

Human:
"Is approval required and granted?"

Remediation code:
"What exact bounded operation is executed?"

Kubernetes RBAC:
"Is this identity authorized to perform it?"
```

Each layer has a different responsibility.

## Post-Remediation Verification

A successful Kubernetes API request is not considered sufficient evidence of successful remediation.

After an action, the workflow verifies workload recovery.

Verification may include:

* Deployment readiness,
* replacement Pod readiness,
* readiness endpoint responses,
* restart behavior,
* repeated successful health checks.

For the human-approved memory mitigation, the agent required multiple consecutive successful observations before marking the remediation as succeeded.

This avoids treating:

```text
PATCH returned HTTP 200
```

as equivalent to:

```text
service recovered
```

## Auditability

Operational decisions are persisted.

The system records:

* incident identity,
* collected evidence,
* diagnosis,
* confidence,
* policy decision,
* approval requirement,
* human decision,
* remediation action,
* remediation result,
* verification observations.

This allows an engineer to inspect not only what action occurred, but why the system believed the action was appropriate.

## Safety Demonstrated Across the Three Scenarios

### Scenario 1 — Observe Only

Failure:

```text
application Pod disappears
```

Behavior:

```text
Kubernetes replaces Pod
SRE agent observes recovery
SRE agent verifies health
no write action executed
```

Safety property:

The agent does not interfere with normal Kubernetes self-healing.

### Scenario 2 — Bounded Automatic Action

Failure:

```text
recent Deployment introduces runtime regression
```

Behavior:

```text
agent correlates failure with Deployment change
deterministic policy permits rollback
Deployment is patched to previous safe revision
recovery is verified
```

Safety property:

Automatic remediation is permitted only for a specifically supported incident class.

### Scenario 3 — Human-Approved Action

Failure:

```text
repeated OOMKilled restart cycle
```

Behavior:

```text
agent identifies memory-limit breach
root cause remains uncertain
automatic remediation is denied
workflow pauses
human approval is required
memory limit increases from 192Mi to 512Mi
recovery is verified
root cause remains marked unresolved
```

Safety property:

Operational stabilization is explicitly separated from root-cause resolution.

## Defense in Depth

No single mechanism is expected to provide complete safety.

The project instead uses overlapping controls:

```text
Operational evidence
        ↓
AI reasoning
        ↓
Deterministic policy
        ↓
Human approval when required
        ↓
Bounded remediation implementation
        ↓
Kubernetes RBAC
        ↓
Post-action verification
        ↓
Persistent audit record
```

A failure or incorrect judgment at one layer therefore does not automatically grant unrestricted infrastructure control.

## Security Scope

This project is an experimental SRE system and not a production security product.

Production deployment would require additional controls such as:

* stronger identity and secret management,
* production-grade persistent workflow checkpoints,
* network policies,
* admission controls,
* centralized audit logging,
* metrics and alerting,
* multi-user authorization,
* approval authentication,
* rate limiting,
* high availability,
* broader automated testing and policy validation.

The current implementation focuses on demonstrating the architectural principle that AI-assisted operational reasoning can be useful without making the AI model the infrastructure authorization boundary.

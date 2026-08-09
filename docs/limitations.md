# Limitations

## Purpose

This project is a controlled SRE prototype intended to explore safe, evidence-driven AI assistance for Kubernetes incident diagnosis and remediation.

It is not a production-ready autonomous operations platform.

The current implementation deliberately keeps the environment small and the remediation surface narrow so that the safety and reasoning behavior can be inspected clearly.

## Single-Node Kubernetes Environment

The project currently runs on a single-node Kubernetes cluster.

This is sufficient for demonstrating:

* Pod replacement,
* Deployment rollouts,
* readiness behavior,
* application failures,
* resource exhaustion,
* remediation workflows.

It does not demonstrate behavior across:

* multiple worker nodes,
* node failures,
* pod rescheduling between nodes,
* topology constraints,
* cluster autoscaling,
* multi-zone availability.

A production evaluation would require a multi-node cluster.

## Synthetic Application

The bulletin-board service is intentionally small.

This keeps failure behavior understandable and allows the project to focus on SRE reasoning rather than application complexity.

Real production systems would introduce additional challenges such as:

* many microservices,
* asynchronous messaging,
* distributed transactions,
* multiple databases,
* caches,
* external dependencies,
* service meshes,
* complex ownership boundaries.

The diagnostic approach would need to scale to a much larger evidence graph.

## Synthetic Fault Injection

The demonstrated failures are intentionally reproducible.

The bulletin-board application contains controlled demo fault mechanisms used to generate specific operational conditions.

These mechanisms are useful for experimentation but are not representative of every real-world failure mode.

The current scenarios demonstrate only:

1. Pod disappearance and Kubernetes self-healing.
2. A deployment-related runtime regression.
3. Repeated container OOMKilled failures.

The project should therefore not be interpreted as demonstrating general Kubernetes incident coverage.

## Limited Incident Taxonomy

The SRE agent recognizes a small number of incident classes.

It does not currently provide specialized workflows for conditions such as:

* node pressure,
* disk exhaustion,
* DNS failures,
* certificate expiration,
* network policy failures,
* image pull failures,
* scheduling failures,
* persistent-volume failures,
* service mesh problems,
* cluster control-plane failures.

The current scope is intentionally limited to the three demonstrated scenarios.

## No Full Metrics Platform

The current evidence model is primarily based on:

* Kubernetes API state,
* Events,
* container logs,
* Deployment history,
* readiness probes,
* dependency state.

A production SRE system would normally also use metrics and traces from systems such as:

```text
Prometheus
OpenTelemetry
Grafana
distributed tracing platforms
cloud monitoring services
```

For example, Scenario 3 establishes that the container exceeded its memory limit, but it does not collect a full memory-usage time series or heap profile.

This is why the workflow correctly avoids claiming that an application memory leak has been proven.

## LLM Diagnosis Is Not Deterministic

AI-generated diagnosis remains probabilistic.

The same evidence may occasionally result in different wording, confidence, or hypotheses.

The architecture therefore avoids using LLM output as the sole authorization mechanism for infrastructure changes.

Deterministic policy and Kubernetes RBAC provide independent control boundaries.

Further production hardening would require systematic evaluation of diagnostic consistency and failure modes across a larger incident dataset.

## Confidence Is Not Calibrated Scientifically

The system records confidence values for diagnostic conclusions.

These values are useful for workflow reasoning and human interpretation but should not currently be treated as statistically calibrated probabilities.

A production-quality system would need evaluation against labeled incident data to determine whether reported confidence correlates reliably with diagnostic accuracy.

## Narrow Remediation Surface

The agent intentionally supports only a very small set of write operations.

The demonstrated Kubernetes write boundary is restricted to:

```text
PATCH deployment/bulletin-board
```

Supported actions include:

* policy-controlled rollback,
* a bounded human-approved memory-limit increase.

The agent does not provide arbitrary shell or `kubectl` execution.

This limits operational flexibility, but it is intentional.

The project prioritizes controllability over broad autonomous capability.

## Human Approval Is Prototype-Grade

Scenario 3 uses LangGraph human-in-the-loop execution to pause the workflow and request approval.

This demonstrates the control flow, but the current approval mechanism is not a complete production authorization system.

A production implementation would require:

* authenticated users,
* role-based approval permissions,
* approval ownership,
* approval expiration,
* stronger audit identity,
* possibly multi-party approval for high-risk actions.

The current system records the human decision but does not provide enterprise-grade approval governance.

## Development LangGraph Runtime

LangSmith Studio is connected to a development-mode LangGraph Agent Server.

The current runtime stores workflow thread/checkpoint state locally within the development environment.

Replacing the SRE-agent Pod can therefore cause existing workflow thread state to be lost even though incident records remain persisted in PostgreSQL.

This limitation was observed during Scenario 3 testing.

A production implementation should use durable LangGraph checkpoint and thread persistence independent of an individual Pod lifecycle.

## Single SRE Agent Replica

The SRE agent currently runs as a single replica.

The project does not address:

* leader election,
* duplicate incident processing,
* distributed locking,
* active-active execution,
* workflow ownership after failover.

A production deployment would need explicit coordination when multiple observers are running.

## PostgreSQL Deployment Is Development-Oriented

PostgreSQL runs inside the same demonstration Kubernetes cluster.

This is convenient for the project but is not representative of a production-grade database architecture.

The current setup does not include:

* database high availability,
* automated backups,
* disaster recovery,
* replication,
* managed database infrastructure.

The database is sufficient for demonstration persistence only.

## Storage Class Is Environment-Specific

The PostgreSQL manifest currently references:

```text
local-path-retain
```

as its StorageClass.

This exists in the development cluster but may not exist in another Kubernetes environment.

Anyone reproducing the project may need to replace this value with an available StorageClass.

## No NetworkPolicy Enforcement

Namespaces and Kubernetes RBAC provide logical separation, but the current demo does not implement Kubernetes NetworkPolicies.

For example, the user-agent is isolated from the SRE agent operationally, but network-level communication restrictions are not currently used as an additional enforcement layer.

A production system should explicitly define permitted network paths.

## Secrets Management Is Simplified

The repository contains only example Kubernetes Secret manifests with placeholder values.

Real credentials are intentionally excluded from Git.

The demonstration uses Kubernetes Secrets, but a production deployment would typically integrate with a dedicated secret-management solution such as:

* a cloud secrets manager,
* HashiCorp Vault,
* an external secrets operator,
* hardware-backed key management where appropriate.

## No Admission-Control Layer

The project currently relies on:

```text
application policy
+
Kubernetes RBAC
```

for write safety.

A production architecture could add another enforcement layer using admission policy systems such as:

* ValidatingAdmissionPolicy,
* OPA Gatekeeper,
* Kyverno.

This could independently reject changes outside defined remediation boundaries.

## Limited Security Hardening

The containers use several basic hardening measures, including:

* non-root execution,
* dropped Linux capabilities,
* disabled privilege escalation,
* disabled ServiceAccount token mounting where it is unnecessary.

However, a production environment would require a broader security review covering areas such as:

* image provenance,
* vulnerability scanning,
* signed images,
* network policy,
* Pod Security Standards,
* runtime security,
* dependency management,
* supply-chain security.

## No Production-Scale Observability for the SRE Agent

The system persists incident and remediation information, but the SRE agent itself is not yet operated as a production SRE service.

A mature implementation would require its own:

* metrics,
* SLOs,
* alerts,
* structured logging,
* traces,
* health dashboards,
* capacity planning.

An SRE system must itself be observable and reliable.

## Verification Windows Are Short

Post-remediation verification intentionally uses short windows so scenarios can be demonstrated interactively.

For example, Scenario 3 verifies multiple successful readiness checks after increasing memory.

This establishes short-term stabilization.

It does not prove long-term stability.

A slow memory-growth defect could exceed the new limit later.

This is why the system records:

```text
root_cause_resolved = false
```

even after successful mitigation.

## Mitigation Does Not Equal Root-Cause Resolution

Scenario 3 demonstrates an important limitation intentionally.

Increasing the memory limit from:

```text
192Mi -> 512Mi
```

provided additional operational headroom.

It did not identify or eliminate the underlying source of memory growth.

The system therefore treats the action as mitigation rather than resolution.

A complete investigation could require:

* memory usage metrics,
* heap profiling,
* allocation analysis,
* workload analysis,
* application debugging.

## No Formal Benchmark Yet

The project's original evaluation dimensions are:

* diagnostic accuracy,
* evidence traceability,
* operational safety,
* practical usability.

The current scenarios provide qualitative demonstrations of all four.

However, the project does not yet contain a statistically meaningful benchmark.

A larger evaluation could introduce:

* many repeated incident runs,
* known ground-truth causes,
* false-positive measurements,
* false-negative measurements,
* diagnosis accuracy scoring,
* remediation success rates,
* human-review measurements.

The current results should therefore be interpreted as experimental demonstrations rather than benchmark claims.

## Scope of the Conclusion

The project does not attempt to demonstrate that AI should autonomously operate production Kubernetes environments.

A narrower conclusion is supported by the experiments:

```text
AI-assisted operational reasoning can be useful when it is grounded in
observable evidence and separated from deterministic authorization,
bounded remediation, human oversight, and infrastructure-level access control.
```

That separation is the central architectural principle being evaluated by this project.

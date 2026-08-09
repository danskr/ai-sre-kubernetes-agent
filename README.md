# AI SRE Kubernetes Agent

Evidence-driven AI-assisted Kubernetes incident diagnosis and remediation with deterministic safety controls, bounded automation, human approval, and verifiable operational evidence.

## Project Question

> Can an AI agent system diagnose Kubernetes deployment failures using verifiable operational evidence while remaining safe, explainable, and useful to production engineers?

This project explores that question across four dimensions:

* **Diagnostic accuracy** — can the system correctly characterize an operational failure?
* **Evidence traceability** — can an engineer see what evidence supports the diagnosis?
* **Operational safety** — can AI reasoning be separated from infrastructure authorization?
* **Practical usability** — can the system actually participate in incident response rather than only produce text?

The project demonstrates three Kubernetes incident scenarios with intentionally different remediation policies.

---

## Key Idea

The project does **not** give an LLM unrestricted Kubernetes access.

Instead, it separates:

```text
observation
    ↓
evidence collection
    ↓
AI-assisted diagnosis
    ↓
deterministic policy
    ↓
human approval when required
    ↓
bounded remediation
    ↓
Kubernetes RBAC
    ↓
post-action verification
    ↓
persistent audit record
```

The LLM can help answer:

```text
What appears to be happening?
```

It does not independently decide:

```text
What infrastructure change am I allowed to execute?
```

Authorization remains controlled by deterministic workflow logic and Kubernetes RBAC.

---

# Demonstrated Scenarios

| Scenario                        | Failure                                                         | Agent Behavior                                                            | Remediation                              |
| ------------------------------- | --------------------------------------------------------------- | ------------------------------------------------------------------------- | ---------------------------------------- |
| **1 — Kubernetes self-healing** | Application Pod deleted                                         | Detects disruption, observes Kubernetes recovery, verifies health         | None                                     |
| **2 — Deployment regression**   | Recent application revision develops runtime DB-pool exhaustion | Correlates failures with recent deployment and healthy dependency         | Automatic bounded rollback               |
| **3 — Repeated OOMKilled**      | Container repeatedly exceeds 192Mi memory limit                 | Identifies memory-limit breach but preserves uncertainty about root cause | Human-approved bounded increase to 512Mi |

The scenarios deliberately demonstrate three different authority levels:

```text
Scenario 1
observe only

Scenario 2
bounded autonomous remediation

Scenario 3
human-approved remediation
```

---

# Architecture

The project runs as several Kubernetes workloads.

```text
                         ┌──────────────────────────┐
                         │       user-agent         │
                         │ synthetic API traffic    │
                         └─────────────┬────────────┘
                                       │
                                       │ HTTP
                                       ▼
                         ┌──────────────────────────┐
                         │ bulletin-board-service   │
                         │ Python API               │
                         │                          │
                         │ health probes            │
                         │ controlled fault injection│
                         └─────────────┬────────────┘
                                       │
                                       ▼
                              ┌─────────────────┐
                              │   PostgreSQL    │
                              └─────────────────┘


         Kubernetes API / Logs / Events / Deployment history / Health
                               │
                               ▼
                    ┌─────────────────────────┐
                    │       sre-agent         │
                    │                         │
                    │ continuous observer     │
                    │ evidence collection     │
                    │ incident persistence    │
                    │ AI-assisted diagnosis   │
                    │ deterministic policy    │
                    │ remediation execution   │
                    │ recovery verification   │
                    └────────────┬────────────┘
                                 │
                                 ▼
                    ┌─────────────────────────┐
                    │ LangGraph Agent Server  │
                    │                         │
                    │ unified sre_agent graph │
                    │ HITL interrupts         │
                    │ LangSmith Studio        │
                    └─────────────────────────┘
```

More detail:

* [Architecture](docs/architecture.md)
* [Safety Model](docs/safety.md)
* [Evidence Model](docs/evidence-model.md)
* [Limitations](docs/limitations.md)

---

# Components

## `bulletin-board-service`

A small Python API used as the workload under observation.

It provides:

* REST message API,
* PostgreSQL persistence,
* liveness probe,
* readiness probe,
* controlled fault injection for reproducible experiments.

The application is intentionally simple so the project can focus on SRE behavior rather than application complexity.

Current demonstration baseline:

```text
image: bulletin-board-service:0.4.0

memory request: 96Mi
memory limit:   192Mi
```

---

## `user-agent`

A synthetic traffic generator.

It calls the bulletin-board API every few seconds to simulate external application usage.

It is intentionally excluded from the SRE evidence path.

The SRE agent:

* does not read user-agent logs,
* has no RBAC access to the `user-agents` namespace,
* does not use user-agent telemetry for diagnosis.

The traffic generator exists only to create realistic application activity.

---

## `sre-agent`

The primary operational component.

It performs:

* continuous Kubernetes observation,
* incident detection,
* Kubernetes evidence collection,
* application health checks,
* deployment-history analysis,
* AI-assisted diagnosis,
* deterministic remediation policy,
* automatic rollback where permitted,
* human-in-the-loop approval,
* bounded resource remediation,
* post-remediation verification,
* persistent incident and audit history.

Current demonstration version:

```text
sre-agent:0.5.1
```

---

# Unified LangGraph Workflow

A single graph supports chat and all three incident scenarios.

Conceptually:

```text
START
  |
  v
route_request
  |
  +---------------- chat ----------------+
  |                                      |
  v                                      v
chat_agent <-> read-only tools           END

incident
  |
  v
load_incident
  |
  v
classify
  |
  +---- pod disappearance
  |       |
  |       v
  |   verify self-heal
  |       |
  |       v
  |      END
  |
  +---- deployment regression
  |       |
  |       v
  |   collect evidence
  |       |
  |       v
  |   diagnose
  |       |
  |       v
  |   deterministic policy
  |       |
  |       +---- rollback allowed
  |       |          |
  |       |          v
  |       |       rollback
  |       |          |
  |       |          v
  |       |       verify
  |       |
  |       +---- no action
  |
  +---- resource OOM
          |
          v
      resource triage
          |
          v
      human approval
          |
          +---- reject --> END
          |
          +---- approve
                    |
                    v
             bounded mitigation
                    |
                    v
               verify
                    |
                    v
                   END
```

The Kubernetes Pod running `sre-agent` contains two containers:

```text
sre-agent
    FastAPI service
    continuous observer
    incident persistence

studio-agent-server
    LangGraph development Agent Server
    same exported sre_agent graph
    LangSmith Studio interaction
```

These are two runtime containers, not two independent AI agents.

---

# Evidence-Driven Diagnosis

The agent reasons over evidence collected from operational systems rather than receiving unrestricted cluster access.

Evidence can include:

* Pod state,
* readiness,
* restart counts,
* previous termination reason,
* exit code,
* container logs,
* Kubernetes Events,
* Deployment revisions,
* ReplicaSets,
* container image,
* Pod-template configuration,
* resource requests and limits,
* Services,
* Endpoints,
* application readiness,
* dependency health.

A central design principle is the distinction between:

```text
observed condition
```

and:

```text
inferred root cause
```

For example:

```text
Observed:

reason = OOMKilled
exit code = 137
memory limit = 192Mi
repeated restart cycles

Strongly supported condition:

container_memory_limit_exceeded

Possible root causes:

application memory-growth defect
workload-driven memory demand
undersized memory limit
```

The system should not convert:

```text
OOMKilled
```

into:

```text
confirmed memory leak
```

without supporting evidence.

See [Evidence Model](docs/evidence-model.md).

---

# Safety Model

The core safety principle is:

> AI reasoning is not the infrastructure authorization boundary.

Several independent layers constrain operational behavior.

## 1. Read-oriented evidence collection

The LLM primarily reasons over controlled operational evidence.

## 2. Deterministic remediation policy

Application logic determines:

* when automatic remediation is permitted,
* whether rollback is supported,
* whether human approval is required,
* which human actions are allowed,
* resource limits and hard maximums,
* required verification behavior.

## 3. Human approval

Higher-risk actions pause the LangGraph workflow and require explicit approval.

## 4. Bounded remediation implementations

Infrastructure changes are predefined operations rather than arbitrary shell commands generated by an LLM.

## 5. Kubernetes RBAC

The SRE agent uses a dedicated ServiceAccount.

Its read access includes:

```text
Pods
Pod logs
Events
Services
Endpoints
Deployments
ReplicaSets
```

Its only demonstrated Kubernetes write permission is:

```text
PATCH
apps/deployments
resourceName: bulletin-board
```

The agent cannot:

* delete Pods,
* patch arbitrary Deployments,
* modify unrelated namespaces,
* access the user-agent namespace.

## 6. Verification

A successful Kubernetes PATCH is not considered equivalent to successful recovery.

The workflow checks workload behavior after remediation.

See [Safety Model](docs/safety.md).

---

# Scenario 1 — Kubernetes Self-Healing

## Failure

The running bulletin-board Pod is manually deleted.

## Kubernetes behavior

The Deployment controller detects the missing replica and creates another Pod.

## SRE-agent behavior

The agent observes:

```text
old Pod disappeared
        ↓
replacement Pod created
        ↓
container started
        ↓
application temporarily unavailable
        ↓
readiness succeeded
        ↓
recovery verified
```

The agent performs **no remediation**.

This is intentional.

The useful behavior is:

```text
observe
+
verify
+
explain
```

rather than modifying a workload Kubernetes is already recovering successfully.

Full experiment:

[Scenario 1 — Kubernetes Self-Healing](demo/scenario-1.md)

---

# Scenario 2 — Deployment Regression

## Failure

A newly deployed application revision starts successfully and then gradually exhausts its database connection pool.

Application readiness deteriorates while PostgreSQL remains healthy.

## Evidence

The agent correlates:

```text
recent Deployment change
+
new ReplicaSet
+
runtime failures after rollout
+
readiness degradation
+
healthy PostgreSQL dependency
```

## Policy

Deterministic policy evaluates whether rollback is justified.

Relevant conditions include:

* recent Deployment,
* sufficient failure threshold,
* supported previous revision,
* diagnosis confidence,
* remediation cooldown,
* automatic remediation enabled.

## Remediation

When policy permits it:

```text
degraded revision
      ↓
automatic rollback
      ↓
previous safe revision
      ↓
replacement Pod
      ↓
readiness verification
      ↓
remediation succeeded
```

The LLM participates in diagnosis but does not independently authorize rollback.

Full experiment:

[Scenario 2 — Deployment Regression](demo/scenario-2.md)

---

# Scenario 3 — Repeated OOMKilled

Scenario 3 demonstrates the strongest safety behavior in the project.

## Failure

A controlled memory-growth fault causes the application process to repeatedly exceed its configured:

```text
192Mi
```

memory limit.

Kubernetes reports:

```text
reason = OOMKilled
exit code = 137
```

The application restarts and begins growing memory again.

This creates repeated failure rather than durable self-healing.

## Diagnosis

The agent can confidently identify:

```text
observed_condition:
container_memory_limit_exceeded
```

but available evidence does not prove why memory reached the limit.

The root cause therefore remains uncertain.

## Policy

The system produces:

```text
automatic_action_allowed = false
```

Increasing resources could:

* hide an application defect,
* consume additional node capacity,
* only delay another OOM.

The workflow therefore pauses.

## Human approval

LangGraph creates a human-in-the-loop interrupt.

The allowed action is predefined:

```text
increase_memory_limit
```

The operator explicitly approves the action.

## Bounded remediation

The permitted change is:

```text
Deployment: bulletin-board
Container:  api

192Mi
   ↓
512Mi

hard maximum = 512Mi
```

The operator is not granting permission for an arbitrary memory value.

## Verification

After the Deployment changes:

```text
new Pod created
        ↓
readiness becomes healthy
        ↓
restart count remains stable
        ↓
5 consecutive successful observations
        ↓
remediation = succeeded
```

The remediation record explicitly retains:

```text
root_cause_resolved = false
```

The result is therefore:

```text
mitigation succeeded
```

not:

```text
root cause fixed
```

Full experiment:

[Scenario 3 — Human-Approved OOM Mitigation](demo/scenario-3.md)

---

# Repository Structure

```text
.
├── bulletin-board-service/
│   ├── app/
│   ├── k8s/
│   ├── tests/
│   ├── Dockerfile
│   ├── README.md
│   ├── requirements.txt
│   └── VERSION
│
├── sre-agent/
│   ├── app/
│   ├── k8s/
│   ├── Dockerfile
│   ├── langgraph.json
│   ├── README.md
│   ├── requirements.txt
│   └── VERSION
│
├── user-agent/
│   ├── app/
│   ├── k8s/
│   ├── Dockerfile
│   ├── README.md
│   ├── requirements.txt
│   └── VERSION
│
├── demo/
│   ├── scenario-1.md
│   ├── scenario-2.md
│   ├── scenario-3.md
│   ├── verify.sh
│   └── reset.sh
│
├── docs/
│   ├── architecture.md
│   ├── safety.md
│   ├── evidence-model.md
│   └── limitations.md
│
├── Makefile
├── README.md
└── .gitignore
```

---

# Technology

The project uses:

```text
Python
FastAPI
PostgreSQL
Docker-compatible OCI images
containerd
Kubernetes
Calico
LangGraph
LangSmith Studio
OpenAI models
Kubernetes RBAC
```

The development Kubernetes environment is a real single-node `kubeadm` cluster rather than a mocked Kubernetes API.

---

# Kubernetes Namespaces

The core workloads are separated into namespaces:

```text
bulletin-board
    bulletin-board-service
    PostgreSQL

sre-agents
    sre-agent

user-agents
    user-agent
```

The SRE agent has no RBAC access to the `user-agents` namespace.

---

# Requirements

The current demo assumes:

* Linux environment,
* working Kubernetes cluster,
* `kubectl`,
* container runtime capable of loading locally built images,
* PostgreSQL-compatible persistent storage,
* OpenAI API key,
* LangSmith API key for Studio interaction,
* StorageClass compatible with the PostgreSQL manifest.

The current PostgreSQL manifest references:

```text
local-path-retain
```

If your cluster does not provide this StorageClass, update the manifest to use one available in your environment.

---

# Secrets

Real credentials are intentionally excluded from Git.

Example manifests are provided under the component `k8s/` directories.

Copy the examples and provide your own values locally.

Never commit:

```text
OpenAI API keys
LangSmith API keys
database passwords
real Kubernetes Secret manifests
.env files containing credentials
```

The root `.gitignore` excludes common secret and runtime artifacts.

---

# Useful Commands

The repository provides a small Makefile interface.

## Show workload status

```bash
make status
```

## Verify the environment

```bash
make verify
```

This checks:

* namespaces,
* workloads,
* application image,
* memory baseline,
* SRE-agent image,
* application readiness,
* selected RBAC safety properties.

Expected SRE-agent authorization includes:

```text
read bulletin-board Pods       = yes
read bulletin-board logs       = yes
patch bulletin-board Deployment = yes
delete Pods                    = no
access user-agent Pods         = no
```

## Restore the demo baseline

```bash
make reset
```

This:

* disables synthetic faults,
* reapplies the canonical bulletin-board Deployment,
* restores the 192Mi memory limit,
* waits for rollout,
* verifies readiness.

## Port-forward SRE APIs

```bash
make port-forward
```

This exposes:

```text
SRE API:
http://127.0.0.1:8081

LangGraph Agent Server:
http://127.0.0.1:2024
```

## Follow application logs

```bash
make logs
```

---

# SRE Agent API

The agent exposes endpoints useful for inspecting operational history:

```text
GET /health
GET /incidents
GET /incidents/{incident_id}
GET /events
GET /probe-history
GET /pod-history
GET /deployment-history
GET /remediations
GET /approvals
POST /chat
```

For example:

```bash
curl -s \
  'http://127.0.0.1:8081/incidents?hours=1' \
  | python3 -m json.tool
```

Or:

```bash
curl -s \
  'http://127.0.0.1:8081/remediations?hours=1' \
  | python3 -m json.tool
```

---

# Operational Audit Trail

Incident state is persisted in PostgreSQL.

Records include:

* incidents,
* Pod observations,
* probe history,
* Deployment history,
* Kubernetes Events,
* diagnosis,
* confidence,
* policy decisions,
* human approvals,
* remediation actions,
* verification observations.

This means the system can answer more than:

```text
What action did the agent take?
```

It can also help answer:

```text
What did it observe?

Why did it reach that diagnosis?

How confident was it?

Why was an action allowed or denied?

Was human approval required?

What exactly changed?

Did the application actually recover?
```

---

# Limitations

This project is an experimental prototype, not a production autonomous-operations platform.

Current limitations include:

* single-node Kubernetes cluster,
* synthetic application and failure injection,
* only three specialized incident classes,
* no full Prometheus/OpenTelemetry metrics pipeline,
* no heap profiling for memory diagnosis,
* probabilistic LLM diagnosis,
* diagnostic confidence is not statistically calibrated,
* narrow remediation surface,
* prototype-grade human approval,
* development LangGraph Agent Server,
* non-durable development workflow thread state across Pod replacement,
* single SRE-agent replica,
* development-oriented PostgreSQL deployment,
* environment-specific StorageClass,
* no Kubernetes NetworkPolicies,
* simplified secret management,
* no admission-policy enforcement,
* short post-remediation verification windows.

The project intentionally documents these limitations rather than presenting the prototype as production-ready.

See:

[Limitations](docs/limitations.md)

---

# What the Experiments Demonstrate

The experiments do **not** demonstrate that an LLM should autonomously operate arbitrary production Kubernetes environments.

They support a narrower architectural conclusion:

> AI-assisted operational reasoning can be useful when it is grounded in observable evidence and separated from deterministic authorization, bounded remediation, human oversight, infrastructure-level access control, and post-action verification.

The most important property is not maximum autonomy.

It is controlled escalation of authority:

```text
Scenario 1
AI observes.
Kubernetes heals.

Scenario 2
AI diagnoses.
Policy allows a narrow reversible automatic action.

Scenario 3
AI diagnoses.
Policy refuses autonomous action.
A human approves a bounded mitigation.
The system verifies stabilization.
The system does not falsely claim that the root cause was fixed.
```

That separation between **reasoning and authority** is the central design principle of this project.

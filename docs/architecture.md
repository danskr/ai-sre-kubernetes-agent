# Architecture

## Overview

This project demonstrates an AI-assisted Site Reliability Engineering workflow
for diagnosing and responding to Kubernetes application incidents using
verifiable operational evidence.

The system deliberately separates:

- operational observation,
- AI-assisted diagnosis,
- deterministic safety policy,
- remediation execution,
- human approval,
- and post-remediation verification.

The objective is not to give an LLM unrestricted control of a Kubernetes
cluster. Instead, the LLM participates in diagnosis while Kubernetes RBAC and
deterministic workflow logic constrain what actions may be executed.

## System Components

### Bulletin Board Service

`bulletin-board-service` is the application under observation.

It is a Python API deployed to Kubernetes and backed by PostgreSQL.

The service exposes:

- application APIs,
- liveness and readiness probes,
- controlled demo fault injection.

The application is intentionally simple so that the project can focus on SRE
diagnosis and remediation behavior rather than application complexity.

### PostgreSQL

PostgreSQL provides persistent storage for the bulletin-board application.

It is also used by the SRE agent for incident and evidence persistence.

Application and SRE data use separate logical credentials/databases.

### User Agent

`user-agent` simulates external application traffic.

It periodically calls the bulletin-board API every few seconds.

The user-agent is intentionally isolated from the SRE agent:

- it has no Kubernetes API credentials,
- the SRE agent does not read its logs,
- it is not used as a diagnostic signal.

Its purpose is only to generate realistic application traffic.

### SRE Agent

`sre-agent` is the central diagnostic and remediation component.

It performs:

- Kubernetes observation,
- incident detection,
- evidence collection,
- AI-assisted diagnosis,
- deterministic policy evaluation,
- bounded remediation,
- human approval workflows,
- recovery verification,
- incident persistence.

The SRE agent runs with a dedicated Kubernetes ServiceAccount.

## Kubernetes Evidence Sources

The SRE agent collects operational evidence from the Kubernetes API, including:

- Pods
- container states
- restart counts
- previous container termination state
- container logs
- Kubernetes Events
- Deployments
- ReplicaSets
- Deployment revisions
- Services
- Endpoints
- application readiness probes

The system attempts to distinguish observed facts from inferred root causes.

For example:

`OOMKilled` and exit code `137` are observable facts.

An application memory leak is only a hypothesis unless additional evidence
supports that conclusion.

## LangGraph Workflow

A single LangGraph workflow supports all demonstrated incident types.

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

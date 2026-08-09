# AI-Assisted Kubernetes Incident Diagnosis and Remediation

An experimental SRE system exploring whether an AI agent can diagnose Kubernetes
deployment failures using verifiable operational evidence while remaining safe,
explainable, and useful to production engineers.

The project evaluates four dimensions:

- Diagnostic accuracy
- Evidence traceability
- Operational safety
- Practical usability

## Components

- `bulletin-board-service` — application under observation
- `user-agent` — synthetic external traffic generator
- `sre-agent` — evidence collection, diagnosis, policy and remediation workflow

## Demonstrated Scenarios

1. Kubernetes pod self-healing with observational diagnosis
2. Deployment regression with bounded automatic rollback
3. Repeated OOMKilled failures with human-approved bounded mitigation

Full architecture, setup instructions, experiments and results are being documented.

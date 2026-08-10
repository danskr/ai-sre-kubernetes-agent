# SRE Agent

Canonical SRE agent for the Kubernetes reliability portfolio project.

Version `0.5.2` exports **one LangGraph workflow** named `sre_agent`. The background observer, LangSmith Studio, and the conversational interface all use the same workflow definition.

`0.5.2` keeps the Studio human-approval normalization from 0.5.1 and adds bounded conversational evidence tools. Recent-incident chat now returns compact summaries first and fetches bounded detail for a single incident on demand, preventing large historical records from overflowing the model request budget.

## One workflow, three scenarios

```text
START
  ↓
route_request
  ├── chat → chat_agent ↔ read-only tools → END
  │
  └── incident → load_incident
                    ↓
                classify kind
             ┌──────┼─────────┐
             │      │         │
        pod loss  regression  OOM
             │      │         │
       verify K8s  diagnose  resource triage
       self-heal     ↓         ↓
             │     policy   interrupt()
             │    /    \       ↓
             │ rollback stop  human
             │    ↓           /   \
             │  verify    reject approve
             │    ↓          ↓      ↓
             └── END         END  bounded patch
                                      ↓
                                    verify
                                      ↓
                                     END
```

### Scenario 1 — observe

Detect unexpected Pod loss, persist evidence, submit the incident to `sre_agent`, and let Kubernetes recreate the workload. The graph verifies replica/readiness recovery and records that **Kubernetes self-healed; the agent executed no write**.

### Scenario 2 — bounded autonomous remediation

Detect a post-deployment readiness regression, collect operational evidence, obtain an LLM diagnosis, evaluate deterministic rollback gates, restore the previous complete Pod template only if every gate passes, and verify recovery.

### Scenario 3 — human-approved resource mitigation

Detect repeated `OOMKilled` restarts, distinguish the observed memory-limit breach from uncertain root cause, explicitly disallow autonomous action, and pause the **same `sre_agent` graph** with LangGraph `interrupt()`. A human may approve or reject the single bounded mitigation. Approval permits only an increase of the `api` container memory limit to `512Mi`, followed by verification.

## Execution architecture

The `sre-agent` Pod still has two containers, but there is only one agent workflow:

```text
sre-agent Pod
├── sre-agent :8080
│   └── background observer
│        └── detects incident
│             └── POST to localhost:2024
│
└── studio-agent-server :2024
    └── ONE graph: sre_agent
         ├── chat
         ├── Scenario 1
         ├── Scenario 2
         └── Scenario 3 + interrupt
```

The observer creates an Agent Server thread for each operational incident and stores its `thread_id` in the incident record. Because LangSmith Studio connects to the same Agent Server, Scenario 3's paused thread is the exact workflow run the operator resumes in Studio.

## Evidence boundary

`sre-agent` has no access to the `user-agents` namespace and no user-agent tools. It uses only Kubernetes state/events, bulletin-board logs, its own readiness-probe history, persisted pod/restart history, incidents, deployment history, approvals, remediation audit records, and PostgreSQL Kubernetes-level health.

## Safety boundary

The ServiceAccount has one write capability: `patch` on the single Deployment `bulletin-board` in namespace `bulletin-board`.

- Normal chat is read-only; no Kubernetes write tool is exposed to the LLM.
- Scenario 1 executes no agent write.
- Scenario 2 writes only through the deterministic rollback node after every gate passes.
- Scenario 3 writes only after `interrupt()` is explicitly resumed with approval, and the target memory limit is hard-bounded to `512Mi`.
- The agent cannot delete Pods or patch another Deployment.

## Build

```bash
docker build -t sre-agent:0.5.2 .
docker save sre-agent:0.5.2 -o /tmp/sre-agent-0.5.2.tar
sudo ctr -n k8s.io images import /tmp/sre-agent-0.5.2.tar
```

## Deploy

```bash
kubectl apply -f k8s/00-namespace-serviceaccount.yaml
kubectl apply -f k8s/01-rbac-bulletin-board.yaml
kubectl apply -f k8s/03-deployment.yaml
kubectl -n sre-agents rollout status deployment/sre-agent
```

Expected Pod readiness: `2/2 Running`.

## Studio

```bash
kubectl -n sre-agents port-forward svc/sre-agent 8081:8080 2024:2024
```

Studio should show exactly one graph: `sre_agent`.

## API

```text
GET  /health
GET  /incidents
GET  /incidents/{incident_id}
GET  /events
GET  /probe-history
GET  /pod-history
GET  /deployment-history
GET  /remediations
GET  /approvals
POST /chat
```

There is intentionally no REST endpoint that bypasses Scenario 3 human approval.

# Bulletin Board Service

Python/FastAPI bulletin-board microservice used by the Kubernetes SRE agent project. PostgreSQL is mandatory.

This is the canonical service package and progressively supports all demo scenarios:

1. pod deletion / Kubernetes self-healing,
2. controlled DB-session leak / safe automatic rollback,
3. controlled memory growth / repeated OOMKilled restarts and human-approved mitigation.

All demo faults are dormant until explicitly activated.

## API

Application API:

- `POST /api/v1/messages`
- `GET /api/v1/messages`
- `GET /api/v1/messages/{id}`
- `PATCH /api/v1/messages/{id}`
- `DELETE /api/v1/messages/{id}`
- `GET /health/live`
- `GET /health/ready`
- `GET /docs`

Demo-control API, available only when `DEMO_FAULTS_ENABLED=true`:

- `GET /demo/faults`
- `POST /demo/faults/db-leak/start`
- `POST /demo/faults/db-leak/stop`
- `POST /demo/faults/memory-growth/start`
- `POST /demo/faults/memory-growth/stop`

Demo-control routes are excluded from OpenAPI and from normal operational request logs. The SRE agent therefore observes operational symptoms rather than the hidden test trigger.

## Scenario 2 fault

The DB-leak fault retains request-scoped SQLAlchemy sessions. With the demo pool configured as 2 + 1 overflow connections, normal traffic gradually exhausts the pool. `/health/live` stays independent of PostgreSQL while `/health/ready` fails when the application cannot obtain a DB connection.

## Scenario 3 fault

The memory-growth fault allocates and retains resident memory in small increments. Its enable flag is stored in the bulletin application's PostgreSQL database, while allocated memory remains process-local. Consequently, after Kubernetes OOM-kills and restarts the container, the new process sees that the fault is still enabled and resumes memory growth. This creates a repeatable OOM/restart loop without requiring the SRE agent to know how the test was triggered.

The Kubernetes demo manifest intentionally starts the application with a `192Mi` memory limit. The Scenario 3 human-review workflow may, after explicit approval, increase that limit to a bounded `512Mi` as a temporary mitigation. The active memory-growth fault continues after the rollout, so the action is correctly described as additional headroom rather than a root-cause fix.

## Build

```bash
docker build --target production -t bulletin-board-service:0.4.0 .
docker save bulletin-board-service:0.4.0 -o /tmp/bulletin-board-service-0.4.0.tar
sudo ctr -n k8s.io images import /tmp/bulletin-board-service-0.4.0.tar
```

## Deploy

```bash
kubectl apply -f k8s/bulletin-board.yaml
kubectl -n bulletin-board rollout status deployment/bulletin-board
```

## Validate normal behavior

```bash
curl http://localhost:30080/health/live
curl http://localhost:30080/health/ready
curl 'http://localhost:30080/api/v1/messages?limit=1'
curl -s http://localhost:30080/demo/faults | python3 -m json.tool
```

## Trigger Scenario 3 memory growth

```bash
curl -s -X POST \
  http://localhost:30080/demo/faults/memory-growth/start \
  | python3 -m json.tool
```

Reset after the demonstration:

```bash
curl -s -X POST \
  http://localhost:30080/demo/faults/memory-growth/stop \
  | python3 -m json.tool
```

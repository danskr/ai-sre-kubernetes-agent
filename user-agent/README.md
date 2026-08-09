# user-agent

A minimal deterministic traffic generator for the bulletin-board Kubernetes demo.

## Purpose

`user-agent` behaves like an external client. It periodically sends a read-only HTTP request to the bulletin-board API every random 3 to 5 seconds.

It intentionally has:

- no LLM
- no LangGraph
- no Kubernetes API access
- no RBAC permissions
- no shared database with `sre-agent`
- no metrics endpoint
- no service
- no telemetry integration with `sre-agent`

Its stdout logs exist only for manual debugging by the cluster administrator. The `sre-agent` Role is scoped to the `bulletin-board` namespace and therefore does not have access to `user-agent` pod logs in the `user-agents` namespace.

## Default request

```text
GET http://bulletin-board.bulletin-board.svc.cluster.local/api/v1/messages?limit=1
```

## Configuration

Environment variables:

- `TARGET_URL` - request URL
- `MIN_INTERVAL_SECONDS` - minimum delay, default `3`
- `MAX_INTERVAL_SECONDS` - maximum delay, default `5`
- `REQUEST_TIMEOUT_SECONDS` - HTTP timeout, default `2`
- `LOG_LEVEL` - default `INFO`

## Build

```bash
docker build -t user-agent:0.1.0 .
```

For a kubeadm/containerd cluster:

```bash
docker save user-agent:0.1.0 -o /tmp/user-agent-0.1.0.tar
sudo ctr -n k8s.io images import /tmp/user-agent-0.1.0.tar
sudo ctr -n k8s.io images list | grep user-agent
```

## Deploy

```bash
kubectl apply -f k8s/00-namespace.yaml
kubectl apply -f k8s/01-deployment.yaml
kubectl -n user-agents rollout status deployment/user-agent
kubectl -n user-agents get pods
```

## Verify traffic

For manual debugging only:

```bash
kubectl -n user-agents logs -f deployment/user-agent
```

You should see requests every 3-5 seconds, normally with HTTP 200.

## Verify isolation from sre-agent

Because the `sre-agent` ServiceAccount only has a Role in the `bulletin-board` namespace, it should not be able to inspect the `user-agents` namespace.

```bash
kubectl auth can-i get pods \
  --as=system:serviceaccount:sre-agents:sre-agent \
  -n user-agents

kubectl auth can-i get pods/log \
  --as=system:serviceaccount:sre-agents:sre-agent \
  -n user-agents
```

Both should return `no`.

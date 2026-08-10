#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "=== Resetting AI SRE demo environment ==="
echo

echo "=== Disable synthetic application faults ==="

kubectl -n bulletin-board exec postgres-0 -- sh -lc '
psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -c "
UPDATE demo_fault_state
SET enabled = false,
    activated_at = NULL,
    updated_at = now();
"
'

echo
echo "=== Supersede stale active SRE incidents ==="
kubectl -n sre-agents exec deployment/sre-agent -c sre-agent -- python -c '
from app import db

kinds = ("pod_disappearance", "runtime_regression", "resource_oom")
count = 0

for kind in kinds:
    while True:
        incident = db.get_active_incident(kind)
        if incident is None:
            break

        db.update_incident(
            incident["incident_id"],
            status="superseded",
            summary="Superseded during demo reset; environment returned to canonical healthy baseline.",
            details={"superseded_reason": "demo_reset"},
            end=True,
        )

        print("superseded", kind, incident["incident_id"])
        count += 1

print("superseded incidents:", count)
'
echo

echo "=== Restore canonical bulletin-board Deployment ==="

kubectl apply \
  -f "$ROOT_DIR/bulletin-board-service/k8s/bulletin-board.yaml"

echo
echo "=== Wait for application rollout ==="

kubectl -n bulletin-board rollout status \
  deployment/bulletin-board \
  --timeout=120s

echo
echo "=== Verify baseline memory limit ==="

MEMORY_LIMIT="$(
  kubectl -n bulletin-board get deployment bulletin-board \
    -o jsonpath='{.spec.template.spec.containers[?(@.name=="api")].resources.limits.memory}'
)"

echo "memory limit: $MEMORY_LIMIT"

if [[ "$MEMORY_LIMIT" != "192Mi" ]]; then
  echo "ERROR: expected canonical memory limit 192Mi"
  exit 1
fi

echo
echo "=== Verify Pods ==="

kubectl -n bulletin-board get pods

echo
echo "=== Verify application readiness ==="

for attempt in {1..20}; do
  if curl -sf http://127.0.0.1:30080/health/ready >/dev/null; then
    echo "bulletin-board readiness: OK"
    break
  fi

  if [[ "$attempt" -eq 20 ]]; then
    echo "ERROR: bulletin-board did not become ready"
    exit 1
  fi

  sleep 2
done

echo
echo "=== Verify fault state ==="

kubectl -n bulletin-board exec postgres-0 -- sh -lc '
psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
SELECT fault_name, enabled
FROM demo_fault_state
ORDER BY fault_name;
"
'

echo
echo "=== RESET COMPLETE ==="
echo
echo "Canonical baseline:"
echo "  bulletin-board image: bulletin-board-service:0.4.0"
echo "  memory limit:         192Mi"
echo "  synthetic faults:     disabled"

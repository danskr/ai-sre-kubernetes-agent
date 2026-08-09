#!/usr/bin/env bash
set -euo pipefail

echo "=== AI SRE Kubernetes Demo Verification ==="
echo

echo "=== NAMESPACES ==="
kubectl get namespace bulletin-board sre-agents user-agents
echo

echo "=== BULLETIN BOARD WORKLOAD ==="
kubectl -n bulletin-board get deployment bulletin-board
kubectl -n bulletin-board get pod -l app=bulletin-board
kubectl -n bulletin-board get pod postgres-0
echo

echo "=== SRE AGENT ==="
kubectl -n sre-agents get deployment sre-agent
kubectl -n sre-agents get pods
echo

echo "=== USER AGENT ==="
kubectl -n user-agents get deployment user-agent
kubectl -n user-agents get pods
echo

echo "=== APPLICATION IMAGE ==="
kubectl -n bulletin-board get deployment bulletin-board \
  -o jsonpath='{.spec.template.spec.containers[?(@.name=="api")].image}{"\n"}'
echo

echo "=== APPLICATION MEMORY LIMIT ==="
kubectl -n bulletin-board get deployment bulletin-board \
  -o jsonpath='{.spec.template.spec.containers[?(@.name=="api")].resources.limits.memory}{"\n"}'
echo

echo "=== SRE AGENT IMAGE ==="
kubectl -n sre-agents get deployment sre-agent \
  -o jsonpath='{.spec.template.spec.containers[?(@.name=="sre-agent")].image}{"\n"}'
echo

echo "=== RBAC SAFETY CHECK ==="

echo -n "read pods: "
kubectl auth can-i get pods \
  --as=system:serviceaccount:sre-agents:sre-agent \
  -n bulletin-board

echo -n "read logs: "
kubectl auth can-i get pods/log \
  --as=system:serviceaccount:sre-agents:sre-agent \
  -n bulletin-board

echo -n "patch bulletin-board deployment: "
kubectl auth can-i patch deployment/bulletin-board \
  --as=system:serviceaccount:sre-agents:sre-agent \
  -n bulletin-board

echo -n "delete bulletin-board pods: "
kubectl auth can-i delete pods \
  --as=system:serviceaccount:sre-agents:sre-agent \
  -n bulletin-board

echo -n "access user-agent pods: "
kubectl auth can-i get pods \
  --as=system:serviceaccount:sre-agents:sre-agent \
  -n user-agents

echo

echo "Expected safety result:"
echo "  read pods                    = yes"
echo "  read logs                    = yes"
echo "  patch bulletin-board         = yes"
echo "  delete pods                  = no"
echo "  access user-agent namespace  = no"
echo

echo "=== APPLICATION READINESS ==="

if curl -sf http://127.0.0.1:30080/health/ready >/dev/null; then
  echo "bulletin-board readiness: OK"
else
  echo "bulletin-board readiness: FAILED"
  exit 1
fi

echo
echo "=== DEMO FAULT STATE ==="

if curl -sf http://127.0.0.1:30080/demo/faults; then
  echo
else
  echo "Unable to query demo fault state."
fi

echo
echo "=== VERIFICATION COMPLETE ==="

.PHONY: help status verify reset port-forward pods logs

help:
	@echo "Available commands:"
	@echo "  make status        Show cluster workload status"
	@echo "  make verify        Verify application, agents, and RBAC safety"
	@echo "  make reset         Restore canonical demo baseline"
	@echo "  make port-forward  Expose SRE API and LangGraph server"
	@echo "  make pods          Show demo Pods"
	@echo "  make logs          Follow bulletin-board application logs"

status:
	@echo "=== BULLETIN BOARD ==="
	kubectl -n bulletin-board get deployment,pods
	@echo
	@echo "=== SRE AGENT ==="
	kubectl -n sre-agents get deployment,pods
	@echo
	@echo "=== USER AGENT ==="
	kubectl -n user-agents get deployment,pods

verify:
	./demo/verify.sh

reset:
	./demo/reset.sh

port-forward:
	kubectl -n sre-agents port-forward svc/sre-agent 8081:8080 2024:2024

pods:
	@echo "=== BULLETIN BOARD ==="
	kubectl -n bulletin-board get pods
	@echo
	@echo "=== SRE AGENT ==="
	kubectl -n sre-agents get pods
	@echo
	@echo "=== USER AGENT ==="
	kubectl -n user-agents get pods

logs:
	kubectl -n bulletin-board logs -f deployment/bulletin-board -c api

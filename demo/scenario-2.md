# Scenario 2 — Deployment Regression with Automatic Rollback

## Objective

Demonstrate that the SRE agent can detect a runtime regression introduced by a recent Deployment change, correlate the failure with that change, apply deterministic remediation policy, automatically roll back to a previous safe revision, and verify recovery.

This scenario tests a different safety mode from Scenario 1:

```text id="v4i46k"
Scenario 1:
observe and verify

Scenario 2:
diagnose
+
deterministic policy
+
bounded automatic remediation
+
verify
```

The key requirement is that rollback is performed only when operational evidence supports a recent deployment regression.

## Failure

A new version of `bulletin-board-service` is deployed.

The version starts successfully but gradually develops database connection-pool exhaustion approximately 10 seconds after startup.

This causes increasing request failures and readiness degradation.

PostgreSQL remains healthy.

The failure is therefore correlated with the newly deployed application revision rather than with a database outage.

## Expected System Behavior

The expected sequence is:

```text id="y7zuj0"
healthy application revision
        |
        v
new application revision deployed
        |
        v
application initially healthy
        |
        v
runtime failures increase
        |
        v
readiness begins failing
        |
        v
SRE agent collects evidence
        |
        v
recent deployment regression identified
        |
        v
deterministic policy evaluates rollback
        |
        v
rollback permitted
        |
        v
Deployment restored to previous safe revision
        |
        v
replacement Pod becomes Ready
        |
        v
multiple successful health checks
        |
        v
remediation recorded as succeeded
```

## Preconditions

Verify the baseline application is healthy:

```bash id="o4zhxs"
kubectl -n bulletin-board get pods
```

Expected:

```text id="cq9oc0"
bulletin-board-...   1/1   Running   0   ...
postgres-0           1/1   Running   0   ...
```

Verify readiness:

```bash id="q1jq0y"
curl -sf http://127.0.0.1:30080/health/ready
```

Verify the current Deployment image:

```bash id="mqiqz8"
kubectl -n bulletin-board get deployment bulletin-board \
  -o jsonpath='{.spec.template.spec.containers[?(@.name=="api")].image}{"\n"}'
```

The baseline should be the known-safe application revision.

## Trigger

Deploy the intentionally degraded application version used for the regression demonstration.

The exact image version may depend on the local build used for the experiment.

After updating the Deployment, watch the rollout:

```bash id="f0x49h"
kubectl -n bulletin-board rollout status \
  deployment/bulletin-board
```

Then watch the Pods:

```bash id="6ux4j7"
kubectl -n bulletin-board get pods -w
```

The new Pod should initially become Ready.

After the delayed failure begins, readiness should become unstable.

## Observe Application Failure

Check readiness repeatedly:

```bash id="myenv5"
while true; do
  date
  curl -s -o /dev/null -w '%{http_code}\n' \
    http://127.0.0.1:30080/health/ready
  sleep 2
done
```

During the degradation period, successful readiness responses should begin changing into failures.

Stop the loop with:

```text id="0ejr6b"
Ctrl+C
```

## Verify PostgreSQL Remains Healthy

Check PostgreSQL:

```bash id="y7zmab"
kubectl -n bulletin-board get pod postgres-0
```

Expected:

```text id="pw7c0x"
READY   STATUS    RESTARTS
1/1     Running   0
```

This is important evidence.

The application is failing while the primary dependency remains healthy.

That weakens the hypothesis of a PostgreSQL outage.

## Evidence Collected by the SRE Agent

Relevant evidence can include:

* recent Deployment revision change,
* newly created ReplicaSet,
* timing of the rollout,
* readiness failures beginning after the new revision,
* application errors,
* database-pool exhaustion behavior,
* healthy PostgreSQL state,
* previously healthy Deployment revision.

The workflow should correlate:

```text id="7r2g2w"
recent application revision
+
new runtime failures
+
healthy dependency
```

rather than simply observing that readiness is failing.

## Deployment History

Inspect rollout history directly:

```bash id="e1ph0q"
kubectl -n bulletin-board rollout history \
  deployment/bulletin-board
```

The SRE agent uses Deployment and ReplicaSet evidence to determine whether a rollback candidate exists.

A rollback is supported only when a previous revision represents a meaningful known-safe state.

## Policy Evaluation

The LLM may diagnose the situation as a likely deployment regression.

However, the LLM does not directly authorize rollback.

Deterministic policy evaluates conditions such as:

* automatic remediation is enabled,
* failure threshold has been reached,
* the Deployment change is sufficiently recent,
* diagnosis confidence satisfies the configured threshold,
* a previous safe revision exists,
* remediation is not within cooldown.

Only when those conditions are satisfied is automatic rollback permitted.

## Kubernetes Authorization

The SRE agent ServiceAccount does not have general write access.

Its relevant write permission is limited to:

```text id="kkz0w2"
PATCH
apps/deployments
resourceName: bulletin-board
```

The rollback therefore occurs through the same narrowly scoped Kubernetes authorization boundary used by the rest of the project.

The agent cannot patch arbitrary Deployments.

## Automatic Rollback

When policy permits remediation, the workflow restores the previous safe Deployment state.

This should result in:

* a new Deployment revision,
* creation of a replacement ReplicaSet or Pod,
* termination of the degraded Pod,
* rollout of the safe configuration.

Watch the rollout:

```bash id="rbk97s"
kubectl -n bulletin-board get pods -w
```

## Verify the Restored Image

Check the application image:

```bash id="cxe4be"
kubectl -n bulletin-board get deployment bulletin-board \
  -o jsonpath='{.spec.template.spec.containers[?(@.name=="api")].image}{"\n"}'
```

It should show the previous known-safe version.

## Verify Application Recovery

Check readiness:

```bash id="caf9of"
curl -sf http://127.0.0.1:30080/health/ready
```

The workflow performs its own repeated post-remediation verification rather than relying only on the successful Deployment patch.

## Inspect the Incident

If required, port-forward the SRE-agent API:

```bash id="57rvql"
kubectl -n sre-agents port-forward \
  svc/sre-agent 8081:8080
```

Then inspect incidents:

```bash id="7crx0j"
curl -s \
  'http://127.0.0.1:8081/incidents?hours=1' \
  | python3 -m json.tool
```

The incident should contain evidence supporting the deployment-regression diagnosis.

## Inspect Deployment History

```bash id="88mswe"
curl -s \
  'http://127.0.0.1:8081/deployment-history?hours=1' \
  | python3 -m json.tool
```

This should show the Deployment changes observed around the incident.

## Inspect Remediation History

```bash id="vku4ct"
curl -s \
  'http://127.0.0.1:8081/remediations?hours=1' \
  | python3 -m json.tool
```

The relevant remediation should show a successful rollback action associated with the incident.

## Verification

The workflow does not consider rollback complete merely because Kubernetes accepted the Deployment patch.

Recovery is verified using repeated observations.

Expected verification evidence includes:

```text id="f1xm4y"
replacement Pod Ready
application readiness = HTTP 200
restart count stable
required consecutive successful checks reached
```

Only after verification succeeds is the remediation recorded as successful.

## Why This Scenario Allows Automatic Remediation

Rollback is lower risk when the evidence strongly supports:

```text id="46bu6h"
known-good previous revision
+
recent configuration change
+
failure begins after change
+
restoring previous state is reversible
```

The remediation is also constrained to a single Deployment.

This makes the action suitable for a narrow deterministic automatic policy.

## Why Rollback Is Not Universal

The system does not apply rollback to every unhealthy application.

For example, Scenario 3 demonstrates a case where multiple affected revisions share the same image and resource configuration.

In that case, rollback is not supported by the evidence.

This prevents:

```text id="4fdf3k"
application unhealthy
        =
always rollback
```

from becoming the operational policy.

## Safety Property Demonstrated

Scenario 2 demonstrates **bounded autonomous remediation**.

The system allows an automatic infrastructure change only when:

* the incident matches a supported class,
* evidence correlates the problem with a recent Deployment,
* deterministic policy authorizes the action,
* Kubernetes RBAC permits the specific mutation,
* post-action verification confirms recovery.

The LLM contributes to diagnosis but does not independently control the authorization boundary.

## Result

The scenario is successful when all of the following are true:

* a degraded application revision is deployed,
* failures begin after that revision,
* PostgreSQL remains healthy,
* the SRE agent correlates the incident with the recent Deployment,
* deterministic policy permits rollback,
* the previous safe revision is restored,
* the replacement Pod becomes Ready,
* application readiness recovers,
* remediation is recorded,
* repeated verification succeeds.

## Key Takeaway

Automatic remediation can be useful when the action is narrow, reversible, supported by evidence, and constrained by deterministic policy.

The important behavior in this scenario is not simply:

```text id="xys5qv"
AI detected failure and ran rollback.
```

It is:

```text id="gbx318"
operational evidence supported a recent deployment regression,
deterministic policy authorized a specific reversible action,
Kubernetes RBAC constrained the write,
and the system verified that the service recovered.
```

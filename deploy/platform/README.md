# Platform layer (installed once, from upstream Helm charts)

The application chart (`deploy/helm/kms`) assumes these are running. Pin each chart version,
mirror images into Harbor, and register each as an Argo CD Application.

| Namespace | Component | Upstream chart | Notes |
|---|---|---|---|
| kms-identity | Keycloak | `codecentric/keycloakx` or Keycloak Operator | import `keycloak/realm-kms.json`, then federate with your IdP (Entra ID / AD / Okta) as an Identity Provider; disable demo users |
| kms-data | PostgreSQL | CloudNativePG operator | run `db/init.sql`; app role gets INSERT/SELECT only on `audit.events` |
| kms-data | Qdrant | `qdrant/qdrant` | 3 replicas, `replicationFactor: 2` |
| kms-data | Valkey | `bitnami/valkey` | persistence on |
| kms-policy | OPA | `open-policy-agent/opa-kube-mgmt` or plain Deployment | mount `policies/`; ship decision logs to Loki |
| kms-inference | vLLM, TEI | `inference.yaml` (this folder) or vLLM production-stack chart | needs NVIDIA GPU Operator |
| kms-inference | LiteLLM | `litellm-helm` | config: `litellm/config.vllm.yaml` |
| kms-observability | Langfuse | `langfuse/langfuse` | self-hosted, ClickHouse included |
| kms-observability | Prometheus, Grafana, Loki | `kube-prometheus-stack`, `grafana/loki` | dashboards in `observability/grafana/dashboards/` |
| kms-pipelines | Airflow | `apache-airflow/airflow` | DAGs: `airflow/dags/kms_dags.py` |
| platform | Argo CD, External Secrets, OpenBao, Harbor, Rook-Ceph | upstream charts | Ceph RGW bucket with Object Lock for audit archive |

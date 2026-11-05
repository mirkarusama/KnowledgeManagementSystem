"""Airflow DAGs for the KM platform (production scheduling).

Each task runs the platform's own container image with one `kms` command, so the
logic lives in one place (kms/ingest/jobs.py) and is identical in dev and prod.
"""
from datetime import datetime, timedelta

from airflow import DAG
from airflow.providers.cncf.kubernetes.operators.pod import KubernetesPodOperator

IMAGE = "harbor.internal/kms/backend:0.1.0"  # mirrored into Harbor, signed
NAMESPACE = "kms-agents"
DEFAULTS = {"owner": "kms-platform", "retries": 2, "retry_delay": timedelta(minutes=2)}


def kms_task(task_id: str, *args: str) -> KubernetesPodOperator:
    return KubernetesPodOperator(
        task_id=task_id,
        name=f"kms-{task_id.replace('_', '-')}",
        namespace=NAMESPACE,
        image=IMAGE,
        cmds=["kms"],
        arguments=list(args),
        env_from=[{"configMapRef": {"name": "kms-config"}}, {"secretRef": {"name": "kms-secrets"}}],
        get_logs=True,
        is_delete_operator_pod=True,
    )


with DAG("kms_acl_sync", schedule="*/15 * * * *", start_date=datetime(2026, 1, 1), catchup=False,
         default_args=DEFAULTS, max_active_runs=1, tags=["kms"],
         doc_md="Re-read Confluence/Jira permissions into the index. Pauses itself if >5% of content would vanish."):
    kms_task("acl_sync", "acl-sync")

with DAG("kms_nightly_reconcile", schedule="30 1 * * *", start_date=datetime(2026, 1, 1), catchup=False,
         default_args=DEFAULTS, max_active_runs=1, tags=["kms"],
         doc_md="Compare source manifest vs index: NEW / UPDATED / MISSING / STALE, and fix drift."):
    kms_task("reconcile", "reconcile")

with DAG("kms_cloud_change_poll", schedule="*/5 * * * *", start_date=datetime(2026, 1, 1), catchup=False,
         default_args=DEFAULTS, max_active_runs=1, tags=["kms"],
         doc_md="Atlassian Cloud only: CQL/JQL 'changed since' poll (no inbound internet port needed)."):
    kms_task("poll_changes", "poll")

with DAG("kms_audit_archive", schedule="15 0 * * *", start_date=datetime(2026, 1, 1), catchup=False,
         default_args=DEFAULTS, max_active_runs=1, tags=["kms", "audit"],
         doc_md="Copy audit rows to S3 Object Lock (write-once) storage."):
    kms_task("archive_audit", "archive-audit")

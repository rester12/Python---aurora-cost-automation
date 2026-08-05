import lambda_function

from lambda_function import (
    apply_cluster_action,
    evaluate_cluster,
    has_target_tag,
    is_dry_run_enabled,
    lambda_handler,
)


class FakeRDSClient:
    """Record stop requests without calling AWS."""

    def __init__(self):
        self.stop_requests = []

    def stop_db_cluster(self, DBClusterIdentifier):
        self.stop_requests.append(DBClusterIdentifier)

        return {
            "DBCluster": {
                "DBClusterIdentifier": DBClusterIdentifier,
                "Status": "stopping",
            }
        }


def test_returns_true_when_target_tag_exists():
    tags = [
        {"Key": "environment", "Value": "dev"},
        {"Key": "owner", "Value": "rester"},
    ]

    result = has_target_tag(
        tags,
        target_key="environment",
        target_value="dev",
    )

    assert result is True


def test_returns_false_when_target_value_is_different():
    tags = [
        {"Key": "environment", "Value": "production"},
    ]

    result = has_target_tag(
        tags,
        target_key="environment",
        target_value="dev",
    )

    assert result is False


def test_returns_false_when_target_key_is_missing():
    tags = [
        {"Key": "owner", "Value": "rester"},
    ]

    result = has_target_tag(
        tags,
        target_key="environment",
        target_value="dev",
    )

    assert result is False


def test_returns_false_when_tag_list_is_empty():
    tags = []

    result = has_target_tag(
        tags,
        target_key="environment",
        target_value="dev",
    )

    assert result is False


def test_handles_incomplete_tag_safely():
    tags = [
        {"Key": "environment"},
        {"Value": "dev"},
        {},
    ]

    result = has_target_tag(
        tags,
        target_key="environment",
        target_value="dev",
    )

    assert result is False


def test_cluster_qualifies_when_tag_matches_and_status_is_available():
    cluster = {
        "DBClusterIdentifier": "dev-aurora-cluster",
        "Engine": "aurora-mysql",
        "Status": "available",
    }

    tags = [
        {"Key": "environment", "Value": "dev"},
    ]

    result = evaluate_cluster(cluster, tags)

    assert result["tag_matches"] is True
    assert result["is_available"] is True
    assert result["would_stop"] is True


def test_cluster_does_not_qualify_when_status_is_stopped():
    cluster = {
        "DBClusterIdentifier": "dev-aurora-cluster",
        "Engine": "aurora-mysql",
        "Status": "stopped",
    }

    tags = [
        {"Key": "environment", "Value": "dev"},
    ]

    result = evaluate_cluster(cluster, tags)

    assert result["tag_matches"] is True
    assert result["is_available"] is False
    assert result["would_stop"] is False


def test_cluster_does_not_qualify_when_tag_is_production():
    cluster = {
        "DBClusterIdentifier": "production-aurora-cluster",
        "Engine": "aurora-postgresql",
        "Status": "available",
    }

    tags = [
        {"Key": "environment", "Value": "production"},
    ]

    result = evaluate_cluster(cluster, tags)

    assert result["tag_matches"] is False
    assert result["is_available"] is True
    assert result["would_stop"] is False


def test_untagged_cluster_does_not_qualify():
    cluster = {
        "DBClusterIdentifier": "untagged-cluster",
        "Engine": "aurora-mysql",
        "Status": "available",
    }

    tags = []

    result = evaluate_cluster(cluster, tags)

    assert result["tag_matches"] is False
    assert result["would_stop"] is False


def test_dry_run_is_enabled_by_default(monkeypatch):
    monkeypatch.delenv("DRY_RUN", raising=False)

    assert is_dry_run_enabled() is True


def test_dry_run_can_be_explicitly_disabled(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "false")

    assert is_dry_run_enabled() is False


def test_dry_run_does_not_call_stop_api():
    fake_client = FakeRDSClient()

    evaluation = {
        "identifier": "aurora-dev-cluster",
        "would_stop": True,
    }

    result = apply_cluster_action(
        fake_client,
        evaluation,
        dry_run=True,
    )

    assert result["action"] == "dry-run"
    assert fake_client.stop_requests == []


def test_live_mode_calls_stop_api_for_qualified_cluster():
    fake_client = FakeRDSClient()

    evaluation = {
        "identifier": "aurora-dev-cluster",
        "would_stop": True,
    }

    result = apply_cluster_action(
        fake_client,
        evaluation,
        dry_run=False,
    )

    assert result["action"] == "stop-requested"
    assert result["status"] == "stopping"
    assert fake_client.stop_requests == [
        "aurora-dev-cluster"
    ]


def test_unqualified_cluster_never_calls_stop_api():
    fake_client = FakeRDSClient()

    evaluation = {
        "identifier": "production-cluster",
        "would_stop": False,
    }

    result = apply_cluster_action(
        fake_client,
        evaluation,
        dry_run=False,
    )

    assert result["action"] == "skipped"
    assert fake_client.stop_requests == []


def test_lambda_handler_runs_workflow_in_dry_run_mode(monkeypatch):
    recorded = {}

    fake_client = object()

    monkeypatch.delenv("DRY_RUN", raising=False)

    monkeypatch.setattr(
        lambda_function,
        "create_rds_client",
        lambda: fake_client,
    )

    def fake_run_workflow(rds_client, dry_run):
        recorded["client"] = rds_client
        recorded["dry_run"] = dry_run

    monkeypatch.setattr(
        lambda_function,
        "run_workflow",
        fake_run_workflow,
    )

    result = lambda_handler(
        event={},
        context=None,
    )

    assert recorded["client"] is fake_client
    assert recorded["dry_run"] is True
    assert result["statusCode"] == 200
    assert result["dryRun"] is True

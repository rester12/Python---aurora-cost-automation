"""Safely discover and stop tagged Amazon Aurora clusters."""

import logging
import os

import boto3
from botocore.exceptions import BotoCoreError, ClientError


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

TARGET_TAG_KEY = "environment"
TARGET_TAG_VALUE = "dev"


def has_target_tag(tags, target_key, target_value):
    """Return True when the requested key-value tag is present."""
    for tag in tags:
        key = tag.get("Key")
        value = tag.get("Value")

        if key == target_key and value == target_value:
            return True

    return False


def is_dry_run_enabled():
    """Return True unless DRY_RUN is explicitly disabled."""
    setting = os.getenv("DRY_RUN", "true").strip().lower()

    false_values = {"false", "0", "no"}
    return setting not in false_values


def create_rds_client():
    """Create and return a Boto3 client for Amazon RDS."""
    return boto3.client("rds")


def list_db_clusters(rds_client):
    """Return all DB clusters visible to the current AWS identity."""
    clusters = []
    paginator = rds_client.get_paginator("describe_db_clusters")

    for page in paginator.paginate():
        page_clusters = page.get("DBClusters", [])
        clusters.extend(page_clusters)

    return clusters


def get_cluster_tags(rds_client, cluster_arn):
    """Return the tags attached to one DB cluster."""
    response = rds_client.list_tags_for_resource(
        ResourceName=cluster_arn
    )

    return response.get("TagList", [])


def evaluate_cluster(cluster, tags):
    """Return a safety evaluation for one DB cluster."""
    identifier = cluster.get("DBClusterIdentifier", "unknown")
    engine = cluster.get("Engine", "unknown")
    status = cluster.get("Status", "unknown")

    tag_matches = has_target_tag(
        tags,
        target_key=TARGET_TAG_KEY,
        target_value=TARGET_TAG_VALUE,
    )

    is_available = status == "available"
    would_stop = tag_matches and is_available

    return {
        "identifier": identifier,
        "engine": engine,
        "status": status,
        "tag_matches": tag_matches,
        "is_available": is_available,
        "would_stop": would_stop,
    }


def stop_cluster(rds_client, cluster_identifier):
    """Request that AWS stop one Aurora DB cluster."""
    return rds_client.stop_db_cluster(
        DBClusterIdentifier=cluster_identifier
    )


def apply_cluster_action(rds_client, evaluation, dry_run):
    """Skip, simulate, or request a cluster stop."""
    if not evaluation["would_stop"]:
        return {
            "action": "skipped",
            "reason": "Cluster did not pass all safety checks.",
        }

    if dry_run:
        return {
            "action": "dry-run",
            "reason": "Cluster qualifies, but no stop request was sent.",
        }

    response = stop_cluster(
        rds_client,
        evaluation["identifier"],
    )

    response_cluster = response.get("DBCluster", {})

    return {
        "action": "stop-requested",
        "status": response_cluster.get("Status", "unknown"),
    }


def display_cluster_evaluation(
    cluster,
    tags,
    evaluation,
    action_result,
):
    """Print the evaluation and action for one DB cluster."""
    cluster_arn = cluster.get("DBClusterArn", "unknown")

    print(f"Cluster identifier: {evaluation['identifier']}")
    print(f"Engine: {evaluation['engine']}")
    print(f"Status before action: {evaluation['status']}")
    print(f"ARN: {cluster_arn}")
    print(f"Tags: {tags}")
    print(
        f"Has {TARGET_TAG_KEY}={TARGET_TAG_VALUE}: "
        f"{evaluation['tag_matches']}"
    )
    print(f"Status is available: {evaluation['is_available']}")
    print(
        "Passed all safety checks: "
        f"{evaluation['would_stop']}"
    )
    print(f"Action: {action_result['action']}")

    if "reason" in action_result:
        print(f"Reason: {action_result['reason']}")

    if "status" in action_result:
        print(
            f"AWS response status: "
            f"{action_result['status']}"
        )

    print("-" * 60)


def run_workflow(rds_client, dry_run):
    """Evaluate every cluster and perform the permitted action."""
    clusters = list_db_clusters(rds_client)

    print(f"Dry-run enabled: {dry_run}")
    print(f"Found {len(clusters)} DB cluster(s).")
    print()

    for cluster in clusters:
        identifier = cluster.get(
            "DBClusterIdentifier",
            "unknown",
        )
        cluster_arn = cluster.get("DBClusterArn")

        if not cluster_arn:
            print(
                f"Skipping {identifier}: "
                "the response did not contain a cluster ARN."
            )
            print("-" * 60)
            continue

        try:
            tags = get_cluster_tags(
                rds_client,
                cluster_arn,
            )

            evaluation = evaluate_cluster(
                cluster,
                tags,
            )

            action_result = apply_cluster_action(
                rds_client,
                evaluation,
                dry_run,
            )

            display_cluster_evaluation(
                cluster,
                tags,
                evaluation,
                action_result,
            )

        except ClientError as error:
            logger.exception(
                "AWS operation failed for cluster %s: %s",
                identifier,
                error,
            )


def lambda_handler(event, context):
    """AWS Lambda entry point."""
    dry_run = is_dry_run_enabled()
    rds_client = create_rds_client()

    run_workflow(
        rds_client,
        dry_run,
    )

    return {
        "statusCode": 200,
        "dryRun": dry_run,
        "message": "Aurora workflow completed.",
    }


def main():
    """Run the workflow locally."""
    dry_run = is_dry_run_enabled()

    try:
        rds_client = create_rds_client()
        run_workflow(rds_client, dry_run)

    except (ClientError, BotoCoreError) as error:
        logger.exception(
            "Unable to run the Aurora workflow: %s",
            error,
        )
        raise


if __name__ == "__main__":
    main()

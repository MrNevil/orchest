import copy
import os
import time
import uuid

import posthog
from posthog.request import APIError

from app.utils import write_config


# Analytics related functions
def send_anonymized_pipeline_definition(app, pipeline):
    """Sends anonymized pings of an anonymized pipeline definition.

    We send the anonymized pipeline definition to understand the
    typical structure of pipelines created in Orchest. This teaches how
    to further improve Orchest's core features.

    What we track and why. Additional metrics are constructed of the
    removed fields:
        * step_count: The number of steps the pipeline contains. This
          teaches us how large typical pipelines can get.
        * step_parameters_count: The cumsum of the number of parameters
          of all steps. For analysis of the parameters usability.
        * pipeline_parameters_count: The sum of the number of parameters
          at the pipeline level. For analysis of the parameters
          usability.
        * environment_count: Number of unique environments used. Teaches
          us whether users build different environments for every step
          or just use one environment.
        * definition: An anonymized version of the pipeline definition.
          This way we can later extract new metrics.

    """
    # Make a copy so that we can remove potentially sensitive fields.
    pipeline = copy.deepcopy(pipeline)

    # Statistics construction.
    pipeline.pop("name")
    pipeline_parameters_count = len(pipeline.pop("parameters", {}))

    steps = pipeline.get("steps", {})
    step_count = len(steps)

    environments = set()
    step_parameters_count = 0
    for _, step in steps.items():
        step.pop("title")
        step.pop("file_path")
        step_parameters_count += len(step.pop("parameters", {}))

        env = step.get("environment", "")
        if len(env):
            environments.add(env)

    send_event(
        app,
        "pipeline save",
        {
            "step_count": step_count,
            "step_parameters_count": step_parameters_count,
            "pipeline_parameters_count": pipeline_parameters_count,
            "environment_count": len(environments),
            "definition": pipeline,
        },
    )


def send_pipeline_run(app, pipeline_identifier, project_path, run_type):
    project_size = sum(
        d.stat().st_size for d in os.scandir(project_path) if d.is_file()
    )
    send_event(
        app,
        "pipeline run",
        {
            "identifier": pipeline_identifier,
            "project_size": project_size,
            "run_type": run_type,
        },
    )


def get_telemetry_uuid(app):

    # get UUID if it exists
    if "TELEMETRY_UUID" in app.config:
        telemetry_uuid = app.config["TELEMETRY_UUID"]
    else:
        telemetry_uuid = str(uuid.uuid4())
        write_config(app, "TELEMETRY_UUID", telemetry_uuid)

    return telemetry_uuid


def send_event(app, event, properties):
    if app.config["TELEMETRY_DISABLED"]:
        return False

    try:
        telemetry_uuid = get_telemetry_uuid(app)

        if "mode" not in properties:
            properties["mode"] = os.environ.get("FLASK_ENV", "production")

        properties["orchest_version"] = app.config["ORCHEST_REPO_TAG"]

        posthog.capture(telemetry_uuid, event, properties)
        app.logger.debug(
            "Sending event[%s] to Posthog for anonymized user [%s] with properties: %s"
            % (event, telemetry_uuid, properties)
        )
        return True
    except (Exception, APIError) as e:
        app.logger.error("Could not send event through posthog %s" % e)
        return False


def analytics_ping(app):
    """
    Note: telemetry can be disabled by including TELEMETRY_DISABLED in
    your user config.json.
    """
    try:
        properties = {"active": check_active(app)}
        send_event(app, "heartbeat trigger", properties)

    except Exception as e:
        app.logger.warning("Exception while sending telemetry request %s" % e)


def check_active(app):
    try:
        t = os.path.getmtime(app.config["WEBSERVER_LOGS"])

        diff_minutes = (time.time() - t) / 60

        return diff_minutes < (
            app.config["TELEMETRY_INTERVAL"] * 0.5
        )  # check whether user was active in last half of TELEMETRY_INTERVAL
    except OSError as e:
        app.logger.debug("Exception while reading request log recency %s" % e)
        return False

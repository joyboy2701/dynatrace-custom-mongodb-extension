from dynatrace_extension import Extension, Status, StatusValue

from mongodb_custom_query.collector_core import (
    build_error_payloads,
    connect_to_mongodb,
    run_query,
    should_run_query,
    mark_query_ran,
)
from mongodb_custom_query.config_from_endpoint import config_from_endpoint


class MongoDBCustomQueryExtension(Extension):
    extension_name = "mongodb_custom_query"

    def initialize(self) -> Status:
        self.logger.info("MongoDB Custom Query extension starting")
        return Status(StatusValue.OK)

    def query(self):
        endpoints = self.activation_config.get("endpoints", [])
        self.logger.info(f"Monitoring cycle: {len(endpoints)} endpoint(s) configured")

        if not endpoints:
            self.logger.warning("No endpoints in monitoring configuration")
            return

        for endpoint in endpoints:
            label = (
                f"{endpoint.get('host')}:"
                f"{endpoint.get('port', 27017)}/"
                f"{endpoint.get('database_name')}"
            )

            try:
                self._run_endpoint(endpoint, label)
            except Exception as error:
                self.logger.error(f"Endpoint {label} failed before query execution: {error}")
                self._report_endpoint_failure(endpoint, label, error, config=None)

    def _report_endpoint_failure(
        self,
        endpoint: dict,
        label: str,
        error: Exception,
        config: dict | None,
    ):
        """
        Send endpoint-level connection/config errors to Grail.

        This should be used for errors before per-query execution starts,
        for example config parsing failure, DB connection failure, missing password,
        or unreachable database.
        """
        if config:
            db_config = config["database"]
            collector_config = config["collector"]
            query_config = {
                "name": "endpoint_connection",
                "frequency_minutes": 1,
            }

            error_payloads = build_error_payloads(
                db_config=db_config,
                collector_config=collector_config,
                query_config=query_config,
                error=error,
            )

            if error_payloads:
                self.report_log_events(error_payloads, send_immediately=True)

            return

        log_source = endpoint.get("log_source", "mongodb_custom_query")

        self.report_log_events(
            [
                {
                    "loglevel": "ERROR",
                    "log.source": log_source,
                    "content": "mongodb custom query endpoint setup failed",
                    "event.type": "error",
                    "query.name": "endpoint_connection",
                    "query.status": "error",
                    "error.message": str(error),
                    "db.host": endpoint.get("host"),
                    "db.name": endpoint.get("database_name"),
                }
            ],
            send_immediately=True,
        )

    def _run_endpoint(self, endpoint: dict, label: str):
        config = config_from_endpoint(endpoint)

        db_config = config["database"]
        collector_config = config["collector"]
        queries = config["queries"]

        self.logger.info(f"{label}: running {len(queries)} query definition(s)")

        try:
            connection = connect_to_mongodb(db_config)
        except Exception as error:
            self.logger.error(f"Endpoint {label} connection failed: {error}")
            self._report_endpoint_failure(endpoint, label, error, config=config)
            return

        try:
            for query in queries:
                name = query.get("name", "unknown")

                if not query.get("enabled", True):
                    self.logger.info(f"{label} | {name}: skipped because query is disabled")
                    continue

                try:
                    if not should_run_query(query, collector_config):
                        self.logger.info(f"{label} | {name}: skipped because query is not due")
                        continue

                except Exception as error:
                    self.logger.error(f"{label} | {name} schedule check failed: {error}")

                    error_payloads = build_error_payloads(
                        db_config=db_config,
                        collector_config=collector_config,
                        query_config=query,
                        error=error,
                    )

                    if error_payloads:
                        self.report_log_events(error_payloads, send_immediately=True)

                    continue

                try:
                    payloads = run_query(
                        connection=connection,
                        db_config=db_config,
                        collector_config=collector_config,
                        query_config=query,
                    )

                    if payloads:
                        self.report_log_events(payloads, send_immediately=True)

                    self.logger.info(f"{label} | {name}: sent {len(payloads)} log(s)")

                except Exception as error:
                    self.logger.error(f"{label} | {name} failed: {error}")

                    error_payloads = build_error_payloads(
                        db_config=db_config,
                        collector_config=collector_config,
                        query_config=query,
                        error=error,
                    )

                    if error_payloads:
                        self.report_log_events(error_payloads, send_immediately=True)

                finally:
                    mark_query_ran(collector_config, query)

        finally:
            connection.close()


def main():
    MongoDBCustomQueryExtension().run()


if __name__ == "__main__":
    main()
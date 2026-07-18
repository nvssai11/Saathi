import json
from decimal import Decimal
from pydantic_settings import BaseSettings
from pydantic import Field
from aiokafka.helpers import create_ssl_context


class Settings(BaseSettings):
    database_url: str = "postgresql://saathi:saathi@localhost:5432/saathi"
    db_pool_min_size: int = 5
    db_pool_max_size: int = 20
    kafka_bootstrap_servers: str = "localhost:9092"
    # Aiven (or any managed Kafka) deployment: set kafka_security_protocol to
    # "SASL_SSL", fill kafka_sasl_username/password + kafka_ssl_cafile (the
    # service's CA cert, e.g. ca.pem downloaded from the Aiven console) in
    # .env. Local Docker Compose Kafka stays PLAINTEXT — no other code changes
    # needed either way, see Settings.kafka_client_kwargs below.
    kafka_security_protocol: str = "PLAINTEXT"
    kafka_sasl_mechanism: str = "PLAIN"
    kafka_sasl_username: str = ""
    kafka_sasl_password: str = ""
    kafka_ssl_cafile: str = ""
    kafka_topic_order_placed: str = "saathi.order.placed"
    kafka_topic_sublot_delivered: str = "saathi.sublot.delivered"
    kafka_topic_sublot_assigned: str = "saathi.sublot.assigned"
    kafka_allocation_worker_group_id: str = "saathi-allocation-worker"
    kafka_verification_worker_group_id: str = "saathi-verification-worker"
    kafka_notification_worker_group_id: str = "saathi-notification-worker"
    kafka_auto_offset_reset: str = "earliest"
    gemini_api_key: str = ""
    verification_model: str = "gemini-flash-lite-latest"
    verification_max_tokens: int = 1024
    verification_max_loop_iterations: int = 4
    verification_retry_attempts: int = 3
    verification_retry_min_wait_seconds: int = 2
    verification_retry_max_wait_seconds: int = 8
    verification_auto_approve_grace_seconds: int = 30
    auto_verify_sweep_interval_seconds: int = 5
    verification_defect_confidence_threshold: float = 0.90
    reference_image_directory: str = "./assets/reference_photos"
    trust_minimum_threshold: float = 0.30
    trust_cold_start_score: float = 0.500
    trust_window_size: int = 10
    trust_penalty_factor: float = 0.5
    spec_disputes_threshold: int = 3
    spec_disputes_mip_penalty_factor: float = 0.10
    allocation_solver_time_limit_seconds: int = 30
    workshop_notification_list_limit: int = 20
    workshop_sublot_list_limit: int = 200
    platform_fee_percentage: Decimal = Decimal("0.05")
    penalty_non_delivery_percentage: Decimal = Decimal("0.20")
    penalty_workshop_defect_percentage: Decimal = Decimal("0.15")
    buyer_token: str = "buyer-demo-token"
    admin_token: str = "admin-demo-token"
    workshop_tokens_json: str = Field(
        default='{"token-ws-1": 1, "token-ws-2": 2, "token-ws-3": 3, "token-factory": 99}'
    )

    upload_directory: str = "./uploads"
    max_upload_size_bytes: int = 10 * 1024 * 1024

    stuck_order_threshold_seconds: int = 60

    @property
    def workshop_tokens(self) -> dict[str, int]:
        return json.loads(self.workshop_tokens_json)

    @property
    def kafka_client_kwargs(self) -> dict:
        """Extra kwargs to spread into every AIOKafkaProducer/Consumer call.

        Centralized so the four call sites (producer + 3 workers) don't each
        duplicate SASL/SSL setup and can't drift out of sync.
        """
        kwargs: dict = {"security_protocol": self.kafka_security_protocol}
        if self.kafka_security_protocol in ("SSL", "SASL_SSL"):
            kwargs["ssl_context"] = create_ssl_context(
                cafile=self.kafka_ssl_cafile or None
            )
        if self.kafka_security_protocol in ("SASL_SSL", "SASL_PLAINTEXT"):
            kwargs["sasl_mechanism"] = self.kafka_sasl_mechanism
            kwargs["sasl_plain_username"] = self.kafka_sasl_username
            kwargs["sasl_plain_password"] = self.kafka_sasl_password
        return kwargs

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()

from tradingng_platform.model_routing import ModelRoutingPolicy
from tradingng_platform.scheduler.policy import AdmissionDecision
from tradingng_platform.scheduler.repository import ExecutionMetadata, _configured_vendors


class AdmissionService:
    def __init__(
        self,
        scheduler_repository,
        policy_repository,
        gateway_client,
        system_probe,
        metadata: ExecutionMetadata,
        *,
        model_routing_repository=None,
        alpha_broker_client=None,
        alpha_broker_queue_limit: int = 6,
    ):
        self.scheduler_repository = scheduler_repository
        self.policy_repository = policy_repository
        self.gateway_client = gateway_client
        self.system_probe = system_probe
        self.metadata = metadata
        self.model_routing_repository = model_routing_repository
        self.alpha_broker_client = alpha_broker_client
        self.alpha_broker_queue_limit = alpha_broker_queue_limit

    async def admit_one(self) -> AdmissionDecision:
        gateway = await self.gateway_client.status()
        system = self.system_probe.sample()
        policy = await self.policy_repository.get()
        model_routing = (
            await self.model_routing_repository.get()
            if self.model_routing_repository is not None
            else ModelRoutingPolicy()
        )
        external_blockers = ()
        if self.alpha_broker_client is not None and "alpha_vantage" in _configured_vendors(
            self.metadata
        ):
            broker = await self.alpha_broker_client.status()
            if not broker.admission_allowed(queue_limit=self.alpha_broker_queue_limit):
                external_blockers = ("vendor:alpha_vantage:global_quota",)
        return await self.scheduler_repository.admit_one(
            policy,
            gateway,
            system,
            self.metadata,
            model_routing,
            external_blockers=external_blockers,
        )

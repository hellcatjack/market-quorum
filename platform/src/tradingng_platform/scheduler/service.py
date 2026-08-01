from tradingng_platform.model_routing import ModelRoutingPolicy
from tradingng_platform.scheduler.policy import AdmissionDecision
from tradingng_platform.scheduler.repository import ExecutionMetadata


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
    ):
        self.scheduler_repository = scheduler_repository
        self.policy_repository = policy_repository
        self.gateway_client = gateway_client
        self.system_probe = system_probe
        self.metadata = metadata
        self.model_routing_repository = model_routing_repository

    async def admit_one(self) -> AdmissionDecision:
        gateway = await self.gateway_client.status()
        system = self.system_probe.sample()
        policy = await self.policy_repository.get()
        model_routing = (
            await self.model_routing_repository.get()
            if self.model_routing_repository is not None
            else ModelRoutingPolicy()
        )
        return await self.scheduler_repository.admit_one(
            policy,
            gateway,
            system,
            self.metadata,
            model_routing,
            external_blockers=(),
        )

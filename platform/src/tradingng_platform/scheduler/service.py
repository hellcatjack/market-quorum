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
    ):
        self.scheduler_repository = scheduler_repository
        self.policy_repository = policy_repository
        self.gateway_client = gateway_client
        self.system_probe = system_probe
        self.metadata = metadata

    async def admit_one(self) -> AdmissionDecision:
        gateway = await self.gateway_client.status()
        system = self.system_probe.sample()
        policy = await self.policy_repository.get()
        return await self.scheduler_repository.admit_one(
            policy,
            gateway,
            system,
            self.metadata,
        )

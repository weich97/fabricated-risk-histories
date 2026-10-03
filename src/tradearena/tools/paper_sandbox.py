from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Protocol, runtime_checkable

from tradearena.core.domain import Order
from tradearena.tools.broker_export import (
    AlpacaPaperExportAdapter,
    AlpacaPaperOrder,
    BrokerAdapterContractError,
    BrokerAdapterMode,
    BrokerOrderStatus,
    BrokerResponse,
    BrokerSafetyConfig,
    broker_handoff_artifact_hash,
    write_broker_response_artifact,
)


@runtime_checkable
class PaperSandboxClient(Protocol):
    """Minimal injected client surface for optional paper-sandbox adapters."""

    def submit_paper_orders(self, requests: Sequence[AlpacaPaperOrder]) -> Sequence[BrokerResponse | Mapping[str, object]]:
        """Submit already-reviewed paper requests to a sandbox implementation."""


class PaperSandboxAdapterSkeleton:
    """No-default-network paper sandbox adapter skeleton.

    The class deliberately has no built-in broker SDK dependency. Contributors can
    inject a paper client from an optional package and keep CI mock-backed.
    """

    name = "paper-sandbox-adapter-skeleton"

    def __init__(
        self,
        *,
        client: PaperSandboxClient | None = None,
        client_factory: Callable[[], PaperSandboxClient] | None = None,
        client_prefix: str = "paper-sandbox",
        safety: BrokerSafetyConfig | None = None,
    ) -> None:
        if client is not None and client_factory is not None:
            raise BrokerAdapterContractError("provide either client or client_factory, not both")
        self.client = client
        self.client_factory = client_factory
        self.client_prefix = client_prefix
        self.safety = _paper_sandbox_safety(safety)
        self._handoff = AlpacaPaperExportAdapter(client_prefix=client_prefix, safety=self.safety)

    def convert(self, orders: list[Order] | tuple[Order, ...]) -> list[AlpacaPaperOrder]:
        return self._handoff.convert(orders)

    def write(self, orders: list[Order] | tuple[Order, ...], output_dir: str | Path) -> dict[str, str | int | bool]:
        return self._handoff.write(orders, output_dir)

    def submit_paper(self, orders: list[Order] | tuple[Order, ...], output_dir: str | Path) -> dict[str, str | int | bool]:
        client = self._configured_client()
        output_path = Path(output_dir)
        request_export = self.write(orders, output_path)
        requests = self.convert(orders)
        raw_responses = client.submit_paper_orders(requests)
        responses = [_coerce_broker_response(response) for response in raw_responses]
        response_artifact = output_path / "paper_sandbox_response_artifact.json"
        response_export = write_broker_response_artifact(
            requests=requests,
            responses=responses,
            output=response_artifact,
            adapter=self.name,
            adapter_mode=BrokerAdapterMode.PAPER_SANDBOX,
            account_mode="paper",
            request_artifact_hash=broker_handoff_artifact_hash(output_path / "alpaca_paper_orders.json"),
        )
        return {
            "request_artifact": str(request_export["json"]),
            "response_artifact": str(response_artifact),
            "response_count": response_export["response_count"],
            "unmatched_response_count": response_export["unmatched_response_count"],
            "missing_response_count": response_export["missing_response_count"],
            "live_submission": False,
        }

    def _configured_client(self) -> PaperSandboxClient:
        if self.client is not None:
            return self.client
        if self.client_factory is not None:
            return self.client_factory()
        raise BrokerAdapterContractError(
            "paper sandbox optional client is not configured; install a broker-specific extra and inject a client"
        )


def _paper_sandbox_safety(safety: BrokerSafetyConfig | None) -> BrokerSafetyConfig:
    if safety is None:
        return BrokerSafetyConfig(mode=BrokerAdapterMode.PAPER_SANDBOX, account_mode="paper")
    if safety.mode != BrokerAdapterMode.PAPER_SANDBOX:
        raise BrokerAdapterContractError("paper sandbox adapter skeleton requires paper_sandbox mode")
    if safety.account_mode != "paper":
        raise BrokerAdapterContractError("paper sandbox adapter skeleton requires account_mode paper")
    return safety


def _coerce_broker_response(response: BrokerResponse | Mapping[str, object]) -> BrokerResponse:
    if isinstance(response, BrokerResponse):
        return response
    payload = dict(response)
    status = payload.get("status")
    if isinstance(status, str):
        payload["status"] = BrokerOrderStatus(status)
    return BrokerResponse(**payload)  # type: ignore[arg-type]

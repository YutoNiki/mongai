"""検索ゲートウェイ（gateway）のクライアント。core から学外に出る要求は、必ずここを通す。"""

import os
from typing import Any

import httpx

from shared.schemas import BibLookup, GatewayRequest, GatewayResponse, OATextFetch, TermLookup


class GatewayError(RuntimeError):
    pass


class GatewayClient:
    def __init__(
        self, base_url: str | None = None, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.base_url = (base_url or os.environ.get("GATEWAY_URL") or "http://gateway:8000").rstrip(
            "/"
        )
        self._transport = transport

    def _http(self) -> httpx.AsyncClient:
        # gateway は学内のネットワーク上にある。プロキシは使わない
        return httpx.AsyncClient(
            base_url=self.base_url, timeout=120, trust_env=False, transport=self._transport
        )

    async def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        async with self._http() as http:
            try:
                response = await http.request(method, path, **kwargs)
            except httpx.HTTPError as exc:
                raise GatewayError(
                    f"検索ゲートウェイに接続できません：{type(exc).__name__}"
                ) from exc
        if response.status_code == 422:
            detail = response.json().get("detail")
            if isinstance(detail, dict):
                return detail
        if response.status_code >= 400:
            raise GatewayError(
                f"検索ゲートウェイがエラーを返しました（HTTP {response.status_code}）"
            )
        return response.json() if response.content else None

    async def register(self, guard: dict[str, Any]) -> None:
        await self._call("POST", "/manuscripts", json=guard)

    async def forget(self, doc_id: str) -> None:
        await self._call("DELETE", f"/manuscripts/{doc_id}")

    async def submit(
        self, analysis_id: str, doc_id: str, request: BibLookup | OATextFetch | TermLookup
    ) -> GatewayResponse:
        envelope = GatewayRequest(analysis_id=analysis_id, doc_id=doc_id, request=request)
        data = await self._call("POST", "/requests", json=envelope.model_dump(mode="json"))
        return GatewayResponse.model_validate(data)

    async def mode(self) -> str:
        return str((await self._call("GET", "/mode"))["mode"])

    async def queue(self) -> list[dict[str, Any]]:
        return list(await self._call("GET", "/queue"))

    async def approve(self, request_id: str) -> GatewayResponse:
        return GatewayResponse.model_validate(
            await self._call("POST", f"/queue/{request_id}/approve")
        )

    async def reject(self, request_id: str) -> GatewayResponse:
        return GatewayResponse.model_validate(
            await self._call("POST", f"/queue/{request_id}/reject")
        )

    async def audit(self, analysis_id: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit}
        if analysis_id:
            params["analysis_id"] = analysis_id
        return list(await self._call("GET", "/audit", params=params))

    async def external_count(self, analysis_id: str) -> int:
        return int(
            (await self._call("GET", "/audit/count", params={"analysis_id": analysis_id}))["count"]
        )

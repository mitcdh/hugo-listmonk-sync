"""Authenticated Listmonk campaign API client."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, TypeGuard

import httpx

from hugo_listmonk_sync.config import Config
from hugo_listmonk_sync.errors import ListmonkError
from hugo_listmonk_sync.feed import FeedPost
from hugo_listmonk_sync.http import RetryingHttpClient

_UPDATE_FIELDS = (
    "lists",
    "from_email",
    "type",
    "content_type",
    "body_source",
    "altbody",
    "send_at",
    "messenger",
    "template_id",
    "tags",
    "headers",
)


@dataclass(frozen=True, slots=True)
class CampaignRef:
    """The campaign fields required to decide reconciliation behavior."""

    id: int
    name: str
    status: str


class ListmonkClient:
    """Listmonk campaign operations used by the synchronizer."""

    def __init__(
        self,
        http: RetryingHttpClient,
        config: Config,
    ) -> None:
        self._http = http
        self._config = config
        self._campaigns_url = f"{config.listmonk_base_url}/api/campaigns"

    def list_campaigns(self) -> tuple[CampaignRef, ...]:
        """Return all campaigns visible to the configured API user."""
        payload = self._request_json(
            "GET",
            self._campaigns_url,
            retry=True,
            params={"per_page": "all", "no_body": "true"},
        )
        data = payload.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            raise ListmonkError(
                "Malformed Listmonk campaign list response: expected data.results"
            )
        campaigns: list[CampaignRef] = []
        for index, item in enumerate(data["results"]):
            if not isinstance(item, dict):
                raise ListmonkError(
                    f"Malformed Listmonk campaign at results index {index}"
                )
            campaign_id = item.get("id")
            name = item.get("name")
            status = item.get("status")
            if (
                not _is_positive_int(campaign_id)
                or not isinstance(name, str)
                or not isinstance(status, str)
            ):
                raise ListmonkError(
                    f"Malformed Listmonk campaign at results index {index}"
                )
            campaigns.append(CampaignRef(id=campaign_id, name=name, status=status))
        return tuple(campaigns)

    def get_campaign(self, campaign_id: int) -> dict[str, Any]:
        """Fetch a campaign including its body and mutable settings."""
        payload = self._request_json(
            "GET",
            f"{self._campaigns_url}/{campaign_id}",
            retry=True,
        )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ListmonkError(
                f"Malformed Listmonk response for campaign {campaign_id}"
            )
        if (
            not _is_positive_int(data.get("id"))
            or data["id"] != campaign_id
            or not isinstance(data.get("name"), str)
            or not isinstance(data.get("status"), str)
        ):
            raise ListmonkError(
                f"Listmonk returned malformed identity fields for campaign "
                f"{campaign_id}"
            )
        return copy.deepcopy(data)

    def create_campaign(self, post: FeedPost) -> dict[str, Any]:
        """Create a new draft campaign without automatically retrying."""
        request = self.creation_payload(post)
        payload = self._request_json(
            "POST",
            self._campaigns_url,
            retry=False,
            json=request,
        )
        data = payload.get("data")
        if not isinstance(data, dict) or not _is_positive_int(data.get("id")):
            raise ListmonkError("Malformed Listmonk campaign creation response")
        return copy.deepcopy(data)

    def update_campaign(
        self,
        campaign_id: int,
        existing: dict[str, Any],
        post: FeedPost,
    ) -> dict[str, Any]:
        """Update a draft while preserving all non-feed campaign settings."""
        request = self.update_payload(existing, post)
        payload = self._request_json(
            "PUT",
            f"{self._campaigns_url}/{campaign_id}",
            retry=True,
            json=request,
        )
        data = payload.get("data")
        if (
            not isinstance(data, dict)
            or not _is_positive_int(data.get("id"))
            or data["id"] != campaign_id
        ):
            raise ListmonkError(
                f"Malformed Listmonk update response for campaign {campaign_id}"
            )
        return copy.deepcopy(data)

    def creation_payload(self, post: FeedPost) -> dict[str, Any]:
        """Build a Listmonk campaign creation request."""
        request: dict[str, Any] = {
            "name": post.name,
            "subject": post.subject,
            "lists": list(self._config.listmonk_list_ids),
            "type": self._config.listmonk_campaign_type,
            "content_type": self._config.listmonk_content_type,
            "body": post.content,
            "messenger": self._config.listmonk_messenger,
            "attribs": {"post": copy.deepcopy(post.attributes)},
        }
        if self._config.listmonk_template_id is not None:
            request["template_id"] = self._config.listmonk_template_id
        if self._config.listmonk_from_email is not None:
            request["from_email"] = self._config.listmonk_from_email
        if self._config.listmonk_campaign_tags:
            request["tags"] = list(self._config.listmonk_campaign_tags)
        return request

    @staticmethod
    def update_payload(
        existing: dict[str, Any],
        post: FeedPost,
    ) -> dict[str, Any]:
        """Build an update request from a full campaign representation."""
        campaign_id = existing.get("id")
        if existing.get("status") != "draft":
            raise ListmonkError(
                f"Campaign {campaign_id!r} is no longer a draft; refusing update"
            )

        request: dict[str, Any] = {
            "name": post.name,
            "subject": post.subject,
            "body": post.content,
        }
        for field in _UPDATE_FIELDS:
            if field in existing:
                request[field] = copy.deepcopy(existing[field])

        lists = request.get("lists")
        if not isinstance(lists, list):
            raise ListmonkError(f"Campaign {campaign_id!r} has malformed lists")
        list_ids: list[int] = []
        for item in lists:
            list_id = item.get("id") if isinstance(item, dict) else item
            if not _is_positive_int(list_id):
                raise ListmonkError(f"Campaign {campaign_id!r} has malformed lists")
            list_ids.append(list_id)
        request["lists"] = list_ids

        attribs = existing.get("attribs", {})
        if not isinstance(attribs, dict):
            raise ListmonkError(f"Campaign {campaign_id!r} has malformed attribs")
        preserved_attribs = copy.deepcopy(attribs)
        preserved_attribs["post"] = copy.deepcopy(post.attributes)
        request["attribs"] = preserved_attribs
        return request

    def _request_json(
        self,
        method: str,
        url: str,
        *,
        retry: bool,
        **kwargs: Any,
    ) -> dict[str, Any]:
        try:
            response = self._http.request(
                method,
                url,
                retry=retry,
                headers={"Accept": "application/json"},
                **kwargs,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            message = _listmonk_error_message(exc.response)
            raise ListmonkError(
                f"Listmonk {method} {exc.request.url} failed with "
                f"HTTP {exc.response.status_code}: {message}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ListmonkError(f"Listmonk {method} {url} failed: {exc}") from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise ListmonkError(
                f"Listmonk {method} {url} returned invalid JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise ListmonkError(
                f"Listmonk {method} {url} returned a non-object response"
            )
        return payload


def _listmonk_error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:200] or "no response body"
    if isinstance(payload, dict):
        message = payload.get("message")
        if isinstance(message, str):
            return message
    return response.text[:200] or "no response body"


def _is_positive_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0

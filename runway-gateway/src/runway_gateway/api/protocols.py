"""Structural types for the RunwayML client surface we depend on.

Typing against a Protocol (not the concrete ``RunwayML`` class) is what lets the
fake client be injected at construction with full type-checking, and keeps the
wrapper import-light. The real SDK client is structurally compatible.
"""

from __future__ import annotations

from typing import Any, Protocol


class HasId(Protocol):
    id: str


class HasUri(Protocol):
    uri: str


class CreateResource(Protocol):
    def create(self, **params: Any) -> HasId: ...


class TasksResource(Protocol):
    def retrieve(self, id: str) -> Any: ...
    def delete(self, id: str) -> Any: ...


class UploadsResource(Protocol):
    def create_ephemeral(self, *, file: Any) -> HasUri: ...


class RecipesResource(Protocol):
    def ad_localization(self, **params: Any) -> Any: ...
    def marketing_stock_image(self, **params: Any) -> Any: ...
    def multi_shot_video(self, **params: Any) -> Any: ...
    def product_ad(self, **params: Any) -> Any: ...
    def product_campaign_image(self, **params: Any) -> Any: ...
    def product_swap(self, **params: Any) -> Any: ...
    def product_ugc(self, **params: Any) -> Any: ...


class WorkflowsResource(Protocol):
    def retrieve(self, id: str) -> Any: ...
    def list(self) -> Any: ...
    def run(self, id: str, **params: Any) -> Any: ...


class WorkflowInvocationsResource(Protocol):
    def retrieve(self, id: str) -> Any: ...


class OrganizationResource(Protocol):
    def retrieve(self) -> Any: ...
    def retrieve_usage(self, **params: Any) -> Any: ...


class RunwayClient(Protocol):
    """The subset of the RunwayML SDK client the wrapper touches."""

    text_to_image: CreateResource
    image_to_video: CreateResource
    video_to_video: CreateResource
    text_to_speech: CreateResource
    sound_effect: CreateResource
    video_upscale: CreateResource
    tasks: TasksResource
    uploads: UploadsResource
    recipes: RecipesResource
    workflows: WorkflowsResource
    workflow_invocations: WorkflowInvocationsResource
    organization: OrganizationResource

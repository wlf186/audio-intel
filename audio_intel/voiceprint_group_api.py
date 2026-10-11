"""Authenticated voiceprint grouping and explicit batch operations."""
from __future__ import annotations

import sqlite3
from typing import Any, Callable

from fastapi import Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from . import voiceprint_groups as groups


class GroupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80, description="分组名称，规范化后唯一 / Unique normalized group name")


class GroupResponse(BaseModel):
    id: str
    name: str
    builtin: bool
    person_count: int
    created_at: str
    updated_at: str


class GroupsResponse(BaseModel):
    items: list[GroupResponse]


class MovePeopleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    person_ids: list[str] = Field(min_length=1, description="唯一人员 ID / Unique person IDs")
    group_id: str = Field(min_length=1)


class DeletePeopleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    person_ids: list[str] = Field(min_length=1)
    purge: bool = Field(False, description="明确确认永久删除 / Explicit permanent-delete confirmation")


class PersonDeleteResult(BaseModel):
    person_id: str
    deleted: bool
    status: int
    detail: str | None = None


class PeopleDeleteResponse(BaseModel):
    items: list[PersonDeleteResult]


def register(app: Any, api: Any, remove_person: Callable[..., Any]) -> None:
    auth = Depends(api.require_api_key)
    responses = {**api.AUTH_RESPONSES, **api.VALIDATION_RESPONSE, **api.CONFLICT_RESPONSE}
    tag = [api.VOICEPRINT_TAG]

    def change(function: Callable[..., Any], *arguments: Any):
        try:
            return function(*arguments)
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, "A group with this name already exists") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/v1/voiceprints/groups", response_model=GroupsResponse, tags=tag, responses=responses,
             description="返回内置未分组及自定义分组。\n\n**English:** Returns Ungrouped and custom groups.",
             summary="查询声纹分组 / List voiceprint groups", operation_id="listVoiceprintGroups")
    def listing(_: None = auth):
        return {"items": groups.list_groups()}

    @app.post("/api/v1/voiceprints/groups", status_code=201, response_model=GroupResponse, tags=tag, responses=responses,
              description="规范化后的分组名称必须唯一，内置组不可修改。\n\n**English:** Normalized names must be unique; the built-in group is protected.",
              summary="创建声纹分组 / Create voiceprint group", operation_id="createVoiceprintGroup")
    def create(body: GroupRequest, _: None = auth):
        return change(groups.create_group, body.name)

    @app.patch("/api/v1/voiceprints/groups/{group_id}", response_model=GroupResponse, tags=tag, responses=responses,
              description="规范化后的分组名称必须唯一，内置组不可修改。\n\n**English:** Normalized names must be unique; the built-in group is protected.",
               summary="重命名声纹分组 / Rename voiceprint group", operation_id="renameVoiceprintGroup")
    def rename(group_id: str, body: GroupRequest, _: None = auth):
        return change(groups.rename_group, group_id, body.name)

    @app.delete("/api/v1/voiceprints/groups/{group_id}", status_code=204, tags=tag, responses=responses,
                description="组内人员移回未分组，人员和样本保留。内置组不可删除。\n\n**English:** Moves members to Ungrouped; people and samples remain. The built-in group is protected.",
                summary="删除声纹分组 / Delete voiceprint group", operation_id="deleteVoiceprintGroup")
    def delete(group_id: str, _: None = auth):
        change(groups.delete_group, group_id)
        return Response(status_code=204)

    @app.post("/api/v1/voiceprints/people/batch-move", status_code=204, tags=tag, responses=responses,
              description="原子校验并移动全部指定人员，失败不部分移动。\n\n**English:** Validates and moves all people atomically.",
              summary="批量移动人员 / Move people in a batch", operation_id="moveVoiceprintPeople")
    def move(body: MovePeopleRequest, _: None = auth):
        change(groups.move_people, body.person_ids, body.group_id)
        return Response(status_code=204)

    @app.post("/api/v1/voiceprints/people/batch-delete", response_model=PeopleDeleteResponse, tags=tag, responses=responses,
              description="要求 purge=true；逐人返回结果，活动导入及正在读取的音频不可删除。\n\n**English:** Requires purge=true; per-person results retain active imports and audio readers.",
              summary="批量删除人员 / Delete people in a batch", operation_id="deleteVoiceprintPeople")
    def delete_people(body: DeletePeopleRequest, _: None = auth):
        if not body.purge:
            raise HTTPException(409, "Set purge=true to permanently delete people")
        items = []
        for identifier in dict.fromkeys(body.person_ids):
            try:
                remove_person(identifier, True, None)
                items.append({"person_id": identifier, "deleted": True, "status": 204})
            except HTTPException as exc:
                items.append({"person_id": identifier, "deleted": False, "status": exc.status_code, "detail": str(exc.detail)})
            except OSError:
                items.append({"person_id": identifier, "deleted": False, "status": 500, "detail": "Audio cleanup failed; retry deletion"})
        return {"items": items}

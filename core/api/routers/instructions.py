"""個人用の学部・講義設定、課題要件の抽出と確認。"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import Field, field_validator

from core.api.dependencies import QueueDep, RuleStoreDep, StoreDep, UserDep
from core.instructions.models import (
    DocumentType,
    Draft,
    Model,
    Profile,
    ProfileRef,
    ProfileScope,
    Requirement,
)
from core.instructions.store import (
    InstructionConflictError,
    InstructionNotFoundError,
    InstructionStore,
)

router = APIRouter()


class DraftCreate(Model):
    document_type: DocumentType = "paper"
    raw_text: str = Field(default="", max_length=10000)
    profiles: list[ProfileRef] = Field(default_factory=list, max_length=10)


class DraftEdit(Model):
    revision: int = Field(ge=1)
    requirements: list[Requirement] = Field(max_length=50)
    excluded_rule_ids: list[str] = Field(default_factory=list, max_length=1000)


class ConfirmDraft(Model):
    revision: int = Field(ge=1)


class ProfileWrite(Model):
    name: str = Field(min_length=1, max_length=100)
    scope: ProfileScope
    draft_id: str
    draft_revision: int = Field(ge=1)
    revision: int | None = Field(default=None, ge=1)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("設定名を入力してください")
        return value.strip()


def instruction_error(exc: InstructionNotFoundError | InstructionConflictError) -> HTTPException:
    if isinstance(exc, InstructionNotFoundError):
        return HTTPException(status_code=404, detail="設定または確認項目が見つかりません")
    return HTTPException(status_code=409, detail=str(exc))


@router.get("/instruction-profiles")
def list_profiles(store: StoreDep, user: UserDep) -> list[Profile]:
    return InstructionStore(store.path).list_profiles(user.user_id)


@router.post("/instruction-profiles", status_code=201)
def create_profile(request: ProfileWrite, store: StoreDep, user: UserDep) -> Profile:
    try:
        return InstructionStore(store.path).save_profile(
            user.user_id,
            request.name,
            request.scope,
            request.draft_id,
            request.draft_revision,
        )
    except (InstructionNotFoundError, InstructionConflictError) as exc:
        raise instruction_error(exc) from None


@router.patch("/instruction-profiles/{profile_id}")
def update_profile(
    profile_id: str, request: ProfileWrite, store: StoreDep, user: UserDep
) -> Profile:
    try:
        return InstructionStore(store.path).save_profile(
            user.user_id,
            request.name,
            request.scope,
            request.draft_id,
            request.draft_revision,
            profile_id=profile_id,
            revision=request.revision,
        )
    except (InstructionNotFoundError, InstructionConflictError) as exc:
        raise instruction_error(exc) from None


@router.delete("/instruction-profiles/{profile_id}", status_code=204)
def delete_profile(profile_id: str, store: StoreDep, user: UserDep) -> Response:
    try:
        InstructionStore(store.path).delete_profile(user.user_id, profile_id)
    except InstructionNotFoundError as exc:
        raise instruction_error(exc) from None
    return Response(status_code=204)


@router.post("/instruction-drafts", status_code=201)
async def create_draft(
    request: DraftCreate,
    store: StoreDep,
    rules: RuleStoreDep,
    queue: QueueDep,
    user: UserDep,
) -> Draft:
    instructions = InstructionStore(store.path)
    try:
        draft = instructions.create_draft(
            user.user_id,
            request.document_type,
            request.raw_text,
            request.profiles,
            legacy_rules=rules.list_rules(user.user_id, status="approved")
            if request.document_type == "paper"
            else [],
        )
    except (InstructionNotFoundError, InstructionConflictError) as exc:
        raise instruction_error(exc) from None
    if draft.status == "queued":
        await queue.enqueue_instructions(draft.draft_id)
    return draft


@router.get("/instruction-drafts/{draft_id}")
def get_draft(draft_id: str, store: StoreDep, user: UserDep) -> Draft:
    try:
        return InstructionStore(store.path).get_draft(user.user_id, draft_id)
    except InstructionNotFoundError as exc:
        raise instruction_error(exc) from None


@router.patch("/instruction-drafts/{draft_id}")
def edit_draft(draft_id: str, request: DraftEdit, store: StoreDep, user: UserDep) -> Draft:
    try:
        return InstructionStore(store.path).update_draft(
            user.user_id,
            draft_id,
            request.revision,
            request.requirements,
            excluded_rule_ids=request.excluded_rule_ids,
        )
    except (InstructionNotFoundError, InstructionConflictError) as exc:
        raise instruction_error(exc) from None


@router.post("/instruction-drafts/{draft_id}/confirm")
def confirm_draft(draft_id: str, request: ConfirmDraft, store: StoreDep, user: UserDep) -> Draft:
    try:
        return InstructionStore(store.path).update_draft(
            user.user_id,
            draft_id,
            request.revision,
            None,
            confirm=True,
        )
    except (InstructionNotFoundError, InstructionConflictError) as exc:
        raise instruction_error(exc) from None

"""課題の自由記述から、利用者が確認する要件を抽出する。"""

import json
import re

from pydantic import BaseModel, ConfigDict, Field

from core.ingest.textdoc import guess_lang
from core.instructions.models import Requirement
from core.llm.client import OitomaClient
from core.llm.prompts import load_prompt


class ExtractedRequirements(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requirements: list[Requirement] = Field(default_factory=list, max_length=50)


async def extract_requirements(client: OitomaClient, raw_text: str) -> list[Requirement]:
    lang = guess_lang(raw_text)
    output = await client.chat_json(
        "requirement_extraction",
        [
            {"role": "system", "content": load_prompt("requirement_extraction", lang)},
            {"role": "user", "content": json.dumps({"text": raw_text}, ensure_ascii=False)},
        ],
        ExtractedRequirements,
        lang=lang,
    )
    source = re.sub(r"\s+", "", raw_text)
    for item in output.requirements:
        excerpt = re.sub(r"\s+", "", item.source_excerpt)
        if not excerpt or excerpt not in source:
            raise ValueError(
                "原文と確認項目の対応を確認できませんでした。条件を分けて入力してください"
            )
    return [
        Requirement(
            **{
                **r.model_dump(exclude={"requirement_id", "source"}),
                "source": "今回の課題",
            }
        )
        for r in output.requirements
    ]

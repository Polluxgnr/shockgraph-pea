"""Models for LLM-extracted hypotheses (role A)."""

from typing import Literal

from pydantic import BaseModel, Field


class Hypothesis(BaseModel):
    """A relation suggested by a news article. Never a fact until confirmed."""

    claim_id: str
    kind: str = Field(examples=["SUPPLIES"])
    subject_isin: str
    subject_name: str
    object_isin: str
    object_name: str
    confidence: float = Field(ge=0, le=1)
    status: Literal["hypothesis", "confirmed", "rejected"]
    sentence: str | None = None
    source_url: str | None = None
    source_title: str | None = None
    published_at: str | None = None

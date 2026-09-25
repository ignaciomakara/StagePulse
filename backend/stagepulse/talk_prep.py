"""Optional, pre-talk Gemini suggestions for the existing terminology normalizer."""

from __future__ import annotations

import json
from collections.abc import Mapping

from google import genai
from google.genai import errors, types
from pydantic import BaseModel, Field, field_validator


SUGGESTION_MODEL = "gemini-3.5-flash-lite"
MAX_TERMS = 15
MAX_VARIANTS = 5


class TalkMetadata(BaseModel):
    title: str = Field(max_length=200)
    speaker: str = Field(default="", max_length=160)
    abstract: str = Field(max_length=4000)

    @field_validator("title", "speaker", "abstract")
    @classmethod
    def strip_metadata(cls, value: str) -> str:
        return value.strip()

    @field_validator("title", "abstract")
    @classmethod
    def require_content(cls, value: str) -> str:
        if not value:
            raise ValueError("Talk title and description are required")
        return value


class TalkTerm(BaseModel):
    canonical: str
    variants: list[str] = Field(default_factory=list)


class SuggestedTerms(BaseModel):
    terms: list[TalkTerm]


class ApplyTerms(BaseModel):
    terms: list[TalkTerm]


class TalkPrepError(Exception):
    """A safe, user-facing failure from the optional suggestion request."""


def validate_terms(terms: list[TalkTerm]) -> list[TalkTerm]:
    if len(terms) > MAX_TERMS:
        raise ValueError(f"At most {MAX_TERMS} terminology terms are allowed")
    cleaned: list[TalkTerm] = []
    seen: set[str] = set()
    for term in terms:
        canonical = term.canonical.strip()
        if not canonical or len(canonical) > 80:
            raise ValueError("Each canonical term must contain 1 to 80 characters")
        if len(term.variants) > MAX_VARIANTS:
            raise ValueError(f"Each term may have at most {MAX_VARIANTS} variants")
        variants = []
        local_sources: set[str] = set()
        for source in [canonical, *term.variants]:
            source = source.strip()
            if not source or len(source) > 80:
                raise ValueError("Each variant must contain 1 to 80 characters")
            folded = source.casefold()
            if folded in local_sources:
                continue
            if folded in seen:
                raise ValueError(f"Duplicate terminology source: {source}")
            local_sources.add(folded)
            if source != canonical:
                variants.append(source)
        seen.update(local_sources)
        cleaned.append(TalkTerm(canonical=canonical, variants=variants))
    return cleaned


def build_terminology(
    configured: Mapping[str, Mapping[str, str]] | None,
    terms: list[TalkTerm],
    languages: tuple[str, ...],
) -> dict[str, dict[str, str]]:
    result = {language: dict(replacements) for language, replacements in (configured or {}).items()}
    for language in languages:
        replacements = result.setdefault(language, {})
        for term in terms:
            for source in [term.canonical, *term.variants]:
                for existing in list(replacements):
                    if existing.casefold() == source.casefold():
                        del replacements[existing]
                replacements[source] = term.canonical
    return result


class TalkPrepService:
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def suggest(self, metadata: TalkMetadata) -> list[TalkTerm]:
        prompt = (
            "Extract up to 15 terminology entries useful for live transcription from this talk metadata. "
            "Include only likely project names, products, technologies, acronyms, standards, "
            "organizations, and proper nouns grounded in the supplied metadata. "
            "Keep exact canonical spelling. Suggest only obvious spacing, hyphenation, or "
            "punctuation variants; never invent phonetic spellings, especially for people's names. "
            "Do not add generic words, broad topics, "
            "or facts absent from the metadata. An empty list is valid.\n"
            f"Talk metadata: {json.dumps(metadata.model_dump(), ensure_ascii=False)}"
        )
        try:
            with genai.Client(
                api_key=self._api_key,
                http_options=types.HttpOptions(timeout=30_000),
            ) as client:
                response = client.models.generate_content(
                    model=SUGGESTION_MODEL,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=SuggestedTerms,
                        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                        temperature=0.2,
                        max_output_tokens=1024,
                    ),
                )
        except errors.APIError as error:
            raise TalkPrepError(
                f"Gemini terminology request failed (HTTP {error.code}). Check model access or quota."
            ) from None
        except Exception as error:
            raise TalkPrepError(
                f"Could not request Gemini terminology suggestions ({type(error).__name__}). Check the connection."
            ) from None
        try:
            if not response.text:
                raise ValueError("Empty response")
            return validate_terms(SuggestedTerms.model_validate_json(response.text).terms)
        except (ValueError, TypeError):
            raise TalkPrepError("Gemini returned invalid structured terminology. Try again.") from None

"""The languages a story can be written in (see app/core/languages.py)."""

from typing import Any, Dict, List

from fastapi import APIRouter

from app.core.languages import LANGUAGES

router = APIRouter()


@router.get("")
def list_languages() -> List[Dict[str, Any]]:
    """Every language, in the order the page shows them. Public; changes only when the code does."""
    return [language.as_dict() for language in LANGUAGES]

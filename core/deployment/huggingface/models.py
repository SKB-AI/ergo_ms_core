"""Описание снимка Hugging Face из modules/*/huggingface_models.yaml."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ResolvedHfModel:
    repo_id: str
    required: bool = True
    source_module: str = ''
    source_file: str = ''

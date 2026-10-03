import json
from pathlib import Path

import pytest

from scripts.build_mempoll_provenance import load_cache, sha256_text, summary_rows


def test_provenance_cache_verifies_content_and_builds_normalized_key(tmp_path: Path):
    prompt = "private prompt"
    response = '{"signals": []}'
    prompt_hash = sha256_text(prompt)
    path = tmp_path / "cache.jsonl"
    path.write_text(
        json.dumps(
            {
                "cache_key": f"legacy-model:{prompt_hash}:s2",
                "provider": "glm",
                "model": "glm-5",
                "prompt_hash": prompt_hash,
                "prompt": prompt,
                "response_text": response,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    cache = load_cache(path, "glm")

    entry = cache[f"glm:glm-5:{prompt_hash}:s2"]
    assert entry["response_hash"] == sha256_text(response)


def test_provenance_cache_rejects_prompt_hash_mismatch(tmp_path: Path):
    path = tmp_path / "cache.jsonl"
    path.write_text(
        json.dumps(
            {
                "cache_key": "deepseek:model:wrong",
                "provider": "deepseek",
                "model": "model",
                "prompt_hash": "0" * 64,
                "prompt": "actual prompt",
                "response_text": "{}",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="prompt hash mismatch"):
        load_cache(path, "deepseek")


def test_provenance_summary_distinguishes_logical_and_unique_calls():
    base = {
        "mode": "instructed",
        "agent": "deepseek:deepseek-v4-pro",
        "dose": 0.0,
        "prompt_sha256": "a" * 64,
        "response_sha256": "b" * 64,
        "cache_created_at_utc": "2026-06-12T00:00:00Z",
        "collected_before_confirmatory_freeze": True,
    }
    rows = summary_rows([base, dict(base)])

    assert rows[0]["logical_calls"] == 2
    assert rows[0]["unique_prompt_response_pairs"] == 1
    assert rows[0]["logical_calls_collected_before_confirmatory_freeze"] == 2
    assert rows[0]["unique_pairs_collected_before_confirmatory_freeze"] == 1

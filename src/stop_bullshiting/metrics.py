"""Optional reference-token counts; never provider billing estimates."""

from __future__ import annotations

TOKENIZERS = ("o200k_base", "cl100k_base")


def measure_reference_tokens(original: str, visible: str, encoding_name: str) -> dict:
    """Count both texts with one encoding, including the visible marker.

    Only explicit use imports tiktoken. Its first use may download a public
    encoding table; neither input text is sent to a model or API.
    """
    if encoding_name not in TOKENIZERS:
        raise ValueError(f"Choose a reference tokenizer from: {', '.join(TOKENIZERS)}")
    try:
        import tiktoken
    except ImportError as exc:
        raise ValueError(
            "Reference token counting requires: python -m pip install '.[tokens]' "
            "(from the repository)"
        ) from exc
    try:
        encoding = tiktoken.get_encoding(encoding_name)
    except Exception as exc:
        raise ValueError(
            f"Cannot load tokenizer {encoding_name} ({type(exc).__name__}). "
            "First use needs the public encoding table; cache it while online "
            "before running offline. No input text is uploaded."
        ) from exc
    # Tool output may contain special-token spellings: count them as literal text.
    before = len(encoding.encode(original, disallowed_special=()))
    after = len(encoding.encode(visible, disallowed_special=()))
    return {
        "encoding": encoding_name,
        "library": "tiktoken",
        "library_version": getattr(tiktoken, "__version__", "unknown"),
        "original": before,
        "visible": after,
        "saved": before - after,
        "reduction_pct": round(100 * (1 - after / before), 2) if before else 0.0,
        "provider_billing_measured": False,
    }

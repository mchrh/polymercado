from __future__ import annotations


from polymercado.utils import safe_lower


def resolve_binary_tokens(
    token_ids: list[str] | None, outcomes: list[str] | None
) -> tuple[str | None, str | None]:
    if not token_ids or len(token_ids) != 2:
        return None, None
    if outcomes and len(outcomes) == 2:
        lower = [safe_lower(outcome) for outcome in outcomes]
        if "yes" in lower and "no" in lower:
            yes_index = lower.index("yes")
            no_index = lower.index("no")
            return token_ids[yes_index], token_ids[no_index]
    return token_ids[0], token_ids[1]

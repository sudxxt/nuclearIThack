ALLOWED_CENTERS: dict[str, str] = {
    # Canonical name → itself (for fast check)
    "цк фмба": "ЦК ФМБА",
    "фмба": "ЦК ФМБА",
    "fmba": "ЦК ФМБА",
    "цк им. о.к. гаврилова": "ЦК им. О.К. Гаврилова",
    "цк им. гаврилова": "ЦК им. О.К. Гаврилова",
    "гаврилова": "ЦК им. О.К. Гаврилова",
    "gavrilova": "ЦК им. О.К. Гаврилова",
}

CANONICAL_CENTERS: set[str] = {
    "ЦК ФМБА",
    "ЦК им. О.К. Гаврилова",
}


def normalize_center_name(raw_name: str) -> str | None:
    """Return canonical center name if *raw_name* matches one of allowed variations.

    Matching is case‐insensitive and ignores dots/commas/extra spaces.
    Returns ``None`` if the center is not recognized as allowed.
    """
    if not raw_name:
        return None

    cleaned = raw_name.lower().replace(".", " ").replace(",", " ")
    cleaned = " ".join(cleaned.split())  # collapse whitespace

    # Try exact match first
    if cleaned in ALLOWED_CENTERS:
        return ALLOWED_CENTERS[cleaned]

    # Try contains pattern (e.g. "центр фмба")
    for key, canonical in ALLOWED_CENTERS.items():
        if key in cleaned:
            return canonical

    return None 
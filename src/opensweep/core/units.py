def format_size(size: int | float) -> str:
    """Human readable size using binary units (like Windows Explorer)."""
    size = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if abs(size) < 1024:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.2f} TB"

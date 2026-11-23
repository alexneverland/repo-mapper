from langchain_core.tools import tool


@tool
def lookup_item(name: str):
    """Return a synthetic inventory match."""
    return {"name": name}

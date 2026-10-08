"""Read a supplier's category back from its name: a test oracle, never used by the generator.

A supplier is named `{surname | prefix} {noun}` from its category's `business_name_parts.category_nouns`
(`ContentEngine.fictional_business_name`), and its category is stored in the ground truth beside the
layout. The tests use this to check that the stored category and the printed name agree, so a human reading
the name sees the industry the page's lines and clutter were drawn for.
"""


def supplier_name_category(
    name: object, pools: dict, categories_key: str = "service_categories"
) -> str | None:
    """Return the category whose noun ends a supplier's name, the longest noun winning.

    Args:
        name: The supplier name.
        pools: The loaded `config/data_pools.yml`.
        categories_key: `service_categories` (invoices) or `receipt_categories` (receipts).

    Returns:
        The category (e.g. `legal`; "Tax & Advisory" is not read as a shorter noun), or None when the name
        ends in no noun of those categories.
    """
    nouns = pools["business_name_parts"]["category_nouns"]
    text = str(name or "")
    matches = sorted(
        (len(noun), category)
        for category in pools[categories_key]
        for noun in nouns.get(category, [])
        if text == noun or text.endswith(f" {noun}")
    )
    return matches[-1][1] if matches else None

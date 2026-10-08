"""Map a ground-truth record to CORD's Donut-style gt_parse tree.

Implements Mapping A of docs/GroundTruth_Export_Spec.md (section 4). Values only —
CORD scoring is tree-edit distance over values and needs no coordinates.
"""

from generators.exporters.normalise import (
    canonical_identifier,
    is_present,
    zip_line_items,
)

EXTENSION_COLUMNS: dict[str, str] = {
    "SUPPLIER_NAME": "supplier_name",
    "BUSINESS_ABN": "business_abn",
    "BUSINESS_ADDRESS": "business_address",
    "INVOICE_DATE": "invoice_date",
    "PAYER_NAME": "payer_name",
    "PAYER_ADDRESS": "payer_address",
}

IDENTIFIER_COLUMNS: frozenset[str] = frozenset({"BUSINESS_ABN"})


def to_cord(fields: dict[str, str], identifier_form: str) -> dict:
    """Build the CORD gt_parse tree for one receipt or invoice.

    Args:
        fields: The document's field mapping from ground_truth/*.yml.
        identifier_form: 'spaced' or 'digits_only', from
            export_config.yml's abn_tfn_canonical_form.

    Returns:
        The gt_parse tree. Keys with no present source data are omitted.

    Raises:
        ValueError: If the line-item lists have mismatched counts.
    """
    tree: dict = {}

    menu = [
        {
            "nm": item["description"],
            "cnt": item["quantity"],
            "unitprice": item["unit_price"],
            "price": item["total_price"],
        }
        for item in zip_line_items(fields)
    ]
    if menu:
        tree["menu"] = menu

    if is_present(fields.get("GST_AMOUNT")):
        tree["sub_total"] = {"tax_price": fields["GST_AMOUNT"]}

    if is_present(fields.get("TOTAL_AMOUNT")):
        tree["total"] = {"total_price": fields["TOTAL_AMOUNT"]}

    extension = {}
    for column, key in EXTENSION_COLUMNS.items():
        value = fields.get(column)
        if not is_present(value):
            continue
        if column in IDENTIFIER_COLUMNS:
            value = canonical_identifier(str(value), identifier_form)
        extension[key] = value
    if extension:
        tree["extension"] = extension

    return tree

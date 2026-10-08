"""Map a ground-truth record plus its geometry to DocILE KILE and LIR fields.

Implements Mapping B of docs/GroundTruth_Export_Spec.md (section 5). DocILE is
restricted to invoices by design: a receipt prints a quantity and unit price
only on a line of more than one unit (a line of one unit implies both), so a
receipt's quantity-1 members have no bounding box, and DocILE is a localisation
benchmark where every annotation must be findable on the page.
Every DocILE field on every invoice has captured geometry, so a missing box
here always signals a real capture bug, never a structural absence — it is
therefore always a hard error, never silently dropped.
"""

from generators.exporters.normalise import is_present, zip_line_items

KILE_COLUMNS: tuple[str, ...] = (
    "SUPPLIER_NAME",
    "BUSINESS_ADDRESS",
    "BUSINESS_ABN",
    "INVOICE_DATE",
    "PAYMENT_DUE_DATE",
    "GST_AMOUNT",
    "PAYER_NAME",
    "PAYER_ADDRESS",
)

LIR_COLUMNS: tuple[str, ...] = (
    "LINE_ITEM_DESCRIPTIONS",
    "LINE_ITEM_QUANTITIES",
    "LINE_ITEM_PRICES",
    "LINE_ITEM_TOTAL_PRICES",
)

LIR_ITEM_KEYS: dict[str, str] = {
    "LINE_ITEM_DESCRIPTIONS": "description",
    "LINE_ITEM_QUANTITIES": "quantity",
    "LINE_ITEM_PRICES": "unit_price",
    "LINE_ITEM_TOTAL_PRICES": "total_price",
}


def to_docile(
    fields: dict[str, str],
    boxes: dict[str, list[float]],
    fieldtypes: dict[str, str],
) -> dict:
    """Build the DocILE KILE and LIR field lists for one invoice.

    Args:
        fields: The document's field mapping from ground_truth/*.yml.
        boxes: Field key to [left, top, right, bottom] in [0, 1], from the
            geometry.jsonl in the configured `derived_dir` (BoxRecorder.as_dict()).
            Line-item members are suffixed '[i]', e.g. 'LINE_ITEM_DESCRIPTIONS[0]'.
        fieldtypes: The docile_fieldtypes mapping from export_config.yml.

    Returns:
        {'kile': [...], 'lir': [...]}. Every entry carries page, bbox,
        fieldtype and text; every lir entry also carries line_item_id.

    Raises:
        KeyError: If a present field has no configured fieldtype, or no
            captured bounding box.
    """
    kile = [
        _entry(column, fields[column], column, boxes, fieldtypes)
        for column in KILE_COLUMNS
        if is_present(fields.get(column))
    ]

    if is_present(fields.get("TOTAL_AMOUNT")):
        gst_included = str(fields.get("IS_GST_INCLUDED", "")).lower() == "true"
        total_column = "TOTAL_AMOUNT_GROSS" if gst_included else "TOTAL_AMOUNT_NET"
        kile.append(_entry(total_column, fields["TOTAL_AMOUNT"], "TOTAL_AMOUNT", boxes, fieldtypes))

    lir = []
    for index, item in enumerate(zip_line_items(fields)):
        for column in LIR_COLUMNS:
            entry = _entry(column, item[LIR_ITEM_KEYS[column]], f"{column}[{index}]", boxes, fieldtypes)
            entry["line_item_id"] = index
            lir.append(entry)

    return {"kile": kile, "lir": lir}


def _entry(
    fieldtype_key: str,
    text: str,
    box_key: str,
    boxes: dict[str, list[float]],
    fieldtypes: dict[str, str],
) -> dict:
    """Build one DocILE field entry.

    Args:
        fieldtype_key: The export_config docile_fieldtypes key naming this
            field's fieldtype (e.g. 'TOTAL_AMOUNT_GROSS' for a GST-inclusive
            total, distinct from box_key, which locates its geometry).
        text: The field's ground-truth value.
        box_key: The geometry key holding this field's bbox, e.g.
            'SUPPLIER_NAME' or 'LINE_ITEM_DESCRIPTIONS[0]'.
        boxes: The document's captured geometry.
        fieldtypes: The docile_fieldtypes mapping.

    Returns:
        A DocILE field dict with page, bbox, fieldtype and text.

    Raises:
        KeyError: If the fieldtype or the box is missing.
    """
    if fieldtype_key not in fieldtypes:
        msg = (
            f"What: No DocILE fieldtype configured for '{fieldtype_key}'.\n"
            f"Where: docile_fieldtypes in config/export_config.yml.\n"
            f"Expected: A string confirmed byte-exact against rossumai/docile.\n"
            f"Recover: Add '{fieldtype_key}:' under 'docile_fieldtypes:' in "
            f"config/export_config.yml, e.g.:\ndocile_fieldtypes:\n"
            f"  {fieldtype_key}: vendor_name"
        )
        raise KeyError(msg) from None

    if box_key not in boxes:
        msg = (
            f"What: No captured bounding box for '{box_key}'.\n"
            f"Where: the geometry.jsonl in the configured derived_dir, for this document.\n"
            f"Expected: Every DocILE field on an invoice has geometry (DocILE "
            f"is a localisation benchmark; dropping an unboxed field would "
            f"inflate precision instead of failing).\n"
            f"Recover: Re-run 'python -m generators.pipeline generate' to "
            f"regenerate the geometry.jsonl in the configured derived_dir, and confirm the renderer "
            f"draws '{box_key}' through one of the common.py helpers."
        )
        raise KeyError(msg) from None

    return {
        "page": 0,
        "bbox": boxes[box_key],
        "fieldtype": fieldtypes[fieldtype_key],
        "text": text,
    }

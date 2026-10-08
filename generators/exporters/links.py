"""Map the link ground truth to a FinBalance-style doc_refs convention.

Implements Mapping C of docs/GroundTruth_Export_Spec.md (section 6). No public
standard exists for cross-document links; this convention is defined by the spec.
"""

from generators.exporters.normalise import validate_identifier_form


def transaction_links_to_doc_refs(data: dict, identifier_form: str) -> list[dict]:
    """Map transaction_links.yml to doc_refs records.

    Args:
        data: The parsed transaction_links.yml mapping. Each key is a source
            document image name; each value is a list of link dicts.
        identifier_form: 'spaced' or 'digits_only'. Transaction links carry no
            ABN or TFN, so this is never applied to any field here — but it is
            still validated unconditionally so a config typo fails fast on
            this path instead of being silently accepted.

    Returns:
        One record per link, flattened across sources.

    Raises:
        ValueError: If identifier_form is not a recognised identifier form.
    """
    validate_identifier_form(identifier_form)
    records = []
    for source_doc, links in data.items():
        for link in links:
            records.append(
                {
                    "link_type": "receipt_to_bank",
                    "source_doc": str(source_doc),
                    "target_doc": link["bank_statement"],
                    "match_keys": {
                        "supplier": link["supplier"],
                        "date": link["receipt_date"],
                        "amount": link["receipt_total"],
                    },
                    "target_evidence": {
                        "date": link["bank_date"],
                        "description": link["bank_description"],
                        "amount": link["bank_amount"],
                    },
                    "label": link["match_status"],
                    "difficulty": link["match_difficulty"],
                    "notes": link.get("notes", ""),
                }
            )
    return records

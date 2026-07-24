"""Semantic field-name alias resolution for document extraction.

This module complements :func:`app.agents.router._normalize_name`, which
bridges only case/spacing/hyphen differences (e.g. ``"Total Amount"`` →
``"total_amount"``).  The alias table here handles *true semantic synonyms*
— cases where an AI model or ground-truth file uses a completely different
word for the same concept (e.g. ``"order_date"`` vs ``"invoice_date"``).

Usage
-----
After normalizing a field name with ``_normalize_name``, pass the result
through :func:`resolve_field_alias` together with the document type to
obtain the canonical catalog name.  If no alias exists the input is
returned unchanged, making the function safe to call unconditionally.

The alias tables are derived from the canonical field names defined in
``Backend/app/data/knowledge_base/field_catalog/*.json``.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Alias table
# ---------------------------------------------------------------------------
# Keys: doc_type (lowercase) → {normalized_synonym → normalized_canonical}.
# All names must already be in normalized form (lowercase, underscores).
# ---------------------------------------------------------------------------

FIELD_ALIASES: dict[str, dict[str, str]] = {
    # ----- invoice --------------------------------------------------------
    "invoice": {
        # date synonyms → invoice_date
        "order_date": "invoice_date",
        "issue_date": "invoice_date",
        "date": "invoice_date",
        "billing_date": "invoice_date",
        "transaction_date": "invoice_date",
        # payment due synonyms → payment_due_date
        "due_date": "payment_due_date",
        "payment_due": "payment_due_date",
        "pay_by_date": "payment_due_date",
        # seller/vendor synonyms → seller_name  (catalog canonical)
        "vendor_name": "seller_name",
        "merchant_name": "seller_name",
        "supplier_name": "seller_name",
        "from_name": "seller_name",
        "company_name": "seller_name",
        # buyer/bill-to synonyms → bill_to_name
        "bill_to": "bill_to_name",
        "customer_name": "bill_to_name",
        "to_name": "bill_to_name",
        "buyer_name": "bill_to_name",
        "client_name": "bill_to_name",
        # total synonyms → total_amount
        "grand_total": "total_amount",
        "amount_due": "total_amount",
        "total": "total_amount",
        "net_total": "total_amount",
        "invoice_total": "total_amount",
        # tax synonyms → tax_amount
        "vat": "tax_amount",
        "gst": "tax_amount",
        "vat_amount": "tax_amount",
        "gst_amount": "tax_amount",
        "sales_tax": "tax_amount",
        # invoice number synonyms → invoice_number
        "invoice_no": "invoice_number",
        "invoice_id": "invoice_number",
        "inv_no": "invoice_number",
        "inv_number": "invoice_number",
        "receipt_number": "invoice_number",
    },
    # ----- po (purchase order) --------------------------------------------
    "po": {
        # date synonyms → order_date
        "po_date": "order_date",
        "issue_date": "order_date",
        "date": "order_date",
        "purchase_date": "order_date",
        # supplier synonyms → supplier_name
        "vendor_name": "supplier_name",
        "seller_name": "supplier_name",
        "merchant_name": "supplier_name",
        "from_name": "supplier_name",
        # buyer synonyms → buyer_name
        "ship_to": "buyer_name",
        "bill_to": "buyer_name",
        "to_name": "buyer_name",
        "customer_name": "buyer_name",
        "purchaser_name": "buyer_name",
        # total synonyms → total_amount
        "grand_total": "total_amount",
        "total": "total_amount",
        "order_total": "total_amount",
        "net_total": "total_amount",
        # payment terms synonyms → payment_terms
        "terms": "payment_terms",
        "pay_terms": "payment_terms",
        # PO number synonyms → po_number
        "po_no": "po_number",
        "po_id": "po_number",
        "purchase_order_number": "po_number",
        "order_number": "po_number",
        "order_no": "po_number",
    },
    # ----- delivery_note --------------------------------------------------
    "delivery_note": {
        # DN number synonyms → delivery_note_number
        "do_number": "delivery_note_number",
        "do_no": "delivery_note_number",
        "dn_number": "delivery_note_number",
        "dn_no": "delivery_note_number",
        "delivery_order_number": "delivery_note_number",
        "delivery_no": "delivery_note_number",
        # delivered-by synonyms → delivered_by
        "courier": "delivered_by",
        "carrier": "delivered_by",
        "driver": "delivered_by",
        "transporter": "delivered_by",
        "logistics_provider": "delivered_by",
        # recipient synonyms → recipient_name
        "ship_to": "recipient_name",
        "deliver_to": "recipient_name",
        "consignee": "recipient_name",
        "receiver_name": "recipient_name",
        "customer_name": "recipient_name",
        # sender synonyms → sender_name
        "ship_from": "sender_name",
        "shipper": "sender_name",
        "from_name": "sender_name",
        "dispatch_from": "sender_name",
        # weight synonyms → total_weight
        "gross_weight": "total_weight",
        "weight": "total_weight",
        "net_weight": "total_weight",
        "package_weight": "total_weight",
        # notes synonyms → notes
        "remarks": "notes",
        "comments": "notes",
        "special_instructions": "notes",
        "instructions": "notes",
    },
}


def resolve_field_alias(doc_type: str, normalized_name: str) -> str:
    """Resolve a normalized field name to its canonical catalog name.

    Parameters
    ----------
    doc_type:
        Document type (case-insensitive), e.g. ``"invoice"``, ``"PO"``.
    normalized_name:
        Field name that has **already** been passed through
        :func:`app.agents.router._normalize_name`.

    Returns
    -------
    str
        The canonical catalog field name if *normalized_name* is a known
        synonym for the given *doc_type*; otherwise *normalized_name* is
        returned unchanged.
    """
    aliases = FIELD_ALIASES.get(doc_type.strip().lower(), {})
    return aliases.get(normalized_name, normalized_name)

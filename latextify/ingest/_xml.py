"""Hardened XML parsing for untrusted document input.

Protects against XXE (XML External Entity) attacks and entity-expansion attacks
(billion laughs). All parsers produced here resolve_entities=False, no_network=True,
load_dtd=False, dtd_validation=False, and huge_tree=False — an XXE payload fails
with a clean XMLSyntaxError instead of resolving.

Each call to :func:`hardened_xml_parser` constructs a fresh parser instance.
This is the safest approach for code paths that parse serially (the ingest
pipeline), avoiding any potential thread-safety issues with a shared singleton.
"""

from lxml import etree


def hardened_xml_parser() -> etree.XMLParser:
    """Create a fresh hardened XMLParser instance for parsing untrusted input.

    The returned parser disables:
    - resolve_entities: blocks entity substitution (XXE payload fix)
    - no_network: blocks network DTD/entity fetches (defense in depth)
    - load_dtd: blocks DTD loading
    - dtd_validation: no DTD validation
    - huge_tree: mitigates billion-laughs by rejecting unreasonably large trees

    Returns:
        etree.XMLParser: A new hardened parser instance.

    Example:
        >>> parser = hardened_xml_parser()
        >>> root = etree.parse(file_handle, parser=parser).getroot()
    """
    return etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        dtd_validation=False,
        huge_tree=False,
    )

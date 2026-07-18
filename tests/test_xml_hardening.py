"""Regression tests for XML hardening against entity expansion and XXE attacks.

Tests verify that:
1. The hardened parser prevents entity expansion (entities remain unexpanded)
2. The hardened parser accepts well-formed XML
3. The hardened_xml_parser() factory function is available for use
"""

from __future__ import annotations

from lxml import etree

from latextify.ingest import _xml


class TestHardenedXMLParser:
    """Test the hardened XML parser mechanism."""

    def test_hardened_parser_prevents_entity_expansion(self):
        """Verify entity expansion is prevented (entity remains unexpanded).

        With resolve_entities=False, entity references stay unexpanded in the tree,
        preventing entity-expansion attacks (billion laughs, etc.).
        """
        payload = b"""<?xml version="1.0"?>
<!DOCTYPE root [
  <!ENTITY lol "lol">
  <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
]>
<root>&lol2;</root>
"""
        parser = _xml.hardened_xml_parser()
        root = etree.fromstring(payload, parser=parser)
        # The entity was NOT expanded; text is None (entity reference stayed unexpanded)
        assert root.text is None or root.text == ""

    def test_hardened_parser_prevents_external_entity_resolution(self):
        """Verify external entity references are not resolved.

        With resolve_entities=False and load_dtd=False, external entities
        are not resolved, preventing XXE attacks.
        """
        payload = b"""<?xml version="1.0"?>
<!DOCTYPE root [
  <!ENTITY xxe SYSTEM "file:///etc/passwd">
]>
<root>&xxe;</root>
"""
        parser = _xml.hardened_xml_parser()
        root = etree.fromstring(payload, parser=parser)
        # The entity was NOT resolved; text is None
        assert root.text is None or root.text == ""

    def test_hardened_parser_accepts_well_formed_xml(self):
        """Verify well-formed XML parses correctly through hardened parser."""
        payload = b"""<?xml version="1.0"?>
<root>
  <element attr="value">text content</element>
</root>
"""
        parser = _xml.hardened_xml_parser()
        root = etree.fromstring(payload, parser=parser)
        assert root.tag == "root"
        elem = root.find("element")
        assert elem is not None
        assert elem.get("attr") == "value"
        assert elem.text == "text content"

    def test_hardened_parser_accepts_well_formed_with_namespaces(self):
        """Verify well-formed XML with namespaces parses correctly."""
        payload = b"""<?xml version="1.0"?>
<root xmlns="http://example.com/ns">
  <element>text</element>
</root>
"""
        parser = _xml.hardened_xml_parser()
        root = etree.fromstring(payload, parser=parser)
        assert root.tag == "{http://example.com/ns}root"

    def test_hardened_parser_is_fresh_on_each_call(self):
        """Verify each call to hardened_xml_parser() returns a fresh instance.

        Thread-safe: each call constructs a new parser (not shared singleton).
        This prevents thread-safety issues if concurrent parses occur.
        """
        parser1 = _xml.hardened_xml_parser()
        parser2 = _xml.hardened_xml_parser()
        # Different instances (not shared singleton) — thread-safe by construction
        assert parser1 is not parser2
        # Both are XMLParser instances
        assert isinstance(parser1, etree.XMLParser)
        assert isinstance(parser2, etree.XMLParser)

    def test_default_parser_expands_entities(self):
        """Contrast: default parser DOES expand entities (the vulnerability).

        This test demonstrates why the hardened parser is necessary:
        the default parser resolves and expands entity references.
        """
        payload = b"""<?xml version="1.0"?>
<!DOCTYPE root [
  <!ENTITY lol "lol">
  <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
]>
<root>&lol2;</root>
"""
        # DEFAULT parser (no hardening) — expands the entity
        root = etree.fromstring(payload)
        # The entity WAS expanded: lol2 contains 10 copies of "lol"
        assert root.text is not None
        assert len(root.text) > 0
        assert "lol" in root.text
        # Hardened parser does NOT expand it
        parser = _xml.hardened_xml_parser()
        root_hardened = etree.fromstring(payload, parser=parser)
        assert root_hardened.text is None or root_hardened.text == ""
        assert root.text != root_hardened.text

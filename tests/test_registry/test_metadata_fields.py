"""Tests for $metadata XML parsing — EntitySet + EntityType property fields."""

from __future__ import annotations

from bcli.registry._importers import (
    _parse_entity_type_properties,
    _parse_metadata_xml,
)


SAMPLE_EDMX = """<?xml version="1.0" encoding="utf-8"?>
<edmx:Edmx xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx" Version="4.0">
  <edmx:DataServices>
    <Schema Namespace="Microsoft.NAV">
      <EntityType Name="engineUtilization">
        <Key><PropertyRef Name="systemId"/></Key>
        <Property Name="systemId" Type="Edm.Guid"/>
        <Property Name="engineSerialNumber" Type="Edm.String"/>
        <Property Name="tailNo" Type="Edm.String"/>
        <Property Name="asOfDate" Type="Edm.Date"/>
        <Property Name="efh" Type="Edm.Decimal"/>
        <Property Name="efc" Type="Edm.Int32"/>
      </EntityType>
      <EntityType Name="engineOverview">
        <Property Name="systemId" Type="Edm.Guid"/>
        <Property Name="engineModel" Type="Edm.String"/>
      </EntityType>
      <EntityContainer Name="NAV">
        <EntitySet Name="engineUtilizations" EntityType="Microsoft.NAV.engineUtilization"/>
        <EntitySet Name="engineOverviews" EntityType="Microsoft.NAV.engineOverview"/>
      </EntityContainer>
    </Schema>
  </edmx:DataServices>
</edmx:Edmx>
"""


def test_parses_entity_type_properties():
    fields = _parse_entity_type_properties(SAMPLE_EDMX)
    assert fields["engineUtilization"] == [
        "systemId",
        "engineSerialNumber",
        "tailNo",
        "asOfDate",
        "efh",
        "efc",
    ]
    assert fields["engineOverview"] == ["systemId", "engineModel"]


def test_parse_metadata_xml_attaches_field_names():
    endpoints = _parse_metadata_xml(
        SAMPLE_EDMX, publisher="contoso", group="technical", version="v1.5"
    )
    by_name = {ep.entity_set_name: ep for ep in endpoints}

    assert "engineUtilizations" in by_name
    eu = by_name["engineUtilizations"]
    assert eu.api_publisher == "contoso"
    assert eu.api_group == "technical"
    assert eu.api_version == "v1.5"
    assert eu.entity_name == "engineUtilization"
    assert "engineSerialNumber" in eu.field_names
    assert "tailNo" in eu.field_names
    assert "efh" in eu.field_names

    eo = by_name["engineOverviews"]
    assert eo.field_names == ["systemId", "engineModel"]


def test_parse_metadata_xml_handles_missing_properties():
    """An EntityType without properties yields an empty field list, not a crash."""
    minimal = """
    <edmx:Edmx xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx">
      <Schema>
        <EntityType Name="empty"></EntityType>
        <EntityContainer Name="X">
          <EntitySet Name="empties" EntityType="ns.empty"/>
        </EntityContainer>
      </Schema>
    </edmx:Edmx>
    """
    endpoints = _parse_metadata_xml(minimal, publisher="p", group="g", version="v1")
    assert endpoints[0].field_names == []

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
      <EntityType Name="shipmentTracking">
        <Key><PropertyRef Name="systemId"/></Key>
        <Property Name="systemId" Type="Edm.Guid"/>
        <Property Name="shipmentNumber" Type="Edm.String"/>
        <Property Name="trackingNumber" Type="Edm.String"/>
        <Property Name="shipmentDate" Type="Edm.Date"/>
        <Property Name="weight" Type="Edm.Decimal"/>
        <Property Name="packageCount" Type="Edm.Int32"/>
      </EntityType>
      <EntityType Name="shipmentCarrier">
        <Property Name="systemId" Type="Edm.Guid"/>
        <Property Name="carrier" Type="Edm.String"/>
      </EntityType>
      <EntityContainer Name="NAV">
        <EntitySet Name="shipmentTrackings" EntityType="Microsoft.NAV.shipmentTracking"/>
        <EntitySet Name="shipmentCarriers" EntityType="Microsoft.NAV.shipmentCarrier"/>
      </EntityContainer>
    </Schema>
  </edmx:DataServices>
</edmx:Edmx>
"""


def test_parses_entity_type_properties():
    fields = _parse_entity_type_properties(SAMPLE_EDMX)
    assert fields["shipmentTracking"] == [
        "systemId",
        "shipmentNumber",
        "trackingNumber",
        "shipmentDate",
        "weight",
        "packageCount",
    ]
    assert fields["shipmentCarrier"] == ["systemId", "carrier"]


def test_parse_metadata_xml_attaches_field_names():
    endpoints = _parse_metadata_xml(
        SAMPLE_EDMX, publisher="contoso", group="integration", version="v1.5"
    )
    by_name = {ep.entity_set_name: ep for ep in endpoints}

    assert "shipmentTrackings" in by_name
    st = by_name["shipmentTrackings"]
    assert st.api_publisher == "contoso"
    assert st.api_group == "integration"
    assert st.api_version == "v1.5"
    assert st.entity_name == "shipmentTracking"
    assert "shipmentNumber" in st.field_names
    assert "trackingNumber" in st.field_names
    assert "weight" in st.field_names

    sc = by_name["shipmentCarriers"]
    assert sc.field_names == ["systemId", "carrier"]


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

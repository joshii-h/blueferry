"""Canonical introspection XML must match the exported dbus-python service."""
from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree

from blueferry.dbus_service import MessagesService
from blueferry.protocol import EVENTS_IFACE, MESSAGES_IFACE, OBJECT_PATH, TETHER_IFACE

CONTRACT = Path(__file__).resolve().parents[1] / "data/io.weirdware.BlueFerry.xml"


def _signature(member, direction: str) -> str:
    return "".join(
        argument.attrib["type"]
        for argument in member.findall("arg")
        if argument.attrib.get("direction", "out") == direction
    )


def _exported(interface: str, kind: str) -> dict:
    return {
        name: member
        for name, member in vars(MessagesService).items()
        if getattr(member, "_dbus_interface", None) == interface
        and getattr(member, kind, False)
    }


def test_contract_matches_exported_methods_and_signals() -> None:
    node = ElementTree.parse(CONTRACT).getroot()
    assert node.attrib["name"] == OBJECT_PATH
    assert {
        interface.attrib["name"] for interface in node.findall("interface")
    } == {MESSAGES_IFACE, EVENTS_IFACE, TETHER_IFACE}

    for interface_name in (MESSAGES_IFACE, EVENTS_IFACE, TETHER_IFACE):
        interface = node.find(f"interface[@name='{interface_name}']")
        assert interface is not None
        xml_methods = {method.attrib["name"]: method for method in interface.findall("method")}
        exported_methods = _exported(interface_name, "_dbus_is_method")
        assert xml_methods.keys() == exported_methods.keys()
        for name, member in exported_methods.items():
            assert _signature(xml_methods[name], "in") == member._dbus_in_signature
            assert _signature(xml_methods[name], "out") == member._dbus_out_signature

        xml_signals = {signal.attrib["name"]: signal for signal in interface.findall("signal")}
        exported_signals = _exported(interface_name, "_dbus_is_signal")
        assert xml_signals.keys() == exported_signals.keys()
        for name, member in exported_signals.items():
            assert _signature(xml_signals[name], "out") == member._dbus_signature


def test_tether_interface_is_small_and_its_signal_is_content_free() -> None:
    assert set(_exported(TETHER_IFACE, "_dbus_is_method")) == {
        "Connect", "Disconnect", "GetState",
    }
    signals = _exported(TETHER_IFACE, "_dbus_is_signal")
    assert set(signals) == {"TetherChanged"}
    assert signals["TetherChanged"]._dbus_signature == ""
    # Tethering never widens the messaging generation's Events1 contract.
    assert set(_exported(EVENTS_IFACE, "_dbus_is_signal")) == {
        "HistoryChanged", "StatusChanged", "OpenMessageRequested",
    }


def test_every_documented_error_has_the_stable_namespace() -> None:
    root = ElementTree.parse(CONTRACT).getroot()
    annotations = root.findall(".//annotation[@name='io.weirdware.BlueFerry.Errors']")

    errors = {
        value
        for annotation in annotations
        for value in annotation.attrib["value"].split(",")
    }

    assert errors == {
        "AuthorizationRequired",
        "ConfirmationRequired",
        "ContactSyncFailed",
        "InvalidArgs",
        "NotFound",
        "NotReady",
        "QueryFailed",
        "RateLimited",
        "ResponseTooLarge",
        "SendFailed",
        "SendOutcomeUnknown",
    }

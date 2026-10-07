"""Plugin API 1.3: the guided settings form, TestConfig and ConfigLogin."""
from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from blueferry.plugin_api.config import (
    ConfigError,
    validate,
    value_text,
    visible_fields,
)
from blueferry.plugin_api.manifest import ManifestError
from blueferry.plugin_api.testing import manifest

FORM = """
[ConfigGroup account]
Label=Konto
Help=Where BlueFerry signs in.

[ConfigGroup images]
Label=Bilder

[Config url]
Label=Server URL
Type=url
Required=true
Group=account
Placeholder=https://cloud.example.com
Example=https://cloud.example.com/nextcloud
HelpUrl=https://docs.example.com/where-is-my-url
Help=The address you open in the browser.

[Config token]
Label=App password
Type=secret
Required=true
Group=account
Pattern=[A-Za-z0-9-]{5,72}
ErrorText=An app password has five to 72 letters, digits or dashes.

[Config upload_images]
Label=Upload pictures
Type=bool
Group=images

[Config image_size]
Label=Size
Type=choice
Choices=small;large;
Default=small
Group=images
ShowIf=upload_images=true

[Config image_quality]
Label=Quality
Type=int
Min=10
Max=100
Default=80
Required=true
ErrorText=Use a value from 10 to 100.
ShowIf=image_size=large
Group=images

[Config timeout]
Label=Timeout
Type=int
Min=1
Max=60
Default=15
Advanced=true
"""


def _form(extra: str = FORM, header: str = "", version: str = "1.3"):
    return manifest(capabilities="card;", api_version=version, extra=header + extra)


def test_the_manifest_reads_every_new_key() -> None:
    parsed = _form(header="ConfigTest=true\nConfigLogin=nextcloud\n")
    assert parsed.api_minor == 3 and parsed.config_test and parsed.config_login == "nextcloud"
    url, token, upload, size, quality, timeout = parsed.config
    assert url.placeholder == "https://cloud.example.com"
    assert url.example.endswith("/nextcloud")
    assert url.help_url == "https://docs.example.com/where-is-my-url"
    assert (url.group, token.group, upload.group, timeout.group) == (
        "account", "account", "images", "advanced",
    )
    assert token.pattern and token.error_text.startswith("An app password")
    assert size.show_if == ("upload_images", "true")
    assert quality.show_if == ("image_size", "large")
    groups = [(g.name, g.label, g.collapsed) for g in parsed.config_groups]
    assert groups == [
        ("account", "Konto", False), ("images", "Bilder", False),
        ("advanced", "Advanced", True),
    ]
    assert parsed.config_groups[0].help == "Where BlueFerry signs in."


def test_a_1_2_manifest_is_unchanged() -> None:
    old = manifest(api_version="1.2", capabilities="card;",
                   extra="[Config url]\nLabel=URL\nType=url\n")
    assert old.api_minor == 2 and not old.config_test and old.config_login == ""
    (field,) = old.config
    assert (field.placeholder, field.group, field.pattern, field.show_if) == ("", "", "", None)
    assert [g.name for g in old.config_groups] == [""]


def test_ungrouped_fields_come_first_and_advanced_last() -> None:
    parsed = _form(
        "[Config b]\nLabel=B\nType=string\nAdvanced=true\n"
        "[Config a]\nLabel=A\nType=string\n"
        "[Config c]\nLabel=C\nType=string\nGroup=options\n"
    )
    assert [g.name for g in parsed.config_groups] == ["", "options", "advanced"]
    assert parsed.config_groups[1].label == "Options"


@pytest.mark.parametrize("extra,reason", [
    ("[Config a]\nLabel=x\nType=string\nGroup=nope\n", "no \\[ConfigGroup\\]"),
    ("[Config a]\nLabel=x\nType=string\nGroup=account\nAdvanced=true\n", "conflicts"),
    ("[Config a]\nLabel=x\nType=string\nHelpUrl=http://example.org\n", "HelpUrl"),
    ("[Config a]\nLabel=x\nType=string\nHelpUrl=javascript:alert(1)\n", "HelpUrl"),
    ("[Config a]\nLabel=x\nType=string\nPlaceholder=" + "p" * 121 + "\n", "Placeholder"),
    ("[Config a]\nLabel=x\nType=string\nExample=a‮b\n", "Example"),
    ("[Config a]\nLabel=x\nType=string\nPattern=(a+)+\n", "simple subset"),
    ("[Config a]\nLabel=x\nType=string\nPattern=(?=a)\n", "simple subset"),
    ("[Config a]\nLabel=x\nType=string\nPattern=(a)\\1\n", "simple subset"),
    ("[Config a]\nLabel=x\nType=string\nPattern=[a\n", "not a regular"),
    ("[Config a]\nLabel=x\nType=bool\nPattern=a\n", "only for text"),
    ("[Config a]\nLabel=x\nType=string\nMin=3\n", "only for int"),
    ("[Config a]\nLabel=x\nType=string\nShowIf=b=1\n", "earlier setting"),
    ("[Config a]\nLabel=x\nType=string\nShowIf=a\n", "earlier setting"),
    ("[Config b]\nLabel=x\nType=bool\n[Config a]\nLabel=x\nType=string\nShowIf=b=yes\n",
     "true or false"),
    ("[Config b]\nLabel=x\nType=secret\n[Config a]\nLabel=x\nType=string\nShowIf=b=x\n",
     "secret"),
    ("[Config b]\nLabel=x\nType=choice\nChoices=p;q\n"
     "[Config a]\nLabel=x\nType=string\nShowIf=b=r\n", "not a choice"),
    ("[ConfigGroup Bad]\nLabel=x\n", "lowercase"),
    ("[ConfigGroup g]\nHelp=x\n", "missing Label"),
    ("[ConfigGroup g]\nLabel=" + "l" * 41 + "\n", "Label"),
    ("[ConfigGroup g]\nLabel=x\nCollapsed=maybe\n", "Collapsed"),
    ("[ConfigGroup g]\nLabel=x\n[ConfigGroup g]\nLabel=y\n", "duplicate"),
    ("".join(f"[ConfigGroup g{i}]\nLabel=x\n" for i in range(9)), "more than 8"),
])
def test_bad_new_keys_ignore_the_manifest(extra, reason) -> None:
    with pytest.raises(ManifestError, match=reason):
        _form(extra)


@pytest.mark.parametrize("header,reason", [
    ("ConfigTest=maybe\n", "ConfigTest"),
    ("ConfigLogin=Next Cloud\n", "ConfigLogin"),
])
def test_bad_plugin_keys_ignore_the_manifest(header, reason) -> None:
    with pytest.raises(ManifestError, match=reason):
        _form(header=header)
    with pytest.raises(ManifestError, match="need \\[Config"):
        _form("", header="ConfigTest=true\n")


def test_pattern_min_max_and_error_text_are_checked() -> None:
    fields = {field.key: field for field in _form().config}
    assert fields["token"].coerce("abcde-12") == "abcde-12"
    with pytest.raises(ConfigError, match="five to 72"):
        fields["token"].coerce("a b")
    with pytest.raises(ConfigError, match="from 10 to 100"):
        fields["image_quality"].coerce(5)
    # Without ErrorText the generic reasons stay.
    with pytest.raises(ConfigError, match="at most 60"):
        fields["timeout"].coerce(61)


def test_show_if_hides_fields_and_their_requirement() -> None:
    fields = _form().config
    keys = [field.key for field in visible_fields(fields, {})]
    assert "image_size" not in keys and "image_quality" not in keys
    on = {"upload_images": True, "image_size": "large"}
    assert {"image_size", "image_quality"} <= {f.key for f in visible_fields(fields, on)}
    # The chain breaks when the middle field is hidden, whatever its value.
    off = {"upload_images": False, "image_size": "large"}
    assert "image_quality" not in {f.key for f in visible_fields(fields, off)}
    current = {"url": "https://a.example", "token": True}
    _values, errors = validate(fields, current, {"image_quality": None})
    assert errors == {}
    _values, errors = validate(fields, current, {**on, "image_quality": None})
    assert errors == {"image_quality": "is required"}
    assert value_text(True) == "true" and value_text(None) == "" and value_text(3) == "3"


@given(st.text(max_size=60))
def test_a_pattern_never_raises_anything_but_config_error(text) -> None:
    field = _form().config[1]
    try:
        field.coerce(text)
    except ConfigError:
        pass

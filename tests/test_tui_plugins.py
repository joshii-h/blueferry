"""Terminal "From Plugins" and "Send to…": fakes only, no plugin runs."""
from __future__ import annotations

from blueferry import companion_tools
from blueferry import plugin_surfaces as surfaces
from blueferry.plugin_api.surfaces import Action, CardItem
from blueferry.tui import BlueFerryApp, TuiState
from blueferry.tui_phone import PhoneScreen
from blueferry.tui_plugins import SendToScreen, card_actions, card_options, card_sends
from tests.test_tui_phone import _Backend, _run, _until

CARDS = [
    surfaces.PluginCard("io.example.calendar", "Calendar", True, "", [
        CardItem("next", "[b]Dentist", subtitle="10:00\x1b[2J", actions=[
            Action("open", "Open", kind="primary"), Action("snooze", "Snooze")]),
        CardItem("later", "Training")]),
    surfaces.PluginCard("io.example.broken", "Broken", False, "Unavailable: timeout"),
]


def test_card_options_are_plain_and_map_to_actions() -> None:
    options = card_options(CARDS)
    prompts = [str(option.prompt) for option in options]
    assert "[b]Dentist" in prompts[1] and "\x1b" not in "".join(prompts)
    assert [option.id for option in options if option.id] == ["action:0", "action:1"]
    assert card_actions(CARDS) == [
        ("io.example.calendar", "next", "open"), ("io.example.calendar", "next", "snooze")]
    assert any("Unavailable: timeout" in prompt for prompt in prompts)
    assert "Loading" in str(card_options(None)[0].prompt)


def test_phone_screen_runs_plugin_actions_and_opens_send_to(tmp_path) -> None:
    invoked: list[tuple] = []
    opened: list[str] = []
    sent: list[tuple] = []
    payload = tmp_path / "a b.jpg"
    payload.write_bytes(b"x")
    choice = surfaces.ShareChoice("io.example.ls", "LocalSend", "iphone", "iPhone")

    def send_screen(on_sent=None, **_fixed):
        return SendToScreen(
            load=lambda: surfaces.ShareTargets([choice]),
            send=lambda target, paths: sent.append((target.key, paths))
            or surfaces.Outcome(True, "Sending 1 file"),
            on_sent=on_sent,
        )

    async def scenario() -> None:
        backend = _Backend()
        app = BlueFerryApp(TuiState(backend), monitor_factory=lambda: None)
        async with app.run_test(size=(140, 60)) as pilot:
            app.set_focus(None)
            screen = PhoneScreen(
                backend, lambda: app.state.status, lambda: None,
                tools_system=companion_tools.System(which=lambda _n: None,
                                                    open_uri=opened.append),
                load_cards=lambda: CARDS,
                invoke=lambda *ids: invoked.append(ids)
                or surfaces.Outcome(True, "Snoozed", "https://example.org/e"),
                send_screen=send_screen,
            )
            await app.push_screen(screen)
            await _until(pilot, lambda: screen._cards is not None)
            plugins = screen.query_one("#phone-plugins")
            plugins.focus()
            plugins.highlighted = next(
                i for i in range(plugins.option_count)
                if plugins.get_option_at_index(i).id == "action:1")
            await pilot.press("enter")
            await _until(pilot, lambda: bool(invoked) and bool(opened))
            assert invoked == [("io.example.calendar", "next", "snooze")]
            assert opened == ["https://example.org/e"]
            app.set_focus(None)
            await pilot.press("s")
            await _until(pilot, lambda: isinstance(app.screen, SendToScreen))
            send = app.screen
            await _until(pilot, lambda: bool(send._choices))
            send.query_one("#send-paths").value = f'"{payload}"'
            send.send()
            await _until(pilot, lambda: bool(sent))
            assert sent == [("io.example.ls:iphone", [str(payload.resolve())])]

    _run(scenario())


SEND_CARDS = [
    surfaces.PluginCard("io.example.ls", "LocalSend", True, "", [
        CardItem("dev-phone", "iPhone", actions=[
            Action("send", "Send files…", kind="primary", send_to="ls-1"),
            Action("trust", "Always accept")])], can_send=True),
]


def test_card_sends_line_up_with_actions() -> None:
    assert card_sends(SEND_CARDS) == [("ls-1", "iPhone"), ("", "iPhone")]
    no_share = [surfaces.PluginCard("io.example.ls", "LocalSend", True, "",
                                    SEND_CARDS[0].items)]
    assert card_sends(no_share) == [("", "iPhone"), ("", "iPhone")]


def test_a_sending_card_action_asks_for_paths_and_sends_there(tmp_path, monkeypatch) -> None:
    invoked: list[tuple] = []
    sent: list[tuple] = []
    payload = tmp_path / "photo.jpg"
    payload.write_bytes(b"x")
    choice = surfaces.ShareChoice("io.example.ls", "LocalSend", "ls-1", "iPhone")
    monkeypatch.setattr(surfaces, "card_choice", lambda plugin, target, label: (
        choice if (plugin, target, label) == ("io.example.ls", "ls-1", "iPhone") else None))

    def send_screen(on_sent=None, choice=None):
        def never_load():
            raise AssertionError("a card send must not ask for all targets")

        return SendToScreen(
            load=never_load, choice=choice, on_sent=on_sent,
            send=lambda target, paths: sent.append((target.key, paths))
            or surfaces.Outcome(True, "Sending 1 file"),
        )

    async def scenario() -> None:
        backend = _Backend()
        app = BlueFerryApp(TuiState(backend), monitor_factory=lambda: None)
        async with app.run_test(size=(140, 60)) as pilot:
            app.set_focus(None)
            screen = PhoneScreen(
                backend, lambda: app.state.status, lambda: None,
                tools_system=companion_tools.System(which=lambda _n: None),
                load_cards=lambda: SEND_CARDS,
                invoke=lambda *ids: invoked.append(ids) or surfaces.Outcome(True, "x"),
                send_screen=send_screen,
            )
            await app.push_screen(screen)
            await _until(pilot, lambda: screen._cards is not None)
            plugins = screen.query_one("#phone-plugins")
            plugins.focus()
            plugins.highlighted = next(
                i for i in range(plugins.option_count)
                if plugins.get_option_at_index(i).id == "action:0")
            await pilot.press("enter")
            await _until(pilot, lambda: isinstance(app.screen, SendToScreen))
            send = app.screen
            await _until(pilot, lambda: bool(send._choices))
            assert send._choices == [choice]
            send.query_one("#send-paths").value = str(payload)
            send.send()
            await _until(pilot, lambda: bool(sent))
            assert sent == [("io.example.ls:ls-1", [str(payload.resolve())])]
            assert invoked == []

    _run(scenario())

"""Terminal "From Plugins" and "Send to…": fakes only, no plugin runs."""
from __future__ import annotations

from blueferry import companion_tools
from blueferry import plugin_surfaces as surfaces
from blueferry.plugin_api.surfaces import Action, CardItem
from blueferry.tui import BlueFerryApp, TuiState
from blueferry.tui_phone import PhoneScreen
from blueferry.tui_plugins import SendToScreen, card_actions, card_options
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

    def send_screen(on_sent=None):
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

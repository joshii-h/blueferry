"""``blueferry audio``: where the iPhone's media playback is heard."""
from __future__ import annotations

import typer

from blueferry.client import BackendClient, BackendError
from blueferry.text_safety import terminal_text

_ACTIONS = ("status", "pc", "phone")

_REASONS = {
    "keep_phone_audio_on_phone": (
        "BlueFerry keeps phone audio on the iPhone. Set "
        "BLUEFERRY_KEEP_PHONE_AUDIO_ON_PHONE=false in "
        "~/.config/blueferry/local.env and restart the backend to allow it here."
    ),
    "phone_disconnected": "The iPhone is not connected over Bluetooth Classic.",
    "no_a2dp_source": "The iPhone does not offer Bluetooth audio (A2DP) to this computer.",
    "unknown": "Waiting for bluetoothd to report the iPhone's audio state.",
}


def describe_route(status: dict) -> str:
    route = status.get("phone_audio_route")
    pending = status.get("phone_audio_pending")
    if pending in ("pc", "phone"):
        target = "this computer" if pending == "pc" else "the iPhone"
        return f"Switching phone audio to {target}…"
    if route == "pc":
        return "Phone audio plays on this computer."
    if route == "phone":
        return "Phone audio plays on the iPhone."
    reason = str(status.get("phone_audio_reason") or "")
    return "Phone audio switching is unavailable. " + _REASONS.get(
        reason, "Update the BlueFerry backend."
    )


def audio(
    action: str = typer.Argument(
        "status", help="status, pc (play iPhone audio here) or phone", show_default=True,
    ),
) -> None:
    """Show or switch where the iPhone's media playback is heard."""
    selected = action.strip().casefold()
    if selected not in _ACTIONS:
        typer.echo(f"Unknown audio action. Choose one of: {', '.join(_ACTIONS)}", err=True)
        raise typer.Exit(code=2)
    client = BackendClient()
    try:
        if selected == "status":
            status = dict(client.status().extra)
            typer.echo(describe_route(status))
            if status.get("phone_audio_route") not in ("pc", "phone"):
                raise typer.Exit(code=1)
            return
        client.set_phone_audio_route(selected)
        target = "this computer" if selected == "pc" else "the iPhone"
        typer.echo(f"Asked the iPhone to play its audio on {target}.")
    except BackendError as error:
        typer.echo(terminal_text(error), err=True)
        raise typer.Exit(code=2) from None

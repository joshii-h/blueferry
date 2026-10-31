"""The contact cards in the Textual client.

:class:`ContactsScreen` (``C``, or **Contacts** in the command palette)
pages through ``Messages1.ListContactCards`` with a search field and shows
the card opened with Enter (``GetContact``): every number, email and postal
address with its label. **Call**, **Send a Message** and **Copy** act on the
selected detail row. Rows, sections and wording come from the shared
:mod:`blueferry.contacts_view`, so the terminal says what Qt, GTK and
Quickshell say.

Card ids stay valid only within one daemon run; when ``contact_card_epoch``
in ``GetStatus`` changes, the list is read again and an open card is
dropped. A terminal cannot show the photo, so ``GetContact``'s photo bytes
are ignored.

Every blocking backend call runs off the UI thread, and iPhone text is
rendered as Rich ``Text`` after ``terminal_text``, so it carries neither
markup nor terminal controls.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar, Protocol

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, ListItem, ListView, Static

from blueferry import contacts_view, phone_link_view
from blueferry.client import ContactNotFoundError
from blueferry.i18n import _
from blueferry.search_view import TYPING_DELAY_MS
from blueferry.text_safety import terminal_text
from blueferry.tui_tasks import TextualTaskRunner, TextualTimer

_MAX_QUERY = 200

ContactsResult = tuple[str, str] | None


class ContactsClient(Protocol):
    def contact_cards(
        self, offset: int = 0, limit: int = 150, query: str = "",
    ) -> dict: ...

    def contact(self, contact_id: str) -> tuple[dict, bytes]: ...


def _plain(value: object) -> str:
    return terminal_text(value)


def contact_row_text(row: Mapping[str, Any]) -> Text:
    """One ``list_rows`` row: name and, dimmed, organization and first number."""
    text = Text(_plain(row.get("title")), style="bold")
    subtitle = _plain(row.get("subtitle"))
    if subtitle:
        text.append(f"  {subtitle}", style="dim")
    return text


def detail_row_text(row: Mapping[str, Any]) -> Text:
    """One ``detail_sections`` row: label, then the value (addresses span lines)."""
    label = _plain(row.get("label"))
    lines = _plain(row.get("value")).split("\n")
    text = Text(f"{label}  ", style="#7dd3fc")
    text.append(lines[0])
    indent = " " * (len(label) + 2)
    for line in lines[1:]:
        text.append(f"\n{indent}{line}")
    return text


class ContactItem(ListItem):
    def __init__(self, row: Mapping[str, Any]) -> None:
        self.row = dict(row)
        super().__init__(Static(contact_row_text(row)), classes="contacts-row")
        # A Text, never a str: Textual parses a str tooltip as markup.
        self.tooltip = Text(_plain(row.get("title")))


class MoreItem(ListItem):
    """The last row while the daemon has more matches; loads the next page."""

    def __init__(self) -> None:
        super().__init__(Static(_("Load more…")), classes="contacts-more")


class SectionItem(ListItem):
    def __init__(self, title: str) -> None:
        self.title = _plain(title)
        super().__init__(Static(Text(self.title)), classes="contacts-section", disabled=True)


class DetailItem(ListItem):
    def __init__(self, section: str, row: Mapping[str, Any]) -> None:
        self.section = section
        self.row = dict(row)
        super().__init__(Static(detail_row_text(row)), classes="contacts-detail-row")
        self.tooltip = Text(_plain(f"{row.get('label', '')}, {row.get('value', '')}"))


class ContactsScreen(ModalScreen[ContactsResult]):
    """Browse and search the cached contact cards.

    Dismisses with ``("call", number)`` or ``("message", address)`` for the
    selected detail row, or ``None``.
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "close", "Close", show=False),
        Binding("slash", "focus_search", "Search", show=False),
        # Inert while the search field has the focus (it takes the letters).
        Binding("c", "call", "Call", show=False),
        Binding("m", "message", "Send a Message", show=False),
        Binding("y", "copy", "Copy", show=False),
    ]

    def __init__(
        self, client: ContactsClient, *, status: Mapping[str, Any] | None = None,
        open_id: str = "",
    ) -> None:
        super().__init__()
        self._shown = False
        self._client = client
        self._status: Mapping[str, Any] = dict(status or {})
        self._open_request = open_id
        self.query_text = ""
        self.page: dict[str, Any] = contacts_view.normalize_page({})
        self.rows: list[dict[str, Any]] = []
        self.list_error = ""
        self.loaded = False
        self.card: dict[str, Any] | None = None
        self.card_id = ""
        self.card_note = ""
        self._epoch = self._status.get(contacts_view.EPOCH_KEY)
        self._generation = 0
        self._card_generation = 0
        self._loading_more = False
        self._runner = TextualTaskRunner(self, group="contacts")
        self._card_runner = TextualTaskRunner(self, group="contacts-card")
        self._typing = TextualTimer(self, self._search_now)

    def compose(self) -> ComposeResult:
        with Vertical(id="contacts-dialog", classes="dialog"):
            yield Static(_("Contacts"), classes="dialog-title")
            yield Input(
                placeholder=f"{contacts_view.search_placeholder()}  /",
                id="contacts-search", max_length=_MAX_QUERY,
            )
            yield Static("", id="contacts-count", classes="dialog-copy")
            with Horizontal(id="contacts-body"):
                with Vertical(id="contacts-list-pane"):
                    yield Static(_("Loading…"), id="contacts-state", classes="dialog-copy")
                    yield ListView(id="contacts-list")
                with Vertical(id="contacts-detail-pane"):
                    yield Static("", id="contacts-detail-title")
                    yield Static("", id="contacts-detail-subtitle")
                    yield Static(
                        _("Press Enter on a contact to show its details."),
                        id="contacts-detail-state", classes="dialog-copy",
                    )
                    yield ListView(id="contacts-detail")
            with Horizontal(classes="dialog-actions"):
                yield Button(_("Call  c"), id="contacts-call")
                yield Button(_("Send a Message  m"), id="contacts-message")
                yield Button(_("Copy  y"), id="contacts-copy")
                yield Button(_("Close"), variant="primary", id="contacts-close")

    def on_mount(self) -> None:
        self._shown = True
        self.query_one("#contacts-list", ListView).display = False
        self.query_one("#contacts-detail", ListView).display = False
        self._render_buttons()
        self.reload()
        if self._open_request:
            self.open_card(self._open_request)
            self._open_request = ""
        self.query_one("#contacts-search", Input).focus()

    # -- status --------------------------------------------------------

    def status_changed(self, status: Mapping[str, Any]) -> None:
        self._status = dict(status)
        epoch = self._status.get(contacts_view.EPOCH_KEY)
        if epoch != self._epoch:
            # Ids from the old epoch may no longer resolve: read the list
            # again and drop the open card.
            self._epoch = epoch
            self._clear_card(_("Press Enter on a contact to show its details."))
            self.reload()
        self._render_buttons()

    @property
    def calls_hint(self) -> str:
        return phone_link_view.calls_hint(
            self._status, str(self._status.get("calls_state") or "disabled"),
        )

    # -- list ----------------------------------------------------------

    def reload(self) -> None:
        """Read the first page for the current query again."""
        self._typing.stop()
        self._generation += 1
        self._loading_more = False
        self._fetch(self._generation, 0, self.query_text)

    def _fetch(self, generation: int, offset: int, query: str) -> None:
        self._runner.submit(
            lambda: self._client.contact_cards(offset, contacts_view.DEFAULT_PAGE, query),
            lambda page: self._page_loaded(generation, offset, page),
            lambda message: self._page_failed(generation, message),
        )

    def load_more(self) -> None:
        if self._loading_more or not contacts_view.has_more(self.page):
            return
        self._loading_more = True
        self._fetch(self._generation, contacts_view.next_offset(self.page), self.query_text)

    def _page_loaded(self, generation: int, offset: int, payload: object) -> None:
        if generation != self._generation:
            return  # a newer query or reload superseded this answer
        page = contacts_view.normalize_page(payload)
        rows = contacts_view.list_rows(page)
        if offset:
            seen = {row["id"] for row in self.rows}
            self.rows += [row for row in rows if row["id"] not in seen]
        else:
            self.rows = rows
        self._loading_more = False
        self.page, self.list_error, self.loaded = page, "", True
        self._schedule_render(keep_index=bool(offset))

    def _page_failed(self, generation: int, message: str) -> None:
        if generation != self._generation:
            return
        self._loading_more = False
        self.list_error, self.loaded = contacts_view.load_failed_text(message), True
        self._schedule_render(keep_index=False)

    def _schedule_render(self, *, keep_index: bool) -> None:
        self.run_worker(
            self._render_list(keep_index), group="contacts-render", exclusive=True,
        )

    @property
    def body_text(self) -> str:
        """Everything the list shows, for tests and screen readers."""
        lines = []
        for item in self.query_one("#contacts-list", ListView).children:
            if isinstance(item, ContactItem):
                lines.append(contact_row_text(item.row).plain)
            elif isinstance(item, MoreItem):
                lines.append(_("Load more…"))
        return "\n".join(lines)

    @property
    def detail_text(self) -> str:
        card = self.card or {}
        lines = [_plain(card.get("title", "")), _plain(card.get("subtitle", ""))]
        for item in self.query_one("#contacts-detail", ListView).children:
            if isinstance(item, SectionItem):
                lines.append(item.title)
            elif isinstance(item, DetailItem):
                lines.append(detail_row_text(item.row).plain)
        return "\n".join(line for line in lines if line)

    async def _render_list(self, keep_index: bool) -> None:
        listing = self.query_one("#contacts-list", ListView)
        state = self.query_one("#contacts-state", Static)
        count = self.query_one("#contacts-count", Static)
        index = listing.index if keep_index else None
        await listing.clear()
        if self.list_error:
            state.update(Text(_plain(self.list_error)))
            count.update("")
        else:
            state.update(Text(_plain(contacts_view.empty_hint(self.page))))
            count.update(Text(_plain(contacts_view.count_text(self.page))))
        items: list[ListItem] = [ContactItem(row) for row in self.rows]
        if items and contacts_view.has_more(self.page) and not self.list_error:
            items.append(MoreItem())
        if items:
            await listing.extend(items)
            listing.index = min(index or 0, len(items) - 1)
        state.display = not items or bool(self.list_error)
        listing.display = bool(items)

    # -- search --------------------------------------------------------

    @on(Input.Changed, "#contacts-search")
    def search_changed(self, event: Input.Changed) -> None:
        query = " ".join(event.value.split())
        if query == self.query_text:
            return
        self.query_text = query
        self._typing.start(TYPING_DELAY_MS)

    @on(Input.Submitted, "#contacts-search")
    def search_submitted(self) -> None:
        self._search_now()
        self.query_one("#contacts-list", ListView).focus()

    def _search_now(self) -> None:
        self.reload()

    def action_focus_search(self) -> None:
        self.query_one("#contacts-search", Input).focus()

    # -- card ----------------------------------------------------------

    @on(ListView.Selected, "#contacts-list")
    def contact_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, MoreItem):
            self.load_more()
        elif isinstance(event.item, ContactItem):
            self.open_card(event.item.row["id"])
            self.query_one("#contacts-detail", ListView).focus()

    @on(ListView.Highlighted, "#contacts-list")
    def contact_highlighted(self, event: ListView.Highlighted) -> None:
        # Paging on reaching the end keeps arrow keys enough to see it all.
        if isinstance(event.item, MoreItem):
            self.load_more()

    def open_card(self, contact_id: str) -> None:
        """Fetch one card with ``GetContact``; a stale id says so and reloads."""
        if not contact_id:
            return
        self._card_generation += 1
        generation = self._card_generation
        self.card_id = contact_id
        self.query_one("#contacts-detail-state", Static).update(_("Loading…"))

        def fetch() -> tuple[str, object]:
            try:
                document, _photo = self._client.contact(contact_id)
            except ContactNotFoundError:
                return "stale", None
            return "card", document

        self._card_runner.submit(
            fetch,
            lambda result: self._card_loaded(generation, result),
            lambda message: self._card_failed(generation, message),
        )

    def _card_loaded(self, generation: int, result: tuple[str, object]) -> None:
        if generation != self._card_generation:
            return
        kind, document = result
        if kind == "stale":
            self._clear_card(contacts_view.stale_text())
            self.reload()
            return
        self.card = contacts_view.card_view(document)
        self.card_note = ""
        self.run_worker(self._render_card(), group="contacts-card-render", exclusive=True)

    def _card_failed(self, generation: int, message: str) -> None:
        if generation != self._card_generation:
            return
        self._clear_card(contacts_view.load_failed_text(message))

    def _clear_card(self, note: str) -> None:
        self._card_generation += 1
        self.card, self.card_id, self.card_note = None, "", note
        self.run_worker(self._render_card(), group="contacts-card-render", exclusive=True)

    async def _render_card(self) -> None:
        title = self.query_one("#contacts-detail-title", Static)
        subtitle = self.query_one("#contacts-detail-subtitle", Static)
        state = self.query_one("#contacts-detail-state", Static)
        details = self.query_one("#contacts-detail", ListView)
        await details.clear()
        card = self.card
        if card is None:
            title.update("")
            subtitle.update("")
            state.update(Text(_plain(self.card_note)))
            state.display = True
            details.display = False
            self._render_buttons()
            return
        title.update(Text(_plain(card["title"]), style="bold"))
        subtitle.update(Text(_plain(card["subtitle"]), style="dim"))
        items: list[ListItem] = []
        for section in card["sections"]:
            items.append(SectionItem(section["title"]))
            items.extend(DetailItem(section["id"], row) for row in section["rows"])
        state.update(_("This card has no numbers or addresses.") if not items else "")
        state.display = not items
        if items:
            await details.extend(items)
            details.index = 1
        details.display = bool(items)
        self._render_buttons()

    # -- actions -------------------------------------------------------

    def _selected_row(self) -> dict[str, Any] | None:
        item = self.query_one("#contacts-detail", ListView).highlighted_child
        return item.row if isinstance(item, DetailItem) else None

    def _can(self, action: str) -> bool:
        row = self._selected_row()
        return bool(row and row.get("target") and action in row.get("actions", ()))

    def _render_buttons(self) -> None:
        if not self._shown or not self.is_attached:
            return
        call = self.query_one("#contacts-call", Button)
        hint = self.calls_hint
        call.disabled = not self._can(contacts_view.ACTION_DIAL) or bool(hint)
        call.tooltip = Text(_plain(hint)) if hint and self._can(contacts_view.ACTION_DIAL) else None
        self.query_one("#contacts-message", Button).disabled = not self._can(
            contacts_view.ACTION_MESSAGE,
        )
        self.query_one("#contacts-copy", Button).disabled = not self._can(
            contacts_view.ACTION_COPY,
        )

    @on(ListView.Highlighted, "#contacts-detail")
    def detail_highlighted(self) -> None:
        self._render_buttons()

    @on(ListView.Selected, "#contacts-detail")
    def detail_selected(self) -> None:
        # Enter does the row's first action the terminal offers.
        if self._can(contacts_view.ACTION_DIAL) and not self.calls_hint:
            self.action_call()
        elif self._can(contacts_view.ACTION_MESSAGE):
            self.action_message()
        else:
            self.action_copy()

    def _typing_in_search(self) -> bool:
        return isinstance(self.focused, Input)

    @on(Button.Pressed, "#contacts-call")
    def call_button(self) -> None:
        self.action_call()

    def action_call(self) -> None:
        if self._typing_in_search() or not self._can(contacts_view.ACTION_DIAL):
            return
        hint = self.calls_hint
        if hint:
            self.notify(_plain(hint), severity="warning", markup=False)
            return
        row = self._selected_row() or {}
        self.dismiss(("call", phone_link_view.dialable(str(row["target"]))))

    @on(Button.Pressed, "#contacts-message")
    def message_button(self) -> None:
        self.action_message()

    def action_message(self) -> None:
        if self._typing_in_search() or not self._can(contacts_view.ACTION_MESSAGE):
            return
        row = self._selected_row() or {}
        self.dismiss(("message", str(row["target"])))

    @on(Button.Pressed, "#contacts-copy")
    def copy_button(self) -> None:
        self.action_copy()

    def action_copy(self) -> None:
        if self._typing_in_search() or not self._can(contacts_view.ACTION_COPY):
            return
        row = self._selected_row() or {}
        # OSC 52, like Copy Report in diagnostics; terminals may ignore it.
        self.app.copy_to_clipboard(str(row["target"]))
        self.notify(_("Copied"), markup=False)

    @on(Button.Pressed, "#contacts-close")
    def close_button(self) -> None:
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)

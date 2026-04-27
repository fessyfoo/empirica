"""empirica tui — compact Textual cockpit.

Designed for phone terminals and tmux split-strip use. Targets ~50 cols ×
17 rows. Reads the same JSON as the CLI (`aggregate_all`); no business
logic duplication.

Layout:
  ┌─ Header  (title + summary)
  │  Instance table (one row per live Claude)
  │  Action bar    [P sent] [L loops] [S stop] [N notif]
  │  Statusline    (selected instance)
  │  Recent log    (last 5 actions for selected instance)
  └─ Footer  (key bindings)

Actions (mouse OR keyboard):
  p  toggle Sentinel pause/resume for selected instance
  l  toggle all loops on/off (pause-all if any unpaused, else resume-all)
  s  stop = remote interrupt (tmux send-keys Escape)
  n  clear all notifications for selected instance (placeholder — will
     propagate to ntfy + empirica-extension once ENP-cockpit ships)
  R  rename label (input modal)
  D  toggle live-only / include-dead view
  r  refresh now
  q  quit

Kill is intentionally absent from the TUI — too nuclear for a phone
glance. `empirica instance kill <id>` from the CLI is the escape hatch.
"""

from __future__ import annotations

from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Static,
)

from empirica.core.cockpit import (
    LoopRegistry,
    aggregate_all,
    clear_notifications,
    is_loop_paused,
    pause_sentinel,
    recent_actions,
    resume_sentinel,
    set_label,
    set_loop_paused,
    statusline_summary,
    stop_instance,
)

REFRESH_SECONDS = 2.0


# ─── modals (rename only — confirm modal kept tiny) ────────────────────────

class InputScreen(ModalScreen[str | None]):
    DEFAULT_CSS = """
    InputScreen { align: center middle; }
    #dialog {
        width: 56; height: 11;
        border: thick $accent 80%;
        background: $surface;
        padding: 1 2;
    }
    #dialog-input { margin-top: 1; }
    #dialog-buttons {
        align-horizontal: center; height: 3; margin-top: 1;
    }
    #dialog Button { margin: 0 1; min-width: 10; }
    """

    BINDINGS = [Binding('escape', 'dismiss(None)', 'Cancel')]

    def __init__(self, prompt: str, initial: str = '') -> None:
        super().__init__()
        self.prompt = prompt
        self.initial = initial

    def compose(self) -> ComposeResult:
        with Container(id='dialog'):
            yield Label(self.prompt)
            yield Input(value=self.initial, id='dialog-input')
            with Horizontal(id='dialog-buttons'):
                yield Button('OK', id='ok', variant='primary')
                yield Button('Cancel', id='cancel', variant='default')

    def on_mount(self) -> None:
        self.query_one('#dialog-input', Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == 'ok':
            self.dismiss(self.query_one('#dialog-input', Input).value)
        else:
            self.dismiss(None)

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        self.dismiss(self.query_one('#dialog-input', Input).value)


# ─── main app ──────────────────────────────────────────────────────────────

class CockpitApp(App):
    """Compact interactive Empirica cockpit."""

    CSS = """
    Screen { layout: vertical; }

    #summary { padding: 0 1; height: 1; color: $text-muted; }

    #inst-table { height: 1fr; }

    #action-bar {
        height: 3;
        background: $surface;
        align-horizontal: left;
        padding: 0 1;
    }
    #action-bar Button { margin: 0 1; min-width: 10; }

    #statusline {
        height: 1;
        padding: 0 1;
        color: $primary;
        background: $boost;
    }

    #recent-header {
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }
    #recent {
        height: 6;
        padding: 0 1;
        color: $text-muted;
    }
    """

    BINDINGS = [
        Binding('q', 'quit', 'Quit'),
        Binding('r', 'refresh_now', 'Refresh'),
        Binding('p', 'toggle_sentinel', 'Sent.'),
        Binding('l', 'toggle_loops', 'Loops'),
        Binding('s', 'stop', 'Stop'),
        Binding('n', 'clear_notifications', 'Notif'),
        Binding('R', 'relabel', 'Rename'),
        Binding('D', 'toggle_dead', 'Show dead'),
    ]

    def __init__(self, include_dead: bool = False) -> None:
        super().__init__()
        self.payload: dict[str, Any] = {'instances': [], 'summary': {}, 'generated_at': ''}
        self.selected_instance_id: str | None = None
        self.include_dead = include_dead

    # ─── lifecycle ────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static('', id='summary')

        table = DataTable(id='inst-table', cursor_type='row', zebra_stripes=True)
        table.add_columns('stat', 'name', 'phase', 'S', 'L', 'N')
        yield table

        with Horizontal(id='action-bar'):
            yield Button('P sent', id='btn-sent', variant='warning')
            yield Button('L loops', id='btn-loops', variant='warning')
            yield Button('S stop', id='btn-stop', variant='error')
            yield Button('N notif', id='btn-notif', variant='primary')

        yield Static('', id='statusline')
        yield Static('recent', id='recent-header')
        yield Static('', id='recent')
        yield Footer()

    def on_mount(self) -> None:
        self.title = 'empirica cockpit'
        self.refresh_payload()
        self.set_interval(REFRESH_SECONDS, self.refresh_payload)

    # ─── data + rendering ─────────────────────────────────────────────────

    def refresh_payload(self) -> None:
        try:
            self.payload = aggregate_all(include_dead=self.include_dead)
        except Exception as e:
            self._log_status(f'refresh failed: {e}')
            return
        self._render_summary()
        self._render_table()
        self._render_selected_widgets()

    def action_toggle_dead(self) -> None:
        self.include_dead = not self.include_dead
        self.refresh_payload()

    def _render_summary(self) -> None:
        s = self.payload.get('summary', {})
        notif_total = s.get('open_notifications', 0)
        dead_mode = ' (incl. dead)' if self.include_dead else ''
        notif_part = f' · ⊕ {notif_total} notif' if notif_total else ''
        text = (
            f"empirica · {s.get('instances', 0)} inst{notif_part}{dead_mode}"
        )
        self.query_one('#summary', Static).update(text)

    def _render_table(self) -> None:
        table = self.query_one('#inst-table', DataTable)
        previously_selected = self.selected_instance_id
        table.clear()
        rows = self.payload.get('instances', [])
        for inst in rows:
            iid = inst['instance_id']
            stat = self._state_glyph(inst['state'])
            name = (inst.get('label') or iid)[:18]
            phase = self._phase_cell(inst.get('phase'), inst.get('asking', False))
            sentinel = '○' if inst['sentinel']['paused'] else '●'
            loops = self._loops_glyph(inst.get('loops') or {})
            notif = self._notif_glyph(inst.get('notifications') or {})
            table.add_row(stat, name, phase, sentinel, loops, notif, key=iid)

        # Re-select.
        if rows:
            target = previously_selected or rows[0]['instance_id']
            for idx, inst in enumerate(rows):
                if inst['instance_id'] == target:
                    table.move_cursor(row=idx)
                    self.selected_instance_id = target
                    break
            else:
                table.move_cursor(row=0)
                self.selected_instance_id = rows[0]['instance_id']
        else:
            self.selected_instance_id = None

    @staticmethod
    def _state_glyph(state: str) -> str:
        return {
            'active': '🟢', 'idle': '🟡', 'stuck': '🔴',
            'closed': '⊘', 'no-claude': '⊗',
        }.get(state, '?')

    @staticmethod
    def _phase_cell(phase: str | None, asking: bool) -> str:
        if asking:
            return 'ask ⚠'
        return phase or '—'

    @staticmethod
    def _loops_glyph(loops: dict[str, Any]) -> str:
        if not loops:
            return '–'
        paused = sum(1 for v in loops.values() if v.get('paused'))
        if paused == 0:
            return '●'
        if paused == len(loops):
            return '○'
        return '◐'

    @staticmethod
    def _notif_glyph(notif: dict[str, Any]) -> str:
        count = int(notif.get('open_count', 0) or 0)
        if count == 0:
            return '·'
        return f'⊕{count}'

    def _selected_instance(self) -> dict[str, Any] | None:
        if not self.selected_instance_id:
            return None
        for inst in self.payload.get('instances', []):
            if inst['instance_id'] == self.selected_instance_id:
                return inst
        return None

    def _render_selected_widgets(self) -> None:
        """Statusline + recent actions for the currently-selected instance."""
        inst = self._selected_instance()
        statusline_widget = self.query_one('#statusline', Static)
        recent_widget = self.query_one('#recent', Static)

        if inst is None:
            statusline_widget.update('')
            recent_widget.update('(no instance selected)')
            return

        ss = statusline_summary(inst['instance_id'], label_fallback=inst.get('label'))
        if ss.found:
            parts = [f'▌ {ss.label or inst["instance_id"]}']
            if ss.know is not None:
                parts.append(f'know:{ss.know}')
            if ss.uncertainty is not None:
                parts.append(f'u:{ss.uncertainty}')
            if ss.artifact_count is not None:
                parts.append(f'{ss.artifact_count} artifacts')
            statusline_widget.update(' · '.join(parts))
        else:
            statusline_widget.update(f'▌ {inst.get("label") or inst["instance_id"]}')

        # Recent actions — epistemic_events is keyed by session_id which we
        # don't carry through the cockpit payload for v1. Pass project-only
        # and let the reader return the most recent project-wide events.
        actions = recent_actions(inst.get('project_path'), session_id=None, limit=5)
        if not actions:
            recent_widget.update('(no recent actions)')
            return
        lines = [f'  {a.iso_time} {a.summary[:42]}' for a in actions]
        recent_widget.update('\n'.join(lines))

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key and event.row_key.value:
            self.selected_instance_id = str(event.row_key.value)
            self._render_selected_widgets()

    # ─── actions ──────────────────────────────────────────────────────────

    def action_refresh_now(self) -> None:
        self.refresh_payload()

    def action_toggle_sentinel(self) -> None:
        inst = self._require_selected()
        if inst is None:
            return
        if inst['sentinel']['paused']:
            resume_sentinel(inst['instance_id'])
            self._log_status(f'sentinel resumed: {inst["instance_id"]}')
        else:
            pause_sentinel(inst['instance_id'], reason='via tui')
            self._log_status(f'sentinel paused: {inst["instance_id"]}')
        self.refresh_payload()

    def action_toggle_loops(self) -> None:
        inst = self._require_selected()
        if inst is None:
            return
        loops = inst.get('loops') or {}
        if not loops:
            self._log_status(f'{inst["instance_id"]}: no loops registered')
            return
        any_unpaused = any(not v.get('paused') for v in loops.values())
        target_state = True if any_unpaused else False
        for name in loops:
            set_loop_paused(inst['instance_id'], name, target_state)
        verb = 'paused' if target_state else 'resumed'
        self._log_status(f'{verb} {len(loops)} loop(s) on {inst["instance_id"]}')
        self.refresh_payload()

    def action_stop(self) -> None:
        inst = self._require_selected()
        if inst is None:
            return
        result = stop_instance(inst['instance_id'])
        self._log_status(f'stop {inst["instance_id"]}: {result.detail}')
        self.refresh_payload()

    def action_clear_notifications(self) -> None:
        inst = self._require_selected()
        if inst is None:
            return
        cleared = clear_notifications(inst['instance_id'])
        if cleared:
            self._log_status(f'cleared {cleared} notif(s) for {inst["instance_id"]}')
        else:
            self._log_status(f'no notifications to clear for {inst["instance_id"]}')
        self.refresh_payload()

    def action_relabel(self) -> None:
        inst = self._require_selected()
        if inst is None:
            return
        iid = inst['instance_id']
        current = inst.get('label') or ''

        def _on_input(value: str | None) -> None:
            if value is None:
                return
            new = set_label(iid, value if value.strip() else None)
            self._log_status(f'label {iid} → {new or "(cleared)"}')
            self.refresh_payload()

        self.push_screen(InputScreen(f'Label for {iid}:', initial=current), _on_input)

    # ─── button events (mouse / touch) ────────────────────────────────────

    def on_button_pressed(self, event: Button.Pressed) -> None:
        actions = {
            'btn-sent': self.action_toggle_sentinel,
            'btn-loops': self.action_toggle_loops,
            'btn-stop': self.action_stop,
            'btn-notif': self.action_clear_notifications,
        }
        handler = actions.get(event.button.id or '')
        if handler:
            handler()

    # ─── helpers ──────────────────────────────────────────────────────────

    def _require_selected(self) -> dict[str, Any] | None:
        inst = self._selected_instance()
        if inst is None:
            self._log_status('no instance selected')
        return inst

    def _log_status(self, message: str) -> None:
        """Status messages share the recent-actions widget (no separate log)."""
        try:
            recent = self.query_one('#recent', Static)
            recent.update(f'  • {message}')
        except Exception:
            pass


def run_tui(include_dead: bool = False) -> int:
    """Entry point for `empirica tui`. Returns shell exit code."""
    try:
        LoopRegistry.__name__  # noqa: B018
        is_loop_paused  # noqa: B018
    except Exception:
        pass

    try:
        app = CockpitApp(include_dead=include_dead)
        app.run()
        return 0
    except KeyboardInterrupt:
        return 130

"""Mouse and keyboard pane resizing expressed in terminal cells."""

from textual import events
from textual.message import Message
from textual.widget import Widget


class PaneDivider(Widget, can_focus=True):
    """Capture dragging without moving focus into the adjacent document."""

    BINDINGS = [
        ("left,up", "resize(-1)", "Shrink"),
        ("right,down", "resize(1)", "Grow"),
    ]

    class Resized(Message):
        """Request a relative resize along the divider's axis."""

        def __init__(self, divider: PaneDivider, delta: int) -> None:
            """Carry cell movement to the layout owner."""
            super().__init__()
            self.divider = divider
            self.delta = delta

    def __init__(self, *, vertical: bool, **kwargs) -> None:
        """Choose a vertical bar or horizontal row."""
        super().__init__(**kwargs)
        self.vertical = vertical
        self._last_position: int | None = None
        self.tooltip = "Drag to resize panels, or focus here and use arrow keys."

    def render(self) -> str:
        """Draw an obvious handle that also has a keyboard focus target."""
        if self.vertical:
            lines = [" │ "] * max(1, self.size.height)
            lines[len(lines) // 2] = " ↔ "
            return "\n".join(lines)
        label = " ↕ Drag to resize " if self.size.width >= 20 else " ↕ "
        return label.center(self.size.width, "─")[: self.size.width]

    def on_mouse_down(self, event: events.MouseDown) -> None:
        """Capture primary-button dragging even outside the divider."""
        if event.button == 1:
            self.focus()
            self.capture_mouse()
            self._last_position = event.screen_x if self.vertical else event.screen_y
            event.stop()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        """Publish incremental cell movement to the containing layout."""
        if self._last_position is not None:
            position = event.screen_x if self.vertical else event.screen_y
            self.action_resize(position - self._last_position)
            self._last_position = position

    def on_mouse_up(self, event: events.MouseUp) -> None:
        """Release the mouse after a completed drag."""
        self.release_mouse()
        self._last_position = None

    def action_resize(self, delta: int) -> None:
        """Provide the same resize operation to keyboard and mouse users."""
        if delta:
            self.post_message(self.Resized(self, delta))

//! Multi-line text editing with IME composition. The input handler follows gpui-pre 0.3.7
//! examples/input.rs (Apache-2.0, Zed Industries) by way of Keel's text_input.rs; wrapping,
//! line layout, history and highlighting are Lemfolio's.
//!
//! One editor serves two jobs: the LaTeX source pane and the assistant's message box.

use crate::{MONO, Palette, SANS};
use gpui::{
    App, Bounds, ClipboardItem, Context, CursorStyle, Element, ElementId, ElementInputHandler,
    Entity, EntityInputHandler, EventEmitter, FocusHandle, Focusable, Font, FontFeatures,
    FontStyle, FontWeight, GlobalElementId, Hsla, InspectorElementId, KeyBinding, LayoutId,
    MouseButton, MouseDownEvent, MouseMoveEvent, MouseUpEvent, Pixels, Point, ScrollWheelEvent,
    SharedString, Style, TextAlign, TextRun, UTF16Selection, Window, WrappedLine, actions, div,
    fill, point, prelude::*, px, relative, size,
};
use std::{
    collections::HashMap,
    ops::Range,
    time::{Duration, Instant},
};
use unicode_segmentation::UnicodeSegmentation;

actions!(
    lemfolio_editor,
    [
        Backspace, Delete, Left, Right, Up, Down, SelectLeft, SelectRight, SelectUp, SelectDown,
        WordLeft, WordRight, SelectWordLeft, SelectWordRight, Home, End, SelectHome, SelectEnd,
        DocStart, DocEnd, PageUp, PageDown, SelectAll, Paste, Cut, Copy, Undo, Redo, Enter,
        NewLine, Tab, Save, Escape,
    ]
);

pub fn bindings() -> Vec<KeyBinding> {
    let scope = Some("LemfolioEditor");
    vec![
        KeyBinding::new("backspace", Backspace, scope),
        KeyBinding::new("delete", Delete, scope),
        KeyBinding::new("left", Left, scope),
        KeyBinding::new("right", Right, scope),
        KeyBinding::new("up", Up, scope),
        KeyBinding::new("down", Down, scope),
        KeyBinding::new("shift-left", SelectLeft, scope),
        KeyBinding::new("shift-right", SelectRight, scope),
        KeyBinding::new("shift-up", SelectUp, scope),
        KeyBinding::new("shift-down", SelectDown, scope),
        KeyBinding::new("ctrl-left", WordLeft, scope),
        KeyBinding::new("ctrl-right", WordRight, scope),
        KeyBinding::new("ctrl-shift-left", SelectWordLeft, scope),
        KeyBinding::new("ctrl-shift-right", SelectWordRight, scope),
        KeyBinding::new("home", Home, scope),
        KeyBinding::new("end", End, scope),
        KeyBinding::new("shift-home", SelectHome, scope),
        KeyBinding::new("shift-end", SelectEnd, scope),
        KeyBinding::new("ctrl-home", DocStart, scope),
        KeyBinding::new("ctrl-end", DocEnd, scope),
        KeyBinding::new("pageup", PageUp, scope),
        KeyBinding::new("pagedown", PageDown, scope),
        KeyBinding::new("ctrl-a", SelectAll, scope),
        KeyBinding::new("ctrl-v", Paste, scope),
        KeyBinding::new("ctrl-x", Cut, scope),
        KeyBinding::new("ctrl-c", Copy, scope),
        KeyBinding::new("ctrl-z", Undo, scope),
        KeyBinding::new("ctrl-y", Redo, scope),
        KeyBinding::new("ctrl-shift-z", Redo, scope),
        KeyBinding::new("enter", Enter, scope),
        KeyBinding::new("shift-enter", NewLine, scope),
        KeyBinding::new("tab", Tab, scope),
        KeyBinding::new("ctrl-s", Save, scope),
        KeyBinding::new("escape", Escape, scope),
    ]
}

#[derive(Clone, Copy, PartialEq)]
pub enum EditorKind {
    Source,
    Message,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum EditorEvent {
    Changed,
    Selection,
    Submit,
    Save,
    Dismiss,
    AcceptSuggestion,
}

impl EventEmitter<EditorEvent> for Editor {}

const UNDO_DEPTH: usize = 400;
const GUTTER: f32 = 46.;

struct Row {
    start: usize,
    text: String,
    wrapped: WrappedLine,
    y: f32,
    height: f32,
}

pub struct Editor {
    pub focus_handle: FocusHandle,
    pub content: String,
    kind: EditorKind,
    placeholder: SharedString,
    selection: Range<usize>,
    reversed: bool,
    marked: Option<Range<usize>>,
    goal_x: Option<f32>,
    rows: Vec<Row>,
    shaped_width: f32,
    shaped_font: f32,
    pub content_height: f32,
    scroll: f32,
    viewport: Bounds<Pixels>,
    selecting: bool,
    reveal: bool,
    undo: Vec<(String, Range<usize>)>,
    redo: Vec<(String, Range<usize>)>,
    last_edit: Option<Instant>,
}

impl Editor {
    pub fn new(kind: EditorKind, placeholder: &str, cx: &mut Context<Self>) -> Self {
        Self {
            focus_handle: cx.focus_handle(),
            content: String::new(),
            kind,
            placeholder: placeholder.to_owned().into(),
            selection: 0..0,
            reversed: false,
            marked: None,
            goal_x: None,
            rows: Vec::new(),
            shaped_width: 0.,
            shaped_font: 0.,
            content_height: 0.,
            scroll: 0.,
            viewport: Bounds::default(),
            selecting: false,
            reveal: false,
            undo: Vec::new(),
            redo: Vec::new(),
            last_edit: None,
        }
    }

    pub fn set_placeholder(&mut self, placeholder: &str, cx: &mut Context<Self>) {
        self.placeholder = placeholder.to_owned().into();
        cx.notify();
    }

    /// Text the author accepted into the box: it is an edit they can undo, with the caret at its end.
    pub fn accept(&mut self, text: &str, cx: &mut Context<Self>) {
        self.selection = 0..self.content.len();
        self.last_edit = None;
        self.insert(text, cx);
    }

    /// Replace the whole text, as when a file is opened. History starts over with it.
    pub fn load(&mut self, content: String, cx: &mut Context<Self>) {
        self.content = content;
        self.selection = 0..0;
        self.reversed = false;
        self.marked = None;
        self.goal_x = None;
        self.scroll = 0.;
        self.undo.clear();
        self.redo.clear();
        self.last_edit = None;
        cx.notify();
    }

    /// The 1-based source lines the selection covers, when anything is selected.
    pub fn selected_lines(&self) -> Option<(usize, usize)> {
        if self.selection.is_empty() {
            return None;
        }
        let line = |offset: usize| self.content[..offset].matches('\n').count() + 1;
        Some((line(self.selection.start), line(self.selection.end)))
    }

    fn line_height(&self) -> f32 {
        match self.kind {
            EditorKind::Source => 22.,
            EditorKind::Message => 20.,
        }
    }

    fn font_size(&self) -> f32 {
        match self.kind {
            EditorKind::Source => 13.5,
            EditorKind::Message => 13.,
        }
    }

    fn cursor(&self) -> usize {
        if self.reversed {
            self.selection.start
        } else {
            self.selection.end
        }
    }

    fn move_to(&mut self, offset: usize, cx: &mut Context<Self>) {
        self.selection = offset..offset;
        self.reversed = false;
        self.reveal = true;
        cx.emit(EditorEvent::Selection);
        cx.notify();
    }

    fn select_to(&mut self, offset: usize, cx: &mut Context<Self>) {
        if self.reversed {
            self.selection.start = offset;
        } else {
            self.selection.end = offset;
        }
        if self.selection.end < self.selection.start {
            self.reversed = !self.reversed;
            self.selection = self.selection.end..self.selection.start;
        }
        self.reveal = true;
        cx.emit(EditorEvent::Selection);
        cx.notify();
    }

    fn previous_boundary(&self, offset: usize) -> usize {
        self.content[..offset]
            .grapheme_indices(true)
            .next_back()
            .map_or(0, |(index, _)| index)
    }

    fn next_boundary(&self, offset: usize) -> usize {
        self.content[offset..]
            .grapheme_indices(true)
            .nth(1)
            .map_or(self.content.len(), |(index, _)| offset + index)
    }

    fn previous_word(&self, offset: usize) -> usize {
        self.content[..offset]
            .split_word_bound_indices()
            .rev()
            .find(|(_, word)| !word.trim().is_empty())
            .map_or(0, |(index, _)| index)
    }

    fn next_word(&self, offset: usize) -> usize {
        let mut position = offset;
        for (index, word) in self.content[offset..].split_word_bound_indices() {
            position = offset + index + word.len();
            if !word.trim().is_empty() {
                break;
            }
        }
        position
    }

    fn line_start(&self, offset: usize) -> usize {
        self.content[..offset].rfind('\n').map_or(0, |index| index + 1)
    }

    fn line_end(&self, offset: usize) -> usize {
        self.content[offset..]
            .find('\n')
            .map_or(self.content.len(), |index| offset + index)
    }

    fn row_for_offset(&self, offset: usize) -> Option<&Row> {
        let index = self.rows.partition_point(|row| row.start <= offset);
        self.rows.get(index.checked_sub(1)?)
    }

    fn position_for_offset(&self, offset: usize) -> Option<(f32, f32)> {
        let row = self.row_for_offset(offset)?;
        let local = (offset - row.start).min(row.text.len());
        let position = row.wrapped.position_for_index(local, px(self.line_height()))?;
        Some((f32::from(position.x), row.y + f32::from(position.y)))
    }

    fn offset_for_position(&self, x: f32, y: f32) -> usize {
        if self.rows.is_empty() {
            return 0;
        }
        let index = self.rows.partition_point(|row| row.y <= y).max(1) - 1;
        let row = &self.rows[index];
        let local = point(px(x.max(0.)), px((y - row.y).max(0.)));
        let found = match row.wrapped.closest_index_for_position(local, px(self.line_height())) {
            Ok(found) | Err(found) => found,
        };
        row.start + found.min(row.text.len())
    }

    fn text_left(&self) -> f32 {
        match self.kind {
            EditorKind::Source => GUTTER + 8.,
            EditorKind::Message => 0.,
        }
    }

    fn offset_for_mouse(&self, position: Point<Pixels>) -> usize {
        let x = f32::from(position.x - self.viewport.left()) - self.text_left();
        let y = f32::from(position.y - self.viewport.top()) + self.scroll;
        self.offset_for_position(x, y)
    }

    /// Up and down keep the column the caret started from, across wrapped rows too.
    fn vertical(&mut self, rows: f32, cx: &mut Context<Self>) -> usize {
        let Some((x, y)) = self.position_for_offset(self.cursor()) else {
            return self.cursor();
        };
        let goal = *self.goal_x.get_or_insert(x);
        let target = y + rows * self.line_height() + self.line_height() / 2.;
        cx.notify();
        if target < 0. {
            return 0;
        }
        if target > self.content_height {
            return self.content.len();
        }
        self.offset_for_position(goal, target)
    }

    fn record(&mut self) {
        let now = Instant::now();
        // Quick typing undoes as one step, the way editors usually group it.
        let grouped = self
            .last_edit
            .is_some_and(|last| now.duration_since(last) < Duration::from_millis(600));
        if !grouped {
            self.undo.push((self.content.clone(), self.selection.clone()));
            if self.undo.len() > UNDO_DEPTH {
                self.undo.remove(0);
            }
        }
        self.redo.clear();
        self.last_edit = Some(now);
    }

    fn insert(&mut self, text: &str, cx: &mut Context<Self>) {
        self.record();
        let range = self.marked.take().unwrap_or(self.selection.clone());
        self.content.replace_range(range.clone(), text);
        let end = range.start + text.len();
        self.selection = end..end;
        self.reversed = false;
        self.goal_x = None;
        self.reveal = true;
        cx.emit(EditorEvent::Changed);
        cx.notify();
    }

    fn step(&mut self, offset: usize, select: bool, cx: &mut Context<Self>) {
        if select {
            self.select_to(offset, cx);
        } else {
            self.move_to(offset, cx);
        }
    }

    fn on_mouse_down(&mut self, event: &MouseDownEvent, window: &mut Window, cx: &mut Context<Self>) {
        window.focus(&self.focus_handle, cx);
        self.selecting = true;
        self.goal_x = None;
        let offset = self.offset_for_mouse(event.position);
        match event.click_count {
            2 => {
                let line = self.line_start(offset);
                let text = &self.content[line..self.line_end(offset)];
                if let Some((index, word)) = text
                    .split_word_bound_indices()
                    .find(|(index, word)| offset - line < index + word.len())
                {
                    self.selection = line + index..line + index + word.len();
                    self.reversed = false;
                    cx.emit(EditorEvent::Selection);
                    cx.notify();
                }
            }
            3 => {
                self.selection = self.line_start(offset)..self.line_end(offset);
                self.reversed = false;
                cx.emit(EditorEvent::Selection);
                cx.notify();
            }
            _ if event.modifiers.shift => self.select_to(offset, cx),
            _ => self.move_to(offset, cx),
        }
    }

    fn on_mouse_up(&mut self, _: &MouseUpEvent, _: &mut Window, _: &mut Context<Self>) {
        self.selecting = false;
    }

    fn on_mouse_move(&mut self, event: &MouseMoveEvent, _: &mut Window, cx: &mut Context<Self>) {
        if self.selecting && event.dragging() {
            self.select_to(self.offset_for_mouse(event.position), cx);
        }
    }

    fn on_scroll(&mut self, event: &ScrollWheelEvent, _: &mut Window, cx: &mut Context<Self>) {
        let delta = f32::from(event.delta.pixel_delta(px(self.line_height())).y);
        let limit = (self.content_height - f32::from(self.viewport.size.height)).max(0.);
        self.scroll = (self.scroll - delta).clamp(0., limit);
        cx.notify();
    }

    fn utf8_from_utf16(&self, offset: usize) -> usize {
        let (mut utf8, mut utf16) = (0, 0);
        for ch in self.content.chars() {
            if utf16 >= offset {
                break;
            }
            utf16 += ch.len_utf16();
            utf8 += ch.len_utf8();
        }
        utf8
    }

    fn utf16_from_utf8(&self, offset: usize) -> usize {
        self.content[..offset.min(self.content.len())].encode_utf16().count()
    }

    fn range_from_utf16(&self, range: &Range<usize>) -> Range<usize> {
        self.utf8_from_utf16(range.start)..self.utf8_from_utf16(range.end)
    }

    fn range_to_utf16(&self, range: &Range<usize>) -> Range<usize> {
        self.utf16_from_utf8(range.start)..self.utf16_from_utf8(range.end)
    }

    /// Shape lines for this width, keeping every line whose text and width have not changed.
    fn layout(&mut self, width: f32, palette: &Palette, window: &Window) {
        let font_size = self.font_size();
        let reuse = self.shaped_width == width && self.shaped_font == font_size;
        let mut cache: HashMap<String, WrappedLine> = HashMap::new();
        if reuse {
            for row in self.rows.drain(..) {
                cache.insert(row.text, row.wrapped);
            }
        } else {
            self.rows.clear();
        }
        self.shaped_width = width;
        self.shaped_font = font_size;
        let line_height = self.line_height();
        let (mut y, mut start) = (0., 0);
        let kind = self.kind;
        let mut rows = Vec::new();
        for line in self.content.split('\n') {
            let wrapped = cache.remove(line).or_else(|| {
                let runs = text_runs(line, kind, palette);
                window
                    .text_system()
                    .shape_text(line.to_owned().into(), px(font_size), &runs, Some(px(width)), None)
                    .ok()
                    .and_then(|mut lines| lines.pop())
            });
            let Some(wrapped) = wrapped else {
                start += line.len() + 1;
                continue;
            };
            let height = f32::from(wrapped.size(px(line_height)).height).max(line_height);
            rows.push(Row {
                start,
                text: line.to_owned(),
                wrapped,
                y,
                height,
            });
            y += height;
            start += line.len() + 1;
        }
        self.rows = rows;
        self.content_height = y;
    }
}

fn base_font(kind: EditorKind) -> Font {
    Font {
        family: match kind {
            EditorKind::Source => MONO,
            EditorKind::Message => SANS,
        }
        .into(),
        features: FontFeatures::default(),
        fallbacks: None,
        weight: FontWeight::NORMAL,
        style: FontStyle::Normal,
    }
}

/// LaTeX colouring for one line: commands, comments and math. A language server is not involved,
/// so this only ever changes colour and can never change what the file says.
fn text_runs(line: &str, kind: EditorKind, palette: &Palette) -> Vec<TextRun> {
    let run = |len: usize, color: Hsla| TextRun {
        len,
        font: base_font(kind),
        color,
        background_color: None,
        underline: None,
        strikethrough: None,
    };
    if kind == EditorKind::Message || line.is_empty() {
        return vec![run(line.len().max(1), palette.ink.into())];
    }
    let (ink, command, comment, math): (Hsla, Hsla, Hsla, Hsla) = (
        palette.ink.into(),
        palette.accent.into(),
        palette.muted.into(),
        palette.attention.into(),
    );
    let bytes = line.as_bytes();
    let mut runs = Vec::new();
    let (mut index, mut plain, mut in_math) = (0, 0, false);
    while index < bytes.len() {
        match bytes[index] {
            b'%' => {
                if index > plain {
                    runs.push(run(index - plain, if in_math { math } else { ink }));
                }
                runs.push(run(bytes.len() - index, comment));
                return runs;
            }
            b'\\' => {
                let mut end = index + 1;
                while end < bytes.len() && bytes[end].is_ascii_alphabetic() {
                    end += 1;
                }
                if end == index + 1 && end < bytes.len() {
                    end = index + 1 + line[end..].chars().next().map_or(0, char::len_utf8);
                }
                if index > plain {
                    runs.push(run(index - plain, if in_math { math } else { ink }));
                }
                runs.push(run(end - index, command));
                index = end;
                plain = end;
            }
            b'$' => {
                if index > plain {
                    runs.push(run(index - plain, if in_math { math } else { ink }));
                }
                runs.push(run(1, math));
                in_math = !in_math;
                index += 1;
                plain = index;
            }
            _ => index += 1,
        }
    }
    if bytes.len() > plain {
        runs.push(run(bytes.len() - plain, if in_math { math } else { ink }));
    }
    runs
}

impl EntityInputHandler for Editor {
    fn text_for_range(
        &mut self,
        range: Range<usize>,
        actual: &mut Option<Range<usize>>,
        _: &mut Window,
        _: &mut Context<Self>,
    ) -> Option<String> {
        let range = self.range_from_utf16(&range);
        actual.replace(self.range_to_utf16(&range));
        Some(self.content[range].to_owned())
    }

    fn selected_text_range(
        &mut self,
        _: bool,
        _: &mut Window,
        _: &mut Context<Self>,
    ) -> Option<UTF16Selection> {
        Some(UTF16Selection {
            range: self.range_to_utf16(&self.selection),
            reversed: self.reversed,
        })
    }

    fn marked_text_range(&self, _: &mut Window, _: &mut Context<Self>) -> Option<Range<usize>> {
        self.marked.as_ref().map(|range| self.range_to_utf16(range))
    }

    fn unmark_text(&mut self, _: &mut Window, _: &mut Context<Self>) {
        self.marked = None;
    }

    fn replace_text_in_range(
        &mut self,
        range: Option<Range<usize>>,
        text: &str,
        _: &mut Window,
        cx: &mut Context<Self>,
    ) {
        if let Some(range) = range {
            let range = self.range_from_utf16(&range);
            self.selection = range;
            self.marked = None;
        }
        self.insert(text, cx);
    }

    fn replace_and_mark_text_in_range(
        &mut self,
        range: Option<Range<usize>>,
        text: &str,
        selected: Option<Range<usize>>,
        _: &mut Window,
        cx: &mut Context<Self>,
    ) {
        let range = range
            .as_ref()
            .map(|range| self.range_from_utf16(range))
            .or(self.marked.clone())
            .unwrap_or(self.selection.clone());
        if self.marked.is_none() {
            self.record();
        }
        self.content.replace_range(range.clone(), text);
        self.marked = (!text.is_empty()).then(|| range.start..range.start + text.len());
        self.selection = selected
            .map(|within| {
                let local = text.encode_utf16().collect::<Vec<_>>();
                let bytes = |units: usize| {
                    String::from_utf16_lossy(&local[..units.min(local.len())]).len()
                };
                range.start + bytes(within.start)..range.start + bytes(within.end)
            })
            .unwrap_or(range.start + text.len()..range.start + text.len());
        self.reveal = true;
        cx.emit(EditorEvent::Changed);
        cx.notify();
    }

    fn bounds_for_range(
        &mut self,
        range: Range<usize>,
        bounds: Bounds<Pixels>,
        _: &mut Window,
        _: &mut Context<Self>,
    ) -> Option<Bounds<Pixels>> {
        let range = self.range_from_utf16(&range);
        let (x0, y0) = self.position_for_offset(range.start)?;
        let (x1, _) = self.position_for_offset(range.end)?;
        let left = bounds.left() + px(self.text_left() + x0);
        let top = bounds.top() + px(y0 - self.scroll);
        Some(Bounds::new(
            point(left, top),
            size(px((x1 - x0).max(1.)), px(self.line_height())),
        ))
    }

    fn character_index_for_point(
        &mut self,
        position: Point<Pixels>,
        _: &mut Window,
        _: &mut Context<Self>,
    ) -> Option<usize> {
        Some(self.utf16_from_utf8(self.offset_for_mouse(position)))
    }
}

struct EditorElement {
    editor: Entity<Editor>,
}

struct Prepaint {
    palette: Palette,
    numbers: Vec<(f32, gpui::ShapedLine)>,
}

impl IntoElement for EditorElement {
    type Element = Self;
    fn into_element(self) -> Self {
        self
    }
}

impl Element for EditorElement {
    type RequestLayoutState = ();
    type PrepaintState = Prepaint;

    fn id(&self) -> Option<ElementId> {
        None
    }

    fn source_location(&self) -> Option<&'static core::panic::Location<'static>> {
        None
    }

    fn request_layout(
        &mut self,
        _: Option<&GlobalElementId>,
        _: Option<&InspectorElementId>,
        window: &mut Window,
        cx: &mut App,
    ) -> (LayoutId, ()) {
        let mut style = Style::default();
        style.size.width = relative(1.).into();
        style.size.height = relative(1.).into();
        (window.request_layout(style, [], cx), ())
    }

    fn prepaint(
        &mut self,
        _: Option<&GlobalElementId>,
        _: Option<&InspectorElementId>,
        bounds: Bounds<Pixels>,
        _: &mut (),
        window: &mut Window,
        cx: &mut App,
    ) -> Prepaint {
        let palette = Palette::for_appearance(window.appearance());
        let numbers = self.editor.update(cx, |editor, _| {
            editor.viewport = bounds;
            let width = (f32::from(bounds.size.width) - editor.text_left() - 10.).max(40.);
            editor.layout(width, &palette, window);
            let height = f32::from(bounds.size.height);
            let line = editor.line_height();
            if editor.reveal {
                editor.reveal = false;
                if let Some((_, y)) = editor.position_for_offset(editor.cursor()) {
                    if y < editor.scroll {
                        editor.scroll = y;
                    } else if y + line > editor.scroll + height {
                        editor.scroll = y + line - height;
                    }
                }
            }
            editor.scroll = editor.scroll.clamp(0., (editor.content_height - height).max(0.));
            if editor.kind != EditorKind::Source {
                return Vec::new();
            }
            let mut numbers = Vec::new();
            let font = base_font(EditorKind::Source);
            for (index, row) in editor.rows.iter().enumerate() {
                if row.y + row.height < editor.scroll {
                    continue;
                }
                if row.y > editor.scroll + height {
                    break;
                }
                let label: SharedString = (index + 1).to_string().into();
                let run = TextRun {
                    len: label.len(),
                    font: font.clone(),
                    color: palette.muted.into(),
                    background_color: None,
                    underline: None,
                    strikethrough: None,
                };
                let shaped = window.text_system().shape_line(label, px(11.5), &[run], None);
                numbers.push((row.y, shaped));
            }
            numbers
        });
        Prepaint { palette, numbers }
    }

    fn paint(
        &mut self,
        _: Option<&GlobalElementId>,
        _: Option<&InspectorElementId>,
        bounds: Bounds<Pixels>,
        _: &mut (),
        prepaint: &mut Prepaint,
        window: &mut Window,
        cx: &mut App,
    ) {
        let focus = self.editor.read(cx).focus_handle.clone();
        window.handle_input(&focus, ElementInputHandler::new(bounds, self.editor.clone()), cx);
        let palette = &prepaint.palette;
        let editor = self.editor.read(cx);
        let line = editor.line_height();
        let left = bounds.left() + px(editor.text_left());
        let scroll = editor.scroll;
        let height = f32::from(bounds.size.height);
        let selection = editor.selection.clone();
        let marked = editor.marked.clone();
        let cursor = editor.cursor();
        let is_source = editor.kind == EditorKind::Source;
        let empty = editor.content.is_empty();
        let placeholder = editor.placeholder.clone();
        let kind = editor.kind;
        let font_size = editor.font_size();
        let focused = focus.is_focused(window);

        let mut quads = Vec::new();
        let band = |row: &Row, from: usize, to: usize, color: gpui::Rgba, quads: &mut Vec<gpui::PaintQuad>| {
            let (Some(a), Some(b)) = (
                row.wrapped.position_for_index(from, px(line)),
                row.wrapped.position_for_index(to, px(line)),
            ) else {
                return;
            };
            let (ay, by) = (f32::from(a.y), f32::from(b.y));
            let top = |y: f32| bounds.top() + px(row.y + y - scroll);
            let width = f32::from(row.wrapped.width()).max(4.);
            if ay == by {
                let right = f32::from(b.x).max(f32::from(a.x) + 3.);
                quads.push(fill(
                    Bounds::new(point(left + a.x, top(ay)), size(px(right) - a.x, px(line))),
                    color,
                ));
                return;
            }
            quads.push(fill(
                Bounds::new(point(left + a.x, top(ay)), size(px(width) - a.x, px(line))),
                color,
            ));
            let mut y = ay + line;
            while y < by {
                quads.push(fill(Bounds::new(point(left, top(y)), size(px(width), px(line))), color));
                y += line;
            }
            quads.push(fill(Bounds::new(point(left, top(by)), size(b.x, px(line))), color));
        };
        let mut caret = None;
        for row in &editor.rows {
            if row.y + row.height < scroll || row.y > scroll + height {
                continue;
            }
            let end = row.start + row.text.len();
            if !selection.is_empty() && selection.start <= end && selection.end >= row.start {
                band(
                    row,
                    selection.start.max(row.start) - row.start,
                    selection.end.min(end) - row.start,
                    palette.accent_soft,
                    &mut quads,
                );
            }
            if let Some(marked) = &marked
                && marked.start <= end
                && marked.end >= row.start
                && let (Some(a), Some(b)) = (
                    row.wrapped.position_for_index(marked.start.max(row.start) - row.start, px(line)),
                    row.wrapped.position_for_index(marked.end.min(end) - row.start, px(line)),
                )
            {
                quads.push(fill(
                    Bounds::new(
                        point(left + a.x, bounds.top() + px(row.y - scroll) + b.y + px(line - 2.)),
                        size(b.x - a.x, px(1.)),
                    ),
                    palette.ink,
                ));
            }
            if selection.is_empty()
                && cursor >= row.start
                && cursor <= end
                && let Some(position) = row.wrapped.position_for_index(cursor - row.start, px(line))
            {
                caret = Some(Bounds::new(
                    point(left + position.x, bounds.top() + px(row.y - scroll) + position.y),
                    size(px(1.5), px(line)),
                ));
            }
        }
        let rows_view = window.with_content_mask(Some(gpui::ContentMask { bounds }), |window| {
            for quad in quads {
                window.paint_quad(quad);
            }
            let rows = self.editor.update(cx, |editor, _| std::mem::take(&mut editor.rows));
            for row in rows.iter().filter(|row| row.y + row.height >= scroll && row.y <= scroll + height) {
                let origin = point(left, bounds.top() + px(row.y - scroll));
                let _ = row.wrapped.paint(origin, px(line), TextAlign::Left, None, window, cx);
            }
            self.editor.update(cx, |editor, _| editor.rows = rows);
            if is_source {
                for (y, number) in prepaint.numbers.drain(..) {
                    let x = bounds.left() + px(GUTTER - 8.) - number.width();
                    let _ = number.paint(
                        point(x, bounds.top() + px(y - scroll)),
                        px(line),
                        TextAlign::Left,
                        None,
                        window,
                        cx,
                    );
                }
            }
            if empty && !placeholder.is_empty() {
                let run = TextRun {
                    len: placeholder.len(),
                    font: base_font(kind),
                    color: palette.muted.into(),
                    background_color: None,
                    underline: None,
                    strikethrough: None,
                };
                let shaped = window.text_system().shape_line(placeholder.clone(), px(font_size), &[run], None);
                let _ = shaped.paint(point(left, bounds.top()), px(line), TextAlign::Left, None, window, cx);
            }
            if focused && let Some(caret) = caret {
                window.paint_quad(fill(caret, palette.ink));
            }
        });
        let () = rows_view;
    }
}

impl Render for Editor {
    fn render(&mut self, _: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        let entity = cx.entity();
        div()
            .id("lemfolio-editor")
            .key_context("LemfolioEditor")
            .track_focus(&self.focus_handle)
            .role(gpui::Role::TextInput)
            .size_full()
            .cursor(CursorStyle::IBeam)
            .on_action(cx.listener(|this, _: &Backspace, _, cx| {
                if this.selection.is_empty() {
                    let to = this.previous_boundary(this.cursor());
                    if to == this.cursor() {
                        return;
                    }
                    this.selection = to..this.selection.end;
                }
                this.insert("", cx);
            }))
            .on_action(cx.listener(|this, _: &Delete, _, cx| {
                if this.selection.is_empty() {
                    let to = this.next_boundary(this.cursor());
                    if to == this.cursor() {
                        return;
                    }
                    this.selection = this.selection.start..to;
                }
                this.insert("", cx);
            }))
            .on_action(cx.listener(|this, _: &Left, _, cx| {
                this.goal_x = None;
                let to = if this.selection.is_empty() { this.previous_boundary(this.cursor()) } else { this.selection.start };
                this.move_to(to, cx);
            }))
            .on_action(cx.listener(|this, _: &Right, _, cx| {
                this.goal_x = None;
                let to = if this.selection.is_empty() { this.next_boundary(this.cursor()) } else { this.selection.end };
                this.move_to(to, cx);
            }))
            .on_action(cx.listener(|this, _: &SelectLeft, _, cx| {
                this.goal_x = None;
                this.step(this.previous_boundary(this.cursor()), true, cx);
            }))
            .on_action(cx.listener(|this, _: &SelectRight, _, cx| {
                this.goal_x = None;
                this.step(this.next_boundary(this.cursor()), true, cx);
            }))
            .on_action(cx.listener(|this, _: &Up, _, cx| {
                let to = this.vertical(-1., cx);
                let goal = this.goal_x;
                this.move_to(to, cx);
                this.goal_x = goal;
            }))
            .on_action(cx.listener(|this, _: &Down, _, cx| {
                let to = this.vertical(1., cx);
                let goal = this.goal_x;
                this.move_to(to, cx);
                this.goal_x = goal;
            }))
            .on_action(cx.listener(|this, _: &SelectUp, _, cx| {
                let to = this.vertical(-1., cx);
                this.select_to(to, cx);
            }))
            .on_action(cx.listener(|this, _: &SelectDown, _, cx| {
                let to = this.vertical(1., cx);
                this.select_to(to, cx);
            }))
            .on_action(cx.listener(|this, _: &WordLeft, _, cx| {
                this.goal_x = None;
                this.step(this.previous_word(this.cursor()), false, cx);
            }))
            .on_action(cx.listener(|this, _: &WordRight, _, cx| {
                this.goal_x = None;
                this.step(this.next_word(this.cursor()), false, cx);
            }))
            .on_action(cx.listener(|this, _: &SelectWordLeft, _, cx| {
                this.goal_x = None;
                this.step(this.previous_word(this.cursor()), true, cx);
            }))
            .on_action(cx.listener(|this, _: &SelectWordRight, _, cx| {
                this.goal_x = None;
                this.step(this.next_word(this.cursor()), true, cx);
            }))
            .on_action(cx.listener(|this, _: &Home, _, cx| {
                this.goal_x = None;
                this.step(this.line_start(this.cursor()), false, cx);
            }))
            .on_action(cx.listener(|this, _: &End, _, cx| {
                this.goal_x = None;
                this.step(this.line_end(this.cursor()), false, cx);
            }))
            .on_action(cx.listener(|this, _: &SelectHome, _, cx| {
                this.goal_x = None;
                this.step(this.line_start(this.cursor()), true, cx);
            }))
            .on_action(cx.listener(|this, _: &SelectEnd, _, cx| {
                this.goal_x = None;
                this.step(this.line_end(this.cursor()), true, cx);
            }))
            .on_action(cx.listener(|this, _: &DocStart, _, cx| this.move_to(0, cx)))
            .on_action(cx.listener(|this, _: &DocEnd, _, cx| this.move_to(this.content.len(), cx)))
            .on_action(cx.listener(|this, _: &PageUp, _, cx| {
                let rows = -(f32::from(this.viewport.size.height) / this.line_height()).floor();
                let to = this.vertical(rows, cx);
                this.move_to(to, cx);
            }))
            .on_action(cx.listener(|this, _: &PageDown, _, cx| {
                let rows = (f32::from(this.viewport.size.height) / this.line_height()).floor();
                let to = this.vertical(rows, cx);
                this.move_to(to, cx);
            }))
            .on_action(cx.listener(|this, _: &SelectAll, _, cx| {
                this.selection = 0..this.content.len();
                this.reversed = false;
                cx.emit(EditorEvent::Selection);
                cx.notify();
            }))
            .on_action(cx.listener(|this, _: &Paste, _, cx| {
                if let Some(text) = cx.read_from_clipboard().and_then(|item| item.text()) {
                    this.last_edit = None;
                    this.insert(&text.replace("\r\n", "\n"), cx);
                    this.last_edit = None;
                }
            }))
            .on_action(cx.listener(|this, _: &Copy, _, cx| {
                if !this.selection.is_empty() {
                    cx.write_to_clipboard(ClipboardItem::new_string(this.content[this.selection.clone()].to_owned()));
                }
            }))
            .on_action(cx.listener(|this, _: &Cut, _, cx| {
                if !this.selection.is_empty() {
                    cx.write_to_clipboard(ClipboardItem::new_string(this.content[this.selection.clone()].to_owned()));
                    this.last_edit = None;
                    this.insert("", cx);
                }
            }))
            .on_action(cx.listener(|this, _: &Undo, _, cx| {
                if let Some((content, selection)) = this.undo.pop() {
                    this.redo.push((std::mem::replace(&mut this.content, content), this.selection.clone()));
                    this.selection = selection;
                    this.marked = None;
                    this.last_edit = None;
                    this.reveal = true;
                    cx.emit(EditorEvent::Changed);
                    cx.notify();
                }
            }))
            .on_action(cx.listener(|this, _: &Redo, _, cx| {
                if let Some((content, selection)) = this.redo.pop() {
                    this.undo.push((std::mem::replace(&mut this.content, content), this.selection.clone()));
                    this.selection = selection;
                    this.marked = None;
                    this.last_edit = None;
                    this.reveal = true;
                    cx.emit(EditorEvent::Changed);
                    cx.notify();
                }
            }))
            // Enter and Esc reach here only outside a composition; while composing they belong to the IME.
            .on_action(cx.listener(|this, _: &Enter, _, cx| {
                if this.marked.is_some() {
                    return;
                }
                match this.kind {
                    EditorKind::Source => {
                        this.last_edit = None;
                        this.insert("\n", cx);
                    }
                    EditorKind::Message => cx.emit(EditorEvent::Submit),
                }
            }))
            .on_action(cx.listener(|this, _: &NewLine, _, cx| {
                if this.marked.is_none() {
                    this.last_edit = None;
                    this.insert("\n", cx);
                }
            }))
            .on_action(cx.listener(|this, _: &Tab, _, cx| {
                if this.kind == EditorKind::Source {
                    this.insert("  ", cx);
                } else if this.content.is_empty() && this.marked.is_none() {
                    cx.emit(EditorEvent::AcceptSuggestion);
                } else {
                    cx.propagate();
                }
            }))
            .on_action(cx.listener(|_, _: &Save, _, cx| cx.emit(EditorEvent::Save)))
            .on_action(cx.listener(|this, _: &Escape, _, cx| {
                if this.marked.is_none() {
                    cx.emit(EditorEvent::Dismiss);
                }
            }))
            .on_mouse_down(MouseButton::Left, cx.listener(Self::on_mouse_down))
            .on_mouse_up(MouseButton::Left, cx.listener(Self::on_mouse_up))
            .on_mouse_up_out(MouseButton::Left, cx.listener(Self::on_mouse_up))
            .on_mouse_move(cx.listener(Self::on_mouse_move))
            .on_scroll_wheel(cx.listener(Self::on_scroll))
            .child(EditorElement { editor: entity })
    }
}

impl Focusable for Editor {
    fn focus_handle(&self, _: &App) -> FocusHandle {
        self.focus_handle.clone()
    }
}

"""DOM-anchored wireframe crawler. Every collection event contains real page data."""
from __future__ import annotations

import math
import time
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

PALETTE = ('#64ecff', '#ff4eac', '#9882ff', '#ffd884')


@dataclass
class Target:
    key: str
    text: str
    url: str
    kind: str
    rect: tuple[float, float, float, float]

    @property
    def center(self):
        x, y, width, height = self.rect
        return x + width / 2, y + height / 2


def parse_targets(payload, width, height):
    """Validate untrusted DOM geometry before using it for drawing/collection."""
    targets = []
    seen = set()
    if not isinstance(payload, list):
        return targets
    for item in payload[:120]:
        if not isinstance(item, dict):
            continue
        rect = item.get('rect')
        if not isinstance(rect, list) or len(rect) != 4:
            continue
        try:
            x, y, w, h = map(float, rect)
        except (TypeError, ValueError):
            continue
        if not all(math.isfinite(v) for v in (x, y, w, h)) or w < 3 or h < 3:
            continue
        if x >= width or y >= height or x + w <= 0 or y + h <= 0:
            continue
        text = str(item.get('text') or '').strip()[:350]
        url = str(item.get('url') or '')[:4096]
        kind = item.get('kind')
        if kind not in {'link', 'text'} or not text or not url.startswith(('http://', 'https://')):
            continue
        key = kind + '|' + url + '|' + text
        if key in seen:
            continue
        seen.add(key)
        left, top = max(0, x), max(0, y)
        clipped = (left, top, min(width, x + w) - left, min(height, y + h) - top)
        targets.append(Target(key, text, url, kind, clipped))
    return targets


class SpiderOverlay(QWidget):
    collected = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.active = False
        self.targets = []
        self.seen = set()
        self.current = None
        self.body = None
        self.angle = 0.0
        self.feet = []
        self.packets = []
        self.count = 0
        self.contact = 0.0
        self.previous = time.monotonic()
        self.clock = 0.0
        self.timer = QTimer(self)
        self.timer.setInterval(33)
        self.timer.timeout.connect(self.animate)

    def reset_page(self):
        self.targets = []
        self.current = None
        self.body = None
        self.feet = []
        self.packets = []
        self.contact = 0.0

    def reset_session(self):
        self.reset_page()
        self.seen.clear()
        self.count = 0

    def set_targets(self, payload):
        self.targets = parse_targets(payload, self.width(), self.height())
        if self.current:
            updated = next((target for target in self.targets if target.key == self.current.key), None)
            if updated:
                self.current = updated
            else:
                self.current = None
                self.contact = 0.0

    def set_active(self, active):
        self.active = active
        self.previous = time.monotonic()
        self.setVisible(active)
        if active:
            self.timer.start()
        else:
            self.timer.stop()

    def advance(self, dt):
        """One bounded simulation step; deterministic and usable in tests."""
        dt = min(max(dt, 0), .08)
        self.clock += dt
        width, height = self.width(), self.height()
        if self.body is None:
            self.body = [width * .5, height * .55]
        self.packets = [packet for packet in self.packets if self.clock - packet['born'] < 1.8]
        if self.current is None:
            candidates = [target for target in self.targets if target.key not in self.seen]
            if candidates:
                # Spatial locality makes the crawler walk between nearby DOM nodes.
                self.current = min(candidates, key=lambda target: math.dist(self.body, target.center))
        if self.current:
            tx, ty = self.current.center
            gx = min(max(tx, 65), max(65, width - 65))
            gy = min(max(ty + 65, 70), max(70, height - 70))
        else:
            gx = width * .5 + math.sin(self.clock * .35) * width * .15
            gy = height * .5 + math.cos(self.clock * .45) * height * .16
        dx, dy = gx - self.body[0], gy - self.body[1]
        distance = math.hypot(dx, dy)
        if distance > 2:
            travel = min(distance, dt * 210)
            self.body[0] += dx / distance * travel
            self.body[1] += dy / distance * travel
            desired = math.atan2(dy, dx) + math.pi / 2
            delta = (desired - self.angle + math.pi) % (math.pi * 2) - math.pi
            self.angle += delta * min(1, dt * 5)
        if self.current and distance < 14:
            self.contact += dt
            if self.contact >= .62:
                target = self.current
                self.seen.add(target.key)
                self.count += 1
                self.packets.append({'target': target, 'born': self.clock, 'origin': target.center})
                self.current = None
                self.contact = 0.0
                self.collected.emit({'text': target.text, 'url': target.url, 'kind': target.kind})
        else:
            self.contact = 0.0
        self.update_feet(dt)

    def local(self, x, y):
        c, s = math.cos(self.angle), math.sin(self.angle)
        return QPointF(self.body[0] + x * c - y * s, self.body[1] + x * s + y * c)

    def update_feet(self, dt):
        for index in range(8):
            side = -1 if index < 4 else 1
            leg = index % 4
            nominal = self.local(side * (65 + 12 * math.sin(leg + .6)), (leg - 1.5) * 38)
            if len(self.feet) <= index:
                self.feet.append(QPointF(nominal))
            phase = (self.clock * 1.4 + (index % 2) * .5) % 1
            target = nominal
            anchors = [node.center for node in self.targets]
            if anchors:
                anchor = min(anchors, key=lambda point: math.hypot(point[0] - nominal.x(), point[1] - nominal.y()))
                if math.hypot(anchor[0] - nominal.x(), anchor[1] - nominal.y()) < 65:
                    target = QPointF(*anchor)
            if index == 0 and self.current:
                target = QPointF(*self.current.center)
                step = min(1, dt * 9)
            else:
                too_far = math.hypot(self.feet[index].x() - nominal.x(), self.feet[index].y() - nominal.y()) > 95
                step = min(1, dt * 13) if phase < .35 or too_far else 0
            self.feet[index] += (target - self.feet[index]) * step

    def animate(self):
        now = time.monotonic()
        self.advance(now - self.previous)
        self.previous = now
        self.raise_()
        self.update()

    @staticmethod
    def line(painter, start, end, color, thickness=1):
        painter.setPen(QPen(QColor(color), thickness))
        painter.drawLine(start, end)

    def paintEvent(self, event):
        if not self.active or self.body is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(QFont('Consolas', 8))
        # Boxes correspond to current page geometry, not made-up coordinates.
        for index, target in enumerate(self.targets[:35]):
            color = QColor(PALETTE[index % len(PALETTE)])
            color.setAlpha(190 if self.current and target.key == self.current.key else 65)
            painter.setPen(QPen(color, 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(QRectF(*target.rect))
        if self.current:
            rect = QRectF(*self.current.rect)
            tint = QColor(PALETTE[self.count % len(PALETTE)])
            tint.setAlpha(85 + int(self.contact * 90))
            painter.fillRect(rect, tint)
            painter.setPen(QColor('#bbf4ff'))
            painter.drawText(rect.topLeft() + QPointF(0, -5), 'URL' if self.current.kind == 'link' else 'TEXT')
        # Eight articulated legs. The leading foot reaches the actual collection target.
        for index, foot in enumerate(self.feet):
            side = -1 if index < 4 else 1
            leg = index % 4
            shoulder = self.local(side * 7, -12 + leg * 8)
            bend = self.local(side * (30 + 4 * leg), -35 + leg * 22 + math.sin(self.clock * 5 + index) * 4)
            lower = QPointF(foot.x() * .68 + bend.x() * .32, foot.y() * .68 + bend.y() * .32 - 8)
            self.line(painter, shoulder, bend, '#abbedb', 1.2)
            self.line(painter, bend, lower, '#ff72a8', 1)
            self.line(painter, lower, foot, '#8fefff', 1)
            for node in (shoulder, bend, lower, foot):
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor('#d1faff'))
                painter.drawEllipse(node, 1.7, 1.7)
        # Transparent triangulated body rather than a filled icon or sprite.
        body = [self.local(0, -24), self.local(-8, -10), self.local(8, -10),
                self.local(-12, 13), self.local(12, 13), self.local(0, 34)]
        edges = ((0, 1), (0, 2), (1, 2), (1, 3), (2, 4), (3, 4), (3, 5), (4, 5), (1, 4), (2, 3), (0, 5))
        for index, (a, b) in enumerate(edges):
            self.line(painter, body[a], body[b], '#7bdcfc' if index % 3 else '#8c72ff', 1.1)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor('#ec75d9'))
        painter.drawEllipse(body[0], 2.8, 2.8)
        # Lift real captured fragments towards a small collection counter.
        destination = QPointF(max(100, self.width() - 85), 24)
        for index, packet in enumerate(self.packets):
            progress = min(1, (self.clock - packet['born']) / 1.8)
            start = QPointF(*packet['origin'])
            position = start * (1 - progress) + destination * progress
            position.setY(position.y() - math.sin(progress * math.pi) * 65)
            color = QColor(PALETTE[index % len(PALETTE)])
            color.setAlpha(int(230 * (1 - progress)))
            text = packet['target'].text[:34]
            rect = QRectF(position.x(), position.y() - 15, min(240, 12 + len(text) * 6), 20)
            painter.fillRect(rect, color)
            painter.setPen(QColor('#0a0e1a'))
            painter.drawText(rect.adjusted(5, 1, -3, -1), text)
            self.line(painter, self.local(0, -24), position, '#7784ba', .6)
        painter.fillRect(QRectF(self.width() - 160, 7, 150, 27), QColor(6, 13, 25, 210))
        painter.setPen(QColor('#7deaf2'))
        painter.drawText(QPointF(self.width() - 150, 25), f'CAPTURED / {self.count:03}')

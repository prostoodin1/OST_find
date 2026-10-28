# Wireframe verification — 2026-10-28

Version 0.5.0, Windows x64, PySide6 6.10.3.

- 16 automated tests passed, including controlled live DOM extraction, real collection after contact, one event per target, zero invented data without targets, geometry clipping and malformed/out-of-view target rejection.
- The source GUI and standalone EXE both performed the `python requests library` query: 30 findings, including seven actual DOM captures (links/text) plus two page metadata findings; no source errors in these runs.
- Windows UI inspection showed the wireframe body, eight articulated legs, selected DOM boxes, a highlighted real link and a lifted text packet reaching CAPTURED.
- The packaged terminal EXE reported version 0.5.0, completed an IP query and saved JSON/Markdown reports.
- Microsoft Defender custom scans of the standalone GUI, portable folder and CLI EXE produced no matching detections.
- The new JavaScript DOM scanner is bundled as an application resource; it reads visible page rectangles and text. Geometry is re-read during the tour and after scrolling.

The animation is a procedural 2D wireframe approximation of the supplied reference. The video's original implementation is not included. API search results are independent of the visual crawler; only Browser / DOM link and Browser / Text rows come from foot-contact events. Each browser visit has a time/capture budget and does not imply an exhaustive page crawl.

The user's reference video and extracted frames are local QA material and are not included in the source archive or release.

"""Read embedded image metadata and AI-generation provenance from image bytes."""

from __future__ import annotations

import re
import struct
from io import BytesIO
from typing import Any

from PIL import ExifTags, TiffImagePlugin
from PIL import Image as PillowImage

Row = dict[str, Any]

IPTC_SOURCE_TYPE_PREFIX = "http://cv.iptc.org/newscodes/digitalsourcetype/"
# IPTC digital source types that mean generative AI produced all or part of an image.
AI_SOURCE_TYPES = frozenset({"trainedAlgorithmicMedia", "compositeWithTrainedAlgorithmicMedia"})
# Tools whose name in Software/CreatorTool fields marks an image as AI-generated.
AI_GENERATOR_PATTERN = re.compile(
    r"midjourney|dall[\s·-]?e|stable[\s-]?diffusion|novelai|firefly|imagen|gemini|"
    r"gpt-image|chatgpt|comfyui|automatic1111|invokeai|ideogram|leonardo\.ai",
    re.IGNORECASE,
)
# PNG text keys written by local diffusion front ends (A1111/Forge, ComfyUI, InvokeAI).
AI_TEXT_KEYS = frozenset({"parameters", "prompt", "workflow", "invokeai_metadata", "sd-metadata", "dream"})
MAX_TEXT_CHARS = 1000


class CBORDecoder:
    """Decode the subset of CBOR (RFC 8949) used by C2PA manifests."""

    BREAK = object()

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.offset = 0

    @classmethod
    def decode(cls, data: bytes) -> Any:
        return cls(data).item()

    def _take(self, size: int) -> bytes:
        chunk = self.data[self.offset : self.offset + size]
        if len(chunk) != size:
            raise ValueError("Truncated CBOR data.")
        self.offset += size
        return chunk

    def _argument(self, info: int) -> int | None:
        if info < 24:
            return info
        if info in (24, 25, 26, 27):
            return int.from_bytes(self._take(1 << (info - 24)), "big")
        if info == 31:
            return None
        raise ValueError("Unsupported CBOR length.")

    def _until_break(self) -> list[Any]:
        items = []
        while (item := self.item()) is not self.BREAK:
            items.append(item)
        return items

    def item(self) -> Any:
        initial = self._take(1)[0]
        major, info = initial >> 5, initial & 0x1F
        if major == 7:
            if info == 31:
                return self.BREAK
            if info in (25, 26, 27):
                size = 1 << (info - 24)
                raw = self._take(size)
                return struct.unpack({2: ">e", 4: ">f", 8: ">d"}[size], raw)[0]
            return {20: False, 21: True, 22: None, 23: None}.get(info, info)
        argument = self._argument(info)
        if major == 0:
            return argument
        if major == 1:
            return -1 - argument
        if major == 2:
            return b"".join(self._until_break()) if argument is None else self._take(argument)
        if major == 3:
            if argument is None:
                return "".join(self._until_break())
            return self._take(argument).decode("utf-8", "replace")
        if major == 4:
            return self._until_break() if argument is None else [self.item() for _ in range(argument)]
        if major == 5:
            if argument is None:
                flat = self._until_break()
                return dict(zip(flat[::2], flat[1::2]))
            return {self.item(): self.item() for _ in range(argument)}
        return self.item()  # Major type 6: a semantic tag around one item.


class C2PAReader:
    """Find a C2PA manifest store in PNG, JPEG, or WebP bytes and summarize it.

    Only what the manifest claims is reported; its signature is not verified.
    """

    @classmethod
    def jumbf(cls, data: bytes) -> bytes | None:
        """Return the raw JUMBF manifest store embedded in the file, if any."""
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return cls._png(data)
        if data.startswith(b"\xff\xd8"):
            return cls._jpeg(data)
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return cls._webp(data)
        return None

    @staticmethod
    def _png(data: bytes) -> bytes | None:
        offset = 8
        while offset + 8 <= len(data):
            length = int.from_bytes(data[offset : offset + 4], "big")
            kind = data[offset + 4 : offset + 8]
            if kind == b"caBX":
                return data[offset + 8 : offset + 8 + length]
            if kind == b"IEND":
                break
            offset += 12 + length
        return None

    @staticmethod
    def _jpeg(data: bytes) -> bytes | None:
        """Join APP11 JUMBF segments; continuations repeat the 8-byte box header."""
        parts: dict[int, list[tuple[int, bytes]]] = {}
        offset = 2
        while offset + 4 <= len(data) and data[offset] == 0xFF:
            marker = data[offset + 1]
            if marker in (0xD9, 0xDA):
                break
            length = int.from_bytes(data[offset + 2 : offset + 4], "big")
            segment = data[offset + 4 : offset + 2 + length]
            if marker == 0xEB and segment[:2] == b"JP":
                instance = int.from_bytes(segment[2:4], "big")
                sequence = int.from_bytes(segment[4:8], "big")
                parts.setdefault(instance, []).append((sequence, segment[8:]))
            offset += 2 + length
        for chunks in parts.values():
            chunks.sort()
            store = chunks[0][1] + b"".join(chunk[8:] for _, chunk in chunks[1:])
            if b"c2pa" in store[:64]:
                return store
        return None

    @staticmethod
    def _webp(data: bytes) -> bytes | None:
        offset = 12
        while offset + 8 <= len(data):
            kind = data[offset : offset + 4]
            length = int.from_bytes(data[offset + 4 : offset + 8], "little")
            if kind == b"C2PA":
                return data[offset + 8 : offset + 8 + length]
            offset += 8 + length + (length & 1)
        return None

    @classmethod
    def boxes(cls, data: bytes) -> list[tuple[str, bytes]]:
        """Split ISO BMFF-style boxes into (type, payload) pairs."""
        boxes, offset = [], 0
        while offset + 8 <= len(data):
            size = int.from_bytes(data[offset : offset + 4], "big")
            kind = data[offset + 4 : offset + 8].decode("latin-1")
            header = 8
            if size == 1:
                size, header = int.from_bytes(data[offset + 8 : offset + 16], "big"), 16
            elif size == 0:
                size = len(data) - offset
            if size < header:
                break
            boxes.append((kind, data[offset + header : offset + size]))
            offset += size
        return boxes

    @classmethod
    def superbox(cls, payload: bytes) -> tuple[str, list[tuple[str, bytes]]]:
        """Return a JUMBF superbox's label and its content boxes."""
        children = cls.boxes(payload)
        label = ""
        if children and children[0][0] == "jumd":
            description = children[0][1]
            toggles = description[16] if len(description) > 16 else 0
            if toggles & 0x02:
                label = description[17:].split(b"\0", 1)[0].decode("utf-8", "replace")
            children = children[1:]
        return label, children

    @classmethod
    def labeled(cls, payload: bytes) -> dict[str, list[tuple[str, bytes]]]:
        """Map each child superbox's label to its content boxes."""
        _, children = cls.superbox(payload)
        return {
            label: content
            for kind, child in children
            if kind == "jumb"
            for label, content in [cls.superbox(child)]
        }

    @staticmethod
    def cbor(content: list[tuple[str, bytes]]) -> Any:
        return next((CBORDecoder.decode(body) for kind, body in content if kind == "cbor"), None)

    @staticmethod
    def source_type(value: Any) -> str | None:
        if not isinstance(value, str) or not value:
            return None
        return value.removeprefix(IPTC_SOURCE_TYPE_PREFIX)

    @staticmethod
    def agent(value: Any) -> str | None:
        if isinstance(value, dict):
            return " ".join(str(value[key]) for key in ("name", "version") if value.get(key)) or None
        return value if isinstance(value, str) else None

    @classmethod
    def generator(cls, claim: dict) -> str | None:
        info = claim.get("claim_generator_info")
        if isinstance(info, list):
            info = info[0] if info else None
        return cls.agent(info) or claim.get("claim_generator")

    @classmethod
    def read(cls, data: bytes) -> Row | None:
        """Summarize the active manifest: who produced it and how the image was made."""
        store = cls.jumbf(data)
        if store is None:
            return None
        summary: Row = {"present": True, "signature_verified": False}
        try:
            [(kind, payload)] = cls.boxes(store)[:1]
            manifests = list(cls.labeled(payload).items()) if kind == "jumb" else []
            if not manifests:
                return summary
            label, content = manifests[-1]
            summary["manifest"] = label
            parts = {
                part_label: part
                for part_kind, body in content
                if part_kind == "jumb"
                for part_label, part in [cls.superbox(body)]
            }
            claim = next(
                (cls.cbor(part) for key, part in parts.items() if key.startswith("c2pa.claim")), None
            ) or {}
            if isinstance(claim, dict):
                summary["claim_generator"] = cls.generator(claim)
                if claim.get("dc:title"):
                    summary["title"] = claim["dc:title"]
            actions = []
            assertions_box = next(
                (body for kind, body in content if kind == "jumb" and cls.superbox(body)[0] == "c2pa.assertions"),
                None,
            )
            assertions = cls.labeled(assertions_box) if assertions_box else {}
            for key, assertion in assertions.items():
                if not key.startswith("c2pa.actions"):
                    continue
                for action in (cls.cbor(assertion) or {}).get("actions", []):
                    if isinstance(action, dict):
                        actions.append(
                            {
                                "action": action.get("action"),
                                "when": action.get("when"),
                                "software_agent": cls.agent(action.get("softwareAgent")),
                                "digital_source_type": cls.source_type(action.get("digitalSourceType")),
                            }
                        )
            summary["actions"] = [
                {key: value for key, value in action.items() if value is not None} for action in actions
            ]
        except (ValueError, KeyError, IndexError, UnicodeDecodeError, struct.error):
            summary["parse_error"] = "The C2PA manifest could not be fully decoded."
        return summary


class ImageInspector:
    """Describe an image file: format, EXIF, text chunks, XMP, and provenance."""

    @staticmethod
    def _text(value: Any) -> str | None:
        """Render a metadata value as readable text, skipping binary blobs."""
        if isinstance(value, TiffImagePlugin.IFDRational):
            if not value.denominator:
                return None
            if 0 < value < 1 and value.numerator == 1:
                return f"1/{value.denominator}"
            return f"{float(value):g}"
        if isinstance(value, bytes):
            value = value.decode("utf-8", "replace")
        if not isinstance(value, (str, int, float)):
            return None
        text = str(value).strip().strip("\0")
        if not all(char.isprintable() or char in "\n\r\t" for char in text):
            return None
        return text[:MAX_TEXT_CHARS] + "…" if len(text) > MAX_TEXT_CHARS else text

    @staticmethod
    def _gps_degrees(values: Any, reference: Any) -> float | None:
        try:
            degrees, minutes, seconds = (float(value) for value in values)
        except (TypeError, ValueError, ZeroDivisionError):
            return None
        decimal = degrees + minutes / 60 + seconds / 3600
        return round(-decimal if reference in ("S", "W") else decimal, 6)

    @classmethod
    def exif(cls, image: PillowImage.Image) -> Row:
        """Return readable EXIF fields, with GPS converted to decimal degrees."""
        exif = image.getexif()
        fields: Row = {}
        ifds = [(exif, ExifTags.TAGS), (exif.get_ifd(ExifTags.IFD.Exif), ExifTags.TAGS)]
        for values, names in ifds:
            for tag, value in values.items():
                name = names.get(tag)
                if not name or name in {"MakerNote", "ExifOffset", "GPSInfo", "UserComment"}:
                    continue
                text = cls._text(value)
                if text:
                    fields[name] = text
        gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
        latitude = cls._gps_degrees(gps.get(2), gps.get(1))
        longitude = cls._gps_degrees(gps.get(4), gps.get(3))
        if latitude is not None and longitude is not None:
            fields["GPS"] = {"latitude": latitude, "longitude": longitude}
        return fields

    @classmethod
    def text_chunks(cls, image: PillowImage.Image) -> Row:
        """Return PNG/GIF text entries such as diffusion ``parameters``."""
        skipped = {"icc_profile", "exif", "xmp", "XML:com.adobe.xmp", "transparency", "dpi", "gamma"}
        chunks = {}
        for key, value in image.info.items():
            text = cls._text(value) if key not in skipped and isinstance(value, (str, bytes)) else None
            if text:
                chunks[key] = text
        return chunks

    @staticmethod
    def xmp(image: PillowImage.Image) -> Row:
        """Pick the provenance fields out of the raw XMP packet."""
        packet = image.info.get("xmp") or image.info.get("XML:com.adobe.xmp") or ""
        if isinstance(packet, bytes):
            packet = packet.decode("utf-8", "replace")
        fields = {}
        for key, name in (("digital_source_type", "DigitalSourceType"), ("creator_tool", "CreatorTool")):
            match = re.search(
                rf"{name}(?:=\"([^\"]+)\"|>\s*([^<]+?)\s*<)", packet
            )
            if match:
                value = match.group(1) or match.group(2)
                fields[key] = value.removeprefix(IPTC_SOURCE_TYPE_PREFIX)
        return fields

    @staticmethod
    def ai_signals(exif: Row, text: Row, xmp: Row, c2pa: Row | None, data: bytes) -> list[str]:
        """Explain every embedded marker that says generative AI made the image."""
        signals = []
        for action in (c2pa or {}).get("actions", []):
            if action.get("digital_source_type") in AI_SOURCE_TYPES:
                agent = action.get("software_agent") or (c2pa or {}).get("claim_generator") or "unknown tool"
                signals.append(f"C2PA {action.get('action', 'action')} by {agent}: {action['digital_source_type']}")
        if xmp.get("digital_source_type") in AI_SOURCE_TYPES:
            signals.append(f"XMP digital source type: {xmp['digital_source_type']}")
        for source, value in (
            ("EXIF Software", exif.get("Software")),
            ("XMP CreatorTool", xmp.get("creator_tool")),
            ("PNG Software", text.get("Software")),
        ):
            if value and AI_GENERATOR_PATTERN.search(value):
                signals.append(f"{source}: {value}")
        keys = sorted(AI_TEXT_KEYS & set(text))
        if keys:
            signals.append(f"Diffusion generation parameters in text chunks: {', '.join(keys)}")
        if not signals and c2pa and "parse_error" in c2pa:
            marker = next((name for name in AI_SOURCE_TYPES if name.encode() in data), None)
            if marker:
                signals.append(f"C2PA manifest marks the image as {marker}")
        return signals

    def inspect(self, data: bytes) -> Row:
        """Return file facts, embedded metadata, and an AI-generation verdict."""
        with PillowImage.open(BytesIO(data)) as image:
            exif, text, xmp = self.exif(image), self.text_chunks(image), self.xmp(image)
            details: Row = {
                "format": image.format,
                "width": image.width,
                "height": image.height,
                "mode": image.mode,
                "frames": getattr(image, "n_frames", 1),
                "has_transparency": image.has_transparency_data,
                "has_icc_profile": bool(image.info.get("icc_profile")),
            }
        c2pa = C2PAReader.read(data)
        signals = self.ai_signals(exif, text, xmp, c2pa, data)
        return {
            "file": details,
            "exif": exif,
            "text_chunks": text,
            "xmp": xmp,
            "c2pa": c2pa,
            "ai_generated": bool(signals),
            "ai_signals": signals,
        }

    @staticmethod
    def summarize(inspected: Row) -> Row:
        """Reduce an inspection to the fields worth sending for many images at once."""
        file, exif, xmp = inspected["file"], inspected["exif"], inspected["xmp"]
        c2pa = inspected["c2pa"] or {}
        created = next(
            (action for action in c2pa.get("actions", []) if action.get("action") == "c2pa.created"), {}
        )
        camera = " ".join(part for part in (exif.get("Make"), exif.get("Model")) if part)
        summary = {
            "format": file["format"],
            "width": file["width"],
            "height": file["height"],
            "ai_generated": inspected["ai_generated"],
            "ai_signals": inspected["ai_signals"] or None,
            "generator": created.get("software_agent") or c2pa.get("claim_generator"),
            "camera": camera or None,
            "lens": exif.get("LensModel"),
            "captured_at": exif.get("DateTimeOriginal"),
            "software": exif.get("Software") or xmp.get("creator_tool"),
            "gps": exif.get("GPS"),
        }
        return {key: value for key, value in summary.items() if value is not None}

    def is_ai_generated(self, data: bytes) -> bool:
        try:
            return self.inspect(data)["ai_generated"]
        except (OSError, ValueError, SyntaxError):
            return False

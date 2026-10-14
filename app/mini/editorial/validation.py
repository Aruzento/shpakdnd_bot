"""Validation shared by the CLI and loader; schemas are trusted release data."""
import json
import re
from html.parser import HTMLParser
from string import Formatter

class EditorialError(ValueError):
    pass

def fail(file, key, message):
    raise EditorialError(f"{file}: {key}: {message}")

def read_json(file):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                fail(file, key, "duplicate key")
            result[key] = value
        return result
    try:
        if file.stat().st_size > 2_000_000:
            fail(file, "<root>", "file exceeds 2 MB")
        value = json.loads(file.read_text(encoding="utf-8-sig"), object_pairs_hook=pairs)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        fail(file, "<root>", str(error))
    if not isinstance(value, dict):
        fail(file, "<root>", "expected JSON object")
    return value

def placeholders(value):
    result = []
    for _, name, spec, conversion in Formatter().parse(value):
        if name is None:
            continue
        if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", name) or spec or conversion:
            raise EditorialError("only named placeholders allowed; no attributes, indexing or format expressions")
        result.append(name)
    return set(result)

def emoji_id(value):
    return value is None or (isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,19}", value) is not None)

class TelegramHTML(HTMLParser):
    TAGS = {"b", "strong", "i", "em", "u", "ins", "s", "strike", "del",
            "span", "tg-spoiler", "a", "code", "pre", "blockquote", "tg-emoji"}
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.stack = []
    def handle_starttag(self, tag, attrs):
        if tag not in self.TAGS:
            raise EditorialError(f"unsupported Telegram tag {tag}")
        if len({k for k, _ in attrs}) != len(attrs):
            raise EditorialError("duplicate HTML attribute")
        attrs = dict(attrs)
        allowed = {"a": {"href"}, "span": {"class"}, "code": {"class"},
                   "blockquote": {"expandable"}, "tg-emoji": {"emoji-id"}}.get(tag, set())
        if set(attrs) - allowed:
            raise EditorialError(f"unsupported attributes on {tag}")
        if tag == "a" and not re.match(r"^(https?://|tg://)", attrs.get("href") or ""):
            raise EditorialError("link must use https/http/tg")
        if tag == "span" and attrs.get("class") != "tg-spoiler":
            raise EditorialError("span must be tg-spoiler")
        if tag == "tg-emoji" and (not attrs.get("emoji-id") or not emoji_id(attrs["emoji-id"])):
            raise EditorialError("invalid emoji-id")
        self.stack.append(tag)
    def handle_endtag(self, tag):
        if not self.stack or self.stack.pop() != tag:
            raise EditorialError(f"unbalanced Telegram tag {tag}")
    def handle_data(self, data):
        if "<" in data or re.search(r"&(?![A-Za-z]+;|#[0-9]+;|#x[0-9A-Fa-f]+;)", data):
            raise EditorialError("unescaped or malformed HTML")
    def handle_startendtag(self, tag, attrs):
        raise EditorialError("self-closing Telegram tags are not supported")
    def handle_entityref(self, name):
        if name not in {"lt", "gt", "amp", "quot"}:
            raise EditorialError(f"unsupported entity &{name};")
    def handle_charref(self, name):
        try:
            number = int(name[1:],16) if name.startswith("x") else int(name)
            if number <= 0 or number > 0x10ffff:
                raise ValueError
        except ValueError:
            raise EditorialError("invalid numeric HTML entity")
    def finish(self, value):
        # HTMLParser versions differ in their handling of unfinished tags at EOF.
        # Reject incomplete raw '<' syntax before feeding the parser.
        if "<" in re.sub(r"<[^<>]*>", "", value):
            raise EditorialError("incomplete or malformed Telegram tag")
        self.feed(value)
        self.close()
        if self.stack:
            raise EditorialError("unclosed Telegram tag")

def validate_entry(file, key, value, schema):
    try:
        kind = schema["kind"]
        if kind == "text":
            if not isinstance(value, str) or not value.strip():
                raise EditorialError("expected non-empty string")
            if len(value.encode("utf-16-le")) // 2 > schema["limit"]:
                raise EditorialError(f"value exceeds {schema['limit']} UTF-16 units")
            if schema.get("prefix") and not value.startswith(schema["prefix"]):
                raise EditorialError(f"template must begin with {schema['prefix']}")
            if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", value):
                raise EditorialError("control characters are not allowed")
            fields = placeholders(value)
            if fields != set(schema["parameters"]):
                raise EditorialError(f"required placeholders: {schema['parameters']}; found {sorted(fields)}")
            if schema.get("parse_mode") == "HTML":
                TelegramHTML().finish(value)
            elif re.search(r"</?[A-Za-z][^>]*>", value):
                raise EditorialError("HTML markup is not allowed on this plain-text screen")
        elif kind == "glossary":
            if not isinstance(value, dict) or set(value) != {"full", "short", "emoji", "icon_key"}:
                raise EditorialError("expected full/short/emoji/icon_key")
            for field in ("full", "short", "emoji", "icon_key"):
                if not isinstance(value[field], str) or (field != "emoji" and not value[field].strip()):
                    raise EditorialError(f"{field}: expected string")
                value[field].encode("utf-8")
                if re.search(r"[\x00-\x1f]", value[field]):
                    raise EditorialError(f"{field}: control characters not allowed")
                if len(value[field]) > (40 if field == "short" else 120):
                    raise EditorialError(f"{field}: value too long")
                if any(c in value[field] for c in "<>{}"):
                    raise EditorialError(f"{field}: markup/placeholders not allowed")
        elif kind == "icon":
            if not isinstance(value, dict) or set(value) != {"custom_emoji_id", "fallback"}:
                raise EditorialError("expected custom_emoji_id/fallback")
            if not emoji_id(value["custom_emoji_id"]):
                raise EditorialError("custom_emoji_id must be null or positive decimal string (up to 20 digits)")
            fallback = value["fallback"]
            if not isinstance(fallback, str) or len(fallback) > 16 or any(c in fallback for c in "<>{}"):
                raise EditorialError("fallback must be a short plain emoji")
            if fallback and not re.fullmatch(r"[\U0001f000-\U0001ffff\u2600-\u27ff\u2b00-\u2bff\u2190-\u23ff\u200d\ufe0f\u20e30-9#*]+", fallback):
                raise EditorialError("fallback must contain emoji characters only")
            if value["custom_emoji_id"] and not fallback:
                raise EditorialError("custom emoji requires a non-empty fallback")
            if fallback and not re.search(r"[\U0001f000-\U0001ffff\u2600-\u27ff\u2b00-\u2bff\u2190-\u23ff]",fallback):
                raise EditorialError("fallback must be a short plain emoji")
        else:
            raise EditorialError(f"unknown schema kind: {kind}")
    except (EditorialError, ValueError) as error:
        fail(file, key, str(error))
